# -*- coding: utf-8 -*-
"""
Main Entrypoint Runner for ABDUREA STORE.
Supports:
  python run.py             -> Runs both Telegram Bots concurrently
  python run.py --admin     -> Runs only the Dedicated Admin Bot
  python run.py --customer  -> Runs only the Customer Commerce Bot
  python run.py --all       -> Runs both Telegram Bots concurrently
"""
import sys
import asyncio
import logging
import argparse

import config
import models
from database import init_db

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("RUNNER")

async def run_single_bot(bot_type="admin"):
    init_db()
    if bot_type == "admin":
        from admin_bot import build_admin_bot_application
        app = build_admin_bot_application()
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("👑 Admin Dashboard Bot polling active.")
    else:
        from telegram_bot import build_telegram_app
        app = build_telegram_app()
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("🟢 Customer Commerce Bot polling active.")

    while True:
        await asyncio.sleep(3600)

def main():
    parser = argparse.ArgumentParser(description="ABDUREA STORE Runner")
    parser.add_argument("--admin", action="store_true", help="Run only the Admin Bot")
    parser.add_argument("--customer", action="store_true", help="Run only the Customer Bot")
    parser.add_argument("--all", action="store_true", help="Run both bots concurrently")
    args = parser.parse_args()

    if args.admin:
        asyncio.run(run_single_bot("admin"))
    elif args.customer:
        asyncio.run(run_single_bot("customer"))
    else:
        from run_bots import run_bots
        asyncio.run(run_bots())

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Stopped.")
        sys.exit(0)
