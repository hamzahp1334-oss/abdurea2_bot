import os
import time
import logging
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory
from werkzeug.utils import secure_filename

import config
import database
import bot

# Initialize Flask app
app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["UPLOAD_FOLDER"] = config.UPLOAD_FOLDER

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AbdureaWeb")

# Ensure DB is initialized
database.init_db()

# --- WEB PAGE ROUTES ---

@app.route("/")
def index():
    return redirect(url_for("dashboard_view"))

@app.route("/dashboard")
def dashboard_view():
    stats = database.get_dashboard_stats()
    return render_template("dashboard.html", active_page="dashboard", stats=stats)

@app.route("/services")
@app.route("/products")
def products_view():
    products = database.get_products()
    stats = database.get_dashboard_stats()
    return render_template("services.html", active_page="services", products=products, stats=stats)

@app.route("/orders")
def orders_view():
    orders = database.get_orders()
    stats = database.get_dashboard_stats()
    return render_template("orders.html", active_page="orders", orders=orders, stats=stats)

@app.route("/customers")
def customers_view():
    customers = database.get_telegram_users()
    stats = database.get_dashboard_stats()
    return render_template("customers.html", active_page="customers", customers=customers, stats=stats)

@app.route("/broadcast")
def broadcast_view():
    broadcasts = database.get_broadcasts()
    customers = database.get_telegram_users()
    stats = database.get_dashboard_stats()
    products = database.get_products()
    return render_template("broadcast.html", active_page="broadcast", broadcasts=broadcasts, customers=customers, stats=stats, products=products)

@app.route("/settings")
def settings_view():
    settings = database.get_all_settings()
    stats = database.get_dashboard_stats()
    return render_template("settings.html", active_page="settings", settings=settings, stats=stats)

@app.route("/receipts/<filename>")
def serve_receipt(filename):
    return send_from_directory(config.RECEIPTS_FOLDER, filename)

@app.route("/uploads/<folder>/<filename>")
def serve_upload(folder, filename):
    folder_path = config.UPLOAD_FOLDER / folder
    return send_from_directory(folder_path, filename)


# --- REST API ROUTES ---

@app.route("/api/dashboard/stats", methods=["GET"])
def api_dashboard_stats():
    stats = database.get_dashboard_stats()
    return jsonify({"status": "success", "stats": stats})

# --- File Upload API ---
@app.route("/api/upload", methods=["POST"])
def api_upload_file():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"status": "error", "message": "Empty filename"}), 400

    filename = f"img_{int(time.time())}_{secure_filename(file.filename)}"
    save_path = config.PRODUCTS_FOLDER / filename
    file.save(save_path)
    file_url = f"/uploads/products/{filename}"
    return jsonify({"status": "success", "url": file_url})

# --- Products / Services API ---

@app.route("/api/products", methods=["GET", "POST"])
def api_products():
    if request.method == "GET":
        status = request.args.get("status")
        category = request.args.get("category")
        products = database.get_products(status=status, category=category)
        return jsonify({"status": "success", "products": products})

    data = request.json or request.form
    name = data.get("name", "").strip()
    category = data.get("category", "Uncategorized").strip()
    price = float(data.get("price", 0) or 0)
    stock = int(data.get("stock", 999) or 999)
    duration = data.get("duration", "1 Month").strip()
    short_description = data.get("short_description", "").strip()
    description = data.get("description", "").strip()
    image_url = data.get("image_url", "").strip()
    status = data.get("status", "active").strip()
    is_featured = 1 if data.get("is_featured") in [True, 1, "1", "true"] else 0
    is_popular = 1 if data.get("is_popular") in [True, 1, "1", "true"] else 0
    is_recommended = 1 if data.get("is_recommended") in [True, 1, "1", "true"] else 0
    is_premium = 1 if data.get("is_premium") in [True, 1, "1", "true"] else 0
    sort_order = int(data.get("sort_order", 0) or 0)
    features = data.get("features", "").strip()
    requirements = data.get("requirements", "").strip()
    notes = data.get("notes", "").strip()

    if not name:
        return jsonify({"status": "error", "message": "Service name is required"}), 400

    new_id = database.add_product(
        name=name,
        category=category,
        price=price,
        stock=stock,
        duration=duration,
        short_description=short_description,
        description=description,
        image_url=image_url,
        status=status,
        is_featured=is_featured,
        is_popular=is_popular,
        is_recommended=is_recommended,
        is_premium=is_premium,
        sort_order=sort_order,
        features=features,
        requirements=requirements,
        notes=notes
    )

    # Automatically notify every single bot user about the new product
    notify_users = data.get("notify_users")
    if notify_users is None or notify_users in [True, 1, "1", "true"]:
        if status in ["active", "on_sale"]:
            bot.broadcast_new_product_async(new_id)

    return jsonify({"status": "success", "message": "Service created and broadcast notification sent to all users!", "product_id": new_id})

@app.route("/api/products/<int:prod_id>", methods=["GET", "PUT", "DELETE"])
def api_product_detail(prod_id):
    if request.method == "GET":
        prod = database.get_product_by_id(prod_id)
        if not prod:
            return jsonify({"status": "error", "message": "Service not found"}), 404
        return jsonify({"status": "success", "product": prod})

    if request.method == "PUT":
        data = request.json or {}
        prod = database.get_product_by_id(prod_id)
        if not prod:
            return jsonify({"status": "error", "message": "Service not found"}), 404

        name = data.get("name", prod["name"])
        category = data.get("category", prod["category"])
        price = float(data.get("price", prod["price"]) or 0)
        stock = int(data.get("stock", prod["stock"]) or 999)
        duration = data.get("duration", prod["duration"])
        short_description = data.get("short_description", prod.get("short_description", ""))
        description = data.get("description", prod["description"])
        image_url = data.get("image_url", prod.get("image_url", ""))
        status = data.get("status", prod["status"])
        is_featured = 1 if data.get("is_featured") in [True, 1, "1", "true"] else 0
        is_popular = 1 if data.get("is_popular") in [True, 1, "1", "true"] else 0
        is_recommended = 1 if data.get("is_recommended") in [True, 1, "1", "true"] else 0
        is_premium = 1 if data.get("is_premium") in [True, 1, "1", "true"] else 0
        sort_order = int(data.get("sort_order", prod.get("sort_order", 0)) or 0)
        features = data.get("features", prod.get("features", ""))
        requirements = data.get("requirements", prod.get("requirements", ""))
        notes = data.get("notes", prod.get("notes", ""))

        database.update_product(
            prod_id, name, category, price, stock, duration,
            short_description, description, image_url, status,
            is_featured, is_popular, is_recommended, is_premium, sort_order,
            features, requirements, notes
        )
        return jsonify({"status": "success", "message": "Service updated successfully"})

    if request.method == "DELETE":
        database.delete_product(prod_id)
        return jsonify({"status": "success", "message": "Service deleted successfully"})

@app.route("/api/products/<int:prod_id>/duplicate", methods=["POST"])
def api_duplicate_product(prod_id):
    prod = database.get_product_by_id(prod_id)
    if not prod:
        return jsonify({"status": "error", "message": "Service not found"}), 404

    new_id = database.add_product(
        name=f"{prod['name']} (Copy)",
        category=prod["category"],
        price=prod["price"],
        stock=prod["stock"],
        duration=prod["duration"],
        short_description=prod.get("short_description", ""),
        description=prod["description"],
        image_url=prod.get("image_url", ""),
        status="active",
        is_featured=prod.get("is_featured", 0),
        is_popular=prod.get("is_popular", 0),
        is_recommended=prod.get("is_recommended", 0),
        is_premium=prod.get("is_premium", 0),
        sort_order=prod.get("sort_order", 0) + 1,
        features=prod.get("features", ""),
        requirements=prod.get("requirements", ""),
        notes=prod.get("notes", "")
    )
    return jsonify({"status": "success", "message": "Service duplicated successfully", "product_id": new_id})

# --- Orders API ---

@app.route("/api/orders", methods=["GET"])
def api_orders():
    status = request.args.get("status")
    search = request.args.get("search")
    orders = database.get_orders(status=status, search=search)
    return jsonify({"status": "success", "orders": orders})

@app.route("/api/orders/<order_id>", methods=["GET"])
def api_order_detail(order_id):
    order = database.get_order_by_id(order_id)
    if not order:
        return jsonify({"status": "error", "message": "Order not found"}), 404
    return jsonify({"status": "success", "order": order})

@app.route("/api/orders/<order_id>/status", methods=["POST"])
def api_update_order_status(order_id):
    data = request.json or {}
    status = data.get("status", "pending").lower()
    credentials = data.get("credentials", "")
    reason = data.get("reason", "")

    success = database.update_order_status(order_id, status=status, credentials=credentials, rejection_reason=reason)
    if not success:
        return jsonify({"status": "error", "message": "Order not found"}), 404

    # Trigger bot notification if applicable
    if status in ["confirmed", "approved"]:
        creds = credentials or "Your service subscription has been confirmed and activated! Thank you for purchasing from Abdurea Store."
        bot.send_approval_delivery(order_id, creds)
    elif status in ["cancelled", "rejected"]:
        bot.send_rejection_notice(order_id, reason or "Order cancelled by administrator.")

    return jsonify({"status": "success", "message": f"Order status updated to {status.upper()}"})

@app.route("/api/orders/<order_id>/approve", methods=["POST"])
def api_approve_order(order_id):
    data = request.json or {}
    credentials = data.get("credentials", "").strip() or "Your service has been activated! Welcome to Abdurea Store."
    success = database.update_order_status(order_id, status="confirmed", credentials=credentials)
    if not success:
        return jsonify({"status": "error", "message": "Failed to update order"}), 404

    delivery_ok, delivery_msg = bot.send_approval_delivery(order_id, credentials)
    return jsonify({"status": "success", "message": "Order confirmed and credentials sent to buyer!", "notified": delivery_ok})

@app.route("/api/orders/<order_id>/reject", methods=["POST"])
def api_reject_order(order_id):
    data = request.json or {}
    reason = data.get("reason", "").strip() or "Payment receipt could not be verified."
    success = database.update_order_status(order_id, status="cancelled", rejection_reason=reason)
    if not success:
        return jsonify({"status": "error", "message": "Failed to update order"}), 404

    notif_ok, notif_msg = bot.send_rejection_notice(order_id, reason)
    return jsonify({"status": "success", "message": "Order marked as cancelled.", "notified": notif_ok})

# --- Customers API ---

@app.route("/api/customers", methods=["GET"])
def api_customers():
    users = database.get_telegram_users()
    return jsonify({"status": "success", "customers": users})

@app.route("/api/customers/<int:user_id>/message", methods=["POST"])
def api_message_customer(user_id):
    data = request.json or {}
    message_text = data.get("message", "").strip()
    if not message_text:
        return jsonify({"status": "error", "message": "Message content is required"}), 400

    count, status_msg = bot.broadcast_message(message_text, target=user_id)
    if count > 0:
        return jsonify({"status": "success", "message": "Message delivered to user via Telegram!"})
    else:
        return jsonify({"status": "error", "message": f"Could not deliver message: {status_msg}"}), 400

# --- Broadcast API ---

@app.route("/api/broadcast", methods=["POST"])
def api_broadcast():
    data = request.json or {}
    message_text = data.get("message", "").strip()
    target = data.get("target", "all").strip()
    button_text = data.get("button_text", "").strip()
    button_action = data.get("button_action", "none").strip()
    button_url = data.get("button_url", "").strip()
    button_color = data.get("button_color", "blue").strip()
    product_id = data.get("product_id")
    if product_id:
        try:
            product_id = int(product_id)
        except ValueError:
            product_id = None

    if not message_text:
        return jsonify({"status": "error", "message": "Broadcast message cannot be empty"}), 400

    sent_count, msg = bot.broadcast_message(
        message_text=message_text,
        target=target,
        button_text=button_text,
        button_action=button_action,
        button_url=button_url,
        button_color=button_color,
        product_id=product_id
    )
    return jsonify({
        "status": "success",
        "message": f"Broadcast dispatched! Delivered to {sent_count} user(s).",
        "sent_count": sent_count
    })

# --- Settings API ---

@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        settings = database.get_all_settings()
        return jsonify({"status": "success", "settings": settings})

    data = request.json or {}
    for k, v in data.items():
        database.set_setting(k, v)

    if "bot_token" in data and data["bot_token"]:
        bot.run_in_background()

    return jsonify({"status": "success", "message": "Settings saved successfully!"})

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "app": "ABDUREA STORE", "time": time.time()})

# Auto-start bot in background for Render / Gunicorn
try:
    bot.run_in_background()
except Exception:
    pass

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=False)
