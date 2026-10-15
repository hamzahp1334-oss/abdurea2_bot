# -*- coding: utf-8 -*-
"""
Telegram Bot Messaging Bridge for ABDUREA STORE.
Handles ultra-fast outgoing messages, status notifications, media dispatch,
broadcasts, rate-limiting, and conversation history logging.
Optimized with persistent connection pooling and parallel workers for < 2-second delivery.
"""
import logging
import time
import os
import io
import json
import base64
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
import config
from config import STORE_NAME, DEFAULT_SUPPORT_USERNAME

logger = logging.getLogger("bot_bridge")
TELEGRAM_API_URL = "https://api.telegram.org/bot"

# -------------------------------------------------------------
# HIGH-SPEED PERSISTENT SESSION WITH CONNECTION POOLING
# -------------------------------------------------------------
_telegram_session = requests.Session()
_adapter = HTTPAdapter(
    pool_connections=50,
    pool_maxsize=100,
    max_retries=Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
)
_telegram_session.mount("https://", _adapter)
_telegram_session.mount("http://", _adapter)

def _warmup_session():
    """Pre-warm TCP/TLS connection to Telegram Bot API so first message is instant."""
    try:
        token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
        if token and not token.startswith("YOUR_") and len(token) > 10:
            _telegram_session.get(f"{TELEGRAM_API_URL}{token}/getMe", timeout=3)
    except Exception:
        pass

# Trigger warmup
_warmup_session()

def is_telegram_configured():
    """Checks if a valid Telegram bot token is configured."""
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    test_mode = getattr(config, "TESTING_MODE", False)
    return bool(token and not token.startswith("YOUR_") and len(token) > 10 and not test_mode)

def _log_outgoing_message(telegram_id, message_type, text, media_ref=None, order_id=None, status="DELIVERED", msg_id=None):
    """Helper to log outgoing messages to the conversation timeline."""
    try:
        from database import db_session
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO conversation_messages (
                    message_id, telegram_id, direction, message_type,
                    message_text, media_reference, related_order_id, delivery_status
                ) VALUES (?, ?, 'OUTGOING', ?, ?, ?, ?, ?);
            """, (msg_id, telegram_id, message_type, text, media_ref, order_id, status))
    except Exception as ex:
        logger.warning(f"Could not log outgoing message to DB: {ex}")

def send_telegram_message(chat_id, text, parse_mode="HTML", reply_markup=None, order_id=None):
    """Send text message to Telegram user via Bot API in < 2 seconds."""
    if not is_telegram_configured():
        logger.info(f"[SIMULATED BOT] Message to {chat_id}: {text[:100]}...")
        _log_outgoing_message(chat_id, "text", text, None, order_id, "SENT", None)
        return {"ok": True, "simulated": True, "delivery_status": "Sent", "description": "Simulated Telegram dispatch"}

    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    url = f"{TELEGRAM_API_URL}{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = _telegram_session.post(url, json=payload, timeout=5)
        data = resp.json()
        if data.get("ok"):
            msg_id = data.get("result", {}).get("message_id")
            _log_outgoing_message(chat_id, "text", text, None, order_id, "DELIVERED", msg_id)
            return {"ok": True, "result": data.get("result"), "delivery_status": "Sent"}
        else:
            desc = data.get("description", "Unknown Telegram error")
            status = "Blocked" if "blocked" in desc.lower() else ("User not found" if "chat not found" in desc.lower() else "Telegram error")
            _log_outgoing_message(chat_id, "text", text, None, order_id, "FAILED", None)
            return {"ok": False, "error": desc, "delivery_status": status}
    except Exception as e:
        logger.warning(f"Telegram API request failed for {chat_id}: {e}")
        _log_outgoing_message(chat_id, "text", text, None, order_id, "FAILED", None)
        return {"ok": False, "error": str(e), "delivery_status": "Telegram error"}

def send_telegram_photo(chat_id, photo, caption=None, parse_mode="HTML", reply_markup=None, order_id=None):
    """Send photo to Telegram user (base64 Data URI, URL, or local file path)."""
    if not is_telegram_configured():
        logger.info(f"[SIMULATED BOT] Photo to {chat_id}: {str(photo)[:60]}")
        _log_outgoing_message(chat_id, "photo", caption or "", str(photo)[:100], order_id, "SENT")
        return {"ok": True, "simulated": True}

    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    url = f"{TELEGRAM_API_URL}{token}/sendPhoto"
    data = {"chat_id": chat_id, "parse_mode": parse_mode}
    if caption:
        data["caption"] = caption if len(caption) <= 1020 else caption[:1015] + "..."

    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, (dict, list)) else reply_markup

    try:
        # 1. Base64 Data URI (e.g. data:image/jpeg;base64,...)
        if isinstance(photo, str) and photo.startswith("data:image/") and ";base64," in photo:
            try:
                b64_part = photo.split(";base64,")[1]
                raw_bytes = base64.b64decode(b64_part)
                files = {"photo": ("product.jpg", raw_bytes, "image/jpeg")}
                resp = _telegram_session.post(url, data=data, files=files, timeout=15)
                res_json = resp.json()
                if res_json.get("ok"):
                    msg_id = res_json.get("result", {}).get("message_id")
                    _log_outgoing_message(chat_id, "photo", caption or "", "[base64_image]", order_id, "DELIVERED", msg_id)
                    return {"ok": True, "result": res_json.get("result"), "delivery_status": "Sent"}
                else:
                    return {"ok": False, "error": res_json.get("description", "Error"), "delivery_status": "Failed"}
            except Exception as b64e:
                logger.warning(f"Failed to post base64 photo to {chat_id}: {b64e}")
                return {"ok": False, "error": str(b64e), "delivery_status": "Failed"}

        # 2. Check if local candidate in static/uploads
        local_candidate = None
        if isinstance(photo, (str, Path)):
            p_str = str(photo).strip()
            if os.path.exists(p_str) and not os.path.isdir(p_str):
                local_candidate = p_str
            elif p_str.startswith("/static/") or p_str.startswith("static/"):
                c = config.BASE_DIR / p_str.lstrip("/")
                if c.exists() and not c.is_dir():
                    local_candidate = str(c)
            else:
                fname = os.path.basename(p_str.split("?")[0])
                if fname:
                    c2 = config.PRODUCT_IMAGES_DIR / fname
                    if c2.exists() and not c2.is_dir():
                        local_candidate = str(c2)

        if local_candidate:
            with open(local_candidate, "rb") as f:
                files = {"photo": ("banner.jpg", f.read(), "image/jpeg")}
                resp = _telegram_session.post(url, data=data, files=files, timeout=15)
            res_json = resp.json()
            if res_json.get("ok"):
                msg_id = res_json.get("result", {}).get("message_id")
                _log_outgoing_message(chat_id, "photo", caption or "", str(photo), order_id, "DELIVERED", msg_id)
                return {"ok": True, "result": res_json.get("result"), "delivery_status": "Sent"}
            else:
                return {"ok": False, "error": res_json.get("description", "Error"), "delivery_status": "Failed"}

        # 3. HTTP / HTTPS URL
        if isinstance(photo, str) and (photo.startswith("http://") or photo.startswith("https://")):
            fetched = False
            try:
                r = _telegram_session.get(photo, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                if r.status_code == 200 and r.content:
                    files = {"photo": ("product.jpg", r.content, "image/jpeg")}
                    resp = _telegram_session.post(url, data=data, files=files, timeout=15)
                    fetched = True
                    res_json = resp.json()
                    if res_json.get("ok"):
                        msg_id = res_json.get("result", {}).get("message_id")
                        _log_outgoing_message(chat_id, "photo", caption or "", str(photo), order_id, "DELIVERED", msg_id)
                        return {"ok": True, "result": res_json.get("result"), "delivery_status": "Sent"}
            except Exception as fe:
                logger.debug(f"Direct download fallback for photo: {fe}")

            if not fetched:
                data["photo"] = photo
                resp = _telegram_session.post(url, json=data, timeout=10)
                res_json = resp.json()
                if res_json.get("ok"):
                    msg_id = res_json.get("result", {}).get("message_id")
                    _log_outgoing_message(chat_id, "photo", caption or "", str(photo), order_id, "DELIVERED", msg_id)
                    return {"ok": True, "result": res_json.get("result"), "delivery_status": "Sent"}
                else:
                    return {"ok": False, "error": res_json.get("description", "Error"), "delivery_status": "Failed"}

        # 4. Telegram file_id (string without slashes)
        if isinstance(photo, str) and not "/" in photo and not "\\" in photo and len(photo) > 10:
            data["photo"] = photo
            resp = _telegram_session.post(url, json=data, timeout=10)
            res_json = resp.json()
            if res_json.get("ok"):
                msg_id = res_json.get("result", {}).get("message_id")
                _log_outgoing_message(chat_id, "photo", caption or "", str(photo), order_id, "DELIVERED", msg_id)
                return {"ok": True, "result": res_json.get("result"), "delivery_status": "Sent"}
            else:
                return {"ok": False, "error": res_json.get("description", "Error"), "delivery_status": "Failed"}

        return {"ok": False, "error": f"Invalid or non-existent photo: {str(photo)[:100]}", "delivery_status": "Failed"}
    except Exception as e:
        logger.warning(f"Telegram photo error for {chat_id}: {e}")
        return {"ok": False, "error": str(e), "delivery_status": "Failed"}

def send_telegram_document(chat_id, document, caption=None, parse_mode="HTML", reply_markup=None, order_id=None):
    """Send document or media file to Telegram user."""
    if not is_telegram_configured():
        logger.info(f"[SIMULATED BOT] Document to {chat_id}: {document}")
        _log_outgoing_message(chat_id, "document", caption or "", str(document), order_id, "SENT")
        return {"ok": True, "simulated": True}

    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    url = f"{TELEGRAM_API_URL}{token}/sendDocument"
    data = {"chat_id": chat_id, "parse_mode": parse_mode}
    if caption:
        data["caption"] = caption

    try:
        if isinstance(document, str) and (document.startswith("http://") or document.startswith("https://")):
            data["document"] = document
            if reply_markup:
                data["reply_markup"] = reply_markup
            resp = _telegram_session.post(url, json=data, timeout=8)
        else:
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, (dict, list)) else reply_markup
            with open(document, "rb") as f:
                files = {"document": f}
                resp = _telegram_session.post(url, data=data, files=files, timeout=8)
        res_json = resp.json()
        if res_json.get("ok"):
            msg_id = res_json.get("result", {}).get("message_id")
            _log_outgoing_message(chat_id, "document", caption or "", str(document), order_id, "DELIVERED", msg_id)
            return {"ok": True, "result": res_json.get("result"), "delivery_status": "Sent"}
        else:
            return {"ok": False, "error": res_json.get("description", "Error"), "delivery_status": "Failed"}
    except Exception as e:
        logger.warning(f"Telegram document error for {chat_id}: {e}")
        return {"ok": False, "error": str(e), "delivery_status": "Failed"}

def send_telegram_video(chat_id, video, caption=None, parse_mode="HTML", reply_markup=None, order_id=None):
    """Send video file to Telegram user."""
    if not is_telegram_configured():
        logger.info(f"[SIMULATED BOT] Video to {chat_id}: {video}")
        _log_outgoing_message(chat_id, "video", caption or "", str(video), order_id, "SENT")
        return {"ok": True, "simulated": True}

    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    url = f"{TELEGRAM_API_URL}{token}/sendVideo"
    data = {"chat_id": chat_id, "parse_mode": parse_mode}
    if caption:
        data["caption"] = caption

    try:
        if isinstance(video, str) and (video.startswith("http://") or video.startswith("https://")):
            data["video"] = video
            if reply_markup:
                data["reply_markup"] = reply_markup
            resp = _telegram_session.post(url, json=data, timeout=8)
        else:
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, (dict, list)) else reply_markup
            with open(video, "rb") as f:
                files = {"video": f}
                resp = _telegram_session.post(url, data=data, files=files, timeout=8)
        res_json = resp.json()
        if res_json.get("ok"):
            msg_id = res_json.get("result", {}).get("message_id")
            _log_outgoing_message(chat_id, "video", caption or "", str(video), order_id, "DELIVERED", msg_id)
            return {"ok": True, "result": res_json.get("result"), "delivery_status": "Sent"}
        else:
            return {"ok": False, "error": res_json.get("description", "Error"), "delivery_status": "Failed"}
    except Exception as e:
        logger.warning(f"Telegram video error for {chat_id}: {e}")
        return {"ok": False, "error": str(e), "delivery_status": "Failed"}

# -------------------------------------------------------------
# CUSTOMER STATUS NOTIFICATIONS (Exact Customer-Facing Wording)
# -------------------------------------------------------------

def notify_order_submitted(order):
    """
    Exact wording:
    'Your payment receipt has been received. Your order is now waiting for verification.'
    """
    chat_id = order.get("telegram_id")
    order_num = order.get("order_number")
    product_name = order.get("product_name")
    plan = order.get("plan_duration") or order.get("plan_name") or ""

    text = (
        f"⏳ <b>Your payment receipt has been received. Your order is now waiting for verification.</b>\n\n"
        f"<b>Order Number:</b> <code>#{order_num}</code>\n"
        f"<b>Service:</b> {product_name} ({plan})\n"
        f"<b>Status:</b> 🟠 PAYMENT SUBMITTED / UNDER REVIEW\n\n"
        f"Our administration team is manually verifying your receipt. "
        f"You will receive an automatic confirmation here as soon as it is approved!"
    )
    return send_telegram_message(chat_id, text, order_id=order.get("id"))

def notify_order_accepted(order, admin_name="Admin"):
    """
    Exact wording:
    'Your payment has been verified successfully. Your order is now being processed.'
    """
    chat_id = order.get("telegram_id")
    order_num = order.get("order_number")
    product_name = order.get("product_name")
    plan = order.get("plan_duration") or order.get("plan_name") or ""

    text = (
        f"✅ <b>Your payment has been verified successfully. Your order is now being processed.</b>\n\n"
        f"<b>Order Number:</b> <code>#{order_num}</code>\n"
        f"<b>Service:</b> {product_name} ({plan})\n"
        f"<b>Status:</b> 🔵 PAYMENT APPROVED / PROCESSING\n\n"
        f"Your digital credentials / access details are being prepared. "
        f"They will be delivered directly into this chat shortly.\n\n"
        f"🙏 <i>Thank you for choosing {STORE_NAME}!</i>"
    )
    return send_telegram_message(chat_id, text, order_id=order.get("id"))

def notify_order_rejected(order, reason="", admin_name="Admin"):
    """
    Exact wording:
    'Your payment could not be verified. Please review the payment information and submit a valid receipt.'
    """
    chat_id = order.get("telegram_id")
    order_num = order.get("order_number")
    product_name = order.get("product_name")

    text = (
        f"❌ <b>Your payment could not be verified. Please review the payment information and submit a valid receipt.</b>\n\n"
        f"<b>Order Number:</b> <code>#{order_num}</code>\n"
        f"<b>Service:</b> {product_name}\n"
        f"<b>Status:</b> 🔴 PAYMENT REJECTED\n\n"
        f"<b>Details from Admin:</b>\n"
        f"<i>{reason or 'The uploaded transaction receipt was invalid, incomplete, or did not match our records.'}</i>\n\n"
        f"If you made a payment or have any questions, please contact our support team below."
    )
    support_btn = {
        "inline_keyboard": [
            [{"text": "📞 Contact Support", "url": f"https://t.me/{DEFAULT_SUPPORT_USERNAME.replace('@', '')}"}]
        ]
    }
    return send_telegram_message(chat_id, text, reply_markup=support_btn, order_id=order.get("id"))

def notify_order_completed(order, delivery_content, file_path=None, admin_name="Admin"):
    """
    Exact wording:
    'Your order has been completed. Thank you for using ABDUREA STORE.'
    """
    chat_id = order.get("telegram_id")
    order_num = order.get("order_number")
    product_name = order.get("product_name")
    plan = order.get("plan_duration") or order.get("plan_name") or ""

    text = (
        f"🎉 <b>Your order has been completed. Thank you for using {STORE_NAME}.</b>\n\n"
        f"<b>Service:</b> {product_name} ({plan})\n"
        f"<b>Order Number:</b> <code>#{order_num}</code>\n"
        f"<b>Status:</b> 🟢 COMPLETED\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Access Credentials / Instructions:</b>\n\n"
        f"{delivery_content}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 <i>Need assistance setting up? Contact support: {DEFAULT_SUPPORT_USERNAME}</i>\n"
        f"⭐ <i>Enjoy your premium service!</i>"
    )
    msg_res = send_telegram_message(chat_id, text, order_id=order.get("id"))
    if file_path:
        send_telegram_document(chat_id, file_path, caption=f"Delivery Attachment for #{order_num}", order_id=order.get("id"))
    return msg_res

# -------------------------------------------------------------
# DIRECT MESSAGING & CONCURRENT BROADCASTS
# -------------------------------------------------------------

def send_direct_customer_message(telegram_id, message_text, media_url=None, media_type=None, reply_markup=None, order_id=None, admin_name="Admin Support"):
    """Send a direct 1-on-1 message from Admin to a specific customer in < 2 seconds."""
    text = (
        f"💬 <b>Message from {STORE_NAME} Support</b>\n\n"
        f"{message_text}\n\n"
        f"<i>You can reply to this chat or reach support at {DEFAULT_SUPPORT_USERNAME}.</i>"
    )

    if media_url and media_type:
        if media_type == "photo":
            return send_telegram_photo(telegram_id, media_url, caption=text, reply_markup=reply_markup, order_id=order_id)
        elif media_type == "document":
            return send_telegram_document(telegram_id, media_url, caption=text, reply_markup=reply_markup, order_id=order_id)
        elif media_type == "video":
            return send_telegram_video(telegram_id, media_url, caption=text, reply_markup=reply_markup, order_id=order_id)

    return send_telegram_message(telegram_id, text, reply_markup=reply_markup, order_id=order_id)

def broadcast_to_users(recipients, message_text, media_path=None, media_type="text", reply_markup=None):
    """
    Broadcasts message to multiple recipients concurrently using ThreadPoolExecutor.
    Delivers messages in parallel for ultra-fast delivery (< 2 seconds).
    Returns delivery statistics breakdown: Sent, Failed, Blocked, User not found, Telegram error.
    """
    stats = {
        "total": len(recipients),
        "sent": 0,
        "failed": 0,
        "blocked": 0,
        "user_not_found": 0,
        "details": []
    }

    if not recipients:
        return stats

    def _send_one(tid):
        try:
            if media_path and media_type == "photo":
                res = send_telegram_photo(tid, media_path, caption=message_text, reply_markup=reply_markup)
            elif media_path and media_type == "document":
                res = send_telegram_document(tid, media_path, caption=message_text, reply_markup=reply_markup)
            elif media_path and media_type == "video":
                res = send_telegram_video(tid, media_path, caption=message_text, reply_markup=reply_markup)
            else:
                res = send_telegram_message(tid, message_text, reply_markup=reply_markup)
            return tid, res
        except Exception as ex:
            return tid, {"ok": False, "error": str(ex), "delivery_status": "Telegram error"}

    # Throttle concurrency to max 4 parallel workers with 20ms pacing to respect Telegram rate limits
    max_workers = min(4, len(recipients))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for tid in recipients:
            futures.append(executor.submit(_send_one, tid))
            time.sleep(0.02)
        for future in as_completed(futures):
            try:
                tid, res = future.result()
                delivery_status = res.get("delivery_status", "Sent" if res.get("ok") else "Failed")
                if res.get("ok"):
                    stats["sent"] += 1
                else:
                    stats["failed"] += 1
                    if delivery_status == "Blocked":
                        stats["blocked"] += 1
                    elif delivery_status == "User not found":
                        stats["user_not_found"] += 1
                    stats["details"].append({"telegram_id": tid, "status": delivery_status, "error": res.get("error")})
            except Exception as ex:
                stats["failed"] += 1
                stats["details"].append({"telegram_id": "unknown", "status": "Error", "error": str(ex)})

    return stats


# -------------------------------------------------------------
# DEDICATED ADMIN BOT MESSAGING & REAL-TIME ALERTS
# -------------------------------------------------------------

def send_admin_telegram_message(text, parse_mode="HTML", reply_markup=None):
    """Sends a notification directly through the Dedicated Admin Bot to all Admin IDs."""
    admin_token = getattr(config, "ADMIN_BOT_TOKEN", "")
    admin_ids = getattr(config, "ADMIN_TELEGRAM_IDS", [getattr(config, "ADMIN_TELEGRAM_ID", 6666443909)])
    if not admin_token or not admin_ids:
        return {"ok": False, "error": "Admin bot token or ID missing"}
    
    last_resp = {"ok": False}
    for aid in admin_ids:
        url = f"{TELEGRAM_API_URL}{admin_token}/sendMessage"
        payload = {"chat_id": aid, "text": text, "parse_mode": parse_mode}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            resp = _telegram_session.post(url, json=payload, timeout=8)
            last_resp = resp.json()
        except Exception as e:
            logger.warning(f"Failed to send admin bot message to {aid}: {e}")
            last_resp = {"ok": False, "error": str(e)}
    return last_resp

def send_admin_telegram_photo(photo, caption=None, parse_mode="HTML", reply_markup=None):
    """Sends a photo directly through the Dedicated Admin Bot to all Admin IDs."""
    admin_token = getattr(config, "ADMIN_BOT_TOKEN", "")
    admin_ids = getattr(config, "ADMIN_TELEGRAM_IDS", [getattr(config, "ADMIN_TELEGRAM_ID", 6666443909)])
    if not admin_token or not admin_ids:
        return {"ok": False, "error": "Admin bot token or ID missing"}
    
    last_resp = {"ok": False}
    for aid in admin_ids:
        url = f"{TELEGRAM_API_URL}{admin_token}/sendPhoto"
        data = {"chat_id": aid, "parse_mode": parse_mode}
        if caption:
            data["caption"] = caption
        if reply_markup:
            data["reply_markup"] = reply_markup
        try:
            if isinstance(photo, str) and (photo.startswith("http://") or photo.startswith("https://")):
                data["photo"] = photo
                resp = _telegram_session.post(url, json=data, timeout=8)
            else:
                with open(photo, "rb") as f:
                    files = {"photo": f}
                    resp = _telegram_session.post(url, data=data, files=files, timeout=8)
            last_resp = resp.json()
        except Exception as e:
            logger.warning(f"Failed to send admin bot photo to {aid}: {e}")
            last_resp = {"ok": False, "error": str(e)}
    return last_resp

def notify_admin_new_order(order, payment=None):
    """
    Instantly sends an alert with receipt photo and 1-click action buttons
    to the dedicated Telegram Admin Bot.
    """
    order_num = order.get("order_number")
    product_name = order.get("product_name")
    plan = order.get("plan_duration") or order.get("plan_name") or ""
    amount = order.get("amount", 0)
    currency = order.get("currency", "ETB")
    method = order.get("payment_method", "Telebirr")
    c_name = order.get("customer_name") or "Customer"
    c_user = order.get("customer_username") or "none"
    c_id = order.get("telegram_id")
    order_id = order.get("id")

    # Check Telegram Premium VIP status
    is_prem = False
    try:
        user_row = models.get_user_by_telegram_id(c_id)
        if user_row and user_row.get("is_premium"):
            is_prem = True
        elif "VIP Telegram Premium" in (order.get("customer_notes") or ""):
            is_prem = True
    except Exception:
        pass

    vip_badge = " ⭐ <b>[VIP TELEGRAM PREMIUM]</b>" if is_prem else ""

    caption = (
        f"🚨 <b>NEW ORDER RECEIPT SUBMITTED!</b> 🚨\n\n"
        f"🆔 <b>Order:</b> <code>#{order_num}</code>\n"
        f"🛍️ <b>Service:</b> {product_name} ({plan})\n"
        f"💰 <b>Amount:</b> <b>{amount:,.0f} {currency}</b>\n"
        f"💳 <b>Payment Method:</b> {method}\n"
        f"👤 <b>Customer:</b> {c_name} (@{c_user}){vip_badge}\n"
        f"🆔 <b>Telegram ID:</b> <code>{c_id}</code>\n"
        f"📅 <b>Date:</b> {order.get('created_at', '')[:19]}\n\n"
        f"⚡ <i>Quick Admin Actions:</i>"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ 1-Click Approve", "callback_data": f"adm_quick_approve_{order_id}"},
                {"text": "❌ Reject", "callback_data": f"adm_prompt_reject_{order_id}"}
            ],
            [
                {"text": "📦 Deliver Credentials", "callback_data": f"adm_prompt_deliver_{order_id}"},
                {"text": "🔍 Order Details", "callback_data": f"adm_view_order_{order_id}"}
            ]
        ]
    }

    # Locate receipt file on disk if available
    receipt_file = None
    if payment and payment.get("receipt_file"):
        rf = config.RECEIPTS_DIR / payment["receipt_file"]
        if rf.exists():
            receipt_file = rf

    if receipt_file:
        res = send_admin_telegram_photo(receipt_file, caption=caption, reply_markup=reply_markup)
        if res.get("ok"):
            return res

    # Fallback to text message
    return send_admin_telegram_message(caption, reply_markup=reply_markup)

def notify_admin_new_customer(user):
    """Alerts the admin bot when a new customer starts the main bot."""
    name = user.get("full_name") or user.get("first_name") or "New Customer"
    uname = user.get("telegram_username") or "none"
    uid = user.get("telegram_id")
    text = (
        f"👤 <b>New Customer Joined!</b>\n\n"
        f"<b>Name:</b> {name}\n"
        f"<b>Username:</b> @{uname}\n"
        f"<b>Telegram ID:</b> <code>{uid}</code>\n"
        f"<b>Joined:</b> {user.get('created_at', '')[:19]}"
    )
    reply_markup = {
        "inline_keyboard": [
            [{"text": "👤 View Customer Profile", "callback_data": f"adm_cust_{uid}"}]
        ]
    }
    return send_admin_telegram_message(text, reply_markup=reply_markup)

def broadcast_to_users(recipients, message_text, media_path=None, media_type="text", reply_markup=None):
    """
    Dispatches a broadcast announcement to a list of Telegram user IDs via the customer bot.
    Supports text, photo, and custom inline buttons with automatic text fallback.
    """
    total = len(recipients)
    if total == 0:
        return {"total": 0, "sent": 0, "failed": 0}

    def _send_one(uid):
        try:
            res = {"ok": False}
            if media_type == "photo" and media_path:
                res = send_telegram_photo(uid, media_path, caption=message_text, reply_markup=reply_markup)
                if not res.get("ok"):
                    logger.warning(f"Photo broadcast failed for user {uid}, falling back to text: {res.get('error')}")
                    res = send_telegram_message(uid, message_text, reply_markup=reply_markup)
            else:
                res = send_telegram_message(uid, message_text, reply_markup=reply_markup)
            return bool(res.get("ok"))
        except Exception as e:
            logger.warning(f"Broadcast failed for user {uid}: {e}")
            return False

    sent = 0
    failed = 0
    max_workers = min(10, total)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_send_one, uid) for uid in recipients]
        for f in as_completed(futures):
            try:
                if f.result():
                    sent += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

    return {"total": total, "sent": sent, "failed": failed}


