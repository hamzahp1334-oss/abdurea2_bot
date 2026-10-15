# -*- coding: utf-8 -*-
"""
Database layer for ABDUREA STORE.
Manages SQLite connection pooling, WAL mode for high concurrency,
schema creation, and automated migrations.
"""
import sqlite3
import contextlib
import logging
from werkzeug.security import generate_password_hash
from config import (
    DB_PATH,
    STORE_NAME,
    DEFAULT_TELEBIRR_ACCOUNT,
    DEFAULT_TELEBIRR_NAME,
    DEFAULT_CBE_BIRR_ACCOUNT,
    DEFAULT_CBE_BIRR_NAME,
    DEFAULT_SUPPORT_USERNAME,
    ADMIN_DEFAULT_USER,
    ADMIN_DEFAULT_PASS,
    ADMIN_DEFAULT_EMAIL
)

logger = logging.getLogger("database")

def get_db_connection():
    """Returns a thread-safe connection to SQLite with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn

@contextlib.contextmanager
def db_session():
    """Context manager for safe database transactions with auto-commit and rollback."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """Initializes all database tables and executes safe schema migrations."""
    with db_session() as conn:
        cursor = conn.cursor()

        # 1. Users Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                telegram_username TEXT,
                first_name TEXT,
                last_name TEXT,
                full_name TEXT,
                phone TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);")

        # 2. Categories Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT,
                icon TEXT,
                sort_order INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1
            );
        """)

        # 3. Products Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                category_id TEXT REFERENCES categories(id) ON DELETE SET NULL,
                name TEXT NOT NULL,
                description TEXT,
                image_url TEXT,
                features TEXT,
                requirements TEXT,
                active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);")

        # 4. Product Plans Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_plans (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                name TEXT,
                duration TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT DEFAULT 'ETB',
                availability TEXT DEFAULT 'available',
                active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_plans_product_id ON product_plans(product_id);")

        # 5. Orders Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT UNIQUE NOT NULL,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                telegram_id INTEGER NOT NULL,
                product_id TEXT REFERENCES products(id) ON DELETE SET NULL,
                plan_id TEXT REFERENCES product_plans(id) ON DELETE SET NULL,
                product_name TEXT NOT NULL,
                plan_name TEXT,
                plan_duration TEXT,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'ETB',
                customer_name TEXT,
                customer_username TEXT,
                customer_phone TEXT,
                customer_delivery_info TEXT,
                customer_notes TEXT,
                payment_method TEXT,
                status TEXT NOT NULL DEFAULT 'NEW',
                admin_notes TEXT,
                reviewed_by TEXT,
                rejection_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_order_number ON orders(order_number);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_telegram_id ON orders(telegram_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);")

        # 6. Order Status History Table (Audit Trail)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                order_number TEXT,
                old_status TEXT,
                new_status TEXT NOT NULL,
                changed_by TEXT DEFAULT 'SYSTEM',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_status_history_order ON order_status_history(order_id);")

        # 7. Payments Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                payment_method TEXT NOT NULL,
                payment_sender_name TEXT,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'ETB',
                receipt_file TEXT,
                telegram_file_id TEXT,
                file_unique_id TEXT,
                media_type TEXT DEFAULT 'photo',
                message_id INTEGER,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TIMESTAMP,
                review_status TEXT DEFAULT 'PENDING',
                reviewed_by TEXT,
                admin_note TEXT
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id);")

        # 8. Deliveries Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                delivery_type TEXT DEFAULT 'TEXT',
                file_path TEXT,
                delivered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_order_id ON deliveries(order_id);")

        # 9. Customer Conversation Messages Table (Timeline)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                telegram_id INTEGER NOT NULL,
                direction TEXT NOT NULL, -- 'INCOMING' or 'OUTGOING'
                message_type TEXT NOT NULL DEFAULT 'text', -- 'text', 'photo', 'document', 'video'
                message_text TEXT,
                media_reference TEXT,
                related_order_id INTEGER,
                delivery_status TEXT DEFAULT 'DELIVERED', -- 'SENT', 'FAILED', 'BLOCKED', 'DELIVERED'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversation_messages(telegram_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_order ON conversation_messages(related_order_id);")

        # 10. Telegram Updates Idempotency Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telegram_updates (
                update_id INTEGER PRIMARY KEY,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 11. User Persistent Conversation States Table (Restart resilience)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_conversation_states (
                telegram_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                state_data TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 11b. Admin Persistent Conversation States Table (Restart resilience)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_conversation_states (
                admin_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                state_data TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 12. Admin Users Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'SUPER ADMIN',
                full_name TEXT,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login_at TIMESTAMP
            );
        """)

        # 13. Admin Activity Logs Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,
                admin_username TEXT NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                details TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_admin ON admin_activity_logs(admin_id);")

        # 14. Admin Notifications Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                link TEXT,
                read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notif_read ON admin_notifications(read);")

        # 15. Broadcasts Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL,
                target_filter TEXT,
                message_text TEXT NOT NULL,
                media_path TEXT,
                total_recipients INTEGER DEFAULT 0,
                sent_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'COMPLETED',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 16. Store Settings Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS store_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # -------------------------------------------------------------
        # AUTOMATED SCHEMA MIGRATIONS (Safe ALTER statements)
        # -------------------------------------------------------------
        migrations = [
            ("users", "full_name", "TEXT"),
            ("users", "language", "TEXT DEFAULT 'en'"),
            ("orders", "customer_notes", "TEXT"),
            ("orders", "payment_method", "TEXT"),
            ("orders", "reviewed_by", "TEXT"),
            ("orders", "rejection_reason", "TEXT"),
            ("payments", "telegram_file_id", "TEXT"),
            ("payments", "file_unique_id", "TEXT"),
            ("payments", "media_type", "TEXT DEFAULT 'photo'"),
            ("payments", "message_id", "INTEGER"),
            ("payments", "reviewed_by", "TEXT"),
            ("users", "is_premium", "INTEGER DEFAULT 0"),
            ("users", "channel_verified", "INTEGER DEFAULT 0"),
        ]
        for table, col, col_type in migrations:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type};")
            except sqlite3.OperationalError:
                pass  # Column already exists

        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_file_unique_id ON payments(file_unique_id);")
        except sqlite3.OperationalError:
            pass

        # Seed default store settings
        default_settings = [
            ("store_name", STORE_NAME),
            ("telebirr_account", DEFAULT_TELEBIRR_ACCOUNT),
            ("telebirr_name", DEFAULT_TELEBIRR_NAME),
            ("cbe_birr_account", DEFAULT_CBE_BIRR_ACCOUNT),
            ("cbe_birr_name", DEFAULT_CBE_BIRR_NAME),
            ("support_username", DEFAULT_SUPPORT_USERNAME),
            ("about_text", "Welcome to ABDUREA STORE! We provide premium digital accounts, subscriptions, and AI tools with instant Ethiopian payment methods (Telebirr & CBE Birr) and fast digital delivery.")
        ]
        for key, val in default_settings:
            cursor.execute("""
                INSERT OR IGNORE INTO store_settings (key, value)
                VALUES (?, ?);
            """, (key, val))

        # Seed default admin user if not exists
        cursor.execute("SELECT id FROM admins WHERE username = ?", (ADMIN_DEFAULT_USER,))
        if not cursor.fetchone():
            hashed = generate_password_hash(ADMIN_DEFAULT_PASS)
            cursor.execute("""
                INSERT INTO admins (username, email, password_hash, role, full_name, active)
                VALUES (?, ?, ?, 'SUPER ADMIN', 'Store Administrator', 1);
            """, (ADMIN_DEFAULT_USER, ADMIN_DEFAULT_EMAIL, hashed))

if __name__ == "__main__":
    init_db()
    print("Database schema successfully initialized and migrated at:", DB_PATH)


init_admin_db = init_db
