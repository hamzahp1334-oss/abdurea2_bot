import os
import sys
import time
import logging
import threading
from pathlib import Path
import telebot
from telebot import types, apihelper

# Increase timeouts for high-latency connections
apihelper.READ_TIMEOUT = 60
apihelper.CONNECT_TIMEOUT = 30

import database
from config import RECEIPTS_FOLDER, DEFAULT_SETTINGS

logger = logging.getLogger("AbdureaBot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# In-memory checkout state for users: {user_id: {"step": ..., "product_id": ..., "payment_method": ..., "buyer_name": ...}}
user_checkout_sessions = {}

# In-memory dictionary tracking sent message IDs per chat for clearing history
chat_sent_messages = {}

# Global bot instance
bot_instance = None
bot_thread = None
bot_running = False

def track_msg(chat_id, msg):
    """Tracks message IDs sent to a chat so they can be deleted upon Clear History"""
    if not msg:
        return
    if chat_id not in chat_sent_messages:
        chat_sent_messages[chat_id] = set()
    if hasattr(msg, "message_id"):
        chat_sent_messages[chat_id].add(msg.message_id)
    elif isinstance(msg, int):
        chat_sent_messages[chat_id].add(msg)

def clear_chat_history(chat_id, current_msg_id=None):
    """Deletes recent bot messages in the chat to keep history completely clean"""
    bot = get_bot()
    if not bot:
        return

    msg_ids = list(chat_sent_messages.get(chat_id, set()))
    if current_msg_id and current_msg_id not in msg_ids:
        msg_ids.append(current_msg_id)

    # Also try to clean recent contiguous messages if current_msg_id is known
    if current_msg_id:
        for offset in range(1, 30):
            msg_ids.append(current_msg_id - offset)

    # Remove duplicates and sort descending
    unique_ids = list(set(msg_ids))
    unique_ids.sort(reverse=True)

    # Delete messages in batches
    try:
        bot.delete_messages(chat_id, unique_ids[:100])
    except Exception:
        for mid in unique_ids[:30]:
            try:
                bot.delete_message(chat_id, mid)
            except Exception:
                pass

    chat_sent_messages[chat_id] = set()

    welcome_text = (
        "🧹 <b>Chat history cleared successfully!</b> ✨\n\n"
        "Welcome back to <b>Abdurea Pro Store</b>. All previous conversation messages have been cleaned up.\n\n"
        "Tap <b>🛍️ Browse Products</b> to view our services, or use the menu below:"
    )
    m = bot.send_message(chat_id, welcome_text, reply_markup=build_main_keyboard())
    track_msg(chat_id, m)

def get_product_button_badge(prod, index=0):
    """Assigns distinct brand colors, symbols, and badges to each product button"""
    name = (prod.get("name") or "").lower()
    category = (prod.get("category") or "").lower()

    if "gemini" in name:
        color_dot = "🔵"
        icon = "💎"
    elif "canva" in name:
        color_dot = "🟢"
        icon = "🎨"
    elif "chatgpt" in name or "gpt" in name or "openai" in name:
        color_dot = "🟡"
        icon = "⚡"
    elif "telegram" in name:
        color_dot = "🟣"
        icon = "⭐"
    elif "netflix" in name:
        color_dot = "🔴"
        icon = "🎬"
    elif "capcut" in name:
        color_dot = "🟠"
        icon = "✂️"
    elif "spotify" in name:
        color_dot = "🟢"
        icon = "🎵"
    elif "duolingo" in name:
        color_dot = "🟢"
        icon = "🦉"
    elif "apple" in name:
        color_dot = "⚪"
        icon = "🍎"
    elif "internet" in name:
        color_dot = "🔵"
        icon = "🌐"
    elif "ai" in category:
        color_dot = "🔵"
        icon = "🤖"
    elif "design" in category:
        color_dot = "🟢"
        icon = "🎨"
    elif "video" in category:
        color_dot = "🟠"
        icon = "🎥"
    elif "stream" in category:
        color_dot = "🔴"
        icon = "📺"
    else:
        palette = ["🔵", "🟢", "🟣", "🟡", "🔴", "🟠", "🩵", "🩷"]
        color_dot = palette[index % len(palette)]
        icon = "✨"

    price_str = int(prod['price']) if prod['price'] == int(prod['price']) else prod['price']
    return f"{color_dot} {icon} {prod['name']} ── {price_str} ETB"

def get_bot():
    global bot_instance
    token = database.get_setting("bot_token", "").strip()
    if not token:
        token = os.getenv("BOT_TOKEN", "").strip() or os.getenv("MAIN_BOT_TOKEN", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    
    if not token:
        return None

    if bot_instance is None or getattr(bot_instance, "token", None) != token:
        try:
            bot_instance = telebot.TeleBot(token, parse_mode="HTML")
            register_handlers(bot_instance)
            logger.info("Bot handlers successfully registered on bot_instance!")
        except Exception as e:
            logger.error(f"Failed to initialize TeleBot: {e}")
            return None
    return bot_instance

def send_approval_delivery(order_id, credentials):
    """Called from dashboard when admin approves an order"""
    order = database.get_order_by_id(order_id)
    if not order or not order["telegram_user_id"]:
        return False, "Order or Telegram User ID not found"

    bot = get_bot()
    if not bot:
        return False, "Bot token not configured"

    user_id = order["telegram_user_id"]
    msg = (
        f"🎉 <b>CONGRATULATIONS! YOUR ORDER HAS BEEN CONFIRMED</b>\n\n"
        f"🆔 <b>Order ID:</b> <code>#{order['order_number']}</code>\n"
        f"📦 <b>Service:</b> {order['product_name']}\n"
        f"💰 <b>Amount:</b> {order['amount']} ETB ({order['payment_method'].upper()})\n\n"
        f"🔑 <b>DELIVERY CREDENTIALS / ACCESS:</b>\n"
        f"<blockquote>{credentials}</blockquote>\n\n"
        f"Thank you for purchasing from <b>Abdurea Store</b>! ❤️\n"
        f"If you need any assistance, contact our support: @trust994"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
        types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
    )
    try:
        m = bot.send_message(user_id, msg, reply_markup=kb)
        track_msg(user_id, m)
        return True, "Delivered successfully to user"
    except Exception as e:
        logger.error(f"Error sending delivery message: {e}")
        return False, str(e)

def send_rejection_notice(order_id, reason):
    """Called from dashboard when admin cancels/rejects an order"""
    order = database.get_order_by_id(order_id)
    if not order or not order["telegram_user_id"]:
        return False, "Order or Telegram User ID not found"

    bot = get_bot()
    if not bot:
        return False, "Bot token not configured"

    user_id = order["telegram_user_id"]
    msg = (
        f"⚠️ <b>ORDER STATUS UPDATE - CANCELLED</b>\n\n"
        f"🆔 <b>Order ID:</b> <code>#{order['order_number']}</code>\n"
        f"📦 <b>Service:</b> {order['product_name']}\n"
        f"❌ <b>Status:</b> Cancelled\n\n"
        f"<b>Reason:</b>\n"
        f"<blockquote>{reason or 'Payment receipt could not be verified or amount mismatched'}</blockquote>\n\n"
        f"Please contact our support team to verify your payment:\n"
        f"📞 <b>Support:</b> @trust994\n"
        f"📱 <b>Phone:</b> 0992558519"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
        types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
    )
    try:
        m = bot.send_message(user_id, msg, reply_markup=kb)
        track_msg(user_id, m)
        return True, "Rejection notice sent"
    except Exception as e:
        logger.error(f"Error sending rejection notice: {e}")
        return False, str(e)

def broadcast_message(message_text, target="all", button_text="", button_action="none", button_url="", button_color="blue", product_id=None):
    """Broadcasts a message with optional customizable colored inline button to users"""
    bot = get_bot()
    if not bot:
        return 0, "Bot token not configured"

    color_emojis = {
        "blue": "🔵",
        "green": "🟢",
        "red": "🔴",
        "purple": "🟣",
        "gold": "🟡",
        "orange": "🟠",
        "cyan": "🩵",
        "pink": "🩷",
        "white": "⚪"
    }

    style_val = None
    if button_color == "green":
        style_val = "bg_success"
    elif button_color == "blue":
        style_val = "bg_primary"
    elif button_color == "red":
        style_val = "bg_danger"

    reply_markup = None
    kb = types.InlineKeyboardMarkup(row_width=1)

    if button_text and button_text.strip():
        color_dot = color_emojis.get((button_color or "blue").lower(), "🔵")
        btn_label = f"{color_dot} {button_text.strip()}"

        if button_action == "catalog":
            kb.add(types.InlineKeyboardButton(btn_label, callback_data="back_catalog", style=style_val))
        elif button_action == "product" and product_id:
            kb.add(types.InlineKeyboardButton(btn_label, callback_data=f"prod_{product_id}", style=style_val))
        elif button_action == "buy" and product_id:
            kb.add(types.InlineKeyboardButton(btn_label, callback_data=f"buy_{product_id}", style=style_val))
        elif button_action == "support":
            kb.add(types.InlineKeyboardButton(btn_label, url="https://t.me/trust994", style=style_val))
        elif button_action == "website":
            kb.add(types.InlineKeyboardButton(btn_label, url="https://abdureastore.base44.app/", style=style_val))
        elif button_url and button_url.startswith("http"):
            kb.add(types.InlineKeyboardButton(btn_label, url=button_url, style=style_val))
        else:
            kb.add(types.InlineKeyboardButton(btn_label, callback_data="back_catalog", style=style_val))

    # Always add Clear History option
    kb.row(types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history"))
    reply_markup = kb

    success_count = 0
    if target == "all":
        users = database.get_telegram_users()
        for u in users:
            uid = u["user_id"]
            try:
                m = bot.send_message(uid, message_text, reply_markup=reply_markup)
                track_msg(uid, m)
                success_count += 1
                time.sleep(0.04)
            except Exception as e:
                logger.warning(f"Could not send broadcast to {uid}: {e}")
        database.record_broadcast(message_text, target="all", sent_count=success_count)
    else:
        try:
            m = bot.send_message(int(target), message_text, reply_markup=reply_markup)
            track_msg(int(target), m)
            success_count = 1
            database.record_broadcast(message_text, target=str(target), sent_count=1)
        except Exception as e:
            logger.error(f"Failed direct message to {target}: {e}")
            return 0, str(e)

    return success_count, "Broadcast completed"

def notify_new_product_broadcast(prod_id):
    """Broadcasts an announcement to all registered bot users when a new product is added"""
    prod = database.get_product_by_id(prod_id)
    if not prod:
        return 0, "Product not found"

    bot = get_bot()
    if not bot:
        return 0, "Bot not initialized"

    name_lower = prod["name"].lower()
    if "gemini" in name_lower:
        badge = "🔵 [NEW • GOOGLE AI]"
    elif "canva" in name_lower:
        badge = "🟢 [NEW • PRO DESIGN]"
    elif "chatgpt" in name_lower or "gpt" in name_lower:
        badge = "🟡 [NEW • OPENAI PLUS]"
    elif "telegram" in name_lower:
        badge = "🟣 [NEW • TELEGRAM PREMIUM]"
    elif "netflix" in name_lower:
        badge = "🔴 [NEW • UHD 4K]"
    elif "capcut" in name_lower:
        badge = "🟠 [NEW • PRO EDITOR]"
    elif "spotify" in name_lower:
        badge = "🟢 [NEW • SPOTIFY PRO]"
    else:
        badge = "✨ [NEW PRO SERVICE ARRIVED]"

    price_val = int(prod['price']) if prod['price'] == int(prod['price']) else prod['price']
    desc = prod.get('short_description') or prod.get('description') or 'Instant activation and full warranty included.'
    if len(desc) > 300:
        desc = desc[:300] + "..."

    msg = (
        f"🔥 <b>NEW SERVICE LAUNCHED ON ABDUREA STORE!</b> 🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{badge}</b>\n"
        f"💎 <b>{prod['name']}</b>\n"
        f"📂 <b>Category:</b> {prod['category']}\n"
        f"⏳ <b>Duration:</b> {prod['duration']}\n"
        f"💵 <b>Price:</b> <b>{price_val} ETB ({price_val} ብር)</b>\n\n"
        f"📝 <b>Description:</b>\n"
        f"{desc}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <i>Available now! Tap below to view or order instantly:</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    # Green Buy Now button (style="bg_success") matching screenshot!
    kb.row(types.InlineKeyboardButton(f"🛒 Buy Now - {price_val} ብር ይግዙ", callback_data=f"buy_{prod['id']}", style="bg_success"))
    kb.row(
        types.InlineKeyboardButton("🛍️ Browse All Products", callback_data="back_catalog"),
        types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
    )

    users = database.get_telegram_users()
    sent_count = 0
    for u in users:
        uid = u["user_id"]
        try:
            m = bot.send_message(uid, msg, reply_markup=kb)
            track_msg(uid, m)
            sent_count += 1
            time.sleep(0.04)  # prevent hitting Telegram rate limits
        except Exception as e:
            logger.warning(f"Could not send new product notification to {uid}: {e}")

    try:
        database.record_broadcast(f"New Service Alert: {prod['name']} ({price_val} ETB)", target="all", sent_count=sent_count)
    except Exception as e:
        logger.warning(f"Could not record broadcast: {e}")

    logger.info(f"New product broadcast for '{prod['name']}' sent to {sent_count} user(s).")
    return sent_count, "Broadcast complete"

def broadcast_new_product_async(prod_id):
    t = threading.Thread(target=notify_new_product_broadcast, args=(prod_id,), daemon=True)
    t.start()

def build_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("🛍️ Browse Products"),
        types.KeyboardButton("📦 My Orders"),
        types.KeyboardButton("💳 Payment Accounts"),
        types.KeyboardButton("📞 Support & Contact"),
        types.KeyboardButton("🧹 Clear History")
    )
    return kb

def build_catalog_keyboard():
    all_products = database.get_products()
    products = [p for p in all_products if p.get("status") in ["active", "on_sale"]]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, p in enumerate(products):
        button_text = get_product_button_badge(p, idx)
        kb.add(types.InlineKeyboardButton(button_text, callback_data=f"prod_{p['id']}"))
    
    # Navigation & Clear History buttons
    kb.row(
        types.InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
        types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
    )
    return kb

def register_handlers(bot):
    logger.info("Registering all Telegram Bot message and callback handlers...")

    @bot.message_handler(commands=['start', 'help'])
    def handle_start(message):
        user = message.from_user
        database.get_or_create_telegram_user(
            user_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "",
            last_name=user.last_name or ""
        )

        telebirr_no = database.get_setting("telebirr_phone", "0992558519")
        telebirr_name = database.get_setting("telebirr_name", "FARHAN")
        cbe_no = database.get_setting("cbe_account", "1000572238575")
        cbe_name = database.get_setting("cbe_name", "FARHAN")

        welcome_text = (
            f"👋 <b>Welcome to Abdurea Pro Store, {user.first_name}!</b>\n\n"
            f"🚀 <i>Your #1 trusted destination for verified Pro & Premium Digital Services in Ethiopia 🇪🇹.</i>\n\n"
            f"<b>Popular Pro Services:</b>\n"
            f"• 🤖 Gemini Advanced Pro\n"
            f"• 🎨 Canva Pro (Team / Lifetime)\n"
            f"• ⚡ ChatGPT Plus (GPT-4o)\n"
            f"• ⭐ Telegram Premium\n"
            f"• 🎬 Netflix UHD 4K & CapCut Pro\n\n"
            f"<b>Accepted Ethiopian Payments:</b>\n"
            f"📱 <b>Telebirr:</b> <code>{telebirr_no}</code> ({telebirr_name})\n"
            f"🏦 <b>CBE Birr:</b> <code>{cbe_no}</code> ({cbe_name})\n\n"
            f"Tap <b>🛍️ Browse Products</b> below to see available plans and pricing!"
        )
        m = bot.send_message(message.chat.id, welcome_text, reply_markup=build_main_keyboard())
        track_msg(message.chat.id, m)

    @bot.message_handler(commands=['clear', 'reset', 'clean'])
    def handle_clear_command(message):
        clear_chat_history(message.chat.id, message.message_id)

    @bot.message_handler(func=lambda m: m.text and any(k in m.text.lower() for k in ["clear", "🧹", "reset", "clean history"]))
    def handle_clear_text_button(message):
        clear_chat_history(message.chat.id, message.message_id)

    @bot.callback_query_handler(func=lambda call: call.data == "clear_chat_history")
    def handle_clear_history_callback(call):
        try:
            bot.answer_callback_query(call.id, text="🧹 Clearing chat history...")
        except Exception:
            pass
        clear_chat_history(call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda call: call.data == "main_menu")
    def handle_main_menu_callback(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        telebirr_no = database.get_setting("telebirr_phone", "0992558519")
        telebirr_name = database.get_setting("telebirr_name", "FARHAN")
        cbe_no = database.get_setting("cbe_account", "1000572238575")
        cbe_name = database.get_setting("cbe_name", "FARHAN")

        text = (
            f"👋 <b>Abdurea Pro Store — Main Menu</b>\n\n"
            f"Accepted Ethiopian Payments:\n"
            f"📱 <b>Telebirr:</b> <code>{telebirr_no}</code> ({telebirr_name})\n"
            f"🏦 <b>CBE Birr:</b> <code>{cbe_no}</code> ({cbe_name})\n\n"
            f"Tap <b>🛍️ Browse Products</b> below to order!"
        )
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=build_catalog_keyboard())
        except Exception:
            m = bot.send_message(call.message.chat.id, text, reply_markup=build_main_keyboard())
            track_msg(call.message.chat.id, m)

    @bot.callback_query_handler(func=lambda call: call.data == "apply_coupon")
    def handle_apply_coupon(call):
        try:
            bot.answer_callback_query(call.id, text="🎟️ You already have the lowest promotional discounted price applied!", show_alert=True)
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda call: call.data == "custom_quantity")
    def handle_custom_quantity(call):
        try:
            bot.answer_callback_query(call.id, text="✏️ For bulk quantity wholesale orders (5+ accounts), please contact admin @trust994 directly for special rates!", show_alert=True)
        except Exception:
            pass

    @bot.message_handler(func=lambda m: m.text and any(k in m.text.lower() for k in ["browse", "product", "service", "🛍️"]))
    def handle_browse_products(message):
        all_products = database.get_products()
        products = [p for p in all_products if p.get("status") in ["active", "on_sale"]]
        if not products:
            m = bot.send_message(message.chat.id, "Currently no services available. Please check back shortly!", reply_markup=build_main_keyboard())
            track_msg(message.chat.id, m)
            return

        legend = (
            "✨ <b>SELECT A PRO SERVICE:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔵 <b>AI Tools</b> • 🟢 <b>Design</b> • 🟡 <b>Premium GPT</b>\n"
            "🟣 <b>Telegram</b> • 🔴 <b>Streaming</b> • 🟠 <b>Editing</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Click any colored button below to view details and buy:</i>"
        )
        m = bot.send_message(message.chat.id, legend, reply_markup=build_catalog_keyboard())
        track_msg(message.chat.id, m)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("prod_"))
    def handle_product_details(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

        try:
            prod_id = int(call.data.replace("prod_", ""))
        except ValueError:
            return

        prod = database.get_product_by_id(prod_id)
        if not prod:
            bot.send_message(call.message.chat.id, "Service not found.")
            return

        name_lower = prod["name"].lower()
        if "gemini" in name_lower:
            color_badge = "🔵 [BLUE • GOOGLE AI]"
        elif "canva" in name_lower:
            color_badge = "🟢 [GREEN • PRO DESIGN]"
        elif "chatgpt" in name_lower or "gpt" in name_lower:
            color_badge = "🟡 [GOLD • OPENAI PLUS]"
        elif "telegram" in name_lower:
            color_badge = "🟣 [PURPLE • TELEGRAM OFFICIAL]"
        elif "netflix" in name_lower:
            color_badge = "🔴 [RED • UHD 4K]"
        elif "capcut" in name_lower:
            color_badge = "🟠 [ORANGE • PRO EDITOR]"
        else:
            color_badge = "✨ [PRO SERVICE]"

        price_val = int(prod['price']) if prod['price'] == int(prod['price']) else prod['price']
        desc = prod.get('description') or prod.get('short_description') or 'Instant activation and full warranty included.'

        # Formatted exactly matching user screenshot media_1789847707682.png
        text = (
            f"<b>{color_badge}</b>\n"
            f"💎 <b>{prod['name']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📂 <b>Category:</b> {prod['category']}\n"
            f"⏳ <b>Duration:</b> {prod['duration']}\n"
            f"💵 <b>Price:</b> <b>{price_val} ETB ({price_val} ብር)</b>\n"
            f"📦 <b>Stock:</b> In Stock ({prod['stock']} available)\n\n"
            f"✅ 100% Private & Official Access\n"
            f"✅ NO NEED ANY CARD\n"
            f"✅ Full Replacement Warranty\n"
            f"🔥 Works in Ethiopia & Worldwide\n\n"
            f"📝 <b>Features & Description:</b>\n{desc}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Select quantity to buy:</b>"
        )

        kb = types.InlineKeyboardMarkup(row_width=2)
        # Row 1: Green Buy Now button (style="bg_success") matching screenshot!
        kb.row(types.InlineKeyboardButton(f"🛒 Buy Now - {price_val} ብር ይግዙ", callback_data=f"buy_{prod['id']}", style="bg_success"))
        
        # Row 2: Apply Coupon (Blue) and Custom Quantity (Red) matching screenshot!
        kb.row(
            types.InlineKeyboardButton("🎁 Apply Coupon", callback_data="apply_coupon", style="bg_primary"),
            types.InlineKeyboardButton("✏️ Custom Quantity", callback_data="custom_quantity", style="bg_danger")
        )

        # Row 3: Back to Products and Main Menu matching screenshot!
        kb.row(
            types.InlineKeyboardButton("↪️ Back to Products", callback_data="back_catalog"),
            types.InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
        )

        # Row 4: Clear History button!
        kb.row(types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history"))

        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
        except Exception:
            m = bot.send_message(call.message.chat.id, text, reply_markup=kb)
            track_msg(call.message.chat.id, m)

    @bot.callback_query_handler(func=lambda call: call.data == "back_catalog")
    def handle_back_catalog(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

        legend = (
            "✨ <b>SELECT A PRO SERVICE:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔵 <b>AI Tools</b> • 🟢 <b>Design</b> • 🟡 <b>Premium GPT</b>\n"
            "🟣 <b>Telegram</b> • 🔴 <b>Streaming</b> • 🟠 <b>Editing</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Click any colored button below to view details and buy:</i>"
        )
        try:
            bot.edit_message_text(legend, call.message.chat.id, call.message.message_id, reply_markup=build_catalog_keyboard())
        except Exception:
            m = bot.send_message(call.message.chat.id, legend, reply_markup=build_catalog_keyboard())
            track_msg(call.message.chat.id, m)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("buy_"))
    def handle_buy_flow(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

        try:
            prod_id = int(call.data.replace("buy_", ""))
        except ValueError:
            return

        prod = database.get_product_by_id(prod_id)
        if not prod:
            bot.send_message(call.message.chat.id, "Product not found!")
            return

        user_id = call.from_user.id
        user_checkout_sessions[user_id] = {
            "step": "choose_payment",
            "product_id": prod_id,
            "product_name": prod["name"],
            "amount": prod["price"]
        }

        telebirr_no = database.get_setting("telebirr_phone", "0992558519")
        cbe_no = database.get_setting("cbe_account", "1000572238575")

        price_val = int(prod['price']) if prod['price'] == int(prod['price']) else prod['price']
        text = (
            f"🛍️ <b>PURCHASE ORDER:</b> {prod['name']}\n"
            f"💰 <b>Total Price:</b> <b>{price_val} ETB ({price_val} ብር)</b>\n\n"
            f"Please choose your preferred Ethiopian payment method:"
        )

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(f"📱 🔵 Pay via Telebirr ({telebirr_no})", callback_data="pay_telebirr", style="bg_primary"),
            types.InlineKeyboardButton(f"🏦 🟣 Pay via CBE Birr ({cbe_no})", callback_data="pay_cbe", style="bg_primary"),
            types.InlineKeyboardButton("❌ Cancel Order", callback_data="cancel_order", style="bg_danger"),
            types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
        )
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
        except Exception:
            m = bot.send_message(call.message.chat.id, text, reply_markup=kb)
            track_msg(call.message.chat.id, m)

    @bot.callback_query_handler(func=lambda call: call.data in ["pay_telebirr", "pay_cbe"])
    def handle_payment_selected(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

        user_id = call.from_user.id
        session = user_checkout_sessions.get(user_id)
        if not session:
            m = bot.send_message(call.message.chat.id, "Session expired. Tap <b>🛍️ Browse Products</b> to choose your service.", reply_markup=build_main_keyboard())
            track_msg(call.message.chat.id, m)
            return

        method = "telebirr" if call.data == "pay_telebirr" else "cbe"
        session["payment_method"] = method
        session["step"] = "awaiting_identity"

        if method == "telebirr":
            acc_num = database.get_setting("telebirr_phone", "0992558519")
            acc_name = database.get_setting("telebirr_name", "FARHAN")
            title = "📱 TELEBIRR PAYMENT"
        else:
            acc_num = database.get_setting("cbe_account", "1000572238575")
            acc_name = database.get_setting("cbe_name", "FARHAN")
            title = "🏦 COMMERCIAL BANK OF ETHIOPIA (CBE)"

        price_val = int(session['amount']) if session['amount'] == int(session['amount']) else session['amount']
        instructions = (
            f"💳 <b>{title}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Item:</b> {session['product_name']}\n"
            f"💵 <b>Transfer Amount:</b> <b>{price_val} ETB ({price_val} ብር)</b>\n\n"
            f"🔢 <b>Account Number:</b> <code>{acc_num}</code> (tap to copy)\n"
            f"👤 <b>Account Name:</b> <b>{acc_name}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>STEP 1 OF 2:</b>\n"
            f"Please reply by typing your <b>Full Name & Phone Number</b> for your order record.\n"
            f"<i>Example: Abebe Kebede 0911223344</i>"
        )
        cancel_kb = types.InlineKeyboardMarkup()
        cancel_kb.row(
            types.InlineKeyboardButton("❌ Cancel Order", callback_data="cancel_order", style="bg_danger"),
            types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
        )
        m = bot.send_message(call.message.chat.id, instructions, reply_markup=cancel_kb)
        track_msg(call.message.chat.id, m)

    @bot.callback_query_handler(func=lambda call: call.data == "cancel_order")
    def handle_cancel(call):
        try:
            bot.answer_callback_query(call.id, "Order cancelled.")
        except Exception:
            pass

        user_id = call.from_user.id
        if user_id in user_checkout_sessions:
            del user_checkout_sessions[user_id]
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("🛍️ Browse Products", callback_data="back_catalog", style="bg_success"),
            types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
        )
        m = bot.send_message(call.message.chat.id, "❌ Order cancelled. You can browse products anytime!", reply_markup=kb)
        track_msg(call.message.chat.id, m)

    @bot.message_handler(func=lambda m: m.text and any(k in m.text.lower() for k in ["my order", "orders", "order", "📦"]))
    def handle_my_orders(message):
        orders = database.get_orders()
        user_orders = [o for o in orders if o["telegram_user_id"] == message.from_user.id]
        if not user_orders:
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton("🛍️ Browse Products", callback_data="back_catalog", style="bg_success"),
                types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
            )
            m = bot.send_message(message.chat.id, "You have not placed any orders yet. Click <b>🛍️ Browse Products</b> to make your first purchase!", reply_markup=kb)
            track_msg(message.chat.id, m)
            return

        text = "📋 <b>YOUR ORDER HISTORY:</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        for o in user_orders[:5]:
            status_badge = "⏳ Pending" if o["status"] == "pending" else ("✅ Confirmed" if o["status"] in ["approved", "confirmed", "completed"] else "❌ Cancelled")
            text += (
                f"🆔 <b>#{o['order_number']}</b> — {o['product_name']}\n"
                f"💰 {o['amount']} ETB | Status: {status_badge}\n"
                f"📅 {o['created_at']}\n"
            )
            if o["status"] in ["approved", "confirmed", "completed"] and o["delivery_credentials"]:
                text += f"🔑 <b>Delivered Credentials:</b> <code>{o['delivery_credentials']}</code>\n"
            elif o["status"] in ["rejected", "cancelled"] and o["rejection_reason"]:
                text += f"❌ <b>Reason:</b> {o['rejection_reason']}\n"
            text += "────────────────────\n"

        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("🛍️ Browse Products", callback_data="back_catalog", style="bg_success"),
            types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
        )
        m = bot.send_message(message.chat.id, text, reply_markup=kb)
        track_msg(message.chat.id, m)

    @bot.message_handler(func=lambda m: m.text and any(k in m.text.lower() for k in ["payment", "account", "telebirr", "cbe", "💳"]))
    def handle_payment_info(message):
        telebirr_no = database.get_setting("telebirr_phone", "0992558519")
        telebirr_name = database.get_setting("telebirr_name", "FARHAN")
        cbe_no = database.get_setting("cbe_account", "1000572238575")
        cbe_name = database.get_setting("cbe_name", "FARHAN")

        text = (
            f"💳 <b>OFFICIAL PAYMENT DETAILS:</b>\n\n"
            f"📱 <b>TELEBIRR:</b>\n"
            f"• Number: <code>{telebirr_no}</code> (tap to copy)\n"
            f"• Account Name: <b>{telebirr_name}</b>\n\n"
            f"🏦 <b>COMMERCIAL BANK OF ETHIOPIA (CBE):</b>\n"
            f"• Account: <code>{cbe_no}</code> (tap to copy)\n"
            f"• Account Name: <b>{cbe_name}</b>\n\n"
            f"<i>Always ensure you transfer to the exact name <b>FARHAN</b> and save your receipt screenshot!</i>"
        )
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("🛍️ Browse Products", callback_data="back_catalog", style="bg_success"),
            types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
        )
        m = bot.send_message(message.chat.id, text, reply_markup=kb)
        track_msg(message.chat.id, m)

    @bot.message_handler(func=lambda m: m.text and any(k in m.text.lower() for k in ["support", "contact", "help", "📞"]))
    def handle_support(message):
        admin_tg = database.get_setting("admin_telegram", "@trust994")
        phone = database.get_setting("support_phone", "0992558519")

        text = (
            f"💬 <b>CUSTOMER SUPPORT:</b>\n\n"
            f"Need help with an order, service activation, or custom subscription?\n\n"
            f"👤 <b>Admin Telegram:</b> {admin_tg}\n"
            f"📱 <b>Direct Phone:</b> {phone}\n"
            f"🌐 <b>Website:</b> https://abdureastore.base44.app/\n\n"
            f"<i>We are available 24/7 to assist you!</i>"
        )
        kb = types.InlineKeyboardMarkup()
        clean_handle = admin_tg.replace('@', '')
        kb.row(
            types.InlineKeyboardButton("💬 Chat with Admin", url=f"https://t.me/{clean_handle}", style="bg_primary"),
            types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
        )
        m = bot.send_message(message.chat.id, text, reply_markup=kb)
        track_msg(message.chat.id, m)

    # Step 1: User typing their identity (Name & Phone)
    @bot.message_handler(func=lambda m: m.chat.id in user_checkout_sessions and user_checkout_sessions[m.chat.id].get("step") == "awaiting_identity")
    def handle_identity_input(message):
        user_id = message.chat.id
        identity_text = message.text.strip()
        if len(identity_text) < 2:
            bot.send_message(user_id, "Please enter your name and phone number:")
            return

        session = user_checkout_sessions[user_id]
        session["buyer_name"] = identity_text
        session["buyer_phone"] = identity_text
        session["step"] = "awaiting_receipt"

        price_val = int(session['amount']) if session['amount'] == int(session['amount']) else session['amount']
        msg = (
            f"✅ Thank you <b>{identity_text}</b>!\n\n"
            f"📸 <b>FINAL STEP 2 OF 2:</b>\n"
            f"Please transfer <b>{price_val} ETB ({price_val} ብር)</b>, then <b>upload the screenshot of your Bank Payment Receipt</b> (as a Photo or Document) right here in this chat.\n\n"
            f"<i>Or type your transaction SMS reference number if you don't have a screenshot.</i>"
        )
        cancel_kb = types.InlineKeyboardMarkup()
        cancel_kb.row(
            types.InlineKeyboardButton("❌ Cancel Order", callback_data="cancel_order", style="bg_danger"),
            types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
        )
        m = bot.send_message(user_id, msg, reply_markup=cancel_kb)
        track_msg(user_id, m)

    # Step 2: Upload photo receipt
    @bot.message_handler(content_types=['photo', 'document'], func=lambda m: m.chat.id in user_checkout_sessions and user_checkout_sessions[m.chat.id].get("step") == "awaiting_receipt")
    def handle_receipt_upload(message):
        user_id = message.chat.id
        session = user_checkout_sessions.get(user_id)
        if not session:
            return

        try:
            bot.send_chat_action(user_id, "upload_document")
            
            file_id = None
            if message.photo:
                file_id = message.photo[-1].file_id
            elif message.document:
                file_id = message.document.file_id

            filename = f"receipt_{user_id}_{int(time.time())}.jpg"
            save_path = RECEIPTS_FOLDER / filename

            if file_id:
                file_info = bot.get_file(file_id)
                downloaded_file = bot.download_file(file_info.file_path)
                with open(save_path, "wb") as f:
                    f.write(downloaded_file)

            user = message.from_user
            order_num = database.create_order(
                telegram_user_id=user.id,
                buyer_name=session.get("buyer_name", user.first_name or "Telegram User"),
                buyer_username=user.username or "",
                buyer_phone=session.get("buyer_phone", ""),
                product_id=session["product_id"],
                amount=session["amount"],
                payment_method=session["payment_method"],
                receipt_image=filename,
                transaction_ref=f"TXN-{int(time.time()) % 100000}"
            )

            del user_checkout_sessions[user_id]

            price_val = int(session['amount']) if session['amount'] == int(session['amount']) else session['amount']
            confirmation = (
                f"🎉 <b>ORDER RECEIVED SUCCESSFULLY!</b>\n\n"
                f"🆔 <b>Order Number:</b> <code>#{order_num}</code>\n"
                f"📦 <b>Service:</b> {session['product_name']}\n"
                f"💰 <b>Amount:</b> {price_val} ETB ({price_val} ብር)\n"
                f"💳 <b>Payment:</b> {session['payment_method'].upper()}\n"
                f"⏳ <b>Status:</b> <b>Pending Admin Verification</b>\n\n"
                f"Our admin team (@trust994) has received your receipt and is verifying the transaction.\n"
                f"Once confirmed, your pro access credentials will be delivered right here in this chat!\n\n"
                f"Tap <b>📦 My Orders</b> anytime to check status."
            )
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton("📦 My Orders", callback_data="my_orders"),
                types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
            )
            m = bot.send_message(user_id, confirmation, reply_markup=kb)
            track_msg(user_id, m)

        except Exception as e:
            logger.error(f"Error handling receipt upload: {e}")
            bot.send_message(user_id, "There was an error saving your receipt. Please try uploading again.")

    # Step 2 alternative: Text transaction reference
    @bot.message_handler(func=lambda m: m.chat.id in user_checkout_sessions and user_checkout_sessions[m.chat.id].get("step") == "awaiting_receipt")
    def handle_text_receipt_reference(message):
        user_id = message.chat.id
        session = user_checkout_sessions.get(user_id)
        if not session:
            return

        txn_text = message.text.strip()
        user = message.from_user
        order_num = database.create_order(
            telegram_user_id=user.id,
            buyer_name=session.get("buyer_name", user.first_name or "Telegram User"),
            buyer_username=user.username or "",
            buyer_phone=session.get("buyer_phone", ""),
            product_id=session["product_id"],
            amount=session["amount"],
            payment_method=session["payment_method"],
            receipt_image="",
            transaction_ref=txn_text
        )

        del user_checkout_sessions[user_id]

        price_val = int(session['amount']) if session['amount'] == int(session['amount']) else session['amount']
        confirmation = (
            f"🎉 <b>ORDER RECEIVED! (Txn Ref: {txn_text})</b>\n\n"
            f"🆔 <b>Order Number:</b> <code>#{order_num}</code>\n"
            f"📦 <b>Service:</b> {session['product_name']}\n"
            f"💰 <b>Amount:</b> {price_val} ETB ({price_val} ብር)\n"
            f"⏳ <b>Status:</b> <b>Pending Admin Verification</b>\n\n"
            f"Our admin team is verifying your payment. Once approved, your credentials will be sent directly here!"
        )
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("📦 My Orders", callback_data="my_orders"),
            types.InlineKeyboardButton("🧹 Clear History", callback_data="clear_chat_history")
        )
        m = bot.send_message(user_id, confirmation, reply_markup=kb)
        track_msg(user_id, m)

    # Fallback callback handler so no button hangs
    @bot.callback_query_handler(func=lambda call: True)
    def fallback_callback(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

def start_bot_polling():
    global bot_running
    bot_running = True
    last_unauth_warning = 0

    while bot_running:
        token = database.get_setting("bot_token", "").strip() or os.getenv("BOT_TOKEN", "").strip() or os.getenv("MAIN_BOT_TOKEN", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            current_time = time.time()
            if current_time - last_unauth_warning > 30:
                logger.warning("Bot token not configured. Open Admin Dashboard (http://localhost:5000/settings) to set your BOT_TOKEN from @BotFather.")
                last_unauth_warning = current_time
            time.sleep(5)
            continue

        bot = get_bot()
        if not bot:
            time.sleep(5)
            continue

        try:
            logger.info("Connecting Telegram Bot Polling (Infinity Loop)...")
            try:
                bot.delete_webhook(drop_pending_updates=True)
            except Exception:
                pass
            bot.infinity_polling(timeout=20, long_polling_timeout=15, skip_pending=True)
        except Exception as e:
            if "401" in str(e) or "Unauthorized" in str(e):
                current_time = time.time()
                if current_time - last_unauth_warning > 30:
                    logger.warning("=" * 60)
                    logger.warning("[!] Telegram API returned 401 Unauthorized for the current Bot Token.")
                    logger.warning("    Please create or copy a fresh token from @BotFather in Telegram,")
                    logger.warning("    then save it in the Admin Dashboard at: http://localhost:5000/settings")
                    logger.warning("=" * 60)
                    last_unauth_warning = current_time
                time.sleep(10)
            else:
                logger.error(f"Bot polling error: {e}. Retrying in 5 seconds...")
                time.sleep(5)


def run_in_background():
    global bot_thread, bot_running
    if bot_thread and bot_thread.is_alive():
        logger.info("Bot background thread already active.")
        return
    bot_running = True
    bot_thread = threading.Thread(target=start_bot_polling, daemon=True)
    bot_thread.start()

