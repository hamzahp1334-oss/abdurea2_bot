import sys
import time
import os
import webbrowser
from pathlib import Path

import config
import database
import bot
from app import app

def main():
    print("=" * 60)
    print(" 🚀 ABDUREA PRO STORE — TELEGRAM BOT & ADMIN DASHBOARD")
    print("=" * 60)
    print(f" * Bank Accounts: Telebirr (0992558519) | CBE (1000572238575)")
    print(f" * Recipient Name: FARHAN")
    print(f" * Admin Telegram: @trust994")
    print(f" * Dashboard URL: http://localhost:{config.PORT}")
    print("=" * 60)

    # Initialize Database
    database.init_db()

    # Start Telegram Bot thread
    bot.run_in_background()

    print(f" -> Admin Dashboard starting on http://localhost:{config.PORT} ...")
    
    # Run Flask application
    app.run(host="0.0.0.0", port=config.PORT, debug=False)

if __name__ == "__main__":
    main()
