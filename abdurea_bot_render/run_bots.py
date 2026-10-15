# -*- coding: utf-8 -*-
"""
Dual Telegram Bot Runner for ABDUREA STORE.
Runs concurrently in a unified async event loop:
1. Main Customer Commerce Bot (Token: MAIN_BOT_TOKEN)
2. Dedicated Admin Dashboard Bot (Token: ADMIN_BOT_TOKEN)

Features:
- Non-blocking error isolation: if one bot token is invalid or disconnected, the other keeps running.
- Real-time cross-bot communication: customer receipt uploads immediately alert the Admin Bot.
- Shared SQLite database (store.db).
"""
import os
import sys
import time
import asyncio
import logging
from pathlib import Path

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import config
import models
from database import init_db

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("DUAL_RUNNER")

class HealthCheckHandler(BaseHTTPRequestHandler):
    def _send_health_response(self, is_head=False):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "*")
        
        path_lower = self.path.lower()
        if "json" in self.headers.get("Accept", "") or path_lower in ("/health", "/healthz", "/api/health", "/status", "/ping") or path_lower.startswith("/api/"):
            body = b'{"status":"ok","healthy":true,"app":"ABDUREA STORE","bots_active":true}'
            content_type = "application/json; charset=utf-8"
        else:
            body = b"<!DOCTYPE html><html><head><title>ABDUREA STORE</title></head><body><h1>ABDUREA STORE Telegram Bots Active</h1><p>Status: Healthy &amp; Polling 24/7</p></body></html>"
            content_type = "text/html; charset=utf-8"
            
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not is_head:
            try:
                self.wfile.write(body)
            except Exception:
                pass

    def do_GET(self):
        self._send_health_response(is_head=False)

    def do_HEAD(self):
        self._send_health_response(is_head=True)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            try:
                self.rfile.read(content_length)
            except Exception:
                pass
        self._send_health_response(is_head=False)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PUT(self):
        self.do_POST()

    def do_PATCH(self):
        self.do_POST()

    def do_DELETE(self):
        self.do_POST()

    def send_error(self, code, message=None, explain=None):
        if code == 501:
            self._send_health_response(is_head=False)
            return
        super().send_error(code, message, explain)

    def log_message(self, format, *args):
        pass

def start_health_server():
    port_str = os.getenv("PORT", "10000")
    try:
        port = int(port_str)
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        logger.info(f"🌐 Web health server active on port {port} (Render compatible)")
    except Exception as e:
        logger.warning(f"Could not bind web port {port_str}: {e}")

async def run_bots():
    logger.info("=" * 60)
    logger.info(f"  {config.STORE_NAME} — MULTI-BOT SYSTEM STARTUP")
    logger.info("=" * 60)

    # 0. Start Web Health Server for Render
    start_health_server()

    # 1. Initialize DB
    init_db()

    # 2. Check settings & accounts
    settings = models.get_store_settings()
    kpis = models.get_dashboard_kpis()
    logger.info(f"Store: {config.STORE_NAME}")
    logger.info(f"Admin Telegram ID: {config.ADMIN_TELEGRAM_ID} ({config.ADMIN_TELEGRAM_USERNAME})")
    logger.info(f"Telebirr: {settings.get('telebirr_account', config.DEFAULT_TELEBIRR_ACCOUNT)} ({settings.get('telebirr_name', config.DEFAULT_TELEBIRR_NAME)})")
    logger.info(f"CBE: {settings.get('cbe_birr_account', config.DEFAULT_CBE_BIRR_ACCOUNT)} ({settings.get('cbe_birr_name', config.DEFAULT_CBE_BIRR_NAME)})")
    logger.info(f"Active Products: {kpis.get('active_products', 0)} | Customers: {kpis.get('total_customers', 0)}")
    logger.info(f"Pending Reviews: {kpis.get('payments_under_review', 0)}")

    apps_running = []

    # 3. Start Dedicated Admin Bot
    from admin_bot import build_admin_bot_application
    admin_app = None
    try:
        admin_app = build_admin_bot_application()
        await admin_app.initialize()
        await admin_app.start()
        await admin_app.updater.start_polling(drop_pending_updates=True)
        apps_running.append(admin_app)
        logger.info("👑 DEDICATED ADMIN BOT POLLING ACTIVE (@abdurea1_bot)")
    except Exception as e:
        logger.critical(f"Admin Bot startup error: {e}")

    # 4. Start Customer Commerce Bot
    from telegram_bot import build_telegram_app
    customer_app = None
    try:
        customer_app = build_telegram_app()
        if customer_app:
            await customer_app.initialize()
            await customer_app.start()
            await customer_app.updater.start_polling(drop_pending_updates=True)
            apps_running.append(customer_app)
            logger.info("🟢 CUSTOMER COMMERCE BOT POLLING ACTIVE")
    except Exception as e:
        logger.warning(f"Customer bot notice: {e}")
        logger.warning("Main bot token returned an error. Admin bot will continue running.")
        logger.warning("Update MAIN_BOT_TOKEN in .env when you get a fresh token from @BotFather.")

    if not apps_running:
        logger.error("No bots were able to start. Please check tokens in .env.")
        return

    # 5. Start Supabase Canonical Orders Bridge (Realtime + 90s Fallback)
    try:
        import supabase_bridge
        await supabase_bridge.start_supabase_bridge()
        logger.info("⚡ Supabase Canonical Unified Orders Bridge initialized.")
        print("⚡ Supabase Canonical Unified Orders Bridge initialized.", flush=True)
    except Exception as e:
        logger.error(f"❌ Could not initialize Supabase bridge: {e}", exc_info=True)
        print(f"❌ Could not initialize Supabase bridge: {e}", flush=True)

    logger.info("=" * 60)
    logger.info(f"  {len(apps_running)} BOT(S) RUNNING SUCCESSFULLY")
    logger.info("  Press Ctrl+C to terminate.")
    logger.info("=" * 60)

    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Stopping bots...")
        for app in apps_running:
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception:
                pass
        logger.info("Shutdown complete.")

def main():
    try:
        asyncio.run(run_bots())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user.")
        sys.exit(0)

if __name__ == "__main__":
    main()
