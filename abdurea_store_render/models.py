import json
import sqlite3
import random
import string
from datetime import datetime
from database import get_db_connection
from werkzeug.security import check_password_hash, generate_password_hash

# ==========================================
# 1. SETTINGS & BRANDING
# ==========================================

def get_all_settings():
    conn = get_db_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row['key']: row['value'] for row in rows}

def get_setting(key, default=None):
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else default

def update_settings(settings_dict):
    conn = get_db_connection()
    for key, val in settings_dict.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(val))
        )
    conn.commit()
    conn.close()

# ==========================================
# 2. CATEGORIES
# ==========================================

def get_categories(only_active=True):
    conn = get_db_connection()
    sql = "SELECT * FROM categories"
    if only_active:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY sort_order ASC, id ASC"
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_category_by_id(cat_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_category_by_slug(slug):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone()
    conn.close()
    return dict(row) if row else None

def save_category(name, slug, icon="🤖", sort_order=0, is_active=1, cat_id=None):
    conn = get_db_connection()
    if cat_id:
        conn.execute('''
            UPDATE categories SET name=?, slug=?, icon=?, sort_order=?, is_active=? WHERE id=?
        ''', (name, slug, icon, sort_order, is_active, cat_id))
    else:
        conn.execute('''
            INSERT INTO categories (name, slug, icon, sort_order, is_active) VALUES (?, ?, ?, ?, ?)
        ''', (name, slug, icon, sort_order, is_active))
    conn.commit()
    conn.close()

def delete_category(cat_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    conn.commit()
    conn.close()

# ==========================================
# 3. SERVICES & PLANS
# ==========================================

def get_services(category_id=None, search_query=None, only_active=False, is_featured=None, limit=None, offset=None):
    conn = get_db_connection()
    sql = """
        SELECT s.*, c.name as category_name, c.icon as category_icon,
               MIN(p.stars_price) as min_stars_price, MAX(p.stars_price) as max_stars_price,
               COUNT(p.id) as plan_count
        FROM services s
        LEFT JOIN categories c ON s.category_id = c.id
        LEFT JOIN plans p ON s.id = p.service_id AND p.is_active = 1
        WHERE s.is_archived = 0
    """
    params = []

    if only_active:
        sql += " AND s.availability != 'unavailable'"

    if category_id:
        sql += " AND s.category_id = ?"
        params.append(category_id)

    if is_featured:
        sql += " AND s.is_featured = 1"

    if search_query:
        sql += " AND (LOWER(s.name) LIKE ? OR LOWER(s.short_description) LIKE ? OR LOWER(s.description) LIKE ?)"
        like_q = f"%{search_query.lower()}%"
        params.extend([like_q, like_q, like_q])

    sql += " GROUP BY s.id ORDER BY s.sort_order ASC, s.id ASC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
        if offset is not None:
            sql += f" OFFSET {int(offset)}"

    rows = conn.execute(sql, params).fetchall()
    services = []
    for r in rows:
        d = dict(r)
        try:
            d['features_list'] = json.loads(d.get('features') or '[]')
        except Exception:
            d['features_list'] = [f.strip() for f in (d.get('features') or '').split("\n") if f.strip()]
        services.append(d)
    conn.close()
    return services

def count_services(category_id=None, search_query=None, only_active=False):
    conn = get_db_connection()
    sql = "SELECT COUNT(*) as count FROM services s WHERE s.is_archived = 0"
    params = []

    if only_active:
        sql += " AND s.availability != 'unavailable'"

    if category_id:
        sql += " AND s.category_id = ?"
        params.append(category_id)

    if search_query:
        sql += " AND (LOWER(s.name) LIKE ? OR LOWER(s.short_description) LIKE ? OR LOWER(s.description) LIKE ?)"
        like_q = f"%{search_query.lower()}%"
        params.extend([like_q, like_q, like_q])

    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row['count'] if row else 0

def get_service_by_id(service_id, increment_view=False):
    conn = get_db_connection()
    if increment_view:
        conn.execute("UPDATE services SET views_count = views_count + 1 WHERE id = ?", (service_id,))
        conn.commit()

    row = conn.execute("""
        SELECT s.*, c.name as category_name, c.icon as category_icon
        FROM services s
        LEFT JOIN categories c ON s.category_id = c.id
        WHERE s.id = ?
    """, (service_id,)).fetchone()

    if not row:
        conn.close()
        return None

    service = dict(row)
    try:
        service['features_list'] = json.loads(service.get('features') or '[]')
    except Exception:
        service['features_list'] = [f.strip() for f in (service.get('features') or '').split("\n") if f.strip()]

    # Attach plans
    plan_rows = conn.execute("""
        SELECT * FROM plans WHERE service_id = ? AND is_active = 1 ORDER BY sort_order ASC, stars_price ASC
    """, (service_id,)).fetchall()
    service['plans'] = [dict(p) for p in plan_rows]

    # Attach rating summary
    rating_row = conn.execute("""
        SELECT COUNT(*) as review_count, AVG(rating) as avg_rating
        FROM reviews WHERE service_id = ? AND status = 'published'
    """, (service_id,)).fetchone()
    service['review_count'] = rating_row['review_count'] if rating_row else 0
    service['avg_rating'] = round(rating_row['avg_rating'], 1) if (rating_row and rating_row['avg_rating']) else None

    conn.close()
    return service

def get_service_by_slug(slug, increment_view=False):
    conn = get_db_connection()
    row = conn.execute("SELECT id FROM services WHERE slug = ?", (slug,)).fetchone()
    conn.close()
    if not row:
        return None
    return get_service_by_id(row['id'], increment_view=increment_view)

def save_service(data, service_id=None):
    conn = get_db_connection()
    features = data.get('features')
    if isinstance(features, list):
        features = json.dumps(features)

    if service_id:
        conn.execute('''
            UPDATE services SET
                name=?, slug=?, short_description=?, description=?, image_url=?, category_id=?,
                availability=?, is_featured=?, is_popular=?, is_recommended=?, is_premium=?,
                features=?, requirements=?, important_notes=?, sort_order=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (
            data['name'], data['slug'], data.get('short_description'), data.get('description'),
            data.get('image_url'), data.get('category_id'), data.get('availability', 'available'),
            int(data.get('is_featured', 0)), int(data.get('is_popular', 0)),
            int(data.get('is_recommended', 0)), int(data.get('is_premium', 0)),
            features, data.get('requirements'), data.get('important_notes'),
            int(data.get('sort_order', 0)), service_id
        ))
    else:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO services (
                name, slug, short_description, description, image_url, category_id,
                availability, is_featured, is_popular, is_recommended, is_premium,
                features, requirements, important_notes, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['name'], data['slug'], data.get('short_description'), data.get('description'),
            data.get('image_url'), data.get('category_id'), data.get('availability', 'available'),
            int(data.get('is_featured', 0)), int(data.get('is_popular', 0)),
            int(data.get('is_recommended', 0)), int(data.get('is_premium', 0)),
            features, data.get('requirements'), data.get('important_notes'),
            int(data.get('sort_order', 0))
        ))
        service_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return service_id

def archive_service(service_id):
    conn = get_db_connection()
    conn.execute("UPDATE services SET is_archived = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (service_id,))
    conn.commit()
    conn.close()

def duplicate_service(service_id):
    service = get_service_by_id(service_id)
    if not service:
        return None
    new_data = dict(service)
    new_data['name'] = f"{service['name']} (Copy)"
    new_data['slug'] = f"{service['slug']}-copy-{random.randint(100, 999)}"
    new_id = save_service(new_data)

    # Copy plans
    conn = get_db_connection()
    for p in service.get('plans', []):
        conn.execute('''
            INSERT INTO plans (service_id, duration, stars_price, local_price_etb, is_best_value, is_active, availability, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (new_id, p['duration'], p['stars_price'], p.get('local_price_etb', 0), p.get('is_best_value', 0), p.get('is_active', 1), p.get('availability', 'available'), p.get('sort_order', 0)))
    conn.commit()
    conn.close()
    return new_id

def get_plans_for_service(service_id, only_active=True):
    conn = get_db_connection()
    sql = "SELECT * FROM plans WHERE service_id = ?"
    if only_active:
        sql += " AND is_active = 1 AND availability = 'available'"
    sql += " ORDER BY sort_order ASC, stars_price ASC"
    rows = conn.execute(sql, (service_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_plan_by_id(plan_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def save_plan(service_id, duration, stars_price, local_price_etb=0, is_best_value=0, is_active=1, availability='available', sort_order=0, plan_id=None):
    conn = get_db_connection()
    if plan_id:
        conn.execute('''
            UPDATE plans SET
                duration=?, stars_price=?, local_price_etb=?, is_best_value=?, is_active=?, availability=?, sort_order=?
            WHERE id=?
        ''', (duration, int(stars_price), float(local_price_etb or 0), int(is_best_value), int(is_active), availability, int(sort_order), plan_id))
    else:
        conn.execute('''
            INSERT INTO plans (service_id, duration, stars_price, local_price_etb, is_best_value, is_active, availability, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (service_id, duration, int(stars_price), float(local_price_etb or 0), int(is_best_value), int(is_active), availability, int(sort_order)))
    conn.commit()
    conn.close()

def delete_plan(plan_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()

# ==========================================
# 4. CUSTOMERS & CONVERSATION STATES
# ==========================================

def get_or_create_customer(telegram_id, username=None, first_name=None, full_name=None):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM customers WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if row:
        conn.execute("""
            UPDATE customers SET
                username = COALESCE(?, username),
                first_name = COALESCE(?, first_name),
                full_name = COALESCE(?, full_name),
                last_active_at = CURRENT_TIMESTAMP
            WHERE telegram_id = ?
        """, (username, first_name, full_name, telegram_id))
        conn.commit()
        customer = dict(conn.execute("SELECT * FROM customers WHERE telegram_id = ?", (telegram_id,)).fetchone())
    else:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO customers (telegram_id, username, first_name, full_name)
            VALUES (?, ?, ?, ?)
        """, (telegram_id, username, first_name, full_name))
        conn.commit()
        customer = dict(conn.execute("SELECT * FROM customers WHERE id = ?", (cursor.lastrowid,)).fetchone())
    conn.close()
    return customer

def update_customer_details(telegram_id, full_name=None, email=None, is_email_verified=None):
    conn = get_db_connection()
    updates = ["last_active_at = CURRENT_TIMESTAMP"]
    params = []

    if full_name is not None:
        updates.append("full_name = ?")
        params.append(full_name)
    if email is not None:
        updates.append("email = ?")
        params.append(email)
    if is_email_verified is not None:
        updates.append("is_email_verified = ?")
        params.append(int(is_email_verified))

    params.append(telegram_id)
    sql = f"UPDATE customers SET {', '.join(updates)} WHERE telegram_id = ?"
    conn.execute(sql, params)
    conn.commit()
    conn.close()

def get_customer_by_tgid(telegram_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM customers WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_customers(limit=100, offset=0):
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT c.*,
               COUNT(o.id) as order_count,
               COALESCE(SUM(CASE WHEN o.payment_status = 'Paid' THEN o.snapshot_price_stars ELSE 0 END), 0) as total_spent_stars
        FROM customers c
        LEFT JOIN orders o ON c.id = o.customer_id
        GROUP BY c.id
        ORDER BY c.last_active_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_conversation_state(telegram_id):
    conn = get_db_connection()
    row = conn.execute("SELECT current_state, state_data FROM conversation_states WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    if not row:
        return None, {}
    data = {}
    if row['state_data']:
        try:
            data = json.loads(row['state_data'])
        except Exception:
            data = {}
    return row['current_state'], data

def set_conversation_state(telegram_id, state, state_data=None):
    conn = get_db_connection()
    data_str = json.dumps(state_data or {})
    conn.execute("""
        INSERT INTO conversation_states (telegram_id, current_state, state_data, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(telegram_id) DO UPDATE SET
            current_state = excluded.current_state,
            state_data = excluded.state_data,
            updated_at = CURRENT_TIMESTAMP
    """, (telegram_id, state, data_str))
    conn.commit()
    conn.close()

def clear_conversation_state(telegram_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM conversation_states WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()

# ==========================================
# 5. ORDERS & PAYMENTS (IDEMPOTENT & AUDITED)
# ==========================================

def generate_order_ref():
    chars = string.ascii_uppercase + string.digits
    suffix = ''.join(random.choices(chars, k=5))
    return f"ABD-{datetime.utcnow().year}-{suffix}"

def create_order(customer_id, service_id, plan_id, customer_name, customer_email, source='telegram_bot'):
    service = get_service_by_id(service_id)
    plan = get_plan_by_id(plan_id)
    if not service or not plan:
        raise ValueError("Service or Plan not found")

    if service.get('availability') == 'unavailable' or plan.get('availability') == 'unavailable':
        raise ValueError("This service or plan is currently unavailable")

    order_ref = generate_order_ref()
    snapshot_terms = f"Duration: {plan['duration']}. Fulfillment within 30-120 mins. Direct delivery via Telegram and email."

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO orders (
            order_ref, customer_id, service_id, plan_id,
            snapshot_service_name, snapshot_plan_duration, snapshot_price_stars, snapshot_terms,
            customer_name, customer_email, source, payment_status, fulfillment_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Unpaid', 'Not Started')
    """, (
        order_ref, customer_id, service_id, plan_id,
        service['name'], plan['duration'], int(plan['stars_price']), snapshot_terms,
        customer_name, customer_email, source
    ))
    order_id = cursor.lastrowid

    # Record initial history
    cursor.execute("""
        INSERT INTO order_history (order_id, actor, previous_status, new_status, notes)
        VALUES (?, 'customer', NULL, 'Created (Unpaid)', 'Order created waiting for payment')
    """, (order_id,))

    conn.commit()
    conn.close()
    return get_order_by_id(order_id)

def record_stars_payment(order_id, telegram_payment_charge_id, provider_payment_charge_id=None, amount=None, raw_payload=None):
    """
    Idempotently record payment for an order.
    If charge_id was already recorded, returns existing payment record.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check for duplicate charge ID
    existing = cursor.execute(
        "SELECT * FROM payments WHERE telegram_payment_charge_id = ?",
        (telegram_payment_charge_id,)
    ).fetchone()

    if existing:
        conn.close()
        return dict(existing), False  # already processed

    order = cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise ValueError(f"Order #{order_id} not found")

    pay_amount = amount if amount is not None else order['snapshot_price_stars']

    # Insert payment record
    cursor.execute("""
        INSERT INTO payments (
            order_id, method, currency, amount, telegram_payment_charge_id,
            telegram_provider_payment_charge_id, status, raw_payload
        ) VALUES (?, 'stars', 'XTR', ?, ?, ?, 'Paid', ?)
    """, (
        order_id, pay_amount, telegram_payment_charge_id,
        provider_payment_charge_id, json.dumps(raw_payload) if raw_payload else None
    ))
    payment_id = cursor.lastrowid

    # Update order status
    cursor.execute("""
        UPDATE orders SET
            payment_status = 'Paid',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (order_id,))

    # Record history
    cursor.execute("""
        INSERT INTO order_history (order_id, actor, previous_status, new_status, notes)
        VALUES (?, 'telegram_stars', 'Unpaid', 'Paid', ?)
    """, (order_id, f"Payment confirmed via Telegram Stars (Charge ID: {telegram_payment_charge_id})"))

    conn.commit()
    conn.close()
    return get_order_by_id(order_id), True

def get_order_by_ref(order_ref):
    conn = get_db_connection()
    row = conn.execute("""
        SELECT o.*, c.telegram_id, c.username as customer_telegram_username,
               s.image_url as service_image_url
        FROM orders o
        LEFT JOIN customers c ON o.customer_id = c.id
        LEFT JOIN services s ON o.service_id = s.id
        WHERE o.order_ref = ?
    """, (order_ref,)).fetchone()
    if not row:
        conn.close()
        return None
    order = dict(row)
    # Get payments
    payments = conn.execute("SELECT * FROM payments WHERE order_id = ?", (order['id'],)).fetchall()
    order['payments'] = [dict(p) for p in payments]
    conn.close()
    return order

def get_order_by_id(order_id):
    conn = get_db_connection()
    row = conn.execute("""
        SELECT o.*, c.telegram_id, c.username as customer_telegram_username,
               s.image_url as service_image_url
        FROM orders o
        LEFT JOIN customers c ON o.customer_id = c.id
        LEFT JOIN services s ON o.service_id = s.id
        WHERE o.id = ?
    """, (order_id,)).fetchone()
    if not row:
        conn.close()
        return None
    order = dict(row)
    payments = conn.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,)).fetchall()
    order['payments'] = [dict(p) for p in payments]
    history = conn.execute("SELECT * FROM order_history WHERE order_id = ? ORDER BY created_at ASC", (order_id,)).fetchall()
    order['history'] = [dict(h) for h in history]
    conn.close()
    return order

def get_customer_orders(customer_id=None, telegram_id=None, limit=20):
    conn = get_db_connection()
    if telegram_id:
        cust = conn.execute("SELECT id FROM customers WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if not cust:
            conn.close()
            return []
        customer_id = cust['id']

    rows = conn.execute("""
        SELECT o.*, s.image_url as service_image_url
        FROM orders o
        LEFT JOIN services s ON o.service_id = s.id
        WHERE o.customer_id = ?
        ORDER BY o.created_at DESC
        LIMIT ?
    """, (customer_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_orders(payment_status=None, fulfillment_status=None, search_query=None, limit=50, offset=0):
    conn = get_db_connection()
    sql = """
        SELECT o.*, c.telegram_id, c.username as customer_telegram_username,
               (SELECT COUNT(*) FROM payments p WHERE p.order_id = o.id) as payment_count,
               (SELECT telegram_payment_charge_id FROM payments p WHERE p.order_id = o.id LIMIT 1) as charge_id
        FROM orders o
        LEFT JOIN customers c ON o.customer_id = c.id
        WHERE 1=1
    """
    params = []

    if payment_status:
        sql += " AND o.payment_status = ?"
        params.append(payment_status)

    if fulfillment_status:
        sql += " AND o.fulfillment_status = ?"
        params.append(fulfillment_status)

    if search_query:
        sql += " AND (o.order_ref LIKE ? OR LOWER(o.customer_name) LIKE ? OR LOWER(o.customer_email) LIKE ? OR CAST(c.telegram_id AS TEXT) LIKE ?)"
        like_q = f"%{search_query.lower()}%"
        params.extend([like_q, like_q, like_q, f"%{search_query}%"])

    sql += " ORDER BY o.created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_order_fulfillment(order_id, new_status, admin_notes=None, customer_delivery_notes=None, actor='admin'):
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise ValueError(f"Order #{order_id} not found")

    old_status = order['fulfillment_status']

    updates = ["fulfillment_status = ?", "updated_at = CURRENT_TIMESTAMP"]
    params = [new_status]

    if admin_notes is not None:
        updates.append("admin_notes = ?")
        params.append(admin_notes)

    if customer_delivery_notes is not None:
        updates.append("customer_delivery_notes = ?")
        params.append(customer_delivery_notes)

    params.append(order_id)
    conn.execute(f"UPDATE orders SET {', '.join(updates)} WHERE id = ?", params)

    # History record
    note_text = f"Fulfillment updated to {new_status}"
    if customer_delivery_notes:
        note_text += f" (Delivery notes added)"
    conn.execute("""
        INSERT INTO order_history (order_id, actor, previous_status, new_status, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (order_id, actor, old_status, new_status, note_text))

    conn.commit()
    conn.close()
    return get_order_by_id(order_id)

def update_order_payment_status(order_id, new_status, actor='system', notes=None):
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise ValueError(f"Order #{order_id} not found")

    old_status = order['payment_status']
    conn.execute("UPDATE orders SET payment_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_status, order_id))
    conn.execute("""
        INSERT INTO order_history (order_id, actor, previous_status, new_status, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (order_id, actor, old_status, new_status, notes or f"Payment status set to {new_status}"))

    conn.commit()
    conn.close()
    return get_order_by_id(order_id)

# ==========================================
# 6. REVIEWS & RATINGS
# ==========================================

def create_review(order_id, rating, comment=None):
    order = get_order_by_id(order_id)
    if not order:
        raise ValueError("Order not found")

    if order['fulfillment_status'] != 'Completed':
        raise ValueError("Reviews are only allowed for completed purchases.")

    conn = get_db_connection()
    # Check if review already exists for this order
    existing = conn.execute("SELECT id FROM reviews WHERE order_id = ?", (order_id,)).fetchone()
    if existing:
        conn.close()
        raise ValueError("A review has already been submitted for this order.")

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO reviews (order_id, customer_id, service_id, rating, comment, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    """, (order_id, order['customer_id'], order['service_id'], int(rating), comment))
    review_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return review_id

def can_customer_review_order(order_id, telegram_id):
    conn = get_db_connection()
    row = conn.execute("""
        SELECT o.id, o.fulfillment_status, r.id as existing_review_id
        FROM orders o
        JOIN customers c ON o.customer_id = c.id
        LEFT JOIN reviews r ON o.id = r.order_id
        WHERE o.id = ? AND c.telegram_id = ?
    """, (order_id, telegram_id)).fetchone()
    conn.close()
    if not row:
        return False, "Order not found"
    if row['fulfillment_status'] != 'Completed':
        return False, "Order must be Completed to submit a review"
    if row['existing_review_id']:
        return False, "You have already reviewed this order"
    return True, "Eligible"

def get_reviews_for_service(service_id, only_published=True):
    conn = get_db_connection()
    sql = """
        SELECT r.*, c.first_name, c.full_name
        FROM reviews r
        JOIN customers c ON r.customer_id = c.id
        WHERE r.service_id = ?
    """
    if only_published:
        sql += " AND r.status = 'published'"
    sql += " ORDER BY r.created_at DESC"
    rows = conn.execute(sql, (service_id,)).fetchall()
    conn.close()
    reviews = []
    for r in rows:
        d = dict(r)
        # Privacy-preserving display name (e.g. "Dawit T.")
        name = d.get('full_name') or d.get('first_name') or 'Customer'
        parts = name.split()
        if len(parts) > 1:
            d['display_name'] = f"{parts[0]} {parts[1][0]}."
        else:
            d['display_name'] = parts[0]
        reviews.append(d)
    return reviews

def get_all_reviews(status=None, limit=100):
    conn = get_db_connection()
    sql = """
        SELECT r.*, s.name as service_name, c.full_name as customer_name, c.telegram_id, o.order_ref
        FROM reviews r
        JOIN services s ON r.service_id = s.id
        JOIN customers c ON r.customer_id = c.id
        JOIN orders o ON r.order_id = o.id
    """
    params = []
    if status:
        sql += " WHERE r.status = ?"
        params.append(status)
    sql += " ORDER BY r.created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_review_status(review_id, new_status):
    conn = get_db_connection()
    conn.execute("""
        UPDATE reviews SET status = ?, reviewed_at = CURRENT_TIMESTAMP WHERE id = ?
    """, (new_status, review_id))
    conn.commit()
    conn.close()

# ==========================================
# 7. PROMOTIONS
# ==========================================

def get_active_promotions(limit=5):
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT * FROM promotions
        WHERE is_active = 1
        ORDER BY priority DESC, id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_promotions():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM promotions ORDER BY priority DESC, id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_promotion(title, display_text, artwork_url=None, cta_type='service', cta_target=None, is_active=1, priority=0, promo_id=None):
    conn = get_db_connection()
    if promo_id:
        conn.execute('''
            UPDATE promotions SET
                title=?, display_text=?, artwork_url=?, cta_type=?, cta_target=?, is_active=?, priority=?
            WHERE id=?
        ''', (title, display_text, artwork_url, cta_type, cta_target, int(is_active), int(priority), promo_id))
    else:
        conn.execute('''
            INSERT INTO promotions (title, display_text, artwork_url, cta_type, cta_target, is_active, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (title, display_text, artwork_url, cta_type, cta_target, int(is_active), int(priority)))
    conn.commit()
    conn.close()

def delete_promotion(promo_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM promotions WHERE id = ?", (promo_id,))
    conn.commit()
    conn.close()

# ==========================================
# 8. FAQS
# ==========================================

def get_faqs(only_active=True):
    conn = get_db_connection()
    sql = "SELECT * FROM faqs"
    if only_active:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY sort_order ASC, id ASC"
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_faq(question, answer, sort_order=0, is_active=1, faq_id=None):
    conn = get_db_connection()
    if faq_id:
        conn.execute('''
            UPDATE faqs SET question=?, answer=?, sort_order=?, is_active=? WHERE id=?
        ''', (question, answer, int(sort_order), int(is_active), faq_id))
    else:
        conn.execute('''
            INSERT INTO faqs (question, answer, sort_order, is_active) VALUES (?, ?, ?, ?)
        ''', (question, answer, int(sort_order), int(is_active)))
    conn.commit()
    conn.close()

def delete_faq(faq_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM faqs WHERE id = ?", (faq_id,))
    conn.commit()
    conn.close()

# ==========================================
# 9. ADMIN AUTH & AUDIT LOGS
# ==========================================

def authenticate_admin(username, password):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone()
    if user and check_password_hash(user['password_hash'], password):
        conn.execute("UPDATE admin_users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user['id'],))
        conn.commit()
        conn.close()
        return dict(user)
    conn.close()
    return None

def update_admin_password(username, new_password):
    conn = get_db_connection()
    new_hash = generate_password_hash(new_password)
    conn.execute("UPDATE admin_users SET password_hash = ? WHERE username = ?", (new_hash, username))
    conn.commit()
    conn.close()

def log_audit(admin_user, action, target_type=None, target_id=None, details=None, ip_address=None):
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO audit_logs (admin_user, action, target_type, target_id, details, ip_address)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (admin_user, action, target_type, str(target_id) if target_id else None, details, ip_address))
    conn.commit()
    conn.close()

def get_audit_logs(limit=100):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ==========================================
# 10. DASHBOARD METRICS & ANALYTICS
# ==========================================

def get_dashboard_metrics():
    conn = get_db_connection()

    total_revenue_stars = conn.execute("""
        SELECT COALESCE(SUM(amount), 0) as total FROM payments WHERE status = 'Paid' AND currency = 'XTR'
    """).fetchone()['total']

    total_orders = conn.execute("SELECT COUNT(*) as c FROM orders").fetchone()['c']
    paid_orders = conn.execute("SELECT COUNT(*) as c FROM orders WHERE payment_status = 'Paid'").fetchone()['c']
    completed_orders = conn.execute("SELECT COUNT(*) as c FROM orders WHERE fulfillment_status = 'Completed'").fetchone()['c']
    pending_fulfillment = conn.execute("""
        SELECT COUNT(*) as c FROM orders WHERE payment_status = 'Paid' AND fulfillment_status IN ('Not Started', 'Processing')
    """).fetchone()['c']
    total_customers = conn.execute("SELECT COUNT(*) as c FROM customers").fetchone()['c']
    active_services_count = conn.execute("SELECT COUNT(*) as c FROM services WHERE is_archived = 0").fetchone()['c']

    recent_orders = conn.execute("""
        SELECT o.*, c.telegram_id, c.username as customer_telegram_username
        FROM orders o
        LEFT JOIN customers c ON o.customer_id = c.id
        ORDER BY o.created_at DESC LIMIT 6
    """).fetchall()

    top_services = conn.execute("""
        SELECT s.name, COUNT(o.id) as orders_count, COALESCE(SUM(o.snapshot_price_stars), 0) as revenue_stars
        FROM services s
        LEFT JOIN orders o ON s.id = o.service_id AND o.payment_status = 'Paid'
        GROUP BY s.id
        ORDER BY orders_count DESC LIMIT 5
    """).fetchall()

    conn.close()
    return {
        'total_revenue_stars': total_revenue_stars,
        'total_orders': total_orders,
        'paid_orders': paid_orders,
        'completed_orders': completed_orders,
        'pending_fulfillment': pending_fulfillment,
        'total_customers': total_customers,
        'active_services_count': active_services_count,
        'recent_orders': [dict(r) for r in recent_orders],
        'top_services': [dict(r) for r in top_services]
    }

def log_analytics_event(event_type, customer_id=None, service_id=None, service_name=None, plan_duration=None, plan_price=None, ip_address=None, user_agent=None):
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO analytics_events (event_type, customer_id, service_id, service_name, plan_duration, plan_price, ip_address, user_agent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (event_type, customer_id, service_id, service_name, plan_duration, plan_price, ip_address, user_agent))
    conn.commit()
    conn.close()

def get_analytics_events(limit=100):
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT * FROM analytics_events
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

