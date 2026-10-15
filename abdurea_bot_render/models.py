# -*- coding: utf-8 -*-
"""
Business logic and Data Access Layer for ABDUREA STORE.
Encompasses users, catalog, order lifecycle, payments with receipt metadata,
conversation history, idempotency, restart-resilient state, and admin operations.
"""
import json
import uuid
import secrets
import datetime
import logging
from werkzeug.security import generate_password_hash, check_password_hash
from database import db_session
import bot_bridge

logger = logging.getLogger("models")

# Canonical Order Status Lifecycle
STATUS_NEW = "NEW"
STATUS_AWAITING_PAYMENT = "AWAITING_PAYMENT"
STATUS_PAYMENT_SUBMITTED = "PAYMENT_SUBMITTED"
STATUS_UNDER_REVIEW = "UNDER_REVIEW"
STATUS_PAYMENT_APPROVED = "PAYMENT_APPROVED"
STATUS_PAYMENT_REJECTED = "PAYMENT_REJECTED"
STATUS_PROCESSING = "PROCESSING"
STATUS_COMPLETED = "COMPLETED"
STATUS_CANCELLED = "CANCELLED"

# Backward compatibility map
STATUS_ALIASES = {
    "PENDING_PAYMENT": STATUS_AWAITING_PAYMENT,
    "PAYMENT_REVIEW": STATUS_PAYMENT_SUBMITTED,
    "ACCEPTED": STATUS_PAYMENT_APPROVED,
    "REJECTED": STATUS_PAYMENT_REJECTED,
}

# =====================================================================
# 1. USERS
# =====================================================================

def get_or_create_user(telegram_id, username=None, first_name=None, last_name=None, phone=None, full_name=None, is_premium=None):
    """Retrieves or creates a user record by Telegram ID, tracking Telegram Premium status."""
    computed_full_name = full_name or f"{first_name or ''} {last_name or ''}".strip() or username or f"User {telegram_id}"
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        now = datetime.datetime.utcnow().isoformat()
        is_new_user = False
        prem_val = 1 if is_premium else (0 if is_premium is False else (row["is_premium"] if row and "is_premium" in row.keys() else 0))
        if row:
            cursor.execute("""
                UPDATE users
                SET telegram_username = COALESCE(?, telegram_username),
                    first_name = COALESCE(?, first_name),
                    last_name = COALESCE(?, last_name),
                    full_name = COALESCE(?, full_name),
                    phone = COALESCE(?, phone),
                    is_premium = COALESCE(?, is_premium),
                    last_active_at = ?
                WHERE telegram_id = ?
            """, (username, first_name, last_name, computed_full_name, phone, prem_val, now, telegram_id))
            cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            user = dict(cursor.fetchone())
        else:
            cursor.execute("""
                INSERT INTO users (telegram_id, telegram_username, first_name, last_name, full_name, phone, is_premium, created_at, last_active_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (telegram_id, username, first_name, last_name, computed_full_name, phone, prem_val, now, now))
            cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            user = dict(cursor.fetchone())
            is_new_user = True

    if is_new_user:
        try:
            create_admin_notification(
                type="NEW_CUSTOMER",
                title="New Customer Joined",
                message=f"Customer {computed_full_name} (@{username or 'no_user'}) started the bot.",
                link=f"/#customers?id={telegram_id}"
            )
            bot_bridge.notify_admin_new_customer(user)
        except Exception:
            pass

    return user

def upsert_user(telegram_id, username=None, first_name=None, last_name=None, phone=None, full_name=None, is_premium=None):
    return get_or_create_user(telegram_id, username, first_name, last_name, phone, full_name, is_premium)

def get_user_by_telegram_id(telegram_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_user_language(telegram_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT language FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        if row and row["language"]:
            return row["language"]
        return "en"

def set_user_language(telegram_id, language):
    get_or_create_user(telegram_id)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET language = ? WHERE telegram_id = ?", (language, telegram_id))

def is_channel_verified(telegram_id):
    """Checks if user has verified official channel membership."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT channel_verified FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        if row and "channel_verified" in row.keys() and row["channel_verified"]:
            return True
        return False

def set_channel_verified(telegram_id, verified=True):
    """Updates user's channel membership verification status."""
    get_or_create_user(telegram_id)
    val = 1 if verified else 0
    with db_session() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE users SET channel_verified = ? WHERE telegram_id = ?", (val, telegram_id))
        except Exception as e:
            logger.warning(f"Could not update channel_verified: {e}")

def get_customers_list(search_query="", limit=100, offset=0):
    with db_session() as conn:
        cursor = conn.cursor()
        query = """
            SELECT u.*,
                   COUNT(o.id) as total_orders,
                   COALESCE(SUM(CASE WHEN o.status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING') THEN o.amount ELSE 0 END), 0) as total_spend
            FROM users u
            LEFT JOIN orders o ON u.telegram_id = o.telegram_id
        """
        params = []
        if search_query:
            query += " WHERE u.full_name LIKE ? OR u.telegram_username LIKE ? OR CAST(u.telegram_id AS TEXT) LIKE ? OR u.phone LIKE ?"
            p = f"%{search_query}%"
            params.extend([p, p, p, p])
        query += " GROUP BY u.id ORDER BY u.last_active_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]

def get_customer_profile(telegram_id):
    user = get_user_by_telegram_id(telegram_id)
    if not user:
        return None
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE telegram_id = ? ORDER BY created_at DESC", (telegram_id,))
        orders = [dict(r) for r in cursor.fetchall()]

        total_orders = len(orders)
        completed_orders = sum(1 for o in orders if o["status"] in (STATUS_COMPLETED, STATUS_PAYMENT_APPROVED, STATUS_PROCESSING, "ACCEPTED"))
        total_spend = sum(o["amount"] for o in orders if o["status"] in (STATUS_COMPLETED, STATUS_PAYMENT_APPROVED, STATUS_PROCESSING, "ACCEPTED"))

        cursor.execute("SELECT DISTINCT product_name FROM orders WHERE telegram_id = ?", (telegram_id,))
        purchased_products = [r["product_name"] for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM conversation_messages WHERE telegram_id = ? ORDER BY created_at ASC LIMIT 50", (telegram_id,))
        conversation = [dict(r) for r in cursor.fetchall()]

        return {
            **user,
            "total_orders": total_orders,
            "order_count": total_orders,
            "completed_orders": completed_orders,
            "total_spend": total_spend,
            "total_spent": total_spend,
            "total_spending": total_spend,
            "purchased_products": purchased_products,
            "products_purchased": purchased_products,
            "orders": orders,
            "conversation": conversation
        }

# =====================================================================
# 2. CATEGORIES & PRODUCTS
# =====================================================================

def get_active_categories():
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM categories WHERE active = 1 ORDER BY sort_order ASC")
        return [dict(r) for r in cursor.fetchall()]

def get_all_categories():
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM categories ORDER BY sort_order ASC")
        return [dict(r) for r in cursor.fetchall()]

def create_category(name, icon="📦", sort_order=0):
    cid = f"cat_{uuid.uuid4().hex[:8]}"
    slug = name.lower().strip().replace(" ", "-")
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO categories (id, name, slug, icon, sort_order, active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (cid, name.strip(), slug, icon, sort_order))
        cursor.execute("SELECT * FROM categories WHERE id = ?", (cid,))
        return dict(cursor.fetchone())

def get_all_products(include_inactive=True):
    """Catalog products query for Telegram bot and API."""
    with db_session() as conn:
        cursor = conn.cursor()
        if include_inactive:
            cursor.execute("SELECT * FROM products ORDER BY sort_order ASC")
        else:
            cursor.execute("SELECT * FROM products WHERE active = 1 ORDER BY sort_order ASC")
        return [dict(r) for r in cursor.fetchall()]

def get_all_products_admin():
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.*, c.name as category_name, c.icon as category_icon
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            ORDER BY p.sort_order ASC, p.created_at DESC
        """)
        products = [dict(r) for r in cursor.fetchall()]
        for p in products:
            p["plans"] = get_plans_by_product(p["id"], include_inactive=True)
            if isinstance(p.get("features"), str):
                try:
                    p["features"] = json.loads(p["features"])
                except Exception:
                    p["features"] = [p["features"]] if p["features"] else []
            elif p.get("features") is None:
                p["features"] = []

            if isinstance(p.get("requirements"), str):
                try:
                    p["requirements"] = json.loads(p["requirements"])
                except Exception:
                    p["requirements"] = [p["requirements"]] if p["requirements"] else []
            elif p.get("requirements") is None:
                p["requirements"] = []

            p["is_active"] = bool(p.get("active"))

            for pl in p.get("plans", []):
                pl["service_id"] = pl.get("product_id")
                pl["is_active"] = bool(pl.get("active"))
                pl["price"] = float(pl.get("price", 0))

        return products

def get_product_by_id(product_id):
    if not product_id:
        return None
    product_id = str(product_id).strip()
    raw_id = product_id[5:] if product_id.startswith("prod_") else product_id
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM products 
            WHERE id = ? OR id = ? OR id = ?
            LIMIT 1
        """, (product_id, f"prod_{raw_id}", raw_id))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_product_details(product_id):
    prod = get_product_by_id(product_id)
    if not prod:
        return None
    prod["plans"] = get_plans_by_product(product_id, include_inactive=True)
    if prod.get("category_id"):
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories WHERE id = ?", (prod["category_id"],))
            cat = cursor.fetchone()
            if cat:
                prod["category_name"] = cat["name"]
                prod["category_icon"] = cat["icon"]
    return prod

def create_product(name, category_id=None, description="", image_url="", features=None, requirements=None, active=1, plans=None):
    pid = secrets.token_hex(12)
    features_json = json.dumps(features or []) if isinstance(features, list) else (features or "[]")
    reqs_json = json.dumps(requirements or []) if isinstance(requirements, list) else (requirements or "[]")
    now = datetime.datetime.utcnow().isoformat()

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO products (id, category_id, name, description, image_url, features, requirements, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pid, category_id, name.strip(), description.strip(), image_url.strip(), features_json, reqs_json, active, now, now))

        if not plans:
            plans = [{"name": "Standard", "duration": "1 Month", "price": 0.0, "currency": "ETB", "active": 1}]

        for idx, plan in enumerate(plans):
            plan_id = secrets.token_hex(12)
            p_name = plan.get("name") or plan.get("duration", "Standard")
            p_dur = plan.get("duration", "1 Month")
            p_price = float(plan.get("price", 0))
            p_curr = plan.get("currency", "ETB")
            p_avail = plan.get("availability", "available")
            p_act = 1 if plan.get("active", True) in (True, 1, "1") else 0

            cursor.execute("""
                INSERT INTO product_plans (id, product_id, name, duration, price, currency, availability, active, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (plan_id, pid, p_name, p_dur, p_price, p_curr, p_avail, p_act, idx))

    return get_product_details(pid)

def update_product(product_id, name, category_id=None, description="", image_url="", features=None, requirements=None, active=1, plans=None):
    features_json = json.dumps(features or []) if isinstance(features, list) else (features or "[]")
    reqs_json = json.dumps(requirements or []) if isinstance(requirements, list) else (requirements or "[]")
    now = datetime.datetime.utcnow().isoformat()

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE products
            SET name = ?, category_id = ?, description = ?, image_url = ?, features = ?, requirements = ?, active = ?, updated_at = ?
            WHERE id = ?
        """, (name.strip(), category_id, description.strip(), image_url.strip(), features_json, reqs_json, active, now, product_id))

        if plans is not None:
            cursor.execute("DELETE FROM product_plans WHERE product_id = ?", (product_id,))
            for idx, plan in enumerate(plans):
                plan_id = plan.get("id") or f"plan_{uuid.uuid4().hex[:8]}"
                p_name = plan.get("name") or plan.get("duration", "Standard")
                p_dur = plan.get("duration", "1 Month")
                p_price = float(plan.get("price", 0))
                p_curr = plan.get("currency", "ETB")
                p_avail = plan.get("availability", "available")
                p_act = 1 if plan.get("active", True) in (True, 1, "1") else 0

                cursor.execute("""
                    INSERT INTO product_plans (id, product_id, name, duration, price, currency, availability, active, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (plan_id, product_id, p_name, p_dur, p_price, p_curr, p_avail, p_act, idx))

    return get_product_details(product_id)

def toggle_product_active(product_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE products SET active = CASE WHEN active = 1 THEN 0 ELSE 1 END WHERE id = ?", (product_id,))
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        return dict(cursor.fetchone())

def delete_product(product_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM product_plans WHERE product_id = ?", (product_id,))
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        return True

def update_product_field(product_id, field_name, new_value):
    """Updates a single attribute of a product (name, description, image_url, category_id)."""
    allowed_fields = {"name", "description", "image_url", "category_id", "sort_order", "active"}
    if field_name not in allowed_fields:
        raise ValueError(f"Field {field_name} is not editable.")
    now = datetime.datetime.utcnow().isoformat()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE products SET {field_name} = ?, updated_at = ? WHERE id = ?", (new_value, now, product_id))
        return get_product_details(product_id)

def add_product_plan(product_id, name, duration, price, currency="ETB"):
    """Appends a new pricing plan to an existing product."""
    plan_id = secrets.token_hex(12)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM product_plans WHERE product_id = ?", (product_id,))
        sort_order = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO product_plans (id, product_id, name, duration, price, currency, availability, active, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, 'available', 1, ?)
        """, (plan_id, product_id, name.strip(), duration.strip(), float(price), currency, sort_order))
        cursor.execute("SELECT * FROM product_plans WHERE id = ?", (plan_id,))
        return dict(cursor.fetchone())

def update_plan_details(plan_id, name=None, duration=None, price=None, active=None):
    """Updates any attribute of a specific plan."""
    plan = get_plan_by_id(plan_id)
    if not plan:
        return None
    new_name = name if name is not None else plan["name"]
    new_dur = duration if duration is not None else plan["duration"]
    new_price = float(price) if price is not None else plan["price"]
    new_act = active if active is not None else plan.get("active", 1)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE product_plans
            SET name = ?, duration = ?, price = ?, active = ?
            WHERE id = ?
        """, (new_name, new_dur, new_price, new_act, plan_id))
        cursor.execute("SELECT * FROM product_plans WHERE id = ?", (plan_id,))
        return dict(cursor.fetchone())

def delete_plan(plan_id):
    """Removes a pricing plan."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM product_plans WHERE id = ?", (plan_id,))
        return True

def get_plans_by_product(product_id, include_inactive=False):
    if not product_id:
        return []
    product_id = str(product_id).strip()
    raw_id = product_id[5:] if product_id.startswith("prod_") else product_id
    with db_session() as conn:
        cursor = conn.cursor()
        query = """
            SELECT * FROM product_plans 
            WHERE (product_id = ? OR product_id = ? OR product_id = ?)
        """
        params = [product_id, f"prod_{raw_id}", raw_id]
        if not include_inactive:
            query += " AND active = 1"
        query += " ORDER BY sort_order ASC, price ASC"
        cursor.execute(query, tuple(params))
        return [dict(r) for r in cursor.fetchall()]

def get_plan_by_id(plan_id):
    if not plan_id:
        return None
    plan_id = str(plan_id).strip()
    raw_id = plan_id[5:] if plan_id.startswith("plan_") else plan_id
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM product_plans 
            WHERE id = ? OR id = ? OR id = ?
            LIMIT 1
        """, (plan_id, f"plan_{raw_id}", raw_id))
        row = cursor.fetchone()
        return dict(row) if row else None

# =====================================================================
# 3. ORDER MANAGEMENT & STATUS AUDIT TRAIL
# =====================================================================

def generate_order_number():
    """Generates unique sequential Order ID format: ABD-YYYYMMDD-XXX."""
    today = datetime.datetime.utcnow().strftime("%Y%m%d")
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM orders WHERE order_number LIKE ?", (f"ABD-{today}-%",))
        count = cursor.fetchone()[0] + 1
        order_num = f"ABD-{today}-{count:03d}"

        # Double check collision
        cursor.execute("SELECT id FROM orders WHERE order_number = ?", (order_num,))
        if cursor.fetchone():
            order_num = f"ABD-{today}-{count:03d}-{uuid.uuid4().hex[:4].upper()}"
        return order_num

def create_order(telegram_id, product_id, plan_id=None, customer_name=None, customer_username=None, customer_phone=None, customer_delivery_info=None, customer_notes=None, payment_method=None, status=STATUS_NEW):
    """Creates a new customer order with automatic user linkage and initial status audit entry."""
    product = get_product_by_id(product_id)
    if not product:
        raise ValueError(f"Product {product_id} not found.")

    plan = get_plan_by_id(plan_id) if plan_id else None
    product_name = product["name"]
    plan_name = plan["name"] if plan else "Standard"
    plan_duration = plan["duration"] if plan else "Standard"
    amount = plan["price"] if plan else 0.0
    currency = plan.get("currency", "ETB") if plan else "ETB"

    user = get_or_create_user(
        telegram_id=telegram_id,
        username=customer_username,
        full_name=customer_name,
        phone=customer_phone
    )

    # Check for duplicate order creation in the last 60 seconds (protection)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM orders
            WHERE telegram_id = ? AND product_id = ? AND plan_id = ?
              AND status IN ('NEW', 'AWAITING_PAYMENT', 'PENDING_PAYMENT')
              AND datetime(created_at) > datetime('now', '-60 seconds')
            ORDER BY id DESC LIMIT 1
        """, (telegram_id, product_id, plan_id))
        dup = cursor.fetchone()
        if dup:
            logger.info(f"Duplicate order prevented for user {telegram_id}. Returning existing order #{dup['order_number']}")
            return dict(dup)

    order_number = generate_order_number()
    now = datetime.datetime.utcnow().isoformat()

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO orders (
                order_number, user_id, telegram_id, product_id, plan_id,
                product_name, plan_name, plan_duration, amount, currency,
                customer_name, customer_username, customer_phone, customer_delivery_info,
                customer_notes, payment_method, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order_number, user["id"], telegram_id, product_id, plan_id,
            product_name, plan_name, plan_duration, amount, currency,
            customer_name or user.get("full_name"), customer_username, customer_phone, customer_delivery_info,
            customer_notes, payment_method, status, now, now
        ))
        order_id = cursor.lastrowid

        # Record initial status in history
        cursor.execute("""
            INSERT INTO order_status_history (order_id, order_number, old_status, new_status, changed_by, notes)
            VALUES (?, ?, NULL, ?, 'SYSTEM', 'Order initiated')
        """, (order_id, order_number, status))

        cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        order = dict(cursor.fetchone())

    # Create Admin notification
    create_admin_notification(
        type="NEW_ORDER",
        title="New Order Placed",
        message=f"Order #{order_number} for {product_name} ({plan_duration}) by {customer_name or telegram_id}",
        link=f"/#orders?id={order_id}"
    )
    return order

def update_order_status(order_id, new_status, changed_by="SYSTEM", notes=None, notify_customer=True, delivery_content=None, file_path=None, rejection_reason=None):
    """
    Transitions order to a new status.
    Records transition in order_status_history and dispatches customer notification automatically.
    """
    order = get_order_by_id(order_id)
    if not order:
        return None, {"ok": False, "error": "Order not found"}

    old_status = order["status"]
    # Normalize aliases
    norm_new = STATUS_ALIASES.get(new_status, new_status)
    now = datetime.datetime.utcnow().isoformat()

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE orders
            SET status = ?,
                admin_notes = COALESCE(?, admin_notes),
                rejection_reason = COALESCE(?, rejection_reason),
                reviewed_by = COALESCE(?, reviewed_by),
                updated_at = ?
            WHERE id = ?
        """, (norm_new, notes, rejection_reason, changed_by, now, order_id))

        cursor.execute("""
            INSERT INTO order_status_history (order_id, order_number, old_status, new_status, changed_by, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (order_id, order["order_number"], old_status, norm_new, changed_by, notes or rejection_reason))

    updated_order = get_order_by_id(order_id)

    # Automatic Telegram Notification to customer based on new status
    tg_res = {"ok": True, "skipped": True}
    if notify_customer:
        if norm_new in (STATUS_PAYMENT_SUBMITTED, STATUS_UNDER_REVIEW, "PAYMENT_REVIEW"):
            tg_res = bot_bridge.notify_order_submitted(updated_order)
        elif norm_new in (STATUS_PAYMENT_APPROVED, STATUS_PROCESSING, "ACCEPTED"):
            tg_res = bot_bridge.notify_order_accepted(updated_order, admin_name=changed_by)
        elif norm_new in (STATUS_PAYMENT_REJECTED, "REJECTED"):
            tg_res = bot_bridge.notify_order_rejected(updated_order, reason=rejection_reason or notes, admin_name=changed_by)
        elif norm_new in (STATUS_COMPLETED,):
            content = delivery_content or notes or "Your digital service credentials have been configured."
            tg_res = bot_bridge.notify_order_completed(updated_order, delivery_content=content, file_path=file_path, admin_name=changed_by)

    return updated_order, tg_res

def get_order_by_id(order_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = cursor.fetchone()
        if not row:
            return None
        order = dict(row)
        order["payment"] = get_payment_by_order_id(order_id)
        order["delivery"] = get_delivery_by_order_id(order_id)
        order["deliveries"] = [order["delivery"]] if order["delivery"] else []
        order["status_history"] = get_order_status_history(order_id)
        return order

def get_order_by_number(order_number):
    with db_session() as conn:
        cursor = conn.cursor()
        clean = order_number.strip().replace("#", "")
        cursor.execute("SELECT id FROM orders WHERE order_number = ? COLLATE NOCASE", (clean,))
        row = cursor.fetchone()
        return get_order_by_id(row["id"]) if row else None

def get_user_orders(telegram_id, limit=20):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.*, p.image_url as product_image
            FROM orders o
            LEFT JOIN products p ON o.product_id = p.id
            WHERE o.telegram_id = ?
            ORDER BY o.created_at DESC
            LIMIT ?
        """, (telegram_id, limit))
        return [dict(r) for r in cursor.fetchall()]

def get_orders_list(status_filter="all", search_query="", limit=100, offset=0):
    with db_session() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM orders WHERE 1=1"
        params = []

        if status_filter and status_filter.lower() != "all":
            sf = status_filter.upper()
            if sf in ("PAYMENT_REVIEW", "UNDER_REVIEW", "PAYMENT_SUBMITTED"):
                query += " AND status IN ('PAYMENT_REVIEW', 'UNDER_REVIEW', 'PAYMENT_SUBMITTED')"
            elif sf in ("ACCEPTED", "PAYMENT_APPROVED"):
                query += " AND status IN ('ACCEPTED', 'PAYMENT_APPROVED')"
            elif sf in ("REJECTED", "PAYMENT_REJECTED"):
                query += " AND status IN ('REJECTED', 'PAYMENT_REJECTED')"
            else:
                query += " AND status = ?"
                params.append(sf)

        if search_query:
            query += " AND (order_number LIKE ? OR customer_name LIKE ? OR customer_username LIKE ? OR CAST(telegram_id AS TEXT) LIKE ? OR product_name LIKE ?)"
            sq = f"%{search_query}%"
            params.extend([sq, sq, sq, sq, sq])

        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        orders = [dict(r) for r in cursor.fetchall()]
        for o in orders:
            o["payment"] = get_payment_by_order_id(o["id"])
            o["delivery"] = get_delivery_by_order_id(o["id"])
        return orders

def get_order_status_history(order_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM order_status_history WHERE order_id = ? ORDER BY created_at ASC", (order_id,))
        return [dict(r) for r in cursor.fetchall()]

# Admin Workflow actions
def accept_order_payment(order_id, admin_id=1, admin_username="Admin", admin_note=None):
    """Admin manually verifies receipt and approves payment."""
    order = get_order_by_id(order_id)
    if not order:
        return None, {"ok": False, "error": "Order not found"}

    # Update payment review status
    payment = get_payment_by_order_id(order_id)
    if payment:
        update_payment_review(
            payment_id=payment["id"],
            review_status="PAYMENT_APPROVED",
            admin_note=admin_note,
            reviewed_by=admin_username
        )

    # Transition order status to PAYMENT_APPROVED / PROCESSING
    updated_order, tg_res = update_order_status(
        order_id=order_id,
        new_status=STATUS_PAYMENT_APPROVED,
        changed_by=admin_username,
        notes=admin_note,
        notify_customer=True
    )

    log_admin_action(
        admin_id=admin_id,
        admin_username=admin_username,
        action="Accepted Payment",
        target_type="order",
        target_id=str(order_id),
        details=f"Verified payment for order #{order['order_number']}"
    )

    create_admin_notification(
        type="PAYMENT_APPROVED",
        title="Payment Approved",
        message=f"Order #{order['order_number']} marked as approved by {admin_username}",
        link=f"/#orders?id={order_id}"
    )

    return updated_order, tg_res

def reject_order_payment(order_id, reason, admin_id=1, admin_username="Admin"):
    """Admin rejects invalid receipt with mandatory explanation."""
    order = get_order_by_id(order_id)
    if not order:
        return None, {"ok": False, "error": "Order not found"}

    payment = get_payment_by_order_id(order_id)
    if payment:
        update_payment_review(
            payment_id=payment["id"],
            review_status="PAYMENT_REJECTED",
            admin_note=reason,
            reviewed_by=admin_username
        )

    updated_order, tg_res = update_order_status(
        order_id=order_id,
        new_status=STATUS_PAYMENT_REJECTED,
        changed_by=admin_username,
        rejection_reason=reason,
        notify_customer=True
    )

    log_admin_action(
        admin_id=admin_id,
        admin_username=admin_username,
        action="Rejected Payment",
        target_type="order",
        target_id=str(order_id),
        details=f"Rejected payment for #{order['order_number']}. Reason: {reason}"
    )

    create_admin_notification(
        type="PAYMENT_REJECTED",
        title="Payment Rejected",
        message=f"Order #{order['order_number']} payment rejected by {admin_username}",
        link=f"/#orders?id={order_id}"
    )

    return updated_order, tg_res

def deliver_order(order_id, delivery_content, file_path=None, admin_id=1, admin_username="Admin"):
    """Saves delivery content, transitions order to COMPLETED, and notifies customer."""
    order = get_order_by_id(order_id)
    if not order:
        return None, {"ok": False, "error": "Order not found"}

    # Save delivery record
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO deliveries (order_id, content, delivery_type, file_path)
            VALUES (?, ?, ?, ?)
        """, (order_id, delivery_content.strip(), "DOCUMENT" if file_path else "TEXT", file_path))

    updated_order, tg_res = update_order_status(
        order_id=order_id,
        new_status=STATUS_COMPLETED,
        changed_by=admin_username,
        notes="Service delivered",
        notify_customer=True,
        delivery_content=delivery_content,
        file_path=file_path
    )

    log_admin_action(
        admin_id=admin_id,
        admin_username=admin_username,
        action="Delivered Order",
        target_type="order",
        target_id=str(order_id),
        details=f"Delivered service credentials for #{order['order_number']}"
    )

    create_admin_notification(
        type="ORDER_COMPLETED",
        title="Order Completed",
        message=f"Order #{order['order_number']} delivered and marked completed.",
        link=f"/#orders?id={order_id}"
    )

    return updated_order, tg_res

# =====================================================================
# 4. PAYMENTS & RECEIPT METADATA
# =====================================================================

def record_payment(order_id, payment_method, payment_sender_name, receipt_file, telegram_file_id=None, file_unique_id=None, media_type="photo", message_id=None, amount=None):
    """
    Records customer payment and receipt metadata.
    Enforces duplicate receipt protection using file_unique_id.
    """
    order = get_order_by_id(order_id)
    if not order:
        raise ValueError(f"Order #{order_id} does not exist.")

    # Duplicate receipt protection
    if file_unique_id:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, order_id FROM payments WHERE file_unique_id = ?", (file_unique_id,))
            existing = cursor.fetchone()
            if existing:
                logger.warning(f"Duplicate receipt submission detected: file_unique_id={file_unique_id} in payment #{existing['id']}")
                return dict(existing)

    pay_amount = float(amount) if amount is not None else float(order["amount"])
    currency = order.get("currency", "ETB")
    now = datetime.datetime.utcnow().isoformat()

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO payments (
                order_id, payment_method, payment_sender_name, amount, currency,
                receipt_file, telegram_file_id, file_unique_id, media_type,
                message_id, submitted_at, review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
        """, (
            order_id, payment_method, payment_sender_name, pay_amount, currency,
            receipt_file, telegram_file_id, file_unique_id, media_type,
            message_id, now
        ))
        payment_id = cursor.lastrowid
        cursor.execute("SELECT * FROM payments WHERE id = ?", (payment_id,))
        payment = dict(cursor.fetchone())

    # Transition order status to PAYMENT_SUBMITTED / UNDER_REVIEW
    update_order_status(
        order_id=order_id,
        new_status=STATUS_PAYMENT_SUBMITTED,
        changed_by="CUSTOMER",
        notes=f"Receipt submitted via {payment_method}",
        notify_customer=False
    )

    create_admin_notification(
        type="PAYMENT_REVIEW",
        title="Receipt Uploaded",
        message=f"Order #{order['order_number']}: {payment_method} receipt uploaded by {payment_sender_name} ({pay_amount:,.0f} {currency})",
        link=f"/#orders?id={order_id}"
    )
    return payment

def update_payment_review(payment_id, review_status, admin_note=None, reviewed_by="Admin"):
    now = datetime.datetime.utcnow().isoformat()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE payments
            SET review_status = ?, admin_note = ?, reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
        """, (review_status, admin_note, reviewed_by, now, payment_id))
        cursor.execute("SELECT * FROM payments WHERE id = ?", (payment_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_payment_by_order_id(order_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_payment_by_id(payment_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE id = ?", (payment_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_payments_list(status_filter="all", limit=100):
    with db_session() as conn:
        cursor = conn.cursor()
        query = """
            SELECT p.*, o.order_number, o.product_name, o.plan_duration, o.customer_name, o.customer_username, o.telegram_id
            FROM payments p
            JOIN orders o ON p.order_id = o.id
        """
        params = []
        if status_filter and status_filter.lower() != "all":
            query += " WHERE p.review_status = ?"
            params.append(status_filter.upper())
        query += " ORDER BY p.id DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]

def get_delivery_by_order_id(order_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM deliveries WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

# =====================================================================
# 5. CONVERSATION MESSAGES & TIMELINE
# =====================================================================

def log_conversation_message(telegram_id, direction, message_type, message_text, media_ref=None, related_order_id=None, delivery_status="DELIVERED", message_id=None):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO conversation_messages (
                message_id, telegram_id, direction, message_type,
                message_text, media_reference, related_order_id, delivery_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (message_id, telegram_id, direction, message_type, message_text, media_ref, related_order_id, delivery_status))
        msg_id = cursor.lastrowid
        cursor.execute("SELECT * FROM conversation_messages WHERE id = ?", (msg_id,))
        msg = dict(cursor.fetchone())

    return msg

def get_conversation_history(telegram_id, limit=50):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM conversation_messages
            WHERE telegram_id = ?
            ORDER BY created_at ASC
            LIMIT ?
        """, (telegram_id, limit))
        return [dict(r) for r in cursor.fetchall()]

# =====================================================================
# 6. IDEMPOTENCY & RESTART-RESILIENT CONVERSATION STATES
# =====================================================================

def is_update_processed(update_id):
    """Checks if Telegram update_id has already been processed to prevent duplicates."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT update_id FROM telegram_updates WHERE update_id = ?", (update_id,))
        return cursor.fetchone() is not None

def mark_update_processed(update_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO telegram_updates (update_id) VALUES (?)", (update_id,))

def save_user_state(telegram_id, state, state_data=None):
    """Persists customer interactive wizard state to SQLite."""
    data_json = json.dumps(state_data or {})
    now = datetime.datetime.utcnow().isoformat()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_conversation_states (telegram_id, state, state_data, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                state = excluded.state,
                state_data = excluded.state_data,
                updated_at = excluded.updated_at
        """, (telegram_id, state, data_json, now))

def get_user_state(telegram_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_conversation_states WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "state": row["state"],
            "data": json.loads(row["state_data"] or "{}"),
            "updated_at": row["updated_at"]
        }

def clear_user_state(telegram_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_conversation_states WHERE telegram_id = ?", (telegram_id,))

def save_admin_state(admin_id, state, state_data=None):
    """Persists admin interactive wizard state to SQLite so restarts/reloads never drop the state."""
    data_json = json.dumps(state_data or {})
    now = datetime.datetime.utcnow().isoformat()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_conversation_states (
                admin_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                state_data TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            INSERT INTO admin_conversation_states (admin_id, state, state_data, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(admin_id) DO UPDATE SET
                state = excluded.state,
                state_data = excluded.state_data,
                updated_at = excluded.updated_at
        """, (admin_id, state, data_json, now))

def get_admin_state(admin_id):
    """Retrieves active admin wizard state from SQLite."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_conversation_states (
                admin_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                state_data TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("SELECT * FROM admin_conversation_states WHERE admin_id = ?", (admin_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "state": row["state"],
            "data": json.loads(row["state_data"] or "{}"),
            "updated_at": row["updated_at"]
        }

def clear_admin_state(admin_id):
    """Clears admin wizard state upon completion or cancel."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_conversation_states (
                admin_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                state_data TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("DELETE FROM admin_conversation_states WHERE admin_id = ?", (admin_id,))

# =====================================================================
# 7. ADMIN AUTHENTICATION, NOTIFICATIONS & AUDIT LOGS
# =====================================================================

def get_admin_by_username(username):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admins WHERE username = ? COLLATE NOCASE", (username.strip(),))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_admin_by_id(admin_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admins WHERE id = ?", (admin_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def verify_admin_password(admin_dict, password):
    if not admin_dict or not admin_dict.get("password_hash"):
        return False
    return check_password_hash(admin_dict["password_hash"], password)

def update_admin_password(admin_id, new_password):
    hashed = generate_password_hash(new_password)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE admins SET password_hash = ? WHERE id = ?", (hashed, admin_id))

def update_admin_last_login(admin_id):
    now = datetime.datetime.utcnow().isoformat()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE admins SET last_login_at = ? WHERE id = ?", (now, admin_id))

def log_admin_action(admin_id, admin_username, action, target_type=None, target_id=None, details=None, ip_address=None):
    with db_session() as conn:
        cursor = conn.cursor()
        valid_admin_id = None
        if admin_id:
            try:
                cursor.execute("SELECT id FROM admins WHERE id = ?", (admin_id,))
                row = cursor.fetchone()
                if row:
                    valid_admin_id = row["id"]
            except Exception:
                valid_admin_id = None
        cursor.execute("""
            INSERT INTO admin_activity_logs (admin_id, admin_username, action, target_type, target_id, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (valid_admin_id, admin_username, action, target_type, str(target_id) if target_id else None, details, ip_address))

def get_recent_activity_logs(limit=100):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admin_activity_logs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cursor.fetchall()]

def create_admin_notification(type, title, message, link=None):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO admin_notifications (type, title, message, link, read)
            VALUES (?, ?, ?, ?, 0)
        """, (type, title, message, link))

def get_admin_notifications(limit=20, unread_only=False):
    with db_session() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM admin_notifications"
        if unread_only:
            query += " WHERE read = 0"
        query += " ORDER BY id DESC LIMIT ?"
        cursor.execute(query, (limit,))
        return [dict(r) for r in cursor.fetchall()]

def mark_notifications_as_read(notif_id=None):
    with db_session() as conn:
        cursor = conn.cursor()
        if notif_id:
            cursor.execute("UPDATE admin_notifications SET read = 1 WHERE id = ?", (notif_id,))
        else:
            cursor.execute("UPDATE admin_notifications SET read = 1 WHERE read = 0")

def get_unread_notification_count():
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM admin_notifications WHERE read = 0")
        return cursor.fetchone()[0]

def get_dashboard_kpis():
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM orders")
        total_orders = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM orders WHERE status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED')")
        completed_orders = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM orders WHERE status IN ('PAYMENT_REVIEW', 'UNDER_REVIEW', 'PAYMENT_SUBMITTED')")
        payments_under_review = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM orders WHERE status IN ('REJECTED', 'PAYMENT_REJECTED')")
        rejected_orders = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED')")
        total_revenue = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM users")
        total_customers = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM products WHERE active = 1")
        active_products = cursor.fetchone()[0]

        # Revenue today
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED') AND date(created_at) = date('now')")
        revenue_today = cursor.fetchone()[0]

        # Revenue this week
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED') AND date(created_at) >= date('now', '-7 days')")
        revenue_week = cursor.fetchone()[0]

        # Revenue this month
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED') AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')")
        revenue_month = cursor.fetchone()[0]

        # Revenue this year
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED') AND strftime('%Y', created_at) = strftime('%Y', 'now')")
        revenue_year = cursor.fetchone()[0]

        # Pending review queue
        cursor.execute("""
            SELECT o.id, o.order_number, o.product_name, o.amount, o.customer_name, o.customer_username, o.created_at,
                   p.payment_method, p.payment_sender_name, p.receipt_file
            FROM orders o
            JOIN payments p ON o.id = p.order_id
            WHERE o.status IN ('PAYMENT_REVIEW', 'UNDER_REVIEW', 'PAYMENT_SUBMITTED')
            ORDER BY o.id ASC LIMIT 10
        """)
        pending_queue = [dict(r) for r in cursor.fetchall()]

        # Recent orders
        cursor.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 10")
        recent_orders = [dict(r) for r in cursor.fetchall()]

        return {
            "total_revenue": total_revenue,
            "total_orders": total_orders,
            "completed_orders": completed_orders,
            "payments_under_review": payments_under_review,
            "rejected_orders": rejected_orders,
            "total_customers": total_customers,
            "active_products": active_products,
            "revenue_today": revenue_today,
            "revenue_week": revenue_week,
            "revenue_month": revenue_month,
            "revenue_year": revenue_year,
            "pending_review_queue": pending_queue,
            "recent_orders": recent_orders
        }

def get_market_reports(time_range="30d", custom_start=None, custom_end=None):
    with db_session() as conn:
        cursor = conn.cursor()
        date_filter = "date(created_at) >= date('now', '-30 days')"
        if time_range == "today":
            date_filter = "date(created_at) = date('now')"
        elif time_range == "7d":
            date_filter = "date(created_at) >= date('now', '-7 days')"
        elif time_range == "this_month":
            date_filter = "strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
        elif time_range == "this_year":
            date_filter = "strftime('%Y', created_at) = strftime('%Y', 'now')"
        elif custom_start and custom_end:
            date_filter = f"date(created_at) >= date('{custom_start}') AND date(created_at) <= date('{custom_end}')"

        cursor.execute(f"""
            SELECT date(created_at) as order_date,
                   COUNT(*) as total_orders,
                   SUM(CASE WHEN status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED') THEN 1 ELSE 0 END) as completed,
                   SUM(CASE WHEN status IN ('REJECTED', 'PAYMENT_REJECTED') THEN 1 ELSE 0 END) as rejected,
                   COALESCE(SUM(CASE WHEN status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED') THEN amount ELSE 0 END), 0) as revenue
            FROM orders
            WHERE {date_filter}
            GROUP BY date(created_at)
            ORDER BY order_date ASC
        """)
        timeline = [dict(r) for r in cursor.fetchall()]

        cursor.execute(f"""
            SELECT product_name,
                   COUNT(*) as total_sales,
                   COALESCE(SUM(amount), 0) as total_revenue
            FROM orders
            WHERE status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED') AND {date_filter}
            GROUP BY product_name
            ORDER BY total_revenue DESC LIMIT 5
        """)
        top_products = [dict(r) for r in cursor.fetchall()]

        cursor.execute(f"""
            SELECT COALESCE(payment_method, 'Telebirr') as method,
                   COUNT(*) as count,
                   COALESCE(SUM(amount), 0) as total_revenue
            FROM orders
            WHERE status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED') AND {date_filter}
            GROUP BY payment_method
        """)
        payment_methods = [dict(r) for r in cursor.fetchall()]

        cursor.execute(f"""
            SELECT COALESCE(AVG(amount), 0)
            FROM orders
            WHERE status IN ('COMPLETED', 'PAYMENT_APPROVED', 'PROCESSING', 'ACCEPTED') AND {date_filter}
        """)
        aov = cursor.fetchone()[0]

        summary = {
            "period_revenue": sum(t["revenue"] for t in timeline),
            "total_orders": sum(t["total_orders"] for t in timeline),
            "completed_orders": sum(t["completed"] for t in timeline),
            "rejected_orders": sum(t["rejected"] for t in timeline),
            "average_order_value": round(aov, 2)
        }

        return {
            "summary": summary,
            "timeline": timeline,
            "top_products": top_products,
            "payment_methods": payment_methods,
            "average_order_value": round(aov, 2)
        }

# =====================================================================
# 8. BROADCASTING & MESSAGING
# =====================================================================

def get_broadcast_recipients(target_type="all", target_filter=""):
    with db_session() as conn:
        cursor = conn.cursor()
        if target_type == "completed":
            cursor.execute("SELECT DISTINCT telegram_id FROM orders WHERE status IN ('COMPLETED', 'PAYMENT_APPROVED', 'ACCEPTED')")
        elif target_type == "pending":
            cursor.execute("SELECT DISTINCT telegram_id FROM orders WHERE status IN ('PAYMENT_REVIEW', 'UNDER_REVIEW', 'PAYMENT_SUBMITTED', 'PENDING_PAYMENT')")
        elif target_type == "premium":
            cursor.execute("SELECT telegram_id FROM users WHERE is_premium = 1")
        elif target_type == "product" and target_filter:
            cursor.execute("SELECT DISTINCT telegram_id FROM orders WHERE product_id = ? OR product_name LIKE ?", (target_filter, f"%{target_filter}%"))
        elif target_type == "specific" and target_filter:
            tf = target_filter.strip().replace("@", "")
            cursor.execute("SELECT telegram_id FROM users WHERE CAST(telegram_id AS TEXT) = ? OR telegram_username = ? COLLATE NOCASE", (tf, tf))
        else:
            cursor.execute("SELECT telegram_id FROM users")

        return [r["telegram_id"] for r in cursor.fetchall() if r["telegram_id"]]

def send_broadcast_message(target_type="all", target_filter="", message_text="", media_path=None, media_type="text", reply_markup=None, admin_id=1, admin_username="Admin"):
    recipients = get_broadcast_recipients(target_type, target_filter)
    res = bot_bridge.broadcast_to_users(recipients, message_text, media_path=media_path, media_type=media_type, reply_markup=reply_markup)

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO broadcasts (target_type, target_filter, message_text, media_path, total_recipients, sent_count, failed_count, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'COMPLETED')
        """, (target_type, target_filter, message_text, media_path, res["total"], res["sent"], res["failed"]))
        broadcast_id = cursor.lastrowid

    log_admin_action(
        admin_id=admin_id,
        admin_username=admin_username,
        action="Sent Broadcast",
        target_type="broadcast",
        target_id=str(broadcast_id),
        details=f"Broadcast #{broadcast_id} sent to {res['sent']}/{res['total']} users."
    )
    return {**res, "broadcast_id": broadcast_id}

def get_broadcasts_history(limit=50):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cursor.fetchall()]

# =====================================================================
# 9. SETTINGS & MAINTENANCE
# =====================================================================

def get_store_settings():
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM store_settings")
        return {r["key"]: r["value"] for r in cursor.fetchall()}

def get_setting(key, default=None):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM store_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row and row["value"] is not None else default

def update_store_settings(settings_dict, admin_id=1, admin_username="Admin"):
    now = datetime.datetime.utcnow().isoformat()
    with db_session() as conn:
        cursor = conn.cursor()
        for k, v in settings_dict.items():
            if k in ("telegram_bot_token", "secret_key", "password"):
                continue  # Never persist sensitive secrets via generic settings table
            cursor.execute("""
                INSERT INTO store_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (k, str(v), now))

    log_admin_action(
        admin_id=admin_id,
        admin_username=admin_username,
        action="Updated Settings",
        details="Updated store configuration and payment account details"
    )
    return get_store_settings()

DEFAULT_BUTTON_COLORS = {
    "btn_color_browse": "success",     # 🟢 Green
    "btn_color_orders": "default",     # ⚪ Default
    "btn_color_payment": "default",    # ⚪ Default
    "btn_color_support": "default",    # ⚪ Default
    "btn_color_about": "default",      # ⚪ Default
    "btn_color_language": "default",   # ⚪ Default
    "btn_color_back": "primary",       # 🔵 Blue
    "btn_color_refresh": "danger",     # 🔴 Red
    "btn_color_products": "success"    # 🟢 Green
}

def get_button_color(btn_key):
    """Returns the Telegram native style for the given button key ('success', 'primary', 'danger', or None)."""
    default_val = DEFAULT_BUTTON_COLORS.get(btn_key, "default")
    val = get_setting(btn_key, default_val)
    if not val or val in ("default", "none", "None", ""):
        return None
    return val

def set_button_color(btn_key, color_style, admin_id=1, admin_username="Admin"):
    """Saves the background color style for any specific button."""
    if not color_style or color_style in ("default", "none", "None"):
        color_style = "default"
    update_store_settings({btn_key: color_style}, admin_id=admin_id, admin_username=admin_username)
    return color_style

def reset_all_button_colors(admin_id=1, admin_username="Admin"):
    """Resets all button colors back to system defaults."""
    update_store_settings(DEFAULT_BUTTON_COLORS, admin_id=admin_id, admin_username=admin_username)
    return DEFAULT_BUTTON_COLORS

def clear_demo_history(admin_id=1, admin_username="Admin", clear_users=False):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM deliveries")
        cursor.execute("DELETE FROM payments")
        cursor.execute("DELETE FROM order_status_history")
        cursor.execute("DELETE FROM conversation_messages")
        cursor.execute("DELETE FROM orders")
        cursor.execute("DELETE FROM admin_notifications")
        if clear_users:
            cursor.execute("DELETE FROM users")
            cursor.execute("DELETE FROM user_conversation_states")

    log_admin_action(
        admin_id=admin_id,
        admin_username=admin_username,
        action="Cleared Demo History",
        details="Purged demo orders, payments, and delivery records"
    )
