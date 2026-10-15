# -*- coding: utf-8 -*-
"""
Production Entrypoint for ABDUREA STORE Dual-Bot System.
Runs both Customer Commerce Bot and Dedicated Admin Bot concurrently.
Compatible with all hosting environments (Docker, VPS, Render, Railway, Pella).
"""
import os
import sys
import time
import logging
import threading

import config
import models
from database import init_db

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("MAIN")

def run_customer_bot():
    from telegram_bot import build_telegram_app
    bot_logger = logging.getLogger("CUSTOMER_BOT")
    bot_logger.info("Initializing Customer Commerce Bot...")
    while True:
        try:
            app = build_telegram_app()
            if app:
                bot_logger.info("🟢 Customer Commerce Bot polling active.")
                app.run_polling(drop_pending_updates=True)
            else:
                bot_logger.warning("MAIN_BOT_TOKEN not configured.")
                break
        except Exception as e:
            err = str(e).lower()
            if "rejected" in err or "unauthorized" in err:
                bot_logger.critical("Customer Bot token unauthorized (401).")
                break
            bot_logger.warning(f"Customer bot connection notice: {e}. Reconnecting in 5s...")
            time.sleep(5)

def run_admin_bot():
    from admin_bot import build_admin_bot_application
    admin_logger = logging.getLogger("ADMIN_BOT")
    admin_logger.info("Initializing Dedicated Admin Bot...")
    while True:
        try:
            app = build_admin_bot_application()
            if app:
                admin_logger.info("👑 Admin Dashboard Bot polling active.")
                app.run_polling(drop_pending_updates=True)
            else:
                admin_logger.warning("ADMIN_BOT_TOKEN not configured.")
                break
        except Exception as e:
            err = str(e).lower()
            if "rejected" in err or "unauthorized" in err:
                admin_logger.critical("Admin Bot token unauthorized (401).")
                break
            admin_logger.warning(f"Admin bot connection notice: {e}. Reconnecting in 5s...")
            time.sleep(5)

def main():
    try:
        from run_bots import start_health_server
        start_health_server()
    except Exception as e:
        logger.warning(f"Health server startup notice: {e}")

    logger.info("Initializing database...")
    init_db()

    settings = models.get_store_settings()
    kpis = models.get_dashboard_kpis()
    logger.info(f"Store: {config.STORE_NAME}")
    logger.info(f"Admin Telegram ID: {config.ADMIN_TELEGRAM_ID} ({config.ADMIN_TELEGRAM_USERNAME})")
    logger.info(f"Telebirr: {settings.get('telebirr_account', config.DEFAULT_TELEBIRR_ACCOUNT)} ({settings.get('telebirr_name', config.DEFAULT_TELEBIRR_NAME)})")
    logger.info(f"CBE: {settings.get('cbe_birr_account', config.DEFAULT_CBE_BIRR_ACCOUNT)} ({settings.get('cbe_birr_name', config.DEFAULT_CBE_BIRR_NAME)})")

    t_cust = threading.Thread(target=run_customer_bot, name="CustomerBotThread", daemon=True)
    t_adm = threading.Thread(target=run_admin_bot, name="AdminBotThread", daemon=True)
    t_cust.start()
    t_adm.start()

    logger.info("=" * 60)
    logger.info("  CUSTOMER BOT AND ADMIN BOT BOTH ACTIVE")
    logger.info("=" * 60)

    try:
        while True:
            time.sleep(1)
            if not t_cust.is_alive():
                t_cust = threading.Thread(target=run_customer_bot, name="CustomerBotThread", daemon=True)
                t_cust.start()
            if not t_adm.is_alive():
                t_adm = threading.Thread(target=run_admin_bot, name="AdminBotThread", daemon=True)
                t_adm.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)

if __name__ == "__main__":
    main()
