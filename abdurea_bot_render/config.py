# -*- coding: utf-8 -*-
"""
Configuration module for ABDUREA STORE Telegram Commerce Bot and Backend.
Loads settings from .env file or environment variables with production defaults.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory for the application
BASE_DIR = Path(__file__).resolve().parent
BOT_DIR = BASE_DIR.parent / 'bot'
ADMIN_DIR = BASE_DIR

# Check for .env in current directory, parent directory, or bot directory
env_paths = [
    BASE_DIR / ".env",
    BASE_DIR.parent / "bot" / ".env",
    BASE_DIR.parent / "admin" / ".env",
    BASE_DIR.parent / ".env"
]
for ep in env_paths:
    if ep.exists():
        load_dotenv(ep)
        break
else:
    load_dotenv()

# Telegram Bot Credentials
MAIN_BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", "8848082794:AAEzNGEQYHD9dpTqe9kbUFBJhJ9n5OK4NIA")).strip()
TELEGRAM_BOT_TOKEN = MAIN_BOT_TOKEN

# Dedicated Admin Bot Credentials & Chat IDs
TELEGRAM_ADMIN_BOT_TOKEN = os.getenv("TELEGRAM_ADMIN_BOT_TOKEN", os.getenv("ADMIN_BOT_TOKEN", "8920397884:AAHOFpkA-IhcBcCVE5MibMAdj8a6WMfm8gA")).strip()
ADMIN_BOT_TOKEN = TELEGRAM_ADMIN_BOT_TOKEN

ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "6666443909"))
_env_admin_ids = os.getenv("TELEGRAM_ADMIN_CHAT_IDS", os.getenv("ADMIN_TELEGRAM_IDS", "6666443909,2145055956"))
_clean_admin_ids = _env_admin_ids.replace("[", "").replace("]", "").replace('"', '').replace("'", "").replace(";", ",")
ADMIN_TELEGRAM_IDS = []
for _part in _clean_admin_ids.split(","):
    _clean_part = _part.strip()
    if _clean_part.isdigit() or (_clean_part.startswith("-") and _clean_part[1:].isdigit()):
        ADMIN_TELEGRAM_IDS.append(int(_clean_part))
if 2145055956 not in ADMIN_TELEGRAM_IDS:
    ADMIN_TELEGRAM_IDS.append(2145055956)
if ADMIN_TELEGRAM_ID not in ADMIN_TELEGRAM_IDS:
    ADMIN_TELEGRAM_IDS.append(ADMIN_TELEGRAM_ID)
TELEGRAM_ADMIN_CHAT_IDS = ADMIN_TELEGRAM_IDS
ADMIN_TELEGRAM_USERNAME = os.getenv("ADMIN_TELEGRAM_USERNAME", "@trust994").strip()
MINIAPP_URL = os.getenv("MINIAPP_URL", "").strip()

# Supabase Canonical Unified Orders Configuration (Server-Side Only - Never Exposed to Frontend)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_REALTIME_ENABLED = os.getenv("SUPABASE_REALTIME_ENABLED", "True").lower() in ("true", "1", "yes")

# Base44 Integration Credentials
BASE44_APP_ID = os.getenv("BASE44_APP_ID", "abdurea-store").strip()
BASE44_API_BASE_URL = os.getenv("BASE44_API_BASE_URL", "https://abdureastore.base44.app").strip().rstrip("/")
BASE44_API_KEY = os.getenv("BASE44_API_KEY", os.getenv("BASE44_INTERNAL_API_KEY", "")).strip()
BASE44_INTERNAL_API_KEY = os.getenv("BASE44_INTERNAL_API_KEY", os.getenv("BASE44_API_KEY", "")).strip()
INTERNAL_WEBHOOK_SECRET = os.getenv("INTERNAL_WEBHOOK_SECRET", "abdurea-webhook-secret-2026").strip()

# CORS & Allowed Origins for Base44 frontend
BASE44_ALLOWED_ORIGINS = os.getenv(
    "BASE44_ALLOWED_ORIGINS",
    "https://abdureastore.base44.app,https://*.base44.app,https://base44.app,http://localhost:3000,http://localhost:5173,http://localhost:5000"
).strip()

# Flask & Session Security
SECRET_KEY = os.getenv("SECRET_KEY", "abdurea-store-secret-production-key-2026-v2")
ADMIN_SESSION_COOKIE = "abdurea_admin_session"
ADMIN_DEFAULT_USER = os.getenv("ADMIN_DEFAULT_USER", "admin")
ADMIN_DEFAULT_PASS = os.getenv("ADMIN_DEFAULT_PASS", "admin123")
ADMIN_DEFAULT_EMAIL = os.getenv("ADMIN_DEFAULT_EMAIL", "admin@abdureastore.com")

# Database Path
_default_db = BASE_DIR / "store.db"
if not _default_db.exists() and (BASE_DIR.parent / "bot" / "store.db").exists():
    _default_db = BASE_DIR.parent / "bot" / "store.db"
DB_PATH = Path(os.getenv("DB_PATH", str(_default_db))).resolve()

# Uploads & Storage Directories
_default_uploads = BASE_DIR / "uploads"
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(_default_uploads))).resolve()
RECEIPTS_DIR = UPLOADS_DIR / "receipts"
DELIVERIES_DIR = UPLOADS_DIR / "deliveries"
PRODUCT_IMAGES_DIR = BASE_DIR / "static" / "uploads" / "products"

# Ensure all upload directories exist
RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
DELIVERIES_DIR.mkdir(parents=True, exist_ok=True)
PRODUCT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Server Host & Port
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 5000))
DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")

# Official Payment Accounts (Source of Truth)
DEFAULT_TELEBIRR_ACCOUNT = os.getenv("TELEBIRR_ACCOUNT", "0992558519")
DEFAULT_TELEBIRR_NAME = os.getenv("TELEBIRR_NAME", "𝙁𝘼𝙍𝙃𝘼𝙉")
DEFAULT_CBE_BIRR_ACCOUNT = os.getenv("CBE_BIRR_ACCOUNT", "1000572238575")
DEFAULT_CBE_BIRR_NAME = os.getenv("CBE_BIRR_NAME", "𝙁𝘼𝙍𝙃𝘼𝙉")

# Store Branding & Support Info
STORE_NAME = "ABDUREA STORE"
STORE_WEBSITE = "https://abdureastore.base44.app/"
DEFAULT_SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@trust994")
OFFICIAL_CHANNEL_USERNAME = os.getenv("OFFICIAL_CHANNEL_USERNAME", "@abdurea1")
OFFICIAL_CHANNEL_URL = os.getenv("OFFICIAL_CHANNEL_URL", "https://t.me/abdurea1")
OFFICIAL_CHANNEL_ID = os.getenv("OFFICIAL_CHANNEL_ID", "-1002546804176")
REQUIRE_CHANNEL_MEMBERSHIP = os.getenv("REQUIRE_CHANNEL_MEMBERSHIP", "False").lower() in ("true", "1", "yes")

