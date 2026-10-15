# -*- coding: utf-8 -*-
"""
Supabase Canonical Unified Orders Bridge for ABDUREA STORE.
Connects the Base44 Website order system (via Supabase) with the Telegram Admin Bot.

Features:
- Realtime WebSocket listener for newly-created website orders (source = 'website')
- 90-second reconciliation fallback query to prevent any missed events
- 100% persistent SQLite idempotency ledger (never sends duplicate alerts)
- Immediate admin notifications via Dedicated Admin Bot with inline action buttons
- Status synchronization (Confirm / Complete / Cancel) back to Supabase and local store.db
- Graceful standby mode if Supabase credentials are not yet configured on the server
"""
import os
import re
import json
import html
import asyncio
import logging
import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
try:
    import websockets
except ImportError:
    websockets = None

import config
from database import db_session, init_db

logger = logging.getLogger("supabase_bridge")

# In-memory fast cache for idempotency, seeded from SQLite on startup
_PROCESSED_EXTERNAL_IDS = set()
_IDEMPOTENCY_LOCK = asyncio.Lock()

# -------------------------------------------------------------
# 1. PERSISTENT SQLITE IDEMPOTENCY LEDGER
# -------------------------------------------------------------

def init_idempotency_ledger():
    """Ensures the processed_external_orders table exists and pre-loads cache."""
    global _PROCESSED_EXTERNAL_IDS
    try:
        init_db()
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processed_external_orders (
                    external_id TEXT PRIMARY KEY,
                    order_number TEXT,
                    supabase_order_id TEXT,
                    source TEXT DEFAULT 'website',
                    notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_status TEXT
                );
            """)
            cursor.execute("SELECT external_id FROM processed_external_orders;")
            rows = cursor.fetchall()
            _PROCESSED_EXTERNAL_IDS = {r["external_id"] for r in rows if r["external_id"]}
        logger.info(f"Loaded {len(_PROCESSED_EXTERNAL_IDS)} processed external order IDs into idempotency cache.")
    except Exception as e:
        logger.warning(f"Could not initialize idempotency ledger: {e}")

def is_order_processed(external_id: str) -> bool:
    """Checks if external_id has already generated an admin notification."""
    if not external_id:
        return False
    clean_id = str(external_id).strip()
    if clean_id in _PROCESSED_EXTERNAL_IDS:
        return True
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM processed_external_orders WHERE external_id = ? LIMIT 1;", (clean_id,))
            if cursor.fetchone():
                _PROCESSED_EXTERNAL_IDS.add(clean_id)
                return True
    except Exception as e:
        logger.warning(f"Idempotency check query error: {e}")
    return False

def record_order_processed(external_id: str, order_number: str = None, supabase_order_id: str = None, status: str = "pending"):
    """Atomically records an order as processed in SQLite and in-memory cache."""
    clean_id = str(external_id).strip()
    _PROCESSED_EXTERNAL_IDS.add(clean_id)
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO processed_external_orders (
                    external_id, order_number, supabase_order_id, source, last_status
                ) VALUES (?, ?, ?, 'website', ?);
            """, (clean_id, str(order_number or ""), str(supabase_order_id or ""), str(status or "pending")))
    except Exception as e:
        logger.warning(f"Failed to record idempotency key '{clean_id}': {e}")

def update_processed_order_status(supabase_order_id: str, new_status: str):
    """Updates the last_status of a processed order in the ledger."""
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE processed_external_orders
                SET last_status = ?
                WHERE supabase_order_id = ? OR external_id = ? OR order_number = ?;
            """, (str(new_status), str(supabase_order_id), str(supabase_order_id), str(supabase_order_id)))
    except Exception as e:
        logger.warning(f"Failed to update ledger status for {supabase_order_id}: {e}")

# -------------------------------------------------------------
# 2. LOCAL SQLITE ORDERS SYNCHRONIZATION
# -------------------------------------------------------------

def sync_order_to_local_db(order: dict):
    """
    Synchronizes an external Supabase order into the local SQLite store.db
    so it appears in admin order listings, reports, and financial KPI metrics.
    """
    order_num = str(order.get("order_number") or order.get("id") or "UNKNOWN")
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, status FROM orders WHERE order_number = ? LIMIT 1;", (order_num,))
            existing = cursor.fetchone()

            now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            c_name = str(order.get("customer_name") or "Website Customer")
            c_phone = str(order.get("customer_contact") or order.get("customer_phone") or "")
            c_email = str(order.get("customer_email") or "")
            c_tg_id = int(order.get("telegram_user_id") or 0)
            service = str(order.get("service_name") or order.get("product_name") or "Website Service")
            plan = str(order.get("plan_duration") or order.get("plan_name") or "Standard")
            price = float(order.get("price") or order.get("amount") or 0.0)
            currency = str(order.get("currency") or "ETB")
            payment = str(order.get("payment_method") or "Telebirr")
            sb_status = str(order.get("status") or "pending").lower()
            receipt_url = str(order.get("receipt_url") or "")

            status_map = {
                "pending": "PAYMENT_SUBMITTED",
                "confirmed": "PAYMENT_APPROVED",
                "completed": "COMPLETED",
                "cancelled": "CANCELLED"
            }
            local_status = status_map.get(sb_status, "PAYMENT_SUBMITTED")
            notes = f"Website order (Supabase). Email: {c_email or 'N/A'}"

            if not existing:
                cursor.execute("""
                    INSERT INTO orders (
                        order_number, telegram_id, product_name, plan_duration,
                        amount, currency, customer_name, customer_phone,
                        customer_notes, payment_method, status, admin_notes,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    order_num, c_tg_id, service, plan,
                    price, currency, c_name, c_phone,
                    notes, payment, local_status, "Source: Website (Supabase)",
                    order.get("created_at") or now_iso, now_iso
                ))
                local_order_id = cursor.lastrowid

                # If receipt exists, record in payments
                if receipt_url:
                    cursor.execute("""
                        INSERT INTO payments (
                            order_id, payment_method, amount, currency, receipt_file,
                            media_type, review_status, submitted_at
                        ) VALUES (?, ?, ?, ?, ?, 'url', 'PENDING', ?);
                    """, (local_order_id, payment, price, currency, receipt_url, now_iso))
                logger.info(f"Synchronized Supabase order #{order_num} to local store.db (ID: {local_order_id}).")
            else:
                local_order_id = existing["id"]
                cursor.execute("""
                    UPDATE orders
                    SET status = ?, updated_at = ?
                    WHERE id = ?;
                """, (local_status, now_iso, local_order_id))
    except Exception as e:
        logger.warning(f"Could not synchronize order #{order_num} to local store.db: {e}")

def update_local_order_status(order_ref: str, new_sb_status: str, admin_notes: str = None):
    """Synchronizes status change back into local store.db orders table."""
    try:
        status_map = {
            "pending": "PAYMENT_SUBMITTED",
            "confirmed": "PAYMENT_APPROVED",
            "completed": "COMPLETED",
            "cancelled": "CANCELLED"
        }
        loc_status = status_map.get(new_sb_status, "PAYMENT_SUBMITTED")
        now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, order_number FROM orders WHERE order_number = ? OR id = ? LIMIT 1;", (order_ref, order_ref))
            row = cursor.fetchone()
            if row:
                oid = row["id"]
                onum = row["order_number"]
                cursor.execute("""
                    UPDATE orders
                    SET status = ?, admin_notes = COALESCE(?, admin_notes), updated_at = ?
                    WHERE id = ?;
                """, (loc_status, admin_notes, now_iso, oid))
                cursor.execute("""
                    INSERT INTO order_status_history (
                        order_id, order_number, new_status, changed_by, notes, created_at
                    ) VALUES (?, ?, ?, 'Telegram Admin Bot', ?, ?);
                """, (oid, onum, loc_status, admin_notes or f"Updated to {loc_status}", now_iso))
                logger.info(f"Local store.db order #{onum} updated to {loc_status}.")
    except Exception as e:
        logger.debug(f"Local order status sync notice: {e}")

# -------------------------------------------------------------
# 3. SUPABASE REST CLIENT
# -------------------------------------------------------------

_BACKGROUND_TASKS = set()

def _track_task(coro, name: str = "task"):
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)
    def _done_cb(t):
        _BACKGROUND_TASKS.discard(t)
        if not t.cancelled() and t.exception():
            err = t.exception()
            logger.error(f"❌ SUPABASE BRIDGE [{name}] CRASHED: {err}", exc_info=err)
            print(f"❌ SUPABASE BRIDGE [{name}] CRASHED: {err}", flush=True)
    task.add_done_callback(_done_cb)
    return task

def get_supabase_url() -> str:
    url = getattr(config, "SUPABASE_URL", "") or os.getenv("SUPABASE_URL", "")
    return url.strip().rstrip("/")

def get_supabase_service_role_key() -> str:
    key = getattr(config, "SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    return key.strip()

def is_supabase_configured() -> bool:
    """Returns True if valid Supabase URL and service role key are present."""
    url = get_supabase_url()
    key = get_supabase_service_role_key()
    return bool(url and key and url.startswith("https://") and len(key) > 20)

def _get_supabase_headers():
    key = get_supabase_service_role_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def _make_order_or_filter(order_id: str) -> str:
    clean_id = str(order_id).strip()
    is_uuid = bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', clean_id, re.IGNORECASE))
    if is_uuid:
        return f"(id.eq.{clean_id},external_id.eq.{clean_id},order_number.eq.{clean_id})"
    return f"(external_id.eq.{clean_id},order_number.eq.{clean_id})"

async def fetch_recent_website_orders(limit: int = 25) -> list[dict]:
    """Fetches recent website orders from Supabase public.orders via PostgREST."""
    if not is_supabase_configured():
        return []
    url = f"{config.SUPABASE_URL}/rest/v1/orders"
    params = {
        "source": "eq.website",
        "order": "created_at.desc",
        "limit": str(limit)
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=_get_supabase_headers(), params=params)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(f"Supabase REST fetch returned HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Error fetching website orders from Supabase REST: {e}")
    return []

async def fetch_supabase_order_by_id(order_id: str) -> dict | None:
    """Fetches a single order from Supabase by its primary id or external_id."""
    if not is_supabase_configured():
        return None
    url = f"{config.SUPABASE_URL}/rest/v1/orders"
    params = {
        "or": _make_order_or_filter(order_id),
        "limit": "1"
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=_get_supabase_headers(), params=params)
            if resp.status_code == 200:
                rows = resp.json()
                return rows[0] if rows else None
    except Exception as e:
        logger.warning(f"Error fetching Supabase order #{order_id}: {e}")
    return None

async def update_supabase_order_status(order_id: str, new_status: str, admin_notes: str = None) -> bool:
    """
    Updates order status in Supabase canonical table (confirmed, completed, cancelled)
    and synchronizes with local store.db.
    """
    if not is_supabase_configured():
        logger.warning("Cannot update Supabase order: Supabase credentials not configured.")
        return False

    url = f"{config.SUPABASE_URL}/rest/v1/orders"
    params = {"or": _make_order_or_filter(order_id)}
    payload = {
        "status": new_status,
        "updated_at": datetime.datetime.utcnow().isoformat()
    }
    if admin_notes:
        payload["admin_notes"] = admin_notes

    success = False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(url, headers=_get_supabase_headers(), params=params, json=payload)
            if resp.status_code in (200, 204):
                success = True
                update_processed_order_status(order_id, new_status)
                logger.info(f"Supabase order #{order_id} status updated to '{new_status}'")
            else:
                logger.warning(f"Supabase PATCH returned HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Error updating Supabase order status for #{order_id}: {e}")

    # Synchronize locally into store.db
    update_local_order_status(order_id, new_status, admin_notes)

    return success

# -------------------------------------------------------------
# 4. TELEGRAM ADMIN NOTIFICATION DISPATCHER
# -------------------------------------------------------------

def build_website_order_keyboard(order_id: str) -> dict:
    """Returns inline action keyboard for the Telegram Admin Bot."""
    oid = str(order_id).strip()
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Confirm", "callback_data": f"sb_confirm_{oid}"},
                {"text": "📦 Complete", "callback_data": f"sb_complete_{oid}"}
            ],
            [
                {"text": "❌ Cancel", "callback_data": f"sb_cancel_{oid}"},
                {"text": "📋 Details", "callback_data": f"sb_details_{oid}"}
            ]
        ]
    }

async def send_telegram_admin_notification(order: dict) -> bool:
    """
    Sends the exact required website order notification to all configured Telegram Admin Chat IDs.
    Uses dedicated TELEGRAM_ADMIN_BOT_TOKEN and supports receipt images.
    """
    admin_token = getattr(config, "TELEGRAM_ADMIN_BOT_TOKEN", "") or getattr(config, "ADMIN_BOT_TOKEN", "")
    admin_ids = getattr(config, "TELEGRAM_ADMIN_CHAT_IDS", []) or getattr(config, "ADMIN_TELEGRAM_IDS", [6666443909])

    if not admin_token or not admin_ids:
        logger.warning("Cannot send admin notification: TELEGRAM_ADMIN_BOT_TOKEN or TELEGRAM_ADMIN_CHAT_IDS missing.")
        return False

    order_num = html.escape(str(order.get("order_number") or order.get("id") or "UNKNOWN"))
    c_name = html.escape(str(order.get("customer_name") or "Customer"))
    phone = html.escape(str(order.get("customer_contact") or order.get("customer_phone") or "N/A"))
    email = html.escape(str(order.get("customer_email") or "N/A"))
    service = html.escape(str(order.get("service_name") or order.get("product_name") or "Digital Service"))
    plan = html.escape(str(order.get("plan_duration") or order.get("plan_name") or "Standard"))
    price = order.get("price") or order.get("amount") or 0.0
    currency = html.escape(str(order.get("currency") or "ETB"))
    payment = html.escape(str(order.get("payment_method") or "Telebirr"))
    status_raw = str(order.get("status") or "Pending").capitalize()
    status = html.escape(status_raw)
    receipt_url = order.get("receipt_url")
    order_ref = str(order.get("order_number") or order.get("id"))

    try:
        price_fmt = f"{float(price):,.0f}"
    except (ValueError, TypeError):
        price_fmt = str(price)

    text = (
        f"🛒 <b>NEW WEBSITE ORDER</b>\n\n"
        f"<b>Order:</b> <code>#{order_num}</code>\n"
        f"<b>Customer:</b> {c_name}\n"
        f"<b>Phone:</b> {phone}\n"
        f"<b>Email:</b> {email}\n"
        f"<b>Service:</b> {service}\n"
        f"<b>Plan:</b> {plan}\n"
        f"<b>Price:</b> <b>{price_fmt} {currency}</b>\n"
        f"<b>Payment:</b> {payment}\n"
        f"<b>Status:</b> {status}\n"
        f"<b>Source:</b> 🌐 Website"
    )

    reply_markup = build_website_order_keyboard(order_ref)
    api_base = f"https://api.telegram.org/bot{admin_token}"
    dispatched_any = False

    async with httpx.AsyncClient(timeout=10.0) as client:
        for aid in admin_ids:
            try:
                # 1. If photo receipt URL exists, attempt sendPhoto
                if receipt_url and isinstance(receipt_url, str) and (receipt_url.startswith("http://") or receipt_url.startswith("https://")):
                    photo_payload = {
                        "chat_id": aid,
                        "photo": receipt_url,
                        "caption": text,
                        "parse_mode": "HTML",
                        "reply_markup": reply_markup
                    }
                    resp = await client.post(f"{api_base}/sendPhoto", json=photo_payload)
                    res_data = resp.json()
                    if res_data.get("ok"):
                        dispatched_any = True
                        continue

                # 2. Fallback to standard sendMessage
                msg_payload = {
                    "chat_id": aid,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup
                }
                resp = await client.post(f"{api_base}/sendMessage", json=msg_payload)
                res_data = resp.json()
                if res_data.get("ok"):
                    dispatched_any = True
                else:
                    logger.warning(f"Telegram error notifying admin {aid}: {res_data.get('description')}")
            except Exception as e:
                logger.warning(f"Failed to dispatch Telegram message to {aid}: {e}")

    return dispatched_any

# -------------------------------------------------------------
# 5. ATOMIC ORDER PROCESSOR (Idempotent Entrypoint)
# -------------------------------------------------------------

async def process_incoming_order(order: dict, trigger: str = "REALTIME") -> bool:
    """
    Main ingestion function for website orders from Supabase.
    Ensures strict idempotency: if already processed, does NOTHING.
    """
    if not order or not isinstance(order, dict):
        return False

    # Filter source: must be 'website'
    source = str(order.get("source") or "").lower()
    if source != "website":
        return False

    # Primary idempotency key is external_id (fallback to id or order_number)
    ext_id = str(order.get("external_id") or order.get("id") or order.get("order_number") or "").strip()
    if not ext_id:
        return False

    async with _IDEMPOTENCY_LOCK:
        if is_order_processed(ext_id):
            logger.debug(f"Order external_id '{ext_id}' already processed. Skipping duplicate ({trigger}).")
            return False

        order_num = str(order.get("order_number") or ext_id)
        sb_id = str(order.get("id") or ext_id)
        status = str(order.get("status") or "pending")

        # Mark processed immediately in SQLite & memory ledger
        record_order_processed(ext_id, order_num, sb_id, status)

    logger.info(f"⚡ Ingesting NEW website order #{order_num} via {trigger} (external_id={ext_id})")

    # 1. Sync into local SQLite store.db
    sync_order_to_local_db(order)

    # 2. Dispatch immediate Telegram Admin Notification
    sent = await send_telegram_admin_notification(order)
    return sent

# -------------------------------------------------------------
# 6. 90-SECOND RECONCILIATION FALLBACK WORKER
# -------------------------------------------------------------

async def run_reconciliation_once() -> int:
    """Performs a single reconciliation sweep across recent website orders."""
    if not is_supabase_configured():
        return 0
    orders = await fetch_recent_website_orders(limit=25)
    new_count = 0
    for order in orders:
        ext_id = str(order.get("external_id") or order.get("id") or order.get("order_number") or "").strip()
        if ext_id and not is_order_processed(ext_id):
            await process_incoming_order(order, trigger="RECONCILIATION")
            new_count += 1
    if new_count > 0:
        logger.info(f"✅ Reconciliation worker recovered and notified {new_count} new website order(s).")
    return new_count

async def reconciliation_worker():
    """
    Lightweight background loop that queries Supabase every 90 seconds.
    Ensures zero missed website orders even if a WebSocket drops temporarily.
    """
    logger.info("🔁 Supabase 90-second reconciliation fallback worker started.")
    # Run once immediately on start so pending orders are handled without delay
    try:
        await run_reconciliation_once()
    except Exception as e:
        logger.warning(f"Initial reconciliation sweep notice: {e}")

    while True:
        try:
            await asyncio.sleep(90)
            await run_reconciliation_once()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Reconciliation fallback check notice: {e}")
            await asyncio.sleep(10)

# -------------------------------------------------------------
# 7. SUPABASE REALTIME WEBSOCKET LISTENER
# -------------------------------------------------------------

def _extract_project_ref(supabase_url: str) -> str:
    """Extracts project ref from https://xyz.supabase.co -> xyz"""
    try:
        parsed = urlparse(supabase_url.strip())
        host = parsed.netloc or parsed.path
        if ":" in host:
            host = host.split(":")[0]
        return host.split(".")[0]
    except Exception:
        return ""

async def realtime_listener():
    """
    Connects to Supabase Realtime via Phoenix WebSocket protocol.
    Listens for INSERT events on public.orders where source = 'website'.
    Automatically reconnects with exponential backoff on disconnect.
    """
    if websockets is None:
        logger.warning("websockets package not available; running via 90-second reconciliation mode.")
        return

    url = config.SUPABASE_URL
    key = config.SUPABASE_SERVICE_ROLE_KEY
    proj_ref = _extract_project_ref(url)

    if not proj_ref or not key:
        return

    try:
        parsed = urlparse(url.strip())
        if "supabase.co" in (parsed.netloc or ""):
            ws_url = f"wss://{proj_ref}.supabase.co/realtime/v1/websocket?apikey={key}&vsn=1.0.0"
        else:
            scheme = "wss" if parsed.scheme == "https" else "ws"
            ws_url = f"{scheme}://{parsed.netloc}/realtime/v1/websocket?apikey={key}&vsn=1.0.0"
    except Exception:
        ws_url = f"wss://{proj_ref}.supabase.co/realtime/v1/websocket?apikey={key}&vsn=1.0.0"

    reconnect_delay = 3

    while True:
        try:
            logger.info("🔌 Connecting to Supabase Realtime WebSocket...")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                logger.info("🟢 Supabase Realtime WebSocket connection established.")
                reconnect_delay = 3

                # Join orders channel with postgres_changes filter
                join_msg = {
                    "topic": "realtime:orders",
                    "event": "phx_join",
                    "payload": {
                        "config": {
                            "broadcast": {"ack": False, "self": False},
                            "presence": {"key": ""},
                            "postgres_changes": [
                                {
                                    "event": "INSERT",
                                    "schema": "public",
                                    "table": "orders",
                                    "filter": "source=eq.website"
                                }
                            ]
                        }
                    },
                    "ref": "join_orders_1",
                    "join_ref": "join_orders_1"
                }
                await ws.send(json.dumps(join_msg))

                # Background heartbeat task
                async def heartbeat():
                    while True:
                        try:
                            await asyncio.sleep(25)
                            hb_msg = {"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": "hb"}
                            await ws.send(json.dumps(hb_msg))
                        except Exception:
                            break

                hb_task = asyncio.create_task(heartbeat())

                try:
                    async for raw_message in ws:
                        try:
                            data = json.loads(raw_message)
                            event = data.get("event")
                            payload = data.get("payload", {})
                            if event == "phx_reply":
                                status = payload.get("status")
                                if status == "ok":
                                    logger.info("🟢 Supabase Realtime channel subscription confirmed.")
                                elif status == "error":
                                    logger.warning(f"⚠️ Realtime subscription reply notice: {payload.get('response')}")
                            elif event == "postgres_changes":
                                record = payload.get("data", {}).get("record") or payload.get("record", {})
                                if record:
                                    asyncio.create_task(process_incoming_order(record, trigger="REALTIME"))
                            elif event in ("INSERT", "system"):
                                record = payload.get("record") or payload.get("data", {}).get("record")
                                if record:
                                    asyncio.create_task(process_incoming_order(record, trigger="REALTIME"))
                        except Exception as parse_err:
                            logger.debug(f"WebSocket message parsing notice: {parse_err}")
                finally:
                    hb_task.cancel()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Supabase Realtime disconnected ({e}). Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)

# -------------------------------------------------------------
# 8. MAIN BRIDGE ENTRYPOINT (Started by run_bots.py)
# -------------------------------------------------------------

async def start_supabase_bridge():
    """Initializes the idempotency ledger, starts Realtime and reconciliation tasks."""
    logger.info("⚡ SUPABASE BRIDGE: STARTING...")
    print("⚡ SUPABASE BRIDGE: STARTING...", flush=True)

    try:
        init_idempotency_ledger()
    except Exception as e:
        logger.error(f"❌ SUPABASE BRIDGE: Error initializing ledger: {e}", exc_info=True)
        print(f"❌ SUPABASE BRIDGE: Error initializing ledger: {e}", flush=True)

    url = get_supabase_url()
    key = get_supabase_service_role_key()
    has_url = bool(url)
    has_key = bool(key)
    has_bot_token = bool(getattr(config, "TELEGRAM_ADMIN_BOT_TOKEN", "") or os.getenv("TELEGRAM_ADMIN_BOT_TOKEN", "") or getattr(config, "ADMIN_BOT_TOKEN", "") or os.getenv("ADMIN_BOT_TOKEN", ""))
    admin_ids = getattr(config, "TELEGRAM_ADMIN_CHAT_IDS", None) or os.getenv("TELEGRAM_ADMIN_CHAT_IDS", "")
    has_chat_ids = bool(admin_ids)

    logger.info(f"⚡ SUPABASE BRIDGE: ENV CHECK -> SUPABASE_URL: {'SET' if has_url else 'MISSING'} | SUPABASE_SERVICE_ROLE_KEY: {'SET' if has_key else 'MISSING'} | ADMIN_BOT_TOKEN: {'SET' if has_bot_token else 'MISSING'} | ADMIN_CHAT_IDS: {'SET' if has_chat_ids else 'MISSING'}")
    print(f"⚡ SUPABASE BRIDGE: ENV CHECK -> SUPABASE_URL: {'SET' if has_url else 'MISSING'} | SUPABASE_SERVICE_ROLE_KEY: {'SET' if has_key else 'MISSING'} | ADMIN_BOT_TOKEN: {'SET' if has_bot_token else 'MISSING'} | ADMIN_CHAT_IDS: {'SET' if has_chat_ids else 'MISSING'}", flush=True)

    if not is_supabase_configured():
        logger.warning("⚠️ SUPABASE BRIDGE: Standby mode — missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.")
        print("⚠️ SUPABASE BRIDGE: Standby mode — missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.", flush=True)
        return

    logger.info("✅ SUPABASE BRIDGE: CONFIG OK")
    print("✅ SUPABASE BRIDGE: CONFIG OK", flush=True)

    logger.info("🚀 SUPABASE BRIDGE: STARTING RECONCILIATION...")
    print("🚀 SUPABASE BRIDGE: STARTING RECONCILIATION...", flush=True)
    _track_task(reconciliation_worker(), name="reconciliation_worker")

    rt_enabled = getattr(config, "SUPABASE_REALTIME_ENABLED", True)
    if rt_enabled and websockets is not None:
        logger.info("🔌 SUPABASE BRIDGE: STARTING REALTIME...")
        print("🔌 SUPABASE BRIDGE: STARTING REALTIME...", flush=True)
        _track_task(realtime_listener(), name="realtime_listener")
    else:
        logger.info(f"ℹ️ SUPABASE BRIDGE: Realtime disabled or websockets unavailable (enabled={rt_enabled}, websockets={websockets is not None}). Running in reconciliation mode.")
        print(f"ℹ️ SUPABASE BRIDGE: Realtime disabled or websockets unavailable (enabled={rt_enabled}, websockets={websockets is not None}). Running in reconciliation mode.", flush=True)
