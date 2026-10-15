import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "store.db"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_FOLDER = STATIC_DIR / "uploads"
RECEIPTS_FOLDER = UPLOAD_FOLDER / "receipts"
PRODUCTS_FOLDER = UPLOAD_FOLDER / "products"

# Ensure upload folders exist
RECEIPTS_FOLDER.mkdir(parents=True, exist_ok=True)
PRODUCTS_FOLDER.mkdir(parents=True, exist_ok=True)

# Default Payment Details (Ethiopia)
DEFAULT_SETTINGS = {
    "store_name": "Abdurea Pro Store",
    "telebirr_phone": "0992558519",
    "telebirr_name": "FARHAN",
    "cbe_account": "1000572238575",
    "cbe_name": "FARHAN",
    "admin_telegram": "@trust994",
    "admin_telegram_id": os.getenv("ADMIN_TELEGRAM_ID", ""),
    "bot_token": os.getenv("BOT_TOKEN", ""),
    "currency_symbol": "ETB",
    "support_contact": "@trust994",
    "support_phone": "0992558519"
}

SECRET_KEY = os.getenv("SECRET_KEY", "abdurea-super-secret-key-2026")
PORT = int(os.getenv("PORT", 5000))
HOST = os.getenv("HOST", "0.0.0.0")
