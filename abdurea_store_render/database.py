import sqlite3
import datetime
import random
from config import DB_PATH, DEFAULT_SETTINGS

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    # Products / Services table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_code TEXT UNIQUE,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER DEFAULT 999,
        duration TEXT DEFAULT '1 Month',
        short_description TEXT DEFAULT '',
        description TEXT DEFAULT '',
        image_url TEXT DEFAULT '',
        status TEXT DEFAULT 'active', -- active, inactive
        is_featured INTEGER DEFAULT 0,
        is_popular INTEGER DEFAULT 0,
        is_recommended INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        sort_order INTEGER DEFAULT 0,
        features TEXT DEFAULT '',
        requirements TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        icon TEXT DEFAULT 'sparkles',
        views_count INTEGER DEFAULT 12,
        clicks_count INTEGER DEFAULT 5,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Check and add missing columns if upgrading existing table
    existing_cols = [r[1] for r in cursor.execute("PRAGMA table_info(products)").fetchall()]
    cols_to_ensure = [
        ("short_description", "TEXT DEFAULT ''"),
        ("image_url", "TEXT DEFAULT ''"),
        ("is_featured", "INTEGER DEFAULT 0"),
        ("is_popular", "INTEGER DEFAULT 0"),
        ("is_recommended", "INTEGER DEFAULT 0"),
        ("is_premium", "INTEGER DEFAULT 0"),
        ("sort_order", "INTEGER DEFAULT 0"),
        ("features", "TEXT DEFAULT ''"),
        ("requirements", "TEXT DEFAULT ''"),
        ("notes", "TEXT DEFAULT ''"),
        ("views_count", "INTEGER DEFAULT 12"),
        ("clicks_count", "INTEGER DEFAULT 5")
    ]
    for col_name, col_def in cols_to_ensure:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_def}")

    # Telegram Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS telegram_users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        phone_number TEXT,
        total_spent REAL DEFAULT 0.0,
        orders_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Orders table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE NOT NULL,
        telegram_user_id INTEGER,
        buyer_name TEXT NOT NULL,
        buyer_username TEXT,
        buyer_phone TEXT,
        product_id INTEGER,
        product_name TEXT,
        amount REAL NOT NULL,
        payment_method TEXT NOT NULL,
        receipt_image TEXT,
        transaction_reference TEXT,
        status TEXT DEFAULT 'pending', -- pending, confirmed, completed, cancelled
        delivery_credentials TEXT,
        rejection_reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL,
        FOREIGN KEY(telegram_user_id) REFERENCES telegram_users(user_id) ON DELETE SET NULL
    )
    """)

    # Broadcasts table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS broadcasts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT NOT NULL,
        target TEXT DEFAULT 'all',
        target_username TEXT,
        status TEXT DEFAULT 'sent',
        sent_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Default settings
    for key, val in DEFAULT_SETTINGS.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, str(val)))

    # Seed demo telegram users
    cursor.execute("SELECT COUNT(*) FROM telegram_users")
    if cursor.fetchone()[0] == 0:
        demo_users = [
            (987654321, "hey994", "Abdu", "", "0911223344", 999.0, 1),
            (876543210, "hamzuu3", "hamzuuu", "", "0922334455", 0.0, 1),
            (765432109, "trust994", "Farhan", "", "0992558519", 0.0, 0)
        ]
        cursor.executemany("""
        INSERT INTO telegram_users (user_id, username, first_name, last_name, phone_number, total_spent, orders_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, demo_users)

    # Seed default products / services matching the screenshot
    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0:
        seed_services = [
            ("SRV-NETFLIX", "NETFLIX", "Streaming", 999.0, 100, "1 Month", "NETFLIX PREMIUM", "1month shared account with Ultra HD 4K screen and private PIN protection.", "https://upload.wikimedia.org/wikipedia/commons/7/75/Netflix_icon.svg", "active", 1, 1, 1, 1, 1, "4K UHD\n1 Screen\nInstant Delivery", "Email / Profile PIN provided", "Do not modify other profiles", "tv"),
            ("SRV-INTERNET", "Unlimited Internet", "Subscriptions", 350.0, 200, "1 Month", "Unlimited internet at the lowest price.", "Fast high-speed unlimited package with no throttling.", "https://upload.wikimedia.org/wikipedia/commons/9/98/Microsoft_Edge_logo_%282019%29.svg", "active", 1, 1, 0, 0, 2, "High Speed\nZero Limit", "Active SIM Card", "Works nationwide", "globe"),
            ("SRV-DUOLINGO", "Duolingo pro", "Education", 250.0, 50, "1 Year", "Learn languages with Duolingo Pro.", "Ad-free experience, unlimited hearts, and personalized practice to improve your skills with confidence.", "https://upload.wikimedia.org/wikipedia/commons/1/15/Duolingo_logo.svg", "active", 1, 0, 1, 0, 3, "Unlimited Hearts\nNo Ads", "Duolingo Account Email", "1 Year Warranty", "award"),
            ("SRV-APPLETV", "APPLE TV", "Streaming", 450.0, 0, "1 Month", "^^Apple TV Pro^^ gives you access to premium Apple Originals.", "Exclusive series, movies, and documentaries in one place. Enjoy high-quality streaming.", "https://upload.wikimedia.org/wikipedia/commons/a/ab/Apple-logo.png", "inactive", 0, 0, 0, 0, 4, "Apple Originals\n4K HDR", "Apple ID", "Unavailable currently", "film"),
            ("SRV-TGPREM", "TELEGRAM PREMIUM", "Subscriptions", 400.0, 150, "3 Months", "Upgrade Your Telegram Experience.", "Enjoy an enhanced messaging experience with Telegram Premium. Unlock eligible premium features designed to improve com...", "https://upload.wikimedia.org/wikipedia/commons/8/82/Telegram_logo.svg", "active", 0, 1, 1, 1, 5, "4GB Uploads\nVoice to Text\nExclusive Stickers", "Telegram @username", "Official Gift", "send"),
            ("SRV-GEMINI", "GEMINI PRO", "AI Tools", 350.0, 80, "1 Month", "Google Gemini Advanced Pro with 2M context.", "Access Google's most capable AI model with Workspace integration and high-speed generation.", "https://upload.wikimedia.org/wikipedia/commons/8/8a/Google_Gemini_logo.svg", "active", 1, 1, 1, 1, 6, "Gemini Advanced\nImagen 3", "Google Account", "Instant Activation", "sparkles"),
            ("SRV-CANVA", "CANVA PRO", "Design", 250.0, 120, "1 Year", "Canva Pro lifetime/yearly team access.", "Full Canva library, background remover, brand kits, and premium templates.", "https://upload.wikimedia.org/wikipedia/commons/0/08/Canva_icon_2021.svg", "active", 1, 1, 1, 0, 7, "Unlimited Assets\nBackground Remover", "Canva Email", "100% Private", "palette")
        ]
        cursor.executemany("""
        INSERT INTO products (product_code, name, category, price, stock, duration, short_description, description, image_url, status, is_featured, is_popular, is_recommended, is_premium, sort_order, features, requirements, notes, icon)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, seed_services)

    # Seed demo orders matching the screenshot
    cursor.execute("SELECT COUNT(*) FROM orders")
    if cursor.fetchone()[0] == 0:
        demo_orders = [
            ("ORD-1092", 987654321, "Abdu", "hey994", "0911223344", 1, "NETFLIX (1month shared account)", 999.0, "telebirr", "demo_receipt_1.jpg", "FT26091801", "pending", None, None),
            ("ORD-1093", 876543210, "hamzuuu", "hamzuu3", "0922334455", 4, "APPLE TV", 450.0, "cbe", "demo_receipt_2.jpg", "CBE98472910", "cancelled", None, "Service currently unavailable")
        ]
        cursor.executemany("""
        INSERT INTO orders (order_number, telegram_user_id, buyer_name, buyer_username, buyer_phone, product_id, product_name, amount, payment_method, receipt_image, transaction_reference, status, delivery_credentials, rejection_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, demo_orders)

    conn.commit()
    conn.close()

def get_setting(key, default=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def get_all_settings():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, value FROM settings")
    rows = c.fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

def get_products(status=None, category=None):
    conn = get_db()
    c = conn.cursor()
    query = "SELECT * FROM products WHERE 1=1"
    params = []
    if status and status != "all":
        query += " AND status = ?"
        params.append(status)
    if category and category != "all":
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY sort_order ASC, id DESC"
    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_product_by_id(prod_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM products WHERE id = ?", (prod_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def add_product(name, category, price, stock=999, duration='1 Month', short_description='', description='', image_url='', status='active', is_featured=0, is_popular=0, is_recommended=0, is_premium=0, sort_order=0, features='', requirements='', notes='', icon='sparkles'):
    conn = get_db()
    c = conn.cursor()
    code = f"SRV-{random.randint(10000, 99999)}"
    c.execute("""
    INSERT INTO products (product_code, name, category, price, stock, duration, short_description, description, image_url, status, is_featured, is_popular, is_recommended, is_premium, sort_order, features, requirements, notes, icon)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (code, name, category, float(price), int(stock), duration, short_description, description, image_url, status, int(is_featured), int(is_popular), int(is_recommended), int(is_premium), int(sort_order), features, requirements, notes, icon))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id

def update_product(prod_id, name, category, price, stock, duration, short_description, description, image_url, status, is_featured=0, is_popular=0, is_recommended=0, is_premium=0, sort_order=0, features='', requirements='', notes=''):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    UPDATE products
    SET name = ?, category = ?, price = ?, stock = ?, duration = ?, short_description = ?, description = ?, image_url = ?, status = ?, is_featured = ?, is_popular = ?, is_recommended = ?, is_premium = ?, sort_order = ?, features = ?, requirements = ?, notes = ?
    WHERE id = ?
    """, (name, category, float(price), int(stock), duration, short_description, description, image_url, status, int(is_featured), int(is_popular), int(is_recommended), int(is_premium), int(sort_order), features, requirements, notes, prod_id))
    conn.commit()
    conn.close()

def delete_product(prod_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM products WHERE id = ?", (prod_id,))
    conn.commit()
    conn.close()

def get_orders(status=None, search=None):
    conn = get_db()
    c = conn.cursor()
    query = "SELECT * FROM orders WHERE 1=1"
    params = []
    if status and status != "all":
        # Handle 'confirmed' as 'approved' or 'confirmed'
        if status in ['confirmed', 'approved']:
            query += " AND status IN ('confirmed', 'approved')"
        elif status in ['cancelled', 'rejected']:
            query += " AND status IN ('cancelled', 'rejected')"
        else:
            query += " AND status = ?"
            params.append(status)
    if search:
        query += " AND (order_number LIKE ? OR buyer_name LIKE ? OR buyer_username LIKE ? OR product_name LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term, term])
    query += " ORDER BY id DESC"
    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_order_by_id(order_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE id = ? OR order_number = ?", (order_id, str(order_id)))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def create_order(telegram_user_id, buyer_name, buyer_username, buyer_phone, product_id, amount, payment_method, receipt_image="", transaction_ref=""):
    conn = get_db()
    c = conn.cursor()
    order_number = f"ORD-{random.randint(1000, 9999)}"
    prod = get_product_by_id(product_id)
    prod_name = prod["name"] if prod else "Pro Service"

    c.execute("""
    INSERT INTO orders (order_number, telegram_user_id, buyer_name, buyer_username, buyer_phone, product_id, product_name, amount, payment_method, receipt_image, transaction_reference, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (order_number, telegram_user_id, buyer_name, buyer_username, buyer_phone, product_id, prod_name, float(amount), payment_method, receipt_image, transaction_ref))
    
    c.execute("""
    UPDATE telegram_users 
    SET orders_count = orders_count + 1, last_active = CURRENT_TIMESTAMP
    WHERE user_id = ?
    """, (telegram_user_id,))

    conn.commit()
    conn.close()
    return order_number

def update_order_status(order_id, status, credentials=None, rejection_reason=None):
    conn = get_db()
    c = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    order = get_order_by_id(order_id)
    if not order:
        conn.close()
        return False

    c.execute("""
    UPDATE orders 
    SET status = ?, delivery_credentials = ?, rejection_reason = ?, updated_at = ?
    WHERE id = ?
    """, (status, credentials, rejection_reason, now, order["id"]))

    if status in ["approved", "confirmed", "completed"]:
        c.execute("""
        UPDATE telegram_users
        SET total_spent = total_spent + ?
        WHERE user_id = ?
        """, (order["amount"], order["telegram_user_id"]))

    conn.commit()
    conn.close()
    return True

def get_telegram_users():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM telegram_users ORDER BY last_active DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_or_create_telegram_user(user_id, username="", first_name="", last_name=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM telegram_users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if user:
        c.execute("""
        UPDATE telegram_users 
        SET username = ?, first_name = ?, last_name = ?, last_active = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """, (username, first_name, last_name, user_id))
    else:
        c.execute("""
        INSERT INTO telegram_users (user_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
        """, (user_id, username, first_name, last_name))
    conn.commit()
    c.execute("SELECT * FROM telegram_users WHERE user_id = ?", (user_id,))
    res = dict(c.fetchone())
    conn.close()
    return res

def get_dashboard_stats():
    conn = get_db()
    c = conn.cursor()

    # Total Services
    c.execute("SELECT COUNT(*) FROM products")
    total_services = c.fetchone()[0]

    # Active Services
    c.execute("SELECT COUNT(*) FROM products WHERE status = 'active'")
    active_services = c.fetchone()[0]

    # Unavailable Services
    c.execute("SELECT COUNT(*) FROM products WHERE status = 'inactive'")
    unavailable_services = c.fetchone()[0]

    # Categories Count
    c.execute("SELECT COUNT(DISTINCT category) FROM products")
    categories_count = c.fetchone()[0]

    # Featured Services
    c.execute("SELECT COUNT(*) FROM products WHERE is_featured = 1")
    featured_count = c.fetchone()[0]

    # Order clicks and views (calculated from products or default)
    c.execute("SELECT COALESCE(SUM(clicks_count), 26) FROM products")
    order_clicks = c.fetchone()[0] or 26

    c.execute("SELECT COALESCE(SUM(views_count), 59) FROM products")
    total_views = c.fetchone()[0] or 59

    # Orders stats
    c.execute("SELECT COUNT(*) FROM orders")
    total_orders = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
    pending_orders = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('confirmed', 'approved')")
    confirmed_orders = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM orders WHERE status = 'completed'")
    completed_orders = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('cancelled', 'rejected')")
    cancelled_orders = c.fetchone()[0]

    c.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status IN ('approved', 'confirmed', 'completed')")
    total_revenue = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM telegram_users")
    total_customers = c.fetchone()[0]

    conn.close()
    return {
        "total_services": total_services,
        "active_services": active_services,
        "unavailable_services": unavailable_services,
        "categories_count": categories_count,
        "featured_count": featured_count,
        "order_clicks": order_clicks,
        "total_views": total_views,
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "confirmed_orders": confirmed_orders,
        "completed_orders": completed_orders,
        "cancelled_orders": cancelled_orders,
        "total_revenue": total_revenue,
        "total_customers": total_customers
    }

def record_broadcast(message, target="all", target_username="", sent_count=0):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    INSERT INTO broadcasts (message, target, target_username, sent_count)
    VALUES (?, ?, ?, ?)
    """, (message, str(target), target_username, sent_count))
    conn.commit()
    conn.close()

def get_broadcasts():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM broadcasts ORDER BY id DESC LIMIT 50")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
