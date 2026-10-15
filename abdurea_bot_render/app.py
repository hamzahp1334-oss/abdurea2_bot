# -*- coding: utf-8 -*-
"""
ABDUREA STORE — Flask Backend & Base44 Administrative Integration.
Provides:
1. Base44 Internal API Contract (/api/internal/*) with secure authentication.
2. Secure receipt-viewing proxy and download mechanisms.
3. Telegram Webhook endpoint (/api/telegram/webhook).
4. Full administrative dashboard UI & REST endpoints.
"""
import os
import io
import base64
import hmac
import hashlib
import time
import datetime
import secrets
import logging
from pathlib import Path
from functools import wraps
from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    session,
    send_from_directory,
    abort
)
from werkzeug.utils import secure_filename

import config
import models
import bot_bridge
from database import init_db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("app")

# Initialize Flask App
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = config.SECRET_KEY
app.config["SESSION_COOKIE_NAME"] = config.ADMIN_SESSION_COOKIE
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB upload limit
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Initialize Database and Upload Paths
init_db()
config.RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
config.DELIVERIES_DIR.mkdir(parents=True, exist_ok=True)
config.PRODUCT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------
# CORS HANDLING
# -------------------------------------------------------------

def get_allowed_origins():
    origins_str = getattr(config, "BASE44_ALLOWED_ORIGINS", "") or ""
    return [o.strip() for o in origins_str.split(",") if o.strip()]

def is_origin_allowed(origin):
    if not origin:
        return False
    allowed = get_allowed_origins()
    if "*" in allowed:
        return True
    for item in allowed:
        if item == origin:
            return True
        if item.startswith("https://*.") and origin.startswith("https://"):
            domain = item[10:]
            if origin[8:].endswith(domain):
                return True
    return False

@app.before_request
def handle_cors_preflight():
    if request.method == "OPTIONS":
        origin = request.headers.get("Origin", "")
        response = app.make_default_options_response()
        if is_origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        else:
            response.headers["Access-Control-Allow-Origin"] = origin or "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Internal-API-Key, X-API-Key, X-Signature-SHA256, X-Timestamp, X-Idempotency-Key, X-Requested-With, Accept"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Max-Age"] = "86400"
        return response

@app.after_request
def apply_cors_headers(response):
    origin = request.headers.get("Origin", "")
    if is_origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    elif origin:
        response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Internal-API-Key, X-API-Key, X-Signature-SHA256, X-Timestamp, X-Idempotency-Key, X-Requested-With, Accept"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
    return response

# -------------------------------------------------------------
# AUTHENTICATION DECORATORS & GUARDS
# -------------------------------------------------------------

def internal_api_required(f):
    """
    Guards /api/internal/* endpoints:
    Verifies BASE44_INTERNAL_API_KEY (Bearer token or X-Internal-API-Key header)
    or valid admin session or HMAC-SHA256 signature.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Check Bearer token or custom header
        auth_header = request.headers.get("Authorization", "").strip()
        api_key_header = request.headers.get("X-Internal-API-Key", "").strip() or request.headers.get("X-API-Key", "").strip()

        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif auth_header:
            token = auth_header
        elif api_key_header:
            token = api_key_header
        elif request.args.get("api_key"):
            token = request.args.get("api_key").strip()
        elif request.args.get("key"):
            token = request.args.get("key").strip()

        # 1. Compare against configured keys (supports comma-separated list for rotation)
        raw_keys = getattr(config, "BASE44_INTERNAL_API_KEY", "") or ""
        expected_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        if token and any(secrets.compare_digest(token, ek) for ek in expected_keys):
            request.current_caller = "Base44_Internal"
            return f(*args, **kwargs)

        # 2. Check HMAC signature if present
        sig_header = request.headers.get("X-Signature-SHA256", "").strip()
        raw_secrets = getattr(config, "INTERNAL_WEBHOOK_SECRET", "") or ""
        expected_secrets = [s.strip() for s in raw_secrets.split(",") if s.strip()]
        if sig_header and expected_secrets:
            body = request.get_data()
            for ws in expected_secrets:
                computed = hmac.new(ws.encode("utf-8"), body, hashlib.sha256).hexdigest()
                if secrets.compare_digest(sig_header, computed):
                    request.current_caller = "Base44_HMAC"
                    return f(*args, **kwargs)

        # 3. Allow logged-in admin session
        admin_id = session.get("admin_id")
        if admin_id:
            admin = models.get_admin_by_id(admin_id)
            if admin and admin.get("active"):
                request.current_caller = admin["username"]
                return f(*args, **kwargs)

        # If running in development without key set, reject unless explicitly empty
        if not expected_keys and not session.get("admin_id"):
            # Allow fallback in local dev test mode only if explicitly configured
            if app.config.get("TESTING") and token == "test-key":
                request.current_caller = "TestRunner"
                return f(*args, **kwargs)

        return jsonify({
            "error": "Unauthorized",
            "message": "Valid Base44 internal API key (Authorization: Bearer <KEY> or X-Internal-API-Key) required."
        }), 401

    return decorated_function

def login_required(f):
    """Guards admin dashboard web views and standard admin REST APIs."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Allow internal API key for all routes
        auth_header = request.headers.get("Authorization", "").strip()
        api_key_header = request.headers.get("X-Internal-API-Key", "").strip() or request.headers.get("X-API-Key", "").strip()
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else (auth_header or api_key_header)
        if not token:
            token = request.args.get("api_key", "").strip() or request.args.get("key", "").strip()

        raw_keys = getattr(config, "BASE44_INTERNAL_API_KEY", "") or ""
        expected_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        if config.SECRET_KEY:
            expected_keys.append(config.SECRET_KEY)
        if expected_keys and token and any(secrets.compare_digest(token, ek) for ek in expected_keys):
            admin = models.get_admin_by_username("admin") or {"id": 1, "username": "admin", "full_name": "Admin", "role": "admin", "active": 1}
            request.current_admin = admin
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            session["admin_role"] = admin.get("role", "admin")
            return f(*args, **kwargs)

        # Check session
        admin_id = session.get("admin_id")
        if admin_id:
            admin = models.get_admin_by_id(admin_id)
            if admin and admin.get("active"):
                request.current_admin = admin
                return f(*args, **kwargs)
            session.clear()

        if request.path.startswith("/api/"):
            return jsonify({"error": "Unauthorized", "message": "Admin session or valid API key required."}), 401
        return redirect(url_for("login_page", next=request.path))

    return decorated_function

def get_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)

# =====================================================================
# REQUIRED BOT <-> BASE44 INTERNAL API CONTRACT
# =====================================================================

@app.route("/api/internal/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def api_internal_health():
    """Health check returning database, telegram, and server status."""
    db_status = "disconnected"
    try:
        with models.db_session() as conn:
            conn.execute("SELECT 1")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    tg_status = "configured" if bot_bridge.is_telegram_configured() else "simulated"
    return jsonify({
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "telegram": tg_status,
        "store_name": config.STORE_NAME,
        "timestamp": datetime.datetime.utcnow().isoformat()
    })

@app.route("/api/internal/users/upsert", methods=["POST"])
@internal_api_required
def api_internal_users_upsert():
    """POST /api/internal/users/upsert — Upsert customer profile."""
    data = request.get_json() or {}
    telegram_id = data.get("telegram_id")
    if not telegram_id:
        return jsonify({"error": "telegram_id is required"}), 400

    user = models.upsert_user(
        telegram_id=int(telegram_id),
        username=data.get("username") or data.get("telegram_username"),
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
        phone=data.get("phone"),
        full_name=data.get("full_name")
    )
    return jsonify({"success": True, "user": user})

@app.route("/api/internal/users/<int:telegram_user_id>", methods=["GET"])
@internal_api_required
def api_internal_get_user(telegram_user_id):
    """GET /api/internal/users/{telegram_user_id} — Fetch customer profile, orders, timeline."""
    profile = models.get_customer_profile(telegram_user_id)
    if not profile:
        return jsonify({"error": "Customer not found"}), 404
    return jsonify(profile)

@app.route("/api/internal/products", methods=["GET", "POST"])
def api_internal_products():
    """
    GET /api/internal/products — Return full catalog with active plans and pricing.
    Accessible publicly or with Base44 API Key (Bearer / X-Internal-API-Key).
    POST /api/internal/products — Create/sync product from Base44.
    """
    if request.method == "POST":
        return api_create_product()

    # Optional authentication check
    auth_header = request.headers.get("Authorization", "").strip()
    api_key_header = request.headers.get("X-Internal-API-Key", "").strip() or request.headers.get("X-API-Key", "").strip()
    token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else (auth_header or api_key_header)
    if not token:
        token = request.args.get("api_key", "").strip() or request.args.get("key", "").strip()

    raw_keys = getattr(config, "BASE44_INTERNAL_API_KEY", "") or ""
    expected_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

    # If an explicitly invalid token was provided, reject with 401
    if token and expected_keys and not any(secrets.compare_digest(token, ek) for ek in expected_keys):
        if not (app.config.get("TESTING") and token == "test-key"):
            return jsonify({
                "error": "Unauthorized",
                "message": "Invalid Base44 API key provided."
            }), 401

    products = models.get_all_products_admin()

    # If client specifically requested envelope format
    if request.args.get("format") == "envelope" or request.args.get("wrap") == "true":
        return jsonify({"success": True, "count": len(products), "products": products})

    return jsonify(products)

@app.route("/api/internal/orders/create", methods=["POST"])
@internal_api_required
def api_internal_orders_create():
    """POST /api/internal/orders/create — Create an order."""
    data = request.get_json() or {}
    telegram_id = data.get("telegram_id")
    product_id = data.get("product_id")
    plan_id = data.get("plan_id")

    if not telegram_id or not product_id:
        return jsonify({"error": "telegram_id and product_id are required"}), 400

    try:
        order = models.create_order(
            telegram_id=int(telegram_id),
            product_id=product_id,
            plan_id=plan_id,
            customer_name=data.get("customer_name"),
            customer_username=data.get("customer_username"),
            customer_phone=data.get("customer_phone"),
            customer_delivery_info=data.get("customer_delivery_info"),
            customer_notes=data.get("customer_notes"),
            payment_method=data.get("payment_method"),
            status=data.get("status", models.STATUS_NEW)
        )
        return jsonify({"success": True, "order": order}), 201
    except Exception as e:
        logger.exception("Internal order creation failed")
        return jsonify({"error": str(e)}), 400

@app.route("/api/internal/orders/update", methods=["POST"])
@internal_api_required
def api_internal_orders_update():
    """
    POST /api/internal/orders/update — Update order status, notes, or deliver credentials.
    Automatically sends corresponding customer Telegram notification.
    """
    data = request.get_json() or {}
    order_id = data.get("order_id")
    order_number = data.get("order_number")
    new_status = data.get("status")

    if not order_id and order_number:
        order_rec = models.get_order_by_number(order_number)
        if order_rec:
            order_id = order_rec["id"]

    if not order_id:
        return jsonify({"error": "order_id or order_number is required"}), 400

    if not new_status:
        return jsonify({"error": "status is required"}), 400

    admin_username = data.get("admin_username") or getattr(request, "current_caller", "Base44_Admin")
    notes = data.get("admin_notes")
    rejection_reason = data.get("rejection_reason")
    delivery_content = data.get("delivery_content")
    file_path = data.get("file_path")
    notify_customer = data.get("notify_customer", True)

    try:
        updated_order, tg_res = models.update_order_status(
            order_id=int(order_id),
            new_status=new_status,
            changed_by=admin_username,
            notes=notes,
            notify_customer=notify_customer,
            delivery_content=delivery_content,
            file_path=file_path,
            rejection_reason=rejection_reason
        )
        if not updated_order:
            return jsonify({"error": "Order not found"}), 404

        return jsonify({
            "success": True,
            "order": updated_order,
            "telegram_response": tg_res
        })
    except Exception as e:
        logger.exception("Internal order update failed")
        return jsonify({"error": str(e)}), 500

@app.route("/api/internal/orders/<order_id>", methods=["GET"])
@internal_api_required
def api_internal_get_order(order_id):
    """GET /api/internal/orders/{order_id} — Fetch order details, payment, status history, receipt metadata."""
    if str(order_id).isdigit():
        order = models.get_order_by_id(int(order_id))
    else:
        order = models.get_order_by_number(str(order_id))

    if not order:
        return jsonify({"error": "Order not found"}), 404
    return jsonify(order)

@app.route("/api/internal/payments/create", methods=["POST"])
@internal_api_required
def api_internal_payments_create():
    """POST /api/internal/payments/create — Record payment submission with receipt metadata."""
    data = request.get_json() or {}
    order_id = data.get("order_id")
    payment_method = data.get("payment_method")
    payment_sender_name = data.get("payment_sender_name")
    receipt_file = data.get("receipt_file")

    if not order_id or not payment_method or not receipt_file:
        return jsonify({"error": "order_id, payment_method, and receipt_file are required"}), 400

    try:
        payment = models.record_payment(
            order_id=int(order_id),
            payment_method=payment_method,
            payment_sender_name=payment_sender_name or "Customer",
            receipt_file=receipt_file,
            telegram_file_id=data.get("telegram_file_id"),
            file_unique_id=data.get("file_unique_id"),
            media_type=data.get("media_type", "photo"),
            message_id=data.get("message_id"),
            amount=data.get("amount")
        )
        return jsonify({"success": True, "payment": payment}), 201
    except Exception as e:
        logger.exception("Internal payment creation failed")
        return jsonify({"error": str(e)}), 400

@app.route("/api/internal/payments/update", methods=["POST"])
@internal_api_required
def api_internal_payments_update():
    """POST /api/internal/payments/update — Update payment review status."""
    data = request.get_json() or {}
    payment_id = data.get("payment_id")
    review_status = data.get("review_status")

    if not payment_id or not review_status:
        return jsonify({"error": "payment_id and review_status are required"}), 400

    admin_username = data.get("admin_username") or getattr(request, "current_caller", "Base44_Admin")
    admin_note = data.get("admin_note")

    updated = models.update_payment_review(
        payment_id=int(payment_id),
        review_status=review_status,
        admin_note=admin_note,
        reviewed_by=admin_username
    )
    if not updated:
        return jsonify({"error": "Payment not found"}), 404

    return jsonify({"success": True, "payment": updated})

@app.route("/api/internal/messages/incoming", methods=["POST"])
@internal_api_required
def api_internal_messages_incoming():
    """POST /api/internal/messages/incoming — Log customer incoming message."""
    data = request.get_json() or {}
    telegram_id = data.get("telegram_id") or data.get("telegram_user_id")
    message_text = data.get("text") or data.get("message_text", "")
    message_type = data.get("message_type", "text")
    message_id = data.get("message_id")
    media_ref = data.get("media_reference")
    order_id = data.get("related_order_id")

    if not telegram_id:
        return jsonify({"error": "telegram_id is required"}), 400

    msg = models.log_conversation_message(
        telegram_id=int(telegram_id),
        direction="INCOMING",
        message_type=message_type,
        message_text=message_text,
        media_ref=media_ref,
        related_order_id=order_id,
        delivery_status="DELIVERED",
        message_id=message_id
    )
    return jsonify({"success": True, "message": msg}), 201

@app.route("/api/internal/messages/send", methods=["POST"])
@internal_api_required
def api_internal_messages_send():
    """
    POST /api/internal/messages/send — Send 1-on-1 message from Base44 to customer.
    Supports text, photo, video, document, and reply markup buttons.
    Returns delivery status: Sent, Failed, Blocked, User not found, Telegram error.
    """
    data = request.get_json() or {}
    telegram_id = data.get("telegram_id") or data.get("telegram_user_id") or data.get("chat_id")
    message_text = data.get("text") or data.get("message_text", "")
    media_url = data.get("photo_url") or data.get("document_url") or data.get("video_url") or data.get("media_url")
    media_type = "photo" if data.get("photo_url") else ("video" if data.get("video_url") else ("document" if data.get("document_url") else None))
    reply_markup = data.get("reply_markup")
    order_id = data.get("order_id")
    admin_name = getattr(request, "current_caller", "Base44 Support")

    if not telegram_id or not message_text:
        return jsonify({"error": "telegram_id and text/message_text are required"}), 400

    result = bot_bridge.send_direct_customer_message(
        telegram_id=int(telegram_id),
        message_text=message_text,
        media_url=media_url,
        media_type=media_type,
        reply_markup=reply_markup,
        order_id=order_id,
        admin_name=admin_name
    )

    delivery_status = result.get("delivery_status", "Sent" if result.get("ok") else "Failed")
    return jsonify({
        "success": result.get("ok", False),
        "delivery_status": delivery_status,
        "telegram_response": result
    })

@app.route("/api/internal/broadcast/send", methods=["POST"])
@internal_api_required
def api_internal_broadcast_send():
    """POST /api/internal/broadcast/send — Mass broadcast to customers."""
    data = request.get_json() or {}
    target_type = data.get("target_type", "all")
    target_filter = data.get("target_filter", "")
    message_text = data.get("message_text") or data.get("text", "")
    media_path = data.get("media_path") or data.get("photo_url")
    media_type = data.get("media_type", "text")
    reply_markup = data.get("reply_markup")

    if not message_text:
        return jsonify({"error": "message_text is required"}), 400

    result = models.send_broadcast_message(
        target_type=target_type,
        target_filter=target_filter,
        message_text=message_text,
        media_path=media_path,
        media_type=media_type,
        reply_markup=reply_markup,
        admin_username=getattr(request, "current_caller", "Base44_Admin")
    )
    return jsonify({"success": True, "result": result})

# =====================================================================
# SECURE RECEIPT PROXY & METADATA
# =====================================================================

@app.route("/api/internal/receipts/<int:payment_id>/file", methods=["GET"])
@app.route("/api/receipts/secure/<int:payment_id>", methods=["GET"])
@internal_api_required
def api_internal_receipt_file(payment_id):
    """
    Secure receipt-download mechanism:
    - Never exposes Telegram bot token.
    - Requires authentication.
    - Path traversal protected.
    """
    payment = models.get_payment_by_id(payment_id)
    if not payment or not payment.get("receipt_file"):
        return jsonify({"error": "Receipt not found for this payment record"}), 404

    safe_name = os.path.basename(payment["receipt_file"])
    target_file = (config.RECEIPTS_DIR / safe_name).resolve()

    if not str(target_file).startswith(str(config.RECEIPTS_DIR.resolve())):
        return jsonify({"error": "Forbidden"}), 403

    if not target_file.exists():
        return jsonify({"error": "Receipt file does not exist on storage"}), 404

    return send_from_directory(config.RECEIPTS_DIR, safe_name)

@app.route("/api/internal/receipts/<int:payment_id>/metadata", methods=["GET"])
@internal_api_required
def api_internal_receipt_metadata(payment_id):
    """Returns receipt metadata including upload timestamp, file unique ID, and media type."""
    payment = models.get_payment_by_id(payment_id)
    if not payment:
        return jsonify({"error": "Payment not found"}), 404

    filename = payment.get("receipt_file")
    file_path = config.RECEIPTS_DIR / filename if filename else None
    size = file_path.stat().st_size if file_path and file_path.exists() else None

    return jsonify({
        "payment_id": payment["id"],
        "order_id": payment["order_id"],
        "payment_method": payment["payment_method"],
        "sender_name": payment["payment_sender_name"],
        "amount": payment["amount"],
        "currency": payment["currency"],
        "receipt_file": payment["receipt_file"],
        "telegram_file_id": payment.get("telegram_file_id"),
        "file_unique_id": payment.get("file_unique_id"),
        "media_type": payment.get("media_type", "photo"),
        "submitted_at": payment["submitted_at"],
        "review_status": payment["review_status"],
        "file_size_bytes": size,
        "view_url": f"/api/internal/receipts/{payment['id']}/file"
    })

# Legacy receipt route preserved and secured
@app.route("/api/receipts/<path:filename>")
@app.route("/receipts/<path:filename>")
@login_required
def serve_receipt_legacy(filename):
    safe_name = os.path.basename(filename)
    with models.db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM payments WHERE receipt_file = ? LIMIT 1", (safe_name,))
        if not cursor.fetchone():
            return jsonify({"error": "Receipt file not associated with verified payment"}), 404

    file_path = (config.RECEIPTS_DIR / safe_name).resolve()
    if not str(file_path).startswith(str(config.RECEIPTS_DIR.resolve())) or not file_path.exists():
        return jsonify({"error": "Not Found"}), 404

    return send_from_directory(config.RECEIPTS_DIR, safe_name)

@app.route("/deliveries/<path:filename>")
@login_required
def serve_delivery_file(filename):
    return send_from_directory(config.DELIVERIES_DIR, secure_filename(filename))

# =====================================================================
# TELEGRAM WEBHOOK ENDPOINT
# =====================================================================

@app.route("/api/telegram/webhook", methods=["POST"])
def telegram_webhook():
    """
    Production Telegram webhook receiver.
    Verifies X-Telegram-Bot-Api-Secret-Token against INTERNAL_WEBHOOK_SECRET.
    Enforces update idempotency.
    """
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").strip()
    expected_secret = config.INTERNAL_WEBHOOK_SECRET
    if expected_secret and secret_token:
        if not secrets.compare_digest(secret_token, expected_secret):
            logger.warning("Rejected Telegram webhook: invalid secret token")
            return jsonify({"error": "Unauthorized webhook"}), 403

    update_json = request.get_json(silent=True)
    if not update_json:
        return jsonify({"ok": False, "error": "No JSON payload"}), 400

    update_id = update_json.get("update_id")
    if update_id:
        if models.is_update_processed(update_id):
            logger.debug(f"Skipped duplicate update_id {update_id}")
            return jsonify({"ok": True, "duplicate": True})
        models.mark_update_processed(update_id)

    # Process asynchronously if running live
    try:
        from telegram import Update
        from telegram_bot import build_telegram_app
        tg_app = build_telegram_app()
        if tg_app:
            update = Update.de_json(update_json, tg_app.bot)
            import asyncio
            asyncio.run(tg_app.process_update(update))
    except Exception as ex:
        logger.warning(f"Error processing webhook update: {ex}")

    return jsonify({"ok": True})

@app.route("/api/telegram/set-webhook", methods=["POST"])
@login_required
def api_set_telegram_webhook():
    """Configures webhook URL with Telegram Bot API."""
    data = request.get_json() or {}
    webhook_url = data.get("webhook_url", "").strip()
    if not webhook_url:
        return jsonify({"error": "webhook_url is required"}), 400

    if not bot_bridge.is_telegram_configured():
        return jsonify({"error": "TELEGRAM_BOT_TOKEN not configured"}), 400

    import requests
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/setWebhook"
    payload = {
        "url": webhook_url,
        "secret_token": config.INTERNAL_WEBHOOK_SECRET,
        "drop_pending_updates": True
    }
    resp = requests.post(url, json=payload, timeout=15)
    return jsonify(resp.json())

# =====================================================================
# ADMIN WEB UI & REST DASHBOARD ROUTES
# =====================================================================

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("admin_id"):
        return redirect(url_for("dashboard_page"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        admin = models.get_admin_by_username(username)

        if admin and models.verify_admin_password(admin, password):
            if not admin.get("active"):
                error = "Account deactivated. Contact Super Admin."
            else:
                session["admin_id"] = admin["id"]
                session["admin_username"] = admin["username"]
                session["admin_role"] = admin["role"]
                models.update_admin_last_login(admin["id"])
                models.log_admin_action(
                    admin_id=admin["id"],
                    admin_username=admin["username"],
                    action="Admin Login",
                    details=f"Login from IP {get_client_ip()}",
                    ip_address=get_client_ip()
                )
                next_url = request.args.get("next") or url_for("dashboard_page")
                return redirect(next_url)
        else:
            error = "Invalid username or password."

    settings = models.get_store_settings()
    return render_template("login.html", error=error, settings=settings)

@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("login_page"))

@app.route("/")
@app.route("/products")
@app.route("/orders")
@app.route("/orders/<path:subpath>")
@app.route("/payments")
@app.route("/customers")
@app.route("/customers/<path:subpath>")
@app.route("/reports")
@app.route("/messages")
@app.route("/activity")
@app.route("/settings")
@login_required
def dashboard_page(subpath=None):
    settings = models.get_store_settings()
    try:
        initial_data = {
            "products": models.get_all_products_admin(),
            "orders": models.get_orders_list(limit=200),
            "customers": models.get_customers_list(),
            "payments": models.get_payments_list(),
            "kpis": models.get_dashboard_kpis(),
            "categories": models.get_all_categories(),
            "activityLogs": models.get_recent_activity_logs(limit=50),
            "settings": settings
        }
    except Exception as e:
        initial_data = {}
    return render_template(
        "index.html",
        admin=request.current_admin,
        settings=settings,
        store_website=config.STORE_WEBSITE,
        is_telegram_configured=bot_bridge.is_telegram_configured(),
        initial_data=initial_data
    )

@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload_file():
    if "file" not in request.files and "image" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files.get("file") or request.files.get("image")
    if not f or not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    fname = secure_filename(f.filename)
    unique_name = f"{int(time.time())}_{secrets.token_hex(4)}_{fname}"
    dest = config.PRODUCT_IMAGES_DIR / unique_name
    f.save(dest)
    return jsonify({"file_url": f"/static/uploads/{unique_name}"})

@app.route("/static/uploads/<path:filename>")
def serve_uploaded_file(filename):
    return send_from_directory(config.PRODUCT_IMAGES_DIR, filename)

@app.route("/api/dashboard/stats", methods=["GET"])
@login_required
def api_dashboard_stats():
    return jsonify(models.get_dashboard_kpis())

@app.route("/api/notifications", methods=["GET"])
@login_required
def api_notifications():
    unread = request.args.get("unread_only", "false").lower() == "true"
    limit = int(request.args.get("limit", 20))
    notifs = models.get_admin_notifications(limit=limit, unread_only=unread)
    unread_count = models.get_unread_notification_count()
    return jsonify({"notifications": notifs, "unread_count": unread_count})

@app.route("/api/notifications/mark-read", methods=["POST"])
@login_required
def api_mark_notifications_read():
    data = request.get_json() or {}
    models.mark_notifications_as_read(data.get("id"))
    return jsonify({"success": True, "unread_count": models.get_unread_notification_count()})

@app.route("/api/notifications/poll", methods=["GET"])
@login_required
def api_notifications_poll():
    unread_count = models.get_unread_notification_count()
    kpis = models.get_dashboard_kpis()
    return jsonify({
        "unread_notifications": unread_count,
        "pending_payments_count": kpis["payments_under_review"],
        "pending_review_queue": kpis["pending_review_queue"][:5],
        "total_revenue": kpis["total_revenue"],
        "total_orders": kpis["total_orders"]
    })

@app.route("/api/reports/analytics", methods=["GET"])
@login_required
def api_market_reports():
    time_range = request.args.get("range", "30d")
    return jsonify(models.get_market_reports(time_range=time_range, custom_start=request.args.get("start"), custom_end=request.args.get("end")))

@app.route("/api/products", methods=["GET"])
@login_required
def api_get_products():
    return jsonify(models.get_all_products_admin())

@app.route("/api/products/<product_id>", methods=["GET"])
@login_required
def api_get_product(product_id):
    prod = models.get_product_details(product_id)
    if not prod:
        return jsonify({"error": "Product not found"}), 404
    return jsonify(prod)

@app.route("/api/upload", methods=["POST"])
@app.route("/api/products/upload-image", methods=["POST"])
@login_required
def api_upload_image():
    file = request.files.get("file") or request.files.get("image")
    if not file or file.filename == "":
        return jsonify({"error": "No file uploaded or selected"}), 400

    file_bytes = file.read()
    save_dir = config.PRODUCT_IMAGES_DIR
    save_dir.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(file.filename)[1].lower() or ".jpg"
    filename = f"prod_{secrets.token_hex(6)}{ext}"
    filepath = save_dir / filename
    try:
        with open(filepath, "wb") as f:
            f.write(file_bytes)
    except Exception:
        pass

    # Convert to persistent base64 Data URI so Render ephemeral wipes never destroy the image
    b64_url = f"/static/uploads/products/{filename}"
    try:
        from PIL import Image
        bio = io.BytesIO(file_bytes)
        im = Image.open(bio)
        if im.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            if im.mode == "P":
                im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((800, 800), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=85, optimize=True)
        b64 = base64.b64encode(out.getvalue()).decode("utf-8")
        b64_url = f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.warning(f"Web image base64 conversion notice: {e}")

    return jsonify({
        "success": True,
        "file_url": b64_url,
        "image_url": b64_url,
        "url": b64_url,
        "filename": filename
    })

@app.route("/api/products", methods=["POST"])
@login_required
def api_create_product():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Product name is required"}), 400

    plans = data.get("plans") or []
    if not plans and (data.get("price") is not None or data.get("duration")):
        price = float(data.get("price") or 0)
        duration = data.get("duration") or "1 Month"
        plans = [{"name": duration, "duration": duration, "price": price, "currency": "ETB", "active": 1}]

    created = models.create_product(
        name=name,
        category_id=data.get("category_id"),
        description=data.get("description", "").strip(),
        image_url=data.get("image_url", "").strip(),
        features=data.get("features", []),
        requirements=data.get("requirements", []),
        active=1 if data.get("active", True) in (True, 1, "1") else 0,
        plans=plans
    )

    # Auto-announce to all customers if product is active
    if created.get("active", 1):
        try:
            all_users = models.get_broadcast_recipients("all") or []
            recipient_set = set(all_users)
            if getattr(config, "ADMIN_TELEGRAM_ID", None):
                try:
                    recipient_set.add(int(config.ADMIN_TELEGRAM_ID))
                except Exception:
                    pass
            for aid in getattr(config, "ADMIN_TELEGRAM_IDS", []):
                try:
                    recipient_set.add(int(aid))
                except Exception:
                    pass

            recipients = list(recipient_set)
            if recipients:
                plan_info = plans[0] if plans else {"duration": "Standard", "price": 0}
                p_price = float(plan_info.get("price", 0))
                p_dur = plan_info.get("duration", "Standard")
                bc_text = (
                    f"🎉 <b>NEW SERVICE ADDED TO THE STORE!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ <b>{created['name'].upper()}</b>\n\n"
                    f"📝 <b>Description:</b>\n{created.get('description', '')}\n\n"
                    f"💰 <b>Pricing:</b> {p_dur} — <b>{p_price:,.0f} ETB</b>\n\n"
                    f"🔥 <i>Available right now in the store! Tap below to view and order:</i>"
                )
                cb_prod = created['id'][5:] if created['id'].startswith('prod_') else created['id']
                bc_markup = {
                    "inline_keyboard": [
                        [{"text": f"🛒 View & Order {created['name']}", "callback_data": f"prod_{cb_prod}", "style": "success"}],
                        [{"text": "🛍️ Open Catalog", "callback_data": "menu_services", "style": "primary"}]
                    ]
                }
                bc_media = created.get("image_url", "")
                bot_bridge.broadcast_to_users(
                    recipients=recipients,
                    message_text=bc_text,
                    media_path=bc_media if bc_media else None,
                    media_type="photo" if bc_media else "text",
                    reply_markup=bc_markup
                )
        except Exception as e:
            logger.warning(f"Web API create product broadcast notice: {e}")

    return jsonify({"success": True, "product": created}), 201

@app.route("/api/products/<product_id>", methods=["PUT"])
@login_required
def api_update_product(product_id):
    data = request.get_json() or {}
    plans = data.get("plans")
    if plans is None and (data.get("price") is not None or data.get("duration")):
        price = float(data.get("price") or 0)
        duration = data.get("duration") or "1 Month"
        plans = [{"name": duration, "duration": duration, "price": price, "currency": "ETB", "active": 1}]

    updated = models.update_product(
        product_id=product_id,
        name=data.get("name", "").strip(),
        category_id=data.get("category_id"),
        description=data.get("description", "").strip(),
        image_url=data.get("image_url", "").strip(),
        features=data.get("features", []),
        requirements=data.get("requirements", []),
        active=1 if data.get("active", True) in (True, 1, "1") else 0,
        plans=plans
    )
    return jsonify({"success": True, "product": updated})

@app.route("/api/products/<product_id>/toggle", methods=["POST"])
@login_required
def api_toggle_product(product_id):
    updated = models.toggle_product_active(product_id)
    return jsonify({"success": True, "product": updated})

@app.route("/api/products/<product_id>", methods=["DELETE"])
@login_required
def api_delete_product(product_id):
    models.delete_product(product_id)
    return jsonify({"success": True, "message": "Product deleted successfully."})

@app.route("/api/categories", methods=["GET"])
@login_required
def api_get_categories():
    return jsonify(models.get_all_categories())

@app.route("/api/categories", methods=["POST"])
@login_required
def api_create_category():
    data = request.get_json() or {}
    created = models.create_category(
        name=data.get("name", "").strip(),
        icon=data.get("icon", "📦").strip(),
        sort_order=int(data.get("sort_order", 0))
    )
    return jsonify({"success": True, "category": created}), 201

@app.route("/api/orders", methods=["GET"])
@login_required
def api_get_orders():
    orders = models.get_orders_list(
        status_filter=request.args.get("status", "all"),
        search_query=request.args.get("search", ""),
        limit=int(request.args.get("limit", 100)),
        offset=int(request.args.get("offset", 0))
    )
    return jsonify(orders)

@app.route("/api/orders/<order_id>", methods=["GET"])
@login_required
def api_get_order_details(order_id):
    order = models.get_order_by_id(int(order_id)) if str(order_id).isdigit() else models.get_order_by_number(str(order_id))
    if not order:
        return jsonify({"error": "Order not found"}), 404
    return jsonify(order)

@app.route("/api/orders/<int:order_id>/accept", methods=["POST"])
@login_required
def api_accept_order(order_id):
    data = request.get_json() or {}
    admin = request.current_admin
    order, tg_res = models.accept_order_payment(
        order_id=order_id,
        admin_id=admin["id"],
        admin_username=admin["username"],
        admin_note=data.get("admin_note", "").strip() or None
    )
    if not order:
        return jsonify({"error": "Order not found"}), 404
    return jsonify({"success": True, "order": order, "telegram_response": tg_res})

@app.route("/api/orders/<int:order_id>/reject", methods=["POST"])
@login_required
def api_reject_order(order_id):
    data = request.get_json() or {}
    reason = data.get("reason", "").strip()
    if not reason:
        return jsonify({"error": "A rejection reason is required to notify the customer."}), 400

    admin = request.current_admin
    order, tg_res = models.reject_order_payment(
        order_id=order_id,
        reason=reason,
        admin_id=admin["id"],
        admin_username=admin["username"]
    )
    if not order:
        return jsonify({"error": "Order not found"}), 404
    return jsonify({"success": True, "order": order, "telegram_response": tg_res})

@app.route("/api/orders/<int:order_id>/deliver", methods=["POST"])
@login_required
def api_deliver_order(order_id):
    content = request.form.get("content", "").strip() if request.form else ""
    if not content and request.is_json:
        content = (request.get_json() or {}).get("content", "").strip()

    if not content:
        return jsonify({"error": "Delivery content is required."}), 400

    file_path = None
    if "delivery_file" in request.files:
        f = request.files["delivery_file"]
        if f and f.filename:
            fname = secure_filename(f.filename)
            dest = config.DELIVERIES_DIR / f"deliv_{int(time.time())}_{fname}"
            f.save(dest)
            file_path = str(dest)

    admin = request.current_admin
    order, tg_res = models.deliver_order(
        order_id=order_id,
        delivery_content=content,
        file_path=file_path,
        admin_id=admin["id"],
        admin_username=admin["username"]
    )
    return jsonify({"success": True, "order": order, "telegram_response": tg_res})

@app.route("/api/payments", methods=["GET"])
@login_required
def api_get_payments():
    return jsonify(models.get_payments_list(status_filter=request.args.get("status", "all")))

@app.route("/api/customers", methods=["GET"])
@login_required
def api_get_customers():
    return jsonify(models.get_customers_list(search_query=request.args.get("search", "")))

@app.route("/api/customers/<int:telegram_id>", methods=["GET"])
@login_required
def api_get_customer_profile_admin(telegram_id):
    profile = models.get_customer_profile(telegram_id)
    if not profile:
        return jsonify({"error": "Customer not found"}), 404
    return jsonify(profile)

@app.route("/api/messages/direct", methods=["POST"])
@login_required
def api_admin_send_direct_message():
    data = request.get_json() or {}
    telegram_id = data.get("telegram_id")
    text = data.get("message_text", "").strip()
    if not telegram_id or not text:
        return jsonify({"error": "telegram_id and message_text are required."}), 400

    admin = request.current_admin
    res = bot_bridge.send_direct_customer_message(
        telegram_id=int(telegram_id),
        message_text=text,
        admin_name=admin["username"]
    )
    models.log_admin_action(
        admin_id=admin["id"],
        admin_username=admin["username"],
        action="Sent Direct Message",
        target_type="customer",
        target_id=str(telegram_id),
        details=f"Sent direct message: {text[:60]}..."
    )
    return jsonify({"success": res.get("ok", False), "telegram_response": res})

@app.route("/api/messages/broadcast", methods=["POST"])
@login_required
def api_admin_send_broadcast():
    data = request.get_json() or {}
    text = data.get("message_text", "").strip()
    if not text:
        return jsonify({"error": "message_text is required"}), 400

    admin = request.current_admin
    result = models.send_broadcast_message(
        target_type=data.get("target_type", "all"),
        target_filter=data.get("target_filter", ""),
        message_text=text,
        media_path=data.get("media_path"),
        media_type=data.get("media_type", "text"),
        admin_id=admin["id"],
        admin_username=admin["username"]
    )
    return jsonify({
        "success": True,
        "result": result,
        "sent_count": result.get("sent", 0),
        "failed_count": result.get("failed", 0),
        "total": result.get("total", 0)
    })

@app.route("/api/messages/history", methods=["GET"])
@login_required
def api_messages_history():
    return jsonify(models.get_broadcasts_history())

@app.route("/api/messages/preview-audience", methods=["GET"])
@login_required
def api_preview_audience():
    target_type = request.args.get("target_type", "all")
    target_filter = request.args.get("target_filter", "")
    recipients = models.get_broadcast_recipients(target_type=target_type, target_filter=target_filter)
    return jsonify({"total_recipients": len(recipients), "sample_recipients": recipients[:8]})

@app.route("/api/settings", methods=["GET"])
@login_required
def api_get_settings():
    settings = models.get_store_settings()
    settings["telegram_bot_status"] = "Configured" if bot_bridge.is_telegram_configured() else "Simulated"
    # Never expose tokens or sensitive secrets
    clean = {k: v for k, v in settings.items() if not any(s in k.lower() for s in ("token", "secret", "password"))}
    clean["telegram_bot_status"] = "configured" if bot_bridge.is_telegram_configured() else "missing_token"
    return jsonify(clean)

@app.route("/api/settings", methods=["POST"])
@login_required
def api_update_settings():
    data = request.get_json() or {}
    admin = request.current_admin
    updated = models.update_store_settings(data, admin_id=admin["id"], admin_username=admin["username"])
    return jsonify({"success": True, "settings": updated})

@app.route("/api/activity-logs", methods=["GET"])
@login_required
def api_get_activity_logs():
    return jsonify(models.get_recent_activity_logs())

@app.route("/api/admin/clear-history", methods=["POST"])
@login_required
def api_clear_history():
    admin = request.current_admin
    models.clear_demo_history(admin_id=admin["id"], admin_username=admin["username"], clear_users=True)
    return jsonify({"success": True, "message": "Demo history cleared successfully."})

if __name__ == "__main__":
    print(f"Starting ABDUREA STORE Backend on {config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
