import json
import uuid
from pathlib import Path
from database import init_db, db_session

def seed_store():
    init_db()
    
    # Path to dump file
    dump_path = Path("C:/Users/SolNet/.gemini/antigravity-ide/brain/dc70bb08-ec28-40dc-a184-948913da5fcf/scratch/store_dump.json")
    if not dump_path.exists():
        print("Warning: Dump file not found at", dump_path)
        return

    with open(dump_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    category_icons = {
        "AI Tools": "🤖",
        "movie": "🎬",
        "Music": "🎵",
        "EDUCATION": "🎓",
        "Productivity": "⚡",
        "Other Services": "🌐"
    }

    with db_session() as conn:
        cursor = conn.cursor()

        # 1. Categories
        categories = data.get("Category", [])
        print(f"Seeding {len(categories)} categories...")
        for i, cat in enumerate(categories):
            cid = cat.get("id")
            name = cat.get("name")
            slug = cat.get("slug", name.lower().replace(" ", "-"))
            icon = category_icons.get(name, "📦")
            cursor.execute("""
                INSERT OR REPLACE INTO categories (id, name, slug, icon, sort_order, active)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (cid, name, slug, icon, i))

        # 2. Services / Products
        services = data.get("Service", [])
        print(f"Seeding {len(services)} products...")
        for i, s in enumerate(services):
            sid = s.get("id")
            cat_id = s.get("category_id")
            name = s.get("name")
            desc = s.get("description", "")
            img = s.get("image_url", "")
            features = json.dumps(s.get("features", []))
            requirements = json.dumps(s.get("requirements", []))

            cursor.execute("""
                INSERT OR REPLACE INTO products (id, category_id, name, description, image_url, features, requirements, active, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (sid, cat_id, name, desc, img, features, requirements, i))

        # 3. Plans
        plans = data.get("Plan", [])
        print(f"Seeding {len(plans)} plans...")
        for i, p in enumerate(plans):
            pid = p.get("id", str(uuid.uuid4())[:8])
            service_id = p.get("service_id")
            name = p.get("name")
            duration = p.get("duration", "Standard")
            price = float(p.get("price", 0))
            currency = p.get("currency", "ETB")
            avail = p.get("availability", "available")
            active = 1 if p.get("active", True) else 0

            cursor.execute("""
                INSERT OR REPLACE INTO product_plans (id, product_id, name, duration, price, currency, availability, active, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (pid, service_id, name, duration, price, currency, avail, active, i))

    print("Store data successfully seeded into database!")

if __name__ == "__main__":
    seed_store()
