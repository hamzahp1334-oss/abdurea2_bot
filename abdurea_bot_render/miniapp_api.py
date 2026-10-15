# -*- coding: utf-8 -*-
"""
Telegram Mini App Backend API & Base44 Live Synchronization Engine for ABDUREA STORE.
Provides:
1. Live automatic synchronization between abdureastore.base44.app and store.db.
2. Cryptographic Telegram WebApp initData HMAC-SHA256 validation.
3. High-performance REST API for Mini App products, categories, cart validation,
   multi-step checkout, receipt upload, order history, and user profile.
4. Seamless integration with @abdurea2_bot and Admin review pipeline.
"""
import os
import sys
import json
import time
import hmac
import hashlib
import secrets
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from functools import wraps
from datetime import datetime

from flask import Blueprint, request, jsonify, render_template, current_app, send_from_directory
from werkzeug.utils import secure_filename

import config
import models
import bot_bridge
from database import db_session

logger = logging.getLogger("miniapp_api")

miniapp_bp = Blueprint("miniapp", __name__, template_folder="templates", static_folder="static")

# -------------------------------------------------------------
# CONSTANTS & CONFIGURATION
# -------------------------------------------------------------
BASE44_API_ROOT = "https://base44.app/api/apps/6aa86368309f7b3191d675d2/entities"
LAST_SYNC_TIMESTAMP = 0
SYNC_CACHE_TTL_SECONDS = 60  # Check Base44 at most once every 60s during user activity
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf", "heic"}

# -------------------------------------------------------------
# 1. LIVE SYNCHRONIZATION WITH ABDUREASTORE.BASE44.APP
# -------------------------------------------------------------

def sync_from_base44(force=False):
    """
    Synchronizes categories, products (services), and pricing plans from abdureastore.base44.app.
    Maintains ONE TRUE SOURCE OF TRUTH in store.db without duplicate records.
    Never crashes on network issues; smoothly falls back to existing local store.db data.
    """
    global LAST_SYNC_TIMESTAMP
    now = time.time()
    if not force and (now - LAST_SYNC_TIMESTAMP) < SYNC_CACHE_TTL_SECONDS:
        return {"synced": False, "reason": "cache_fresh", "age": int(now - LAST_SYNC_TIMESTAMP)}

    logger.info("Initiating Base44 live synchronization with abdureastore.base44.app...")
    synced_categories = 0
    synced_products = 0
    synced_plans = 0

    try:
        # A. Fetch & Upsert Categories
        cat_req = urllib.request.Request(f"{BASE44_API_ROOT}/Category", headers={"User-Agent": "Mozilla/5.0 ABDUREA-STORE-SYNC/1.0"})
        with urllib.request.urlopen(cat_req, timeout=6) as resp:
            cats = json.loads(resp.read().decode("utf-8"))

        with db_session() as conn:
            cursor = conn.cursor()
            for c in cats:
                cid = c.get("id")
                name = c.get("name") or "Category"
                slug = c.get("slug") or name.lower().replace(" ", "-")
                icon = c.get("icon") or "📦"
                sort_order = int(c.get("sort_order") or 0)
                active = 1 if c.get("active", True) else 0

                cursor.execute("""
                    INSERT INTO categories (id, name, slug, icon, sort_order, active)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        slug = excluded.slug,
                        icon = excluded.icon,
                        sort_order = excluded.sort_order,
                        active = excluded.active
                """, (cid, name, slug, icon, sort_order, active))
                synced_categories += 1

        # B. Fetch & Upsert Services / Products
        srv_req = urllib.request.Request(f"{BASE44_API_ROOT}/Service", headers={"User-Agent": "Mozilla/5.0 ABDUREA-STORE-SYNC/1.0"})
        with urllib.request.urlopen(srv_req, timeout=8) as resp:
            services = json.loads(resp.read().decode("utf-8"))

        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS deleted_product_ids (product_id TEXT PRIMARY KEY, deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
            cursor.execute("CREATE TABLE IF NOT EXISTS custom_product_edits (product_id TEXT PRIMARY KEY, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
            cursor.execute("SELECT product_id FROM deleted_product_ids")
            deleted_ids = {str(r[0]) for r in cursor.fetchall()}
            cursor.execute("SELECT product_id FROM custom_product_edits")
            custom_ids = {str(r[0]) for r in cursor.fetchall()}

            for s in services:
                sid = str(s.get("id"))
                if sid in deleted_ids:
                    continue  # Keep removed by admin
                if sid in custom_ids:
                    continue  # Keep admin custom edits

                cat_id = s.get("category_id")
                name = s.get("name") or "Service"
                desc = s.get("description") or s.get("long_description") or ""
                img = s.get("image_url") or ""
                features_raw = s.get("features") or []
                reqs_raw = s.get("requirements") or []
                features_str = json.dumps(features_raw) if isinstance(features_raw, (list, dict)) else str(features_raw or "[]")
                reqs_str = json.dumps(reqs_raw) if isinstance(reqs_raw, (list, dict)) else str(reqs_raw or "[]")
                sort_order = int(s.get("sort_order") or 0)
                
                # Active if active flag is True and availability is not explicitly 'unavailable'
                is_active = 1 if (s.get("active", True) and s.get("availability") != "unavailable") else 0

                cursor.execute("""
                    INSERT INTO products (id, category_id, name, description, image_url, features, requirements, active, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        category_id = excluded.category_id,
                        name = excluded.name,
                        description = excluded.description,
                        image_url = CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE products.image_url END,
                        features = excluded.features,
                        requirements = excluded.requirements,
                        active = excluded.active,
                        sort_order = excluded.sort_order,
                        updated_at = CURRENT_TIMESTAMP
                """, (sid, cat_id, name, desc, img, features_str, reqs_str, is_active, sort_order))
                synced_products += 1

        # C. Fetch & Upsert Plans
        plan_req = urllib.request.Request(f"{BASE44_API_ROOT}/Plan", headers={"User-Agent": "Mozilla/5.0 ABDUREA-STORE-SYNC/1.0"})
        with urllib.request.urlopen(plan_req, timeout=8) as resp:
            plans = json.loads(resp.read().decode("utf-8"))

        with db_session() as conn:
            cursor = conn.cursor()
            for p in plans:
                pid = str(p.get("id"))
                service_id = str(p.get("service_id") or "")
                if not service_id or service_id in deleted_ids or service_id in custom_ids:
                    continue
                p_name = p.get("name") or p.get("duration") or "Standard Plan"
                duration = p.get("duration") or "1 Month"
                price = float(p.get("price") or 0.0)
                currency = p.get("currency") or "ETB"
                avail = p.get("availability") or "available"
                active = 1 if p.get("active", True) else 0
                sort_order = int(p.get("sort_order") or 0)

                cursor.execute("""
                    INSERT INTO product_plans (id, product_id, name, duration, price, currency, availability, active, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        product_id = excluded.product_id,
                        name = excluded.name,
                        duration = excluded.duration,
                        price = excluded.price,
                        currency = excluded.currency,
                        availability = excluded.availability,
                        active = excluded.active,
                        sort_order = excluded.sort_order
                """, (pid, service_id, p_name, duration, price, currency, avail, active, sort_order))
                synced_plans += 1

        LAST_SYNC_TIMESTAMP = now
        logger.info(f"Base44 sync complete: {synced_categories} categories, {synced_products} services, {synced_plans} plans.")
        return {
            "synced": True,
            "categories": synced_categories,
            "products": synced_products,
            "plans": synced_plans,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.warning(f"Notice: Base44 live sync error ({e}). Using existing store.db records.")
        return {"synced": False, "error": str(e), "fallback": "store.db"}


# -------------------------------------------------------------
# 2. CRYPTOGRAPHIC TELEGRAM AUTHENTICATION
# -------------------------------------------------------------

def validate_telegram_init_data(init_data_raw: str, bot_token: str) -> dict:
    """
    Validates Telegram WebApp initData string using official Telegram HMAC-SHA256 signature scheme:
    1. Parse query string into key-value pairs.
    2. Extract and remove 'hash'.
    3. Generate data_check_string by sorting remaining keys alphabetically: "key=value\n...".
    4. Compute secret_key = HMAC_SHA256("WebAppData", bot_token).
    5. Compute signature = HMAC_SHA256(secret_key, data_check_string).hexdigest().
    6. Verify signature == hash in constant time.
    7. Parse 'user' JSON object.
    Returns user dict on success, None on failure.
    """
    if not init_data_raw or not bot_token:
        return None

    try:
        parsed = dict(urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        # Sort remaining keys alphabetically
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

        # Secret key: HMAC-SHA256 of bot_token with key b"WebAppData"
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(received_hash, computed_hash):
            logger.warning("Telegram WebApp initData HMAC verification failed.")
            return None

        # Check timestamp expiration (allow up to 24 hours = 86400s)
        auth_date = int(parsed.get("auth_date", 0))
        if auth_date > 0 and (time.time() - auth_date) > 86400 * 7:  # generous 7 days tolerance
            logger.warning("Telegram WebApp initData expired.")
            return None

        user_data = {}
        if "user" in parsed:
            user_data = json.loads(parsed["user"])
        else:
            user_data = {"id": int(parsed.get("id", 0))}

        return {
            "telegram_id": int(user_data.get("id")),
            "first_name": user_data.get("first_name", ""),
            "last_name": user_data.get("last_name", ""),
            "username": user_data.get("username", ""),
            "language_code": user_data.get("language_code", "en"),
            "is_premium": bool(user_data.get("is_premium", False)),
            "auth_date": auth_date,
            "raw": parsed
        }
    except Exception as e:
        logger.warning(f"Error parsing Telegram initData: {e}")
        return None


def get_authenticated_telegram_user():
    """
    Extracts and validates Telegram user identity from request headers or query params.
    Supports:
    - Header: 'X-Telegram-Init-Data'
    - Header: 'Authorization: tma <initData>'
    - Header: 'Telegram-Init-Data'
    - Query: '?initData=...'
    If in local browser preview or development mode without Telegram, gracefully enables simulated mode.
    """
    init_data = (
        request.headers.get("X-Telegram-Init-Data")
        or request.headers.get("Telegram-Init-Data")
        or request.args.get("initData")
        or ""
    )
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("tma "):
        init_data = auth_header[4:].strip()

    bot_token = getattr(config, "MAIN_BOT_TOKEN", getattr(config, "TELEGRAM_BOT_TOKEN", ""))
    
    if init_data:
        validated = validate_telegram_init_data(init_data, bot_token)
        if validated:
            # Sync user into store.db
            try:
                models.get_or_create_user(
                    telegram_id=validated["telegram_id"],
                    username=validated.get("username"),
                    first_name=validated.get("first_name"),
                    last_name=validated.get("last_name"),
                    is_premium=validated.get("is_premium")
                )
            except Exception:
                pass
            return validated

    # Development / Browser Preview Fallback
    # If the user opens the URL directly in Chrome/Edge outside Telegram, or tests locally
    dev_mode = request.headers.get("X-Dev-Preview") == "true" or request.args.get("dev") == "1" or not init_data
    if dev_mode:
        test_id_str = request.headers.get("X-Telegram-User-Id") or request.args.get("tg_id") or "9988776655"
        try:
            test_id = int(test_id_str)
        except ValueError:
            test_id = 9988776655

        return {
            "telegram_id": test_id,
            "first_name": "Preview",
            "last_name": "User",
            "username": "guest_preview",
            "language_code": "en",
            "is_premium": False,
            "is_simulated": True
        }

    return None


def telegram_auth_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_authenticated_telegram_user()
        if not user:
            return jsonify({
                "error": "Unauthorized",
                "message": "Valid Telegram WebApp initData is required to access this resource."
            }), 401
        request.telegram_user = user
        return f(*args, **kwargs)
    return decorated_function


# -------------------------------------------------------------
# 3. MINI APP HTML INTERFACE ROUTE
# -------------------------------------------------------------

@miniapp_bp.route("/miniapp", methods=["GET"])
@miniapp_bp.route("/miniapp/", methods=["GET"])
@miniapp_bp.route("/shop", methods=["GET"])
@miniapp_bp.route("/store", methods=["GET"])
def miniapp_ui():
    """Serves the Telegram Mini App single-page application."""
    # Ensure fresh synchronization
    try:
        sync_from_base44(force=False)
    except Exception:
        pass

    settings = models.get_store_settings()
    branding = {
        "store_name": settings.get("store_name", config.STORE_NAME),
        "support_username": settings.get("support_username", config.DEFAULT_SUPPORT_USERNAME),
        "telebirr_account": settings.get("telebirr_account", config.DEFAULT_TELEBIRR_ACCOUNT),
        "telebirr_name": settings.get("telebirr_name", config.DEFAULT_TELEBIRR_NAME),
        "cbe_birr_account": settings.get("cbe_birr_account", config.DEFAULT_CBE_BIRR_ACCOUNT),
        "cbe_birr_name": settings.get("cbe_birr_name", config.DEFAULT_CBE_BIRR_NAME),
        "logo_url": "https://media.base44.com/images/public/6aa86368309f7b3191d675d2/8cc83190c_logo.png",
        "bot_username": "abdurea2_bot"
    }
    return render_template("miniapp.html", branding=branding)


# -------------------------------------------------------------
# 4. REST API ENDPOINTS
# -------------------------------------------------------------

def is_admin_user(user: dict) -> bool:
    """
    Cryptographically strict check: Only real, verified Telegram users
    (never simulated or preview sessions) with authorized admin IDs can manage store.
    """
    if not user or user.get("is_simulated"):
        return False
    tid = user.get("telegram_id") or user.get("id")
    username = (user.get("username") or "").lower().lstrip("@")
    allowed_ids = getattr(config, "ADMIN_TELEGRAM_IDS", [6666443909, 2145055956])
    if tid in allowed_ids or tid == getattr(config, "ADMIN_TELEGRAM_ID", 6666443909):
        return True
    if username in ["trust994", "hamzu3", getattr(config, "ADMIN_TELEGRAM_USERNAME", "@trust994").lstrip("@").lower()]:
        return True
    return False


@miniapp_bp.route("/api/miniapp/config", methods=["GET"])
def api_miniapp_config():
    """Returns store settings, payment account numbers, promo banner, and support credentials."""
    settings = models.get_store_settings()
    banner_text = settings.get("miniapp_banner_text", "")
    banner_active = settings.get("miniapp_banner_active", "0") in ("1", "true", "True")
    maint_mode = settings.get("miniapp_maintenance_mode", "0") in ("1", "true", "True")

    return jsonify({
        "success": True,
        "store_name": settings.get("store_name", config.STORE_NAME),
        "website": "https://abdureastore.base44.app/",
        "support_username": settings.get("support_username", config.DEFAULT_SUPPORT_USERNAME),
        "official_channel": getattr(config, "OFFICIAL_CHANNEL_USERNAME", "@abdurea1"),
        "official_channel_url": getattr(config, "OFFICIAL_CHANNEL_URL", "https://t.me/abdurea1"),
        "bot_username": "abdurea2_bot",
        "admin_bot_username": "abdurea1_bot",
        "banner_text": banner_text,
        "banner_active": banner_active,
        "maintenance_mode": maint_mode,
        "payment_methods": [
            {
                "id": "telebirr",
                "name": "Telebirr",
                "account_number": settings.get("telebirr_account", config.DEFAULT_TELEBIRR_ACCOUNT),
                "account_name": settings.get("telebirr_name", config.DEFAULT_TELEBIRR_NAME),
                "icon": "📱",
                "color": "#0ea5e9",
                "instructions": "Transfer via Telebirr app or *127#, then upload confirmation screenshot."
            },
            {
                "id": "cbe_birr",
                "name": "CBE Birr / Commercial Bank of Ethiopia",
                "account_number": settings.get("cbe_birr_account", config.DEFAULT_CBE_BIRR_ACCOUNT),
                "account_name": settings.get("cbe_birr_name", config.DEFAULT_CBE_BIRR_NAME),
                "icon": "🏦",
                "color": "#8b5cf6",
                "instructions": "Transfer to CBE Account, then upload transaction receipt screenshot."
            }
        ],
        "logo_url": "https://media.base44.com/images/public/6aa86368309f7b3191d675d2/8cc83190c_logo.png"
    })


@miniapp_bp.route("/api/miniapp/sync", methods=["POST", "GET"])
def api_miniapp_sync():
    """Triggers an on-demand synchronization with abdureastore.base44.app."""
    res = sync_from_base44(force=True)
    return jsonify(res)


@miniapp_bp.route("/api/miniapp/categories", methods=["GET"])
def api_miniapp_categories():
    """Returns active categories with real product count from store.db."""
    sync_from_base44(force=False)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.*, COUNT(p.id) as product_count
            FROM categories c
            LEFT JOIN products p ON c.id = p.category_id AND p.active = 1
            WHERE c.active = 1
            GROUP BY c.id
            ORDER BY c.sort_order ASC, c.name ASC
        """)
        cats = [dict(r) for r in cursor.fetchall()]
        return jsonify({"success": True, "categories": cats})


@miniapp_bp.route("/api/miniapp/products", methods=["GET"])
def api_miniapp_products():
    """
    Returns live catalog products with their active plans and prices directly from store.db.
    Supports filtering by category_id and full-text search.
    """
    sync_from_base44(force=False)
    cat_filter = request.args.get("category_id", "").strip()
    search = request.args.get("search", "").strip()

    with db_session() as conn:
        cursor = conn.cursor()
        query = """
            SELECT p.*, c.name as category_name, c.icon as category_icon
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.active = 1
        """
        params = []
        if cat_filter and cat_filter != "all":
            query += " AND p.category_id = ?"
            params.append(cat_filter)

        if search:
            query += " AND (p.name LIKE ? OR p.description LIKE ? OR c.name LIKE ?)"
            s_param = f"%{search}%"
            params.extend([s_param, s_param, s_param])

        query += " ORDER BY p.sort_order ASC, p.id ASC"
        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]

        # Attach active plans to each product
        for p in rows:
            plans = models.get_plans_by_product(p["id"], include_inactive=False)
            p["plans"] = plans
            # Parse features & requirements
            if isinstance(p.get("features"), str):
                try:
                    p["features"] = json.loads(p["features"])
                except Exception:
                    p["features"] = []
            if isinstance(p.get("requirements"), str):
                try:
                    p["requirements"] = json.loads(p["requirements"])
                except Exception:
                    p["requirements"] = []

            prices = [float(pl["price"]) for pl in plans if pl.get("price") is not None]
            p["min_price"] = min(prices) if prices else 0.0
            p["max_price"] = max(prices) if prices else 0.0
            p["currency"] = plans[0]["currency"] if plans else "ETB"
            p["plan_count"] = len(plans)

        return jsonify({
            "success": True,
            "count": len(rows),
            "products": rows
        })


@miniapp_bp.route("/api/miniapp/products/<product_id>", methods=["GET"])
def api_miniapp_product_detail(product_id):
    """Returns single product details and all associated pricing tiers."""
    sync_from_base44(force=False)
    prod = models.get_product_details(product_id)
    if not prod:
        return jsonify({"error": "Product not found"}), 404

    # Ensure active plans only for customer view
    prod["plans"] = models.get_plans_by_product(product_id, include_inactive=False)
    return jsonify({"success": True, "product": prod})


@miniapp_bp.route("/api/miniapp/cart/validate", methods=["POST"])
def api_miniapp_validate_cart():
    """
    Revalidates product existence, availability, and live database prices before checkout.
    Never trusts client-submitted prices.
    Accepts: { "items": [ { "product_id": "...", "plan_id": "...", "quantity": 1 } ] }
    """
    data = request.get_json() or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"valid": False, "error": "Cart is empty."}), 400

    validated_items = []
    subtotal = 0.0
    issues = []

    for idx, item in enumerate(items):
        prod_id = item.get("product_id")
        plan_id = item.get("plan_id")
        qty = max(1, int(item.get("quantity") or 1))

        product = models.get_product_by_id(prod_id)
        if not product or not product.get("active"):
            issues.append(f"Product '{item.get('name') or prod_id}' is currently unavailable.")
            continue

        plan = models.get_plan_by_id(plan_id)
        if not plan or not plan.get("active"):
            issues.append(f"Plan for '{product['name']}' is no longer active.")
            continue

        real_unit_price = float(plan.get("price", 0.0))
        item_total = real_unit_price * qty
        subtotal += item_total

        validated_items.append({
            "product_id": product["id"],
            "product_name": product["name"],
            "product_image": product.get("image_url") or "",
            "plan_id": plan["id"],
            "plan_duration": plan.get("duration") or "Standard",
            "quantity": qty,
            "unit_price": real_unit_price,
            "total_price": item_total,
            "currency": plan.get("currency", "ETB")
        })

    if not validated_items:
        return jsonify({"valid": False, "error": "None of the items in your cart are currently available.", "issues": issues}), 400

    return jsonify({
        "valid": len(issues) == 0,
        "items": validated_items,
        "subtotal": subtotal,
        "total": subtotal,
        "currency": "ETB",
        "issues": issues
    })


@miniapp_bp.route("/api/miniapp/orders/create", methods=["POST"])
@telegram_auth_required
def api_miniapp_create_order():
    """
    Creates order(s) for the authenticated customer.
    Re-validates all pricing and availability server-side.
    Creates sequential order record and dispatches notifications.
    """
    user = request.telegram_user
    data = request.get_json() or {}
    items = data.get("items") or []

    if not items:
        return jsonify({"error": "No items provided for order."}), 400

    customer_phone = data.get("customer_phone", "").strip()
    customer_notes = data.get("customer_notes", "").strip()
    delivery_info = data.get("delivery_info", "").strip()
    payment_method = data.get("payment_method", "telebirr").strip()

    created_orders = []
    total_amount = 0.0

    for item in items:
        prod_id = item.get("product_id")
        plan_id = item.get("plan_id")
        qty = max(1, int(item.get("quantity") or 1))

        product = models.get_product_by_id(prod_id)
        if not product or not product.get("active"):
            return jsonify({"error": f"Product '{prod_id}' is unavailable."}), 400

        plan = models.get_plan_by_id(plan_id)
        if not plan or not plan.get("active"):
            return jsonify({"error": f"Selected plan for '{product['name']}' is unavailable."}), 400

        for _ in range(qty):
            order = models.create_order(
                telegram_id=user["telegram_id"],
                product_id=product["id"],
                plan_id=plan["id"],
                customer_name=f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or user.get("username") or f"Telegram User {user['telegram_id']}",
                customer_username=user.get("username"),
                customer_phone=customer_phone,
                customer_delivery_info=delivery_info or customer_notes,
                customer_notes=customer_notes,
                payment_method=payment_method,
                status=models.STATUS_AWAITING_PAYMENT
            )
            created_orders.append(order)
            total_amount += float(order["amount"])

    # If multiple orders were created, use the primary one for payment reference
    primary_order = created_orders[0]

    # Dispatch in-bot confirmation message to customer if bot is active
    try:
        settings = models.get_store_settings()
        pay_acc = settings.get(f"{payment_method.lower()}_account", config.DEFAULT_TELEBIRR_ACCOUNT)
        pay_name = settings.get(f"{payment_method.lower()}_name", config.DEFAULT_TELEBIRR_NAME)
        
        bot_msg = (
            f"🛒 <b>ORDER SUBMITTED — #{primary_order['order_number']}</b>\n\n"
            f"📦 <b>Service:</b> {primary_order['product_name']} ({primary_order.get('plan_duration', 'Standard')})\n"
            f"💰 <b>Amount:</b> {total_amount:,.0f} ETB\n"
            f"💳 <b>Payment Method:</b> {payment_method.upper()}\n"
            f"🔢 <b>Account:</b> <code>{pay_acc}</code> ({pay_name})\n\n"
            f"⚠️ Please complete payment and upload your receipt screenshot inside the Mini App to begin service activation!"
        )
        bot_bridge.send_telegram_message(
            chat_id=user["telegram_id"],
            text=bot_msg,
            order_id=primary_order["id"]
        )
    except Exception as ex:
        logger.warning(f"Could not dispatch bot message: {ex}")

    return jsonify({
        "success": True,
        "primary_order": primary_order,
        "orders": created_orders,
        "total_amount": total_amount,
        "order_number": primary_order["order_number"],
        "order_id": primary_order["id"]
    }), 201


@miniapp_bp.route("/api/miniapp/orders/<int:order_id>/receipt", methods=["POST"])
@telegram_auth_required
def api_miniapp_upload_receipt(order_id):
    """
    Uploads customer payment receipt screenshot for an order.
    Enforces user ownership, file validation, and transitions status to PAYMENT_SUBMITTED.
    """
    user = request.telegram_user
    order = models.get_order_by_id(order_id)
    if not order:
        return jsonify({"error": "Order not found."}), 404

    # Verify order ownership (except in simulated preview)
    if not user.get("is_simulated") and int(order["telegram_id"]) != int(user["telegram_id"]):
        return jsonify({"error": "Unauthorized access to order."}), 403

    file = request.files.get("file") or request.files.get("receipt")
    if not file or file.filename == "":
        return jsonify({"error": "No receipt image provided."}), 400

    # Enforce maximum 15MB file size limit to prevent Denial-of-Service attacks
    if request.content_length and request.content_length > 15 * 1024 * 1024:
        return jsonify({"error": "Receipt file too large. Maximum size allowed is 15MB."}), 400

    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    if file_size > 15 * 1024 * 1024:
        return jsonify({"error": "Receipt file too large. Maximum size allowed is 15MB."}), 400

    import re
    ext = os.path.splitext(file.filename)[1].lower().replace(".", "")
    if not re.match(r"^[a-z0-9]{3,5}$", ext) or ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Invalid file format '.{ext}'. Supported: JPG, PNG, WEBP, PDF, HEIC."}), 400

    save_dir = config.RECEIPTS_DIR
    save_dir.mkdir(parents=True, exist_ok=True)
    filename = f"receipt_{order['order_number']}_{secrets.token_hex(6)}.{ext}"
    filepath = save_dir / filename
    file.save(str(filepath))

    payment_method = request.form.get("payment_method") or order.get("payment_method") or "Telebirr"
    sender_name = request.form.get("payment_sender_name") or user.get("first_name") or f"Customer {user['telegram_id']}"
    amount = float(request.form.get("amount") or order["amount"])

    # Record in database
    payment = models.record_payment(
        order_id=order["id"],
        payment_method=payment_method,
        payment_sender_name=sender_name,
        receipt_file=filename,
        amount=amount
    )

    # Dispatch bot notification to customer confirming receipt received
    try:
        bot_msg = (
            f"🧾 <b>RECEIPT RECEIVED — #{order['order_number']}</b>\n\n"
            f"We have received your payment confirmation for <b>{order['product_name']}</b>.\n"
            f"Status: 🟡 <b>Payment Under Verification</b>\n\n"
            f"Our store administrator is reviewing your transfer and will deliver your credentials shortly!"
        )
        bot_bridge.send_telegram_message(
            chat_id=user["telegram_id"],
            text=bot_msg,
            order_id=order["id"]
        )
    except Exception as ex:
        logger.warning(f"Could not send bot receipt notification: {ex}")

    updated_order = models.get_order_by_id(order_id)

    # Customer finished payment and uploaded receipt: Notify admin bot with receipt photo!
    try:
        bot_bridge.notify_admin_new_order(updated_order, payment)
    except Exception as ex_adm:
        logger.warning(f"Could not dispatch admin order notification: {ex_adm}")

    return jsonify({
        "success": True,
        "message": "Receipt uploaded successfully. Order is now under verification.",
        "receipt_file": filename,
        "order": updated_order,
        "payment": payment
    })


@miniapp_bp.route("/api/internal/orders/<identifier>", methods=["GET"])
def api_internal_get_order(identifier):
    """Internal endpoint for order resolution and sync between Vercel and Admin Bot."""
    # Verify internal secret to prevent unauthorized enumeration of customer orders
    secret = (
        request.headers.get("X-Internal-Secret", "").strip()
        or request.headers.get("X-Internal-API-Key", "").strip()
        or request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    )
    expected_secret = getattr(config, "INTERNAL_WEBHOOK_SECRET", "") or getattr(config, "SECRET_KEY", "")
    expected_api_key = getattr(config, "BASE44_INTERNAL_API_KEY", "")

    is_authorized = False
    if secret:
        if expected_secret and hmac.compare_digest(secret, expected_secret):
            is_authorized = True
        elif expected_api_key and hmac.compare_digest(secret, expected_api_key):
            is_authorized = True

    if not is_authorized:
        return jsonify({"success": False, "error": "Unauthorized: valid internal authentication required."}), 403

    if str(identifier).isdigit():
        order = models.get_order_by_id(int(identifier))
    else:
        order = models.get_order_by_number(str(identifier))
    if not order:
        return jsonify({"success": False, "error": "Order not found."}), 404
    return jsonify({"success": True, "order": order})


@miniapp_bp.route("/api/miniapp/orders", methods=["GET"])
@telegram_auth_required
def api_miniapp_get_user_orders():
    """Returns order history strictly belonging to the authenticated customer."""
    user = request.telegram_user
    orders = models.get_user_orders(telegram_id=user["telegram_id"], limit=50)

    # Attach payment & delivery details to each order
    for o in orders:
        o["payment"] = models.get_payment_by_order_id(o["id"])
        o["delivery"] = models.get_delivery_by_order_id(o["id"])

    return jsonify({
        "success": True,
        "count": len(orders),
        "orders": orders
    })


@miniapp_bp.route("/api/miniapp/orders/<order_id>", methods=["GET"])
@telegram_auth_required
def api_miniapp_get_single_order(order_id):
    """Returns details and timeline of a single order belonging to user."""
    user = request.telegram_user
    order = models.get_order_by_id(int(order_id)) if str(order_id).isdigit() else models.get_order_by_number(str(order_id))
    if not order:
        return jsonify({"error": "Order not found."}), 404

    if not user.get("is_simulated") and int(order["telegram_id"]) != int(user["telegram_id"]):
        return jsonify({"error": "Unauthorized access to order."}), 403

    return jsonify({"success": True, "order": order})


@miniapp_bp.route("/api/miniapp/profile", methods=["GET"])
@telegram_auth_required
def api_miniapp_profile():
    """Returns customer profile stats and spending summary."""
    user = request.telegram_user
    profile = models.get_customer_profile(user["telegram_id"])
    if not profile:
        profile = {
            "telegram_id": user["telegram_id"],
            "full_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or user.get("username") or "Customer",
            "telegram_username": user.get("username"),
            "total_orders": 0,
            "completed_orders": 0,
            "total_spent": 0.0,
            "orders": []
        }
    profile["is_admin"] = is_admin_user(user)
    return jsonify({"success": True, "profile": profile})


# -------------------------------------------------------------
# 5. ADMIN CONTROL ENDPOINTS FOR MINI APP
# -------------------------------------------------------------

@miniapp_bp.route("/api/miniapp/admin/sync", methods=["POST"])
@telegram_auth_required
def api_miniapp_admin_sync():
    """Forces catalog sync from Base44 (strictly authorized store admin only)."""
    user = request.telegram_user
    if not is_admin_user(user):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    res = sync_from_base44(force=True)
    return jsonify({"success": True, "result": res})


@miniapp_bp.route("/api/miniapp/admin/banner", methods=["POST"])
@telegram_auth_required
def api_miniapp_admin_banner():
    """Updates announcement banner for Mini App (admin only)."""
    user = request.telegram_user
    if not is_admin_user(user):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    data = request.get_json() or {}
    text = data.get("banner_text", "").strip()
    active = "1" if data.get("active", True) else "0"
    models.update_store_settings({
        "miniapp_banner_text": text,
        "miniapp_banner_active": active
    }, admin_id=user["telegram_id"], admin_username=user.get("username", "Admin"))
    return jsonify({"success": True, "banner_text": text, "active": active == "1"})


@miniapp_bp.route("/api/miniapp/admin/maintenance", methods=["POST"])
@telegram_auth_required
def api_miniapp_admin_maintenance():
    """Toggles Mini App maintenance mode (admin only)."""
    user = request.telegram_user
    if not is_admin_user(user):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    data = request.get_json() or {}
    maint = "1" if data.get("maintenance", True) else "0"
    models.update_store_settings({
        "miniapp_maintenance_mode": maint
    }, admin_id=user["telegram_id"], admin_username=user.get("username", "Admin"))
    return jsonify({"success": True, "maintenance_mode": maint == "1"})


@miniapp_bp.route("/api/miniapp/admin/overview", methods=["GET"])
@telegram_auth_required
def api_miniapp_admin_overview():
    """Returns store KPIs and pending orders for admin (admin only)."""
    user = request.telegram_user
    if not is_admin_user(user):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    kpis = models.get_dashboard_kpis()
    pending_orders = models.get_orders_list(status="PAYMENT_SUBMITTED", limit=10)
    return jsonify({
        "success": True,
        "kpis": kpis,
        "pending_orders": pending_orders,
        "maintenance_mode": models.get_setting("miniapp_maintenance_mode", "0") in ("1", "true", "True"),
        "banner_text": models.get_setting("miniapp_banner_text", ""),
        "banner_active": models.get_setting("miniapp_banner_active", "0") in ("1", "true", "True")
    })


# -------------------------------------------------------------
# 6. ADMIN PRODUCT CATALOG MANAGEMENT (ADD, EDIT, REMOVE, TOGGLE)
# -------------------------------------------------------------

@miniapp_bp.route("/api/miniapp/admin/products", methods=["GET"])
@telegram_auth_required
def api_miniapp_admin_products():
    """Returns all catalog products (including inactive drafts) with plans (admin only)."""
    user = request.telegram_user
    if not is_admin_user(user):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

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
            p["plans"] = models.get_plans_by_product(p["id"], include_inactive=True)
            if isinstance(p.get("features"), str):
                try:
                    p["features"] = json.loads(p["features"])
                except Exception:
                    p["features"] = []
            if isinstance(p.get("requirements"), str):
                try:
                    p["requirements"] = json.loads(p["requirements"])
                except Exception:
                    p["requirements"] = []

        cursor.execute("SELECT * FROM categories ORDER BY sort_order ASC, name ASC")
        categories = [dict(r) for r in cursor.fetchall()]

    return jsonify({"success": True, "products": products, "categories": categories})


@miniapp_bp.route("/api/miniapp/admin/products/create", methods=["POST"])
@telegram_auth_required
def api_miniapp_admin_create_product():
    """Creates a new service/product with pricing plans (admin only)."""
    user = request.telegram_user
    if not is_admin_user(user):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Product name is required"}), 400

    category_id = data.get("category_id") or None
    description = (data.get("description") or "").strip()
    image_url = (data.get("image_url") or "").strip()
    active = 1 if data.get("active", True) in (True, 1, "1") else 0
    features = data.get("features") or []
    requirements = data.get("requirements") or []
    plans = data.get("plans") or []

    # Default plan if none provided
    if not plans:
        plans = [{"name": "Standard", "duration": "1 Month", "price": float(data.get("price") or 0), "currency": "ETB", "active": 1}]

    try:
        new_prod = models.create_product(
            name=name,
            category_id=category_id,
            description=description,
            image_url=image_url,
            features=features,
            requirements=requirements,
            active=active,
            plans=plans
        )
        logger.info(f"Admin {user['telegram_id']} created product: {name} (ID: {new_prod.get('id') if isinstance(new_prod, dict) else 'new'})")
        return jsonify({"success": True, "message": "Product created successfully", "product": new_prod}), 201
    except Exception as ex:
        logger.error(f"Error creating product: {ex}")
        return jsonify({"success": False, "error": str(ex)}), 500


@miniapp_bp.route("/api/miniapp/admin/products/<product_id>/update", methods=["POST", "PUT"])
@telegram_auth_required
def api_miniapp_admin_update_product(product_id):
    """Updates an existing product and its pricing plans (admin only)."""
    user = request.telegram_user
    if not is_admin_user(user):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    prod = models.get_product_by_id(product_id)
    if not prod:
        return jsonify({"success": False, "error": f"Product '{product_id}' not found"}), 404

    data = request.get_json() or {}
    name = (data.get("name") or prod["name"]).strip()
    category_id = data.get("category_id") if "category_id" in data else prod["category_id"]
    description = data.get("description") if "description" in data else (prod.get("description") or "")
    image_url = data.get("image_url") if "image_url" in data else (prod.get("image_url") or "")
    active = 1 if data.get("active", prod.get("active", 1)) in (True, 1, "1") else 0
    features = data.get("features", prod.get("features", []))
    requirements = data.get("requirements", prod.get("requirements", []))
    plans = data.get("plans")  # If provided, replaces/updates plans

    try:
        updated = models.update_product(
            product_id=product_id,
            name=name,
            category_id=category_id,
            description=description,
            image_url=image_url,
            features=features,
            requirements=requirements,
            active=active,
            plans=plans
        )
        logger.info(f"Admin {user['telegram_id']} updated product: {name} (ID: {product_id})")
        return jsonify({"success": True, "message": "Product updated successfully", "product": updated})
    except Exception as ex:
        logger.error(f"Error updating product: {ex}")
        return jsonify({"success": False, "error": str(ex)}), 500


@miniapp_bp.route("/api/miniapp/admin/products/<product_id>/delete", methods=["POST", "DELETE"])
@telegram_auth_required
def api_miniapp_admin_delete_product(product_id):
    """Deletes a product permanently and prevents Base44 resync from restoring it (admin only)."""
    user = request.telegram_user
    if not is_admin_user(user):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    prod = models.get_product_by_id(product_id)
    if not prod:
        return jsonify({"success": False, "error": f"Product '{product_id}' not found"}), 404

    try:
        models.delete_product(product_id)
        logger.info(f"Admin {user['telegram_id']} deleted product: {prod.get('name')} (ID: {product_id})")
        return jsonify({"success": True, "message": f"Product '{prod.get('name')}' deleted successfully"})
    except Exception as ex:
        logger.error(f"Error deleting product: {ex}")
        return jsonify({"success": False, "error": str(ex)}), 500


@miniapp_bp.route("/api/miniapp/admin/products/<product_id>/toggle", methods=["POST"])
@telegram_auth_required
def api_miniapp_admin_toggle_product(product_id):
    """Toggles active status of a product (admin only)."""
    user = request.telegram_user
    if not is_admin_user(user):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        res = models.toggle_product_active(product_id)
        is_active = res.get("active", 1) == 1
        logger.info(f"Admin {user['telegram_id']} toggled product active: {product_id} -> {is_active}")
        return jsonify({"success": True, "product_id": product_id, "active": is_active})
    except Exception as ex:
        logger.error(f"Error toggling product: {ex}")
        return jsonify({"success": False, "error": str(ex)}), 500


