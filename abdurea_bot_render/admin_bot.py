# -*- coding: utf-8 -*-
"""
Dedicated Telegram Admin Bot for ABDUREA STORE.
100% Feature Parity with Web Admin Dashboard:
- 📊 Financial Analytics & KPIs (Today, 7D, Month, Year, All-Time, Payment Breakdown, Top Products)
- ⏳ Review Queue & 📦 Order Management (Filter, Search, Photo Receipts, 1-Click Approve, Reject with Reason, Deliver Credentials)
- 🛍️ Product Catalog (Add Product Wizard, Edit Plan Pricing, Toggle Active/Inactive, Delete)
- 👥 Customer Management (Directory, Search, Profiles, Spend/Orders History, Direct Messaging)
- 📢 Broadcast Announcements (Target All, Completed, Pending, Preview & Progress Counter, History)
- ⚙️ Payment & Store Settings (Telebirr, CBE, Support Username, Store Name live editing)
- 📜 Audit Activity Logs
- 🔒 Strict Admin-Only Access (Admin ID: 6666443909 / @trust994)
"""
import os
import re
import io
import json
import html
import base64
import logging
import asyncio
import secrets
from pathlib import Path
from datetime import datetime
from PIL import Image

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    BotCommand,
    WebAppInfo
)
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

import config
import models
import bot_bridge
try:
    import miniapp_api
except ImportError:
    miniapp_api = None

logger = logging.getLogger("admin_bot")

def process_image_to_base64(raw_bytes: bytes, max_size=(800, 800), quality=85) -> str:
    """
    Compresses and converts raw image bytes into a persistent base64 data URI (data:image/jpeg;base64,...).
    Keeps file size minimal (~30-70KB) and guarantees 100% permanence across Render container restarts.
    """
    try:
        bio = io.BytesIO(raw_bytes)
        im = Image.open(bio)
        if im.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            if im.mode == "P":
                im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")

        im.thumbnail(max_size, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(out.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.warning(f"Image compression fallback: {e}")
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"

class AdminStateManager:
    """
    Hybrid fast-memory & persistent-SQLite admin state manager.
    Guarantees state is NEVER lost across container restarts, thread boundaries, or redeployments.
    """
    def __init__(self):
        self._cache = {}

    def get(self, admin_id, default=None):
        if admin_id in self._cache and self._cache[admin_id]:
            return self._cache[admin_id]
        persisted = models.get_admin_state(admin_id)
        if persisted:
            self._cache[admin_id] = persisted
            return persisted
        return default

    def set(self, admin_id, state_info):
        self._cache[admin_id] = state_info
        if state_info and isinstance(state_info, dict):
            models.save_admin_state(admin_id, state_info.get("state", ""), state_info.get("data", {}))
        else:
            models.clear_admin_state(admin_id)

    def clear(self, admin_id):
        self._cache.pop(admin_id, None)
        models.clear_admin_state(admin_id)

    def __getitem__(self, admin_id):
        res = self.get(admin_id)
        if res is None:
            raise KeyError(admin_id)
        return res

    def __setitem__(self, admin_id, value):
        self.set(admin_id, value)

    def __contains__(self, admin_id):
        return self.get(admin_id) is not None

ADMIN_STATES = AdminStateManager()

def is_admin(user_id: int, username: str = None) -> bool:
    """Strict authorization check for authorized admin IDs and usernames."""
    allowed_ids = getattr(config, "ADMIN_TELEGRAM_IDS", [6666443909, 2145055956])
    if user_id in allowed_ids or user_id == getattr(config, "ADMIN_TELEGRAM_ID", 6666443909):
        return True
    allowed_usernames = ["trust994", "hamzu3", getattr(config, "ADMIN_TELEGRAM_USERNAME", "@trust994").lstrip("@").lower()]
    if username and username.lower().lstrip("@") in allowed_usernames:
        return True
    return False

def get_auth_denied_message():
    return (
        "⛔ <b>Access Denied</b>\n\n"
        "This bot is strictly private and restricted to authorized <b>ABDUREA STORE</b> administrators.\n"
        "If you are a customer, please use our main customer bot: @AbdureaStoreBot"
    )

async def safe_edit_or_reply(query, text, reply_markup=None, parse_mode="HTML"):
    """Edits message or replies if edit fails with instant resilient fallback."""
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return
    except Exception as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return
        if "no text in the message" in err:
            try:
                await query.edit_message_caption(caption=text[:1024], parse_mode=parse_mode, reply_markup=reply_markup)
                return
            except Exception:
                pass
        try:
            await query.message.reply_html(text, reply_markup=reply_markup)
            return
        except Exception:
            pass

    # High-reliability fallback via persistent bot_bridge
    try:
        rm = reply_markup.to_dict() if hasattr(reply_markup, "to_dict") else reply_markup
        bot_bridge.send_admin_telegram_message(text, reply_markup=rm)
    except Exception as ex:
        logger.error(f"Fallback send failed: {ex}")

# =====================================================================
# 1. MAIN DASHBOARD / HUB
# =====================================================================

def build_dashboard_text(admin_user=None):
    kpis = models.get_dashboard_kpis()
    tot_rev = kpis.get("total_revenue", 0)
    rev_today = kpis.get("revenue_today", 0)
    rev_month = kpis.get("revenue_month", 0)
    
    tot_orders = kpis.get("total_orders", 0)
    pending = kpis.get("payments_under_review", 0)
    completed = kpis.get("completed_orders", 0)
    rejected = kpis.get("rejected_orders", 0)
    
    customers = kpis.get("total_customers", 0)
    active_prods = kpis.get("active_products", 0)
    
    if admin_user:
        admin_handle = f"@{admin_user.username}" if getattr(admin_user, "username", None) else (getattr(admin_user, "first_name", "") or "Admin")
        admin_id_val = getattr(admin_user, "id", config.ADMIN_TELEGRAM_ID)
    else:
        admin_handle = config.ADMIN_TELEGRAM_USERNAME
        admin_id_val = config.ADMIN_TELEGRAM_ID

    text = (
        f"👑 <b>{config.STORE_NAME} — ADMIN CONTROL HUB</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Admin:</b> {admin_handle} (ID: <code>{admin_id_val}</code>)\n"
        f"🕒 <b>Server Time:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"💰 <b>FINANCIAL SNAPSHOT</b>\n"
        f"• Total Revenue: <b>{tot_rev:,.0f} ETB</b>\n"
        f"• Month's Revenue: <b>{rev_month:,.0f} ETB</b>\n"
        f"• Today's Revenue: <b>{rev_today:,.0f} ETB</b>\n\n"
        f"📦 <b>ORDERS OVERVIEW</b>\n"
        f"• Total Orders: <b>{tot_orders}</b>\n"
        f"• ⏳ <b>Pending Review: {pending}</b>\n"
        f"• 🟢 Completed: <b>{completed}</b>\n"
        f"• 🔴 Rejected: <b>{rejected}</b>\n\n"
        f"👥 Customers: <b>{customers}</b> | 🛍️ Active Services: <b>{active_prods}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <i>Select a management module below:</i>"
    )
    return text, pending

def build_main_dashboard_keyboard(pending_count=0):
    pending_label = f"⏳ Review Queue ({pending_count})" if pending_count > 0 else "⏳ Review Queue"
    review_kwargs = {"api_kwargs": {"style": "danger"}} if pending_count > 0 else {}
    miniapp_url = getattr(config, "MINIAPP_URL", "") or "https://abdureastore.base44.app/miniapp"
    keyboard = [
        [
            InlineKeyboardButton("🛍️ Open Mini App", web_app=WebAppInfo(url=miniapp_url)),
            InlineKeyboardButton("📱 Mini App Controls", callback_data="adm_miniapp_hub")
        ],
        [
            InlineKeyboardButton("📊 Financial Analytics", callback_data="adm_analytics_all"),
            InlineKeyboardButton(pending_label, callback_data="adm_orders_pending_0", **review_kwargs)
        ],
        [
            InlineKeyboardButton("📦 All Orders", callback_data="adm_orders_all_0"),
            InlineKeyboardButton("🛍️ Product Catalog", callback_data="adm_products_list")
        ],
        [
            InlineKeyboardButton("👥 Customer Directory", callback_data="adm_customers_0"),
            InlineKeyboardButton("📢 Broadcast Message", callback_data="adm_broadcast_menu")
        ],
        [
            InlineKeyboardButton("⚙️ Payment & Settings", callback_data="adm_settings_view"),
            InlineKeyboardButton("🎨 Button Colors", callback_data="adm_btn_colors")
        ],
        [
            InlineKeyboardButton("📜 Activity Logs", callback_data="adm_logs_view"),
            InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="adm_hub", api_kwargs={"style": "primary"})
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_miniapp_hub_view():
    miniapp_url = getattr(config, "MINIAPP_URL", "") or "https://abdureastore.base44.app/miniapp"
    maint_mode = models.get_setting("miniapp_maintenance_mode", "0") in ("1", "true", "True")
    banner_text = models.get_setting("miniapp_banner_text", "")
    banner_active = models.get_setting("miniapp_banner_active", "0") in ("1", "true", "True")
    
    all_prods = models.get_all_products(include_inactive=True)
    active_prods = [p for p in all_prods if p.get("active")]
    
    maint_badge = "🔴 ACTIVE (Store in Maintenance)" if maint_mode else "🟢 OFF (Store Live & Operational)"
    banner_badge = f"🟢 Active: <i>\"{banner_text[:40]}...\"</i>" if (banner_active and banner_text) else "⚪ Inactive / None"
    
    text = (
        f"📱 <b>ABDUREA STORE — MINI APP CONTROL HUB</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Mini App Live URL:</b>\n<code>{miniapp_url}</code>\n\n"
        f"📊 <b>LIVE STORE STATS:</b>\n"
        f"• Active Services: <b>{len(active_prods)}</b> (Total: {len(all_prods)})\n"
        f"• Source of Truth: <b>abdureastore.base44.app</b> (Live Sync)\n\n"
        f"⚙️ <b>MINI APP SETTINGS:</b>\n"
        f"• <b>Maintenance Mode:</b> {maint_badge}\n"
        f"• <b>Promo Banner:</b> {banner_badge}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <i>Select an action to control your Mini App:</i>"
    )
    
    maint_btn_label = "🟢 Disable Maintenance Mode" if maint_mode else "🛑 Enable Maintenance Mode"
    
    keyboard = [
        [
            InlineKeyboardButton("🛍️ Open Mini App (Live View)", web_app=WebAppInfo(url=miniapp_url)),
            InlineKeyboardButton("🔄 Sync Base44 with Mini App", callback_data="adm_miniapp_sync_now")
        ],
        [
            InlineKeyboardButton("📢 Edit Announcement Banner", callback_data="adm_miniapp_edit_banner"),
            InlineKeyboardButton(maint_btn_label, callback_data="adm_miniapp_toggle_maint")
        ],
        [
            InlineKeyboardButton("🔗 Change Mini App URL", callback_data="adm_miniapp_edit_url"),
            InlineKeyboardButton("🗑️ Clear Banner", callback_data="adm_miniapp_clear_banner")
        ],
        [
            InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

async def start_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start or /admin command for admin bot."""
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return

    ADMIN_STATES[user.id] = None  # Reset any pending input
    text, pending = build_dashboard_text(user)
    reply_markup = build_main_dashboard_keyboard(pending)
    try:
        await update.message.reply_html(text, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"reply_html timed out or failed in start_admin: {e}. Using fast bridge fallback.")
        try:
            bot_bridge.send_admin_telegram_message(text, reply_markup=reply_markup.to_dict())
        except Exception as ex:
            logger.error(f"Fallback bridge also failed: {ex}")

# =====================================================================
# 2. FINANCIAL ANALYTICS & REPORTS
# =====================================================================

def build_analytics_view(time_range="all"):
    kpis = models.get_dashboard_kpis()
    reports = models.get_market_reports(time_range=time_range if time_range != "all" else "this_year")
    summary = reports.get("summary", {})
    
    range_titles = {
        "today": "📅 Today's Performance",
        "7d": "📅 Last 7 Days Performance",
        "this_month": "📅 This Month's Performance",
        "this_year": "📅 This Year's Performance",
        "all": "📅 All-Time Performance"
    }
    title = range_titles.get(time_range, "Performance Overview")
    
    period_rev = summary.get("period_revenue", kpis.get("total_revenue", 0))
    tot_orders = summary.get("total_orders", kpis.get("total_orders", 0))
    completed = summary.get("completed_orders", kpis.get("completed_orders", 0))
    rejected = summary.get("rejected_orders", kpis.get("rejected_orders", 0))
    aov = summary.get("average_order_value", 0)
    
    text = (
        f"📊 <b>FINANCIAL & STORE ANALYTICS</b>\n"
        f"<b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Revenue:</b> <b>{period_rev:,.0f} ETB</b>\n"
        f"📦 <b>Orders Placed:</b> {tot_orders}\n"
        f"✅ <b>Orders Completed:</b> {completed}\n"
        f"❌ <b>Orders Rejected:</b> {rejected}\n"
        f"📈 <b>Average Order Value (AOV):</b> {aov:,.2f} ETB\n\n"
    )
    
    # Payment Methods Breakdown
    pm_list = reports.get("payment_methods", [])
    if pm_list:
        text += "💳 <b>Payment Methods Breakdown:</b>\n"
        for pm in pm_list:
            text += f"• <b>{pm.get('method', 'Other')}:</b> {pm.get('count', 0)} orders ({pm.get('total_revenue', 0):,.0f} ETB)\n"
        text += "\n"
        
    # Top Products
    top_prods = reports.get("top_products", [])
    if top_prods:
        text += "🏆 <b>Top Selling Services:</b>\n"
        for idx, tp in enumerate(top_prods[:5], 1):
            text += f"{idx}. <b>{tp.get('product_name')}</b>: {tp.get('total_sales')} sales ({tp.get('total_revenue', 0):,.0f} ETB)\n"
        text += "\n"
        
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n👇 <i>Filter by timeframe:</i>"
    
    keyboard = [
        [
            InlineKeyboardButton("Today", callback_data="adm_analytics_today"),
            InlineKeyboardButton("7 Days", callback_data="adm_analytics_7d"),
            InlineKeyboardButton("This Month", callback_data="adm_analytics_this_month")
        ],
        [
            InlineKeyboardButton("This Year", callback_data="adm_analytics_this_year"),
            InlineKeyboardButton("All-Time", callback_data="adm_analytics_all")
        ],
        [
            InlineKeyboardButton("🔙 Back to Main Dashboard", callback_data="adm_hub")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

# =====================================================================
# 3. ORDER MANAGEMENT & 1-CLICK VERIFICATION
# =====================================================================

PAGE_SIZE = 5

def build_orders_list_view(filter_type="all", offset=0):
    status_filter = None
    if filter_type == "pending":
        status_filter = "UNDER_REVIEW"
    elif filter_type == "completed":
        status_filter = "COMPLETED"
    elif filter_type == "rejected":
        status_filter = "REJECTED"
    elif filter_type == "approved":
        status_filter = "ACCEPTED"
        
    orders = models.get_orders_list(status_filter=status_filter or "all", limit=PAGE_SIZE + 1, offset=offset)
    has_next = len(orders) > PAGE_SIZE
    current_orders = orders[:PAGE_SIZE]
    
    titles = {
        "pending": "⏳ PENDING REVIEW ORDERS",
        "approved": "🔵 APPROVED / PROCESSING ORDERS",
        "completed": "🟢 COMPLETED ORDERS",
        "rejected": "🔴 REJECTED ORDERS",
        "all": "📦 ALL ORDERS DIRECTORY"
    }
    title = titles.get(filter_type, "ORDERS")
    page_num = (offset // PAGE_SIZE) + 1
    
    text = (
        f"<b>{title}</b> (Page {page_num})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    
    if not current_orders:
        text += "<i>No orders found in this view.</i>\n\n"
    else:
        text += f"Showing {len(current_orders)} order(s):\n\n"
        
    keyboard = []
    
    status_icons = {
        "PAYMENT_SUBMITTED": "🟠 REVIEW",
        "UNDER_REVIEW": "🟠 REVIEW",
        "PAYMENT_REVIEW": "🟠 REVIEW",
        "PAYMENT_APPROVED": "🔵 APPROVED",
        "ACCEPTED": "🔵 APPROVED",
        "PROCESSING": "🔵 PROCESSING",
        "COMPLETED": "🟢 COMPLETED",
        "REJECTED": "🔴 REJECTED",
        "PAYMENT_REJECTED": "🔴 REJECTED",
        "NEW": "🟡 NEW",
        "AWAITING_PAYMENT": "🟡 AWAITING"
    }
    
    for o in current_orders:
        st_badge = status_icons.get(o.get("status"), o.get("status", ""))
        amt = o.get("amount", 0)
        btn_text = f"#{o.get('order_number')} • {o.get('product_name')[:14]} • {amt:,.0f} ETB • {st_badge}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"adm_view_order_{o['id']}")])
        
    # Navigation row
    nav_row = []
    if offset > 0:
        prev_off = max(0, offset - PAGE_SIZE)
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_orders_{filter_type}_{prev_off}"))
    if has_next:
        next_off = offset + PAGE_SIZE
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"adm_orders_{filter_type}_{next_off}"))
        
    if nav_row:
        keyboard.append(nav_row)
        
    # Filter switchers
    keyboard.append([
        InlineKeyboardButton("⏳ Pending", callback_data="adm_orders_pending_0"),
        InlineKeyboardButton("🔵 Approved", callback_data="adm_orders_approved_0"),
        InlineKeyboardButton("🟢 Done", callback_data="adm_orders_completed_0")
    ])
    keyboard.append([
        InlineKeyboardButton("🔴 Rejected", callback_data="adm_orders_rejected_0"),
        InlineKeyboardButton("📋 All", callback_data="adm_orders_all_0"),
        InlineKeyboardButton("🔍 Search #", callback_data="adm_order_search_prompt")
    ])
    keyboard.append([InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")])
    
    return text, InlineKeyboardMarkup(keyboard)

def parse_and_sync_order_from_text(text, telegram_file_id=None):
    """
    Reconstructs and syncs an order into the local store.db from a Telegram notification text/caption.
    Guarantees that orders submitted via Mini App in remote environments (like Vercel)
    can be viewed and managed with 100% detail in the Admin Bot without 'Order not found' errors.
    """
    if not text:
        return None

    # 1. Order Number: e.g. #ABD-20261006-001 or #ABD-TEST-... or #ABD-...
    num_m = re.search(r"#(ABD-[A-Za-z0-9\-_]+|[A-Za-z0-9\-_]{6,})", text)
    if not num_m:
        return None
    order_number = num_m.group(1).strip()

    # 2. Service & Plan
    svc_m = re.search(r"Service:</b>\s*([^\n]+)", text) or re.search(r"Service:\s*([^\n]+)", text)
    if svc_m:
        raw_svc = re.sub(r"<[^>]+>", "", svc_m.group(1)).strip()
        plan_m = re.search(r"\((.*?)\)$", raw_svc)
        if plan_m:
            plan_duration = plan_m.group(1).strip() or "Standard"
            product_name = re.sub(r"\(.*?\)$", "", raw_svc).strip()
        else:
            product_name = raw_svc
            plan_duration = "Standard"
    else:
        product_name = "Digital Service"
        plan_duration = "Standard"

    # 3. Amount & Currency
    amt_m = re.search(r"Amount:</b>\s*([^\n]+)", text) or re.search(r"Amount:\s*([^\n]+)", text)
    if amt_m:
        raw_amt = re.sub(r"<[^>]+>", "", amt_m.group(1)).strip()
        parts = raw_amt.split()
        try:
            amount = float(parts[0].replace(",", "")) if parts else 0.0
        except Exception:
            amount = 0.0
        currency = parts[1] if len(parts) > 1 else "ETB"
    else:
        amount = 0.0
        currency = "ETB"

    # 4. Payment Method
    pm_m = re.search(r"Payment Method:</b>\s*([^\n]+)", text) or re.search(r"Payment Method:\s*([^\n]+)", text)
    payment_method = re.sub(r"<[^>]+>", "", pm_m.group(1)).strip() if pm_m else "Telebirr"

    # 5. Customer Name & Username
    cust_m = re.search(r"Customer:</b>\s*([^\n]+)", text) or re.search(r"Customer:\s*([^\n]+)", text)
    if cust_m:
        raw_cust = re.sub(r"<[^>]+>", "", cust_m.group(1)).strip()
        raw_cust = re.sub(r"⭐.*", "", raw_cust).strip()
        user_m = re.search(r"\(@([A-Za-z0-9_]+)\)", raw_cust)
        if user_m:
            customer_username = user_m.group(1).strip()
            customer_name = re.sub(r"\(@[A-Za-z0-9_]+\)", "", raw_cust).strip()
        else:
            customer_username = None
            customer_name = raw_cust
        if customer_username == "none":
            customer_username = None
    else:
        customer_name = "Customer"
        customer_username = None

    # 6. Telegram ID
    tg_m = re.search(r"Telegram ID:.*?(\d{5,})", text)
    telegram_id = int(tg_m.group(1)) if tg_m else 0

    # 7. Date
    dt_m = re.search(r"Date:</b>\s*([^\n]+)", text) or re.search(r"Date:\s*([^\n]+)", text)
    created_at = re.sub(r"<[^>]+>", "", dt_m.group(1)).strip() if dt_m else None

    logger.info(f"Auto-syncing external order #{order_number} for customer {customer_name} ({telegram_id}) into store.db...")
    return models.sync_external_order(
        order_number=order_number,
        telegram_id=telegram_id,
        product_name=product_name,
        plan_duration=plan_duration,
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        customer_name=customer_name,
        customer_username=customer_username,
        telegram_file_id=telegram_file_id,
        created_at=created_at
    )

def resolve_order(order_ref, query=None):
    """
    Resolves order whether referenced by integer ID, order number string (e.g. ABD-...),
    or through auto-reconstruction from the Telegram message text/caption.
    Ensures the Admin Bot NEVER fails with 'Order not found' on valid orders.
    """
    ref_str = str(order_ref).strip() if order_ref is not None else ""
    order = None

    # Step 1: If ref_str is formatted like an order number (e.g. ABD-...)
    if ref_str and ("-" in ref_str or ref_str.upper().startswith("ABD")):
        order = models.get_order_by_number(ref_str)
        if order:
            return order

    # Step 2: If ref_str is numeric ID
    if ref_str.isdigit():
        order = models.get_order_by_id(int(ref_str))
        # Prevent ID collision if message caption explicitly has an order number
        if order and query and query.message:
            msg_text = (query.message.caption or "") or (query.message.text or "")
            num_m = re.search(r"#(ABD-[A-Za-z0-9\-_]+|[A-Za-z0-9\-_]{6,})", msg_text)
            if num_m and order.get("order_number") != num_m.group(1).strip():
                matched = models.get_order_by_number(num_m.group(1).strip())
                if matched:
                    return matched
                order = None
        if order:
            return order

    # Step 3: Direct lookup by order number
    if ref_str:
        order = models.get_order_by_number(ref_str)
        if order:
            return order

    # Step 4: Auto-reconstruct from query message caption or text
    if query and query.message:
        msg = query.message
        msg_text = (msg.caption or "") or (msg.text or "")
        photo_fid = msg.photo[-1].file_id if msg.photo else None
        order = parse_and_sync_order_from_text(msg_text, telegram_file_id=photo_fid)
        if order:
            return order

    return order

def build_order_details_view(order_ref, query=None):
    order = resolve_order(order_ref, query)
    if not order:
        return "❌ Order not found.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_orders_all_0")]])
        
    order_id = order["id"]
    payment = order.get("payment") or {}
    delivery = order.get("delivery") or {}
    
    status = order.get("status", "UNKNOWN")
    amt = order.get("amount", 0)
    curr = order.get("currency", "ETB")
    
    user_rec = models.get_user_by_telegram_id(order.get("telegram_id"))
    is_prem = bool(user_rec.get("is_premium")) if user_rec else False
    if "VIP Telegram Premium" in (order.get("customer_notes") or ""):
        is_prem = True
    prem_tag = " ⭐ <b>[VIP TELEGRAM PREMIUM]</b>" if is_prem else ""

    order_num = html.escape(str(order.get('order_number') or ''))
    prod_name = html.escape(str(order.get('product_name') or ''))
    plan_dur = html.escape(str(order.get('plan_duration') or 'Standard'))
    pay_meth = html.escape(str(order.get('payment_method') or ''))
    cust_name = html.escape(str(order.get('customer_name') or 'Customer'))
    cust_user = html.escape(str(order.get('customer_username') or 'none'))
    cust_phone = html.escape(str(order.get('customer_phone') or 'Not provided'))
    cust_notes = html.escape(str(order.get('customer_notes') or 'None'))

    text = (
        f"📦 <b>ORDER DETAILS — #{order_num}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ <b>Status:</b> <b>{status}</b>\n"
        f"📅 <b>Date:</b> {order.get('created_at', '')[:19]}\n\n"
        f"🛍️ <b>Service:</b> {prod_name}\n"
        f"⏳ <b>Plan/Duration:</b> {plan_dur}\n"
        f"💰 <b>Total Amount:</b> <b>{amt:,.0f} {curr}</b>\n"
        f"💳 <b>Payment Method:</b> {pay_meth}\n\n"
        f"👤 <b>CUSTOMER DETAILS:</b>\n"
        f"• Name: {cust_name}{prem_tag}\n"
        f"• Username: @{cust_user}\n"
        f"• Telegram ID: <code>{order.get('telegram_id')}</code>\n"
        f"• Phone: {cust_phone}\n"
        f"• Delivery Notes: {cust_notes}\n\n"
    )
    
    if payment:
        has_receipt = bool(payment.get("receipt_file") or payment.get("telegram_file_id") or (query and query.message and query.message.photo))
        sender_name = html.escape(str(payment.get('payment_sender_name') or 'N/A'))
        rev_status = html.escape(str(payment.get('review_status') or 'N/A'))
        text += (
            f"🧾 <b>PAYMENT DETAILS:</b>\n"
            f"• Sender Name: {sender_name}\n"
            f"• Review Status: {rev_status}\n"
            f"• Receipt: {'Uploaded' if has_receipt else 'N/A'}\n"
        )
        if payment.get("admin_note"):
            text += f"• Admin Note: <i>{html.escape(str(payment.get('admin_note')))}</i>\n"
        text += "\n"
        
    if delivery:
        text += (
            f"🚀 <b>DELIVERED CREDENTIALS:</b>\n"
            f"<code>{html.escape(str(delivery.get('content', '')))}</code>\n\n"
        )
        
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚡ <i>Admin Actions:</i>"
    
    keyboard = []
    
    # Action buttons based on status
    if status in ("PAYMENT_SUBMITTED", "UNDER_REVIEW", "PAYMENT_REVIEW", "NEW", "AWAITING_PAYMENT"):
        keyboard.append([
            InlineKeyboardButton("✅ 1-Click Approve", callback_data=f"adm_quick_approve_{order_id}", api_kwargs={"style": "success"}),
            InlineKeyboardButton("❌ Reject Payment", callback_data=f"adm_prompt_reject_{order_id}", api_kwargs={"style": "danger"})
        ])
        keyboard.append([
            InlineKeyboardButton("📦 Deliver Credentials", callback_data=f"adm_prompt_deliver_{order_id}", api_kwargs={"style": "primary"})
        ])
    elif status in ("PAYMENT_APPROVED", "PROCESSING", "ACCEPTED"):
        keyboard.append([
            InlineKeyboardButton("📦 Deliver Credentials", callback_data=f"adm_prompt_deliver_{order_id}", api_kwargs={"style": "primary"}),
            InlineKeyboardButton("❌ Reject Order", callback_data=f"adm_prompt_reject_{order_id}", api_kwargs={"style": "danger"})
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("📦 Re-Deliver / Update", callback_data=f"adm_prompt_deliver_{order_id}", api_kwargs={"style": "primary"})
        ])
        
    # Receipt photo view button
    if (payment and (payment.get("receipt_file") or payment.get("telegram_file_id"))) or (query and query.message and query.message.photo):
        keyboard.append([
            InlineKeyboardButton("🖼️ View Receipt Photo", callback_data=f"adm_show_receipt_{order_id}")
        ])
        
    keyboard.append([
        InlineKeyboardButton("👤 View Customer Profile", callback_data=f"adm_cust_{order.get('telegram_id')}"),
        InlineKeyboardButton("💬 Message Customer", callback_data=f"adm_prompt_msg_{order.get('telegram_id')}")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 Back to Orders", callback_data="adm_orders_all_0")
    ])
    
    return text, InlineKeyboardMarkup(keyboard)

# =====================================================================
# 4. PRODUCT CATALOG & PRICING MANAGEMENT
# =====================================================================

def build_products_list_view():
    products = models.get_all_products_admin()
    text = (
        f"🛍️ <b>PRODUCT CATALOG & PRICING</b>\n"
        f"Total Services: <b>{len(products)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Click any service below to edit plans, adjust pricing, toggle status, or delete:\n\n"
    )
    
    keyboard = []
    for p in products:
        status_icon = "🟢" if p.get("is_active") else "🔴"
        plans = p.get("plans", [])
        if plans:
            min_price = min(pl.get("price", 0) for pl in plans)
            max_price = max(pl.get("price", 0) for pl in plans)
            price_str = f"{min_price:,.0f} ETB" if min_price == max_price else f"{min_price:,.0f}-{max_price:,.0f} ETB"
        else:
            price_str = "No plans"
            
        btn_text = f"{status_icon} {p.get('name')} • {price_str}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"adm_prod_view_{p['id']}")])
        
    keyboard.append([
        InlineKeyboardButton("➕ Add New Service", callback_data="adm_prod_add_start")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")
    ])
    return text, InlineKeyboardMarkup(keyboard)

def build_product_details_view(product_id):
    p = models.get_product_details(product_id)
    if not p:
        return "❌ Service not found.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_products_list")]])
        
    status_icon = "🟢 Active" if p.get("active") else "🔴 Inactive"
    plans = p.get("plans", [])
    cat_name = p.get("category_name") or "General"
    cat_icon = p.get("category_icon") or "📦"
    img_status = f"🖼️ Set ({p.get('image_url')[:24]}...)" if p.get("image_url") else "⚪ None"
    
    text = (
        f"🛍️ <b>SERVICE: {p.get('name')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ <b>Status:</b> {status_icon}\n"
        f"📁 <b>Category:</b> {cat_icon} {cat_name}\n"
        f"🖼️ <b>Banner/Image:</b> {img_status}\n"
        f"📝 <b>Description:</b>\n<i>{p.get('description') or 'No description set.'}</i>\n\n"
        f"📋 <b>PRICING PLANS ({len(plans)}):</b>\n"
    )
    
    if not plans:
        text += "<i>No plans configured yet. Click 'Manage Plans' to add one.</i>\n"
    else:
        for idx, pl in enumerate(plans, 1):
            pl_status = "🟢" if pl.get("active") else "🔴"
            text += f"{idx}. {pl_status} <b>{pl.get('duration') or pl.get('name')}:</b> <b>{pl.get('price', 0):,.0f} ETB</b>\n"
            
    text += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚡ <i>Edit Product Details:</i>"
    
    keyboard = [
        [
            InlineKeyboardButton("✏️ Edit Name", callback_data=f"adm_ped_name_{product_id}"),
            InlineKeyboardButton("📝 Edit Description", callback_data=f"adm_ped_desc_{product_id}")
        ],
        [
            InlineKeyboardButton("📁 Change Category", callback_data=f"adm_ped_cat_{product_id}"),
            InlineKeyboardButton("🖼️ Change Image", callback_data=f"adm_ped_img_{product_id}")
        ],
        [
            InlineKeyboardButton(f"📋 Manage Plans & Pricing ({len(plans)})", callback_data=f"adm_prod_plans_{product_id}")
        ],
        [
            InlineKeyboardButton("🔴 Deactivate Service" if p.get("active") else "🟢 Activate Service", callback_data=f"adm_prod_toggle_{product_id}"),
            InlineKeyboardButton("🗑️ Delete Service", callback_data=f"adm_prod_del_confirm_{product_id}")
        ],
        [
            InlineKeyboardButton("🔙 Back to Catalog", callback_data="adm_products_list")
        ]
    ]
    
    return text, InlineKeyboardMarkup(keyboard)

def build_product_plans_view(product_id):
    p = models.get_product_details(product_id)
    if not p:
        return "❌ Service not found.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_products_list")]])
        
    plans = p.get("plans", [])
    text = (
        f"📋 <b>MANAGE PLANS & PRICING</b>\n"
        f"Service: <b>{p.get('name')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Configure subscription durations, ETB pricing, and active status.\n"
        f"Click any plan below to edit or delete it:\n\n"
    )
    
    keyboard = []
    if not plans:
        text += "<i>No plans currently exist for this service.</i>\n\n"
    else:
        for pl in plans:
            pl_status = "🟢" if pl.get("active") else "🔴"
            dur = pl.get("duration") or pl.get("name")
            btn_text = f"{pl_status} {dur} • {pl.get('price', 0):,.0f} ETB"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"adm_plan_manage_{pl['id']}")])
            
    keyboard.append([
        InlineKeyboardButton("➕ Add New Plan", callback_data=f"adm_plan_add_{product_id}")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 Back to Service Details", callback_data=f"adm_prod_view_{product_id}")
    ])
    return text, InlineKeyboardMarkup(keyboard)

def build_single_plan_view(plan_id):
    pl = models.get_plan_by_id(plan_id)
    if not pl:
        return "❌ Plan not found.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_products_list")]])
        
    p = models.get_product_by_id(pl.get("product_id"))
    prod_name = p.get("name") if p else "Service"
    pl_status = "🟢 Active" if pl.get("active") else "🔴 Inactive"
    dur = pl.get("duration") or pl.get("name")
    
    text = (
        f"⚙️ <b>PLAN SETTINGS</b>\n"
        f"Service: <b>{prod_name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Duration / Name:</b> <b>{dur}</b>\n"
        f"• <b>Price:</b> <b>{pl.get('price', 0):,.0f} {pl.get('currency', 'ETB')}</b>\n"
        f"• <b>Status:</b> {pl_status}\n\n"
        f"👇 <i>Select an action to modify this plan:</i>"
    )
    
    toggle_label = "🔴 Deactivate Plan" if pl.get("active") else "🟢 Activate Plan"
    keyboard = [
        [
            InlineKeyboardButton("✏️ Edit Price", callback_data=f"adm_plan_editprice_{plan_id}"),
            InlineKeyboardButton("✏️ Edit Duration / Name", callback_data=f"adm_plan_editname_{plan_id}")
        ],
        [
            InlineKeyboardButton(toggle_label, callback_data=f"adm_plan_toggle_{plan_id}"),
            InlineKeyboardButton("🗑️ Delete Plan", callback_data=f"adm_plan_delconfirm_{plan_id}")
        ],
        [
            InlineKeyboardButton("🔙 Back to Plans List", callback_data=f"adm_prod_plans_{pl.get('product_id')}")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

def build_category_picker_view(product_id):
    p = models.get_product_by_id(product_id)
    cats = models.get_all_categories()
    
    text = (
        f"📁 <b>CHANGE CATEGORY</b>\n"
        f"Service: <b>{p.get('name') if p else 'Service'}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Current Category: <b>{p.get('category_name') or 'General'}</b>\n\n"
        f"👇 <i>Select the new category for this service:</i>"
    )
    keyboard = []
    for c in cats:
        keyboard.append([InlineKeyboardButton(f"{c.get('icon', '📦')} {c['name']}", callback_data=f"adm_ped_setcat_{product_id}_{c['id']}")])
        
    keyboard.append([
        InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_prod_view_{product_id}")
    ])
    return text, InlineKeyboardMarkup(keyboard)

# =====================================================================
# 5. CUSTOMER DIRECTORY & DIRECT MESSAGING
# =====================================================================

def build_customers_list_view(offset=0):
    customers = models.get_customers_list(limit=PAGE_SIZE + 1, offset=offset)
    has_next = len(customers) > PAGE_SIZE
    current_customers = customers[:PAGE_SIZE]
    page_num = (offset // PAGE_SIZE) + 1
    
    text = (
        f"👥 <b>CUSTOMER DIRECTORY</b> (Page {page_num})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    
    if not current_customers:
        text += "<i>No customers found.</i>\n\n"
    else:
        text += f"Showing {len(current_customers)} customer(s):\n\n"
        
    keyboard = []
    for c in current_customers:
        name = c.get("full_name") or c.get("first_name") or f"User {c.get('telegram_id')}"
        username = f"@{c.get('telegram_username')}" if c.get('telegram_username') else ""
        tot_spend = c.get("total_spend", 0)
        prem_icon = "⭐ " if c.get("is_premium") else ""
        btn_text = f"👤 {prem_icon}{name[:12]} {username[:10]} • {tot_spend:,.0f} ETB"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"adm_cust_{c.get('telegram_id')}")])
        
    nav_row = []
    if offset > 0:
        prev_off = max(0, offset - PAGE_SIZE)
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_customers_{prev_off}"))
    if has_next:
        next_off = offset + PAGE_SIZE
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"adm_customers_{next_off}"))
        
    if nav_row:
        keyboard.append(nav_row)
        
    keyboard.append([
        InlineKeyboardButton("🔍 Search Customer", callback_data="adm_cust_search_prompt")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")
    ])
    return text, InlineKeyboardMarkup(keyboard)

def build_customer_profile_view(telegram_id):
    profile = models.get_customer_profile(telegram_id)
    if not profile:
        return "❌ Customer profile not found.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_customers_0")]])
        
    name = profile.get("full_name") or profile.get("first_name") or "Customer"
    uname = profile.get("telegram_username") or "none"
    tot_orders = profile.get("total_orders", 0)
    completed = profile.get("completed_orders", 0)
    spend = profile.get("total_spend", 0)
    prem_status = "⭐ <b>YES (Telegram Premium VIP)</b>" if profile.get("is_premium") else "⚪ Standard User"
    
    text = (
        f"👤 <b>CUSTOMER PROFILE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Full Name:</b> {name}\n"
        f"• <b>Username:</b> @{uname}\n"
        f"• <b>Telegram ID:</b> <code>{telegram_id}</code>\n"
        f"• <b>Telegram Premium:</b> {prem_status}\n"
        f"• <b>Phone:</b> {profile.get('phone') or 'Not provided'}\n"
        f"• <b>Language:</b> {profile.get('language', 'en').upper()}\n"
        f"• <b>Joined:</b> {profile.get('created_at', '')[:10]}\n"
        f"• <b>Last Active:</b> {profile.get('last_active_at', '')[:19]}\n\n"
        f"📊 <b>PURCHASE SUMMARY:</b>\n"
        f"• Total Orders: <b>{tot_orders}</b>\n"
        f"• Completed Orders: <b>{completed}</b>\n"
        f"• Lifetime Spend: <b>{spend:,.0f} ETB</b>\n\n"
    )
    
    # Recent orders preview
    orders = profile.get("orders", [])
    if orders:
        text += "📦 <b>Order History:</b>\n"
        for o in orders[:5]:
            text += f"• #{o.get('order_number')}: {o.get('product_name')} ({o.get('amount', 0):,.0f} ETB) — <i>{o.get('status')}</i>\n"
        text += "\n"
        
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n👇 <i>Actions:</i>"
    
    keyboard = [
        [
            InlineKeyboardButton("💬 Send Direct Message", callback_data=f"adm_prompt_msg_{telegram_id}")
        ],
        [
            InlineKeyboardButton("🔙 Back to Directory", callback_data="adm_customers_0")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

# =====================================================================
# =====================================================================
# 6. BROADCAST ANNOUNCEMENTS & BUTTON COMPOSER
# =====================================================================

BUTTON_STYLES = {
    "green": {
        "style": "success",
        "name": "Full Green Button",
        "description": "Solid Green background (Telegram Success style)",
        "badge": "🟢 Green",
        "emoji": "🟢"
    },
    "blue": {
        "style": "primary",
        "name": "Full Blue Button",
        "description": "Solid Blue/Teal background (Telegram Primary style)",
        "badge": "🔵 Blue",
        "emoji": "🔵"
    },
    "red": {
        "style": "danger",
        "name": "Full Red Button",
        "description": "Solid Red/Brown background (Telegram Danger style)",
        "badge": "🔴 Red",
        "emoji": "🔴"
    },
    "default": {
        "style": None,
        "name": "Default Neutral",
        "description": "Standard neutral theme button",
        "badge": "⚪ Neutral",
        "emoji": "⚪"
    }
}

# Compatibility aliases
COLOR_THEMES = {
    "green": BUTTON_STYLES["green"],
    "blue": BUTTON_STYLES["blue"],
    "red": BUTTON_STYLES["red"],
    "default": BUTTON_STYLES["default"],
    "classic": BUTTON_STYLES["default"],
    "purple": BUTTON_STYLES["blue"],
    "gold": BUTTON_STYLES["green"],
    "diamond": BUTTON_STYLES["blue"]
}

def build_broadcast_menu():
    text = (
        f"📢 <b>STORE-WIDE BROADCAST SYSTEM</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Send instant marketing announcements, new product arrivals, or updates directly to your customer base.\n"
        f"You can attach custom full-color solid buttons (Green, Blue, Red) and direct links!\n\n"
        f"👇 <i>Select target recipient group:</i>"
    )
    keyboard = [
        [InlineKeyboardButton("📢 All Customers", callback_data="adm_bc_target_all")],
        [InlineKeyboardButton("✅ Customers with Completed Orders", callback_data="adm_bc_target_completed")],
        [InlineKeyboardButton("⏳ Customers with Pending Orders", callback_data="adm_bc_target_pending")],
        [InlineKeyboardButton("📜 Past Broadcast History", callback_data="adm_bc_history")],
        [InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")]
    ]
    return text, InlineKeyboardMarkup(keyboard)

def build_broadcast_preview_view(bdata):
    target_type = bdata.get("target_type", "all")
    count = bdata.get("count", 0)
    msg_text = bdata.get("text", "")
    buttons = bdata.get("buttons", [])
    media_path = bdata.get("media_path")
    
    target_labels = {
        "all": "📢 All Customers",
        "premium": "⭐ Telegram Premium Members Only",
        "completed": "✅ Customers with Completed Orders",
        "pending": "⏳ Customers with Pending Orders"
    }
    target_name = target_labels.get(target_type, target_type.upper())
    
    preview_text = (
        f"📢 <b>PREVIEW BROADCAST ANNOUNCEMENT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Target Audience:</b> {target_name}\n"
        f"👥 <b>Total Reach:</b> <b>{count} recipient(s)</b>\n"
    )
    if media_path:
        preview_text += "🖼️ <b>Attached Media:</b> Photo included\n"
    preview_text += (
        f"\n📝 <b>Message Content:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{msg_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    if buttons:
        preview_text += "🔘 <b>Attached Full-Color Buttons:</b>\n"
        for idx, btn in enumerate(buttons, 1):
            st = btn.get("style")
            badge = "🟢 Full Green" if st == "success" else ("🔵 Full Blue" if st == "primary" else ("🔴 Full Red" if st == "danger" else "⚪ Neutral"))
            preview_text += f"{idx}. <b>[{btn['text']}]</b> ({badge}) ➔ <code>{btn['url']}</code>\n"
        preview_text += "\n"
    else:
        preview_text += "🔘 <b>Buttons:</b> <i>None attached yet (optional).</i>\n\n"
        
    preview_text += "👇 <i>Add colored buttons or dispatch now:</i>"
    
    keyboard = []
    # Sample display of attached buttons with REAL NATIVE FULL COLOR STYLES in preview!
    for btn in buttons:
        btn_kwargs = {"url": btn["url"]}
        if btn.get("style"):
            btn_kwargs["api_kwargs"] = {"style": btn["style"]}
        keyboard.append([InlineKeyboardButton(btn["text"], **btn_kwargs)])
        
    # DIRECT ACCESS to all button colors right in the broadcast composer!
    keyboard.append([
        InlineKeyboardButton("🟢 Add Green", callback_data="adm_bc_pickcolor_green", api_kwargs={"style": "success"}),
        InlineKeyboardButton("🔵 Add Blue", callback_data="adm_bc_pickcolor_blue", api_kwargs={"style": "primary"}),
        InlineKeyboardButton("🔴 Add Red", callback_data="adm_bc_pickcolor_red", api_kwargs={"style": "danger"})
    ])
    
    action_row = [
        InlineKeyboardButton("⚪ Add Default", callback_data="adm_bc_pickcolor_default")
    ]
    if buttons:
        action_row.append(InlineKeyboardButton("🗑️ Clear Buttons", callback_data="adm_bc_clear_btns"))
    keyboard.append(action_row)
    
    keyboard.append([
        InlineKeyboardButton("🚀 Send Broadcast Now", callback_data="adm_bc_confirm_send", api_kwargs={"style": "success"}),
        InlineKeyboardButton("❌ Cancel", callback_data="adm_broadcast_menu", api_kwargs={"style": "danger"})
    ])
    return preview_text, InlineKeyboardMarkup(keyboard)

def build_button_color_picker_view():
    text = (
        f"🎨 <b>SELECT FULL BUTTON BACKGROUND COLOR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Choose the solid background color for your inline button:\n"
        f"<i>(Note: This renders the entire button background in solid color on Telegram, with clean custom text!)</i>\n"
    )
    keyboard = [
        [
            InlineKeyboardButton("🟢 Full Green Button", callback_data="adm_bc_pickcolor_green", api_kwargs={"style": "success"}),
        ],
        [
            InlineKeyboardButton("🔵 Full Blue Button", callback_data="adm_bc_pickcolor_blue", api_kwargs={"style": "primary"}),
        ],
        [
            InlineKeyboardButton("🔴 Full Red Button", callback_data="adm_bc_pickcolor_red", api_kwargs={"style": "danger"}),
        ],
        [
            InlineKeyboardButton("⚪ Standard Neutral Button", callback_data="adm_bc_pickcolor_default")
        ],
        [
            InlineKeyboardButton("🔙 Back to Preview", callback_data="adm_bc_preview_back")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

BUTTON_DISPLAY_NAMES = {
    "btn_color_browse": "🛍️ Browse Products Button",
    "btn_color_orders": "📦 My Orders Button",
    "btn_color_payment": "💳 Payment Instructions Button",
    "btn_color_support": "📞 Contact Support Button",
    "btn_color_about": "ℹ️ About Store Button",
    "btn_color_language": "🌐 Language Switcher Button",
    "btn_color_back": "🔙 Back Navigation Button",
    "btn_color_refresh": "🔄 Refresh Action Button",
    "btn_color_products": "🏷️ Product Item Cards"
}

def build_button_colors_hub_view():
    text = (
        f"🎨 <b>EVERY BUTTON COLOR CUSTOMIZER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"You have 100% full control to customize the background color of <b>every single button</b> in your store bot:\n\n"
        f"• 🟢 <b>Green</b> (<code>style='success'</code>)\n"
        f"• 🔵 <b>Blue</b> (<code>style='primary'</code>)\n"
        f"• 🔴 <b>Red</b> (<code>style='danger'</code>)\n"
        f"• ⚪ <b>Default Neutral</b>\n\n"
        f"👇 <i>Click any button below to change its background color:</i>"
    )
    keyboard = []
    for key, label in BUTTON_DISPLAY_NAMES.items():
        st = models.get_button_color(key)
        badge = "🟢 Green" if st == "success" else ("🔵 Blue" if st == "primary" else ("🔴 Red" if st == "danger" else "⚪ Default"))
        btn_kwargs = {"callback_data": f"adm_btncfg_{key}"}
        if st in ("success", "primary", "danger"):
            btn_kwargs["api_kwargs"] = {"style": st}
        keyboard.append([
            InlineKeyboardButton(f"{label} ➔ {badge}", **btn_kwargs)
        ])
    
    keyboard.append([
        InlineKeyboardButton("📱 Send Live Demo to My Telegram", callback_data="adm_test_btn_send_live")
    ])
    keyboard.append([
        InlineKeyboardButton("🔄 Reset All to Defaults", callback_data="adm_btncfg_reset_defaults"),
        InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")
    ])
    return text, InlineKeyboardMarkup(keyboard)

def build_single_button_color_picker(btn_key):
    label = BUTTON_DISPLAY_NAMES.get(btn_key, btn_key)
    current_st = models.get_button_color(btn_key)
    current_badge = "🟢 Green" if current_st == "success" else ("🔵 Blue" if current_st == "primary" else ("🔴 Red" if current_st == "danger" else "⚪ Default"))
    text = (
        f"🎨 <b>CUSTOMIZE BUTTON COLOR:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Button: <b>{label}</b>\n"
        f"Current Color: <b>{current_badge}</b>\n\n"
        f"👇 <i>Choose the background color you want for this button:</i>"
    )
    keyboard = [
        [
            InlineKeyboardButton("🟢 Make it Solid Green", callback_data=f"adm_setbtncolor_{btn_key}_success", api_kwargs={"style": "success"})
        ],
        [
            InlineKeyboardButton("🔵 Make it Solid Blue", callback_data=f"adm_setbtncolor_{btn_key}_primary", api_kwargs={"style": "primary"})
        ],
        [
            InlineKeyboardButton("🔴 Make it Solid Red", callback_data=f"adm_setbtncolor_{btn_key}_danger", api_kwargs={"style": "danger"})
        ],
        [
            InlineKeyboardButton("⚪ Make it Default Neutral", callback_data=f"adm_setbtncolor_{btn_key}_default")
        ],
        [
            InlineKeyboardButton("🔙 Back to All Buttons", callback_data="adm_btn_colors")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

# =====================================================================
# 7. STORE & PAYMENT SETTINGS
# =====================================================================

def build_settings_view():
    settings = models.get_store_settings()
    tb_acc = settings.get("telebirr_account", config.DEFAULT_TELEBIRR_ACCOUNT)
    tb_name = settings.get("telebirr_name", config.DEFAULT_TELEBIRR_NAME)
    cbe_acc = settings.get("cbe_birr_account", config.DEFAULT_CBE_BIRR_ACCOUNT)
    cbe_name = settings.get("cbe_birr_name", config.DEFAULT_CBE_BIRR_NAME)
    supp = settings.get("support_username", config.DEFAULT_SUPPORT_USERNAME)
    st_name = settings.get("store_name", config.STORE_NAME)
    
    text = (
        f"⚙️ <b>STORE & PAYMENT CONFIGURATION</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>OFFICIAL PAYMENT ACCOUNTS:</b>\n\n"
        f"📱 <b>Telebirr Account:</b>\n"
        f"• Number: <code>{tb_acc}</code>\n"
        f"• Account Name: <b>{tb_name}</b>\n\n"
        f"🏛️ <b>CBE Account:</b>\n"
        f"• Number: <code>{cbe_acc}</code>\n"
        f"• Account Name: <b>{cbe_name}</b>\n\n"
        f"💬 <b>Support Telegram:</b> {supp}\n"
        f"🏪 <b>Store Name:</b> {st_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <i>Click any setting to modify:</i>"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✏️ Edit Telebirr Number", callback_data="adm_edit_set_telebirr_account"),
            InlineKeyboardButton("✏️ Edit Telebirr Name", callback_data="adm_edit_set_telebirr_name")
        ],
        [
            InlineKeyboardButton("✏️ Edit CBE Number", callback_data="adm_edit_set_cbe_birr_account"),
            InlineKeyboardButton("✏️ Edit CBE Name", callback_data="adm_edit_set_cbe_birr_name")
        ],
        [
            InlineKeyboardButton("✏️ Edit Support Username", callback_data="adm_edit_set_support_username"),
            InlineKeyboardButton("✏️ Edit Store Name", callback_data="adm_edit_set_store_name")
        ],
        [
            InlineKeyboardButton("🎨 Button Colors & Styling", callback_data="adm_btn_colors"),
            InlineKeyboardButton("💾 Download Database Backup", callback_data="adm_backup_download")
        ],
        [
            InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

# =====================================================================
# 8. ACTIVITY & AUDIT LOGS
# =====================================================================

def build_logs_view():
    logs = models.get_recent_activity_logs(limit=10)
    text = (
        f"📜 <b>RECENT AUDIT & ACTIVITY LOGS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    if not logs:
        text += "<i>No activity logs recorded yet.</i>\n\n"
    else:
        for l in logs:
            dt = l.get("created_at", "")[:19]
            admin = l.get("admin_username") or "Admin"
            action = l.get("action")
            det = l.get("details") or ""
            text += f"• <code>[{dt}]</code> <b>{admin}</b>: {action}\n  ↳ <i>{det}</i>\n\n"
            
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━"
    keyboard = [[InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")]]
    return text, InlineKeyboardMarkup(keyboard)

# =====================================================================
# 9. CALLBACK ROUTER & ACTIONS
# =====================================================================

async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    
    if not is_admin(user.id, user.username):
        await query.answer("Access Denied", show_alert=True)
        return
        
    data = query.data
    logger.info(f"Admin {user.id} callback: {data}")
    
    # --- Main Hub ---
    if data == "adm_hub":
        ADMIN_STATES[user.id] = None
        text, pending = build_dashboard_text(user)
        await safe_edit_or_reply(query, text, reply_markup=build_main_dashboard_keyboard(pending))
        return

    # --- Mini App Control Hub ---
    if data == "adm_miniapp_hub":
        ADMIN_STATES[user.id] = None
        text, markup = build_miniapp_hub_view()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data == "adm_miniapp_sync_now":
        await query.answer("🔄 Syncing Base44 catalog with Mini App...", show_alert=False)
        try:
            if miniapp_api:
                res = miniapp_api.sync_from_base44(force=True)
            else:
                res = {"synced": False, "reason": "Mini App catalog sync module not available"}
            if res.get("synced"):
                sync_msg = (
                    f"✅ <b>MINI APP CATALOG SYNCHRONIZED!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• Categories Synced: <b>{res.get('categories', 0)}</b>\n"
                    f"• Products / Services Synced: <b>{res.get('products', 0)}</b>\n"
                    f"• Pricing Plans Synced: <b>{res.get('plans', 0)}</b>\n\n"
                    f"All changes are live in the Mini App and Store Bot immediately!"
                )
            else:
                sync_msg = (
                    f"ℹ️ <b>Sync Status:</b> {res.get('reason', 'completed')}\n"
                    f"Error/Fallback: {res.get('error', 'Using store.db')}"
                )
        except Exception as e:
            sync_msg = f"⚠️ <b>Sync Notice:</b> {e}"

        models.log_admin_action(
            admin_id=user.id,
            admin_username=f"@{user.username}" if user.username else str(user.id),
            action="Synced Base44 Mini App",
            details="Triggered live sync from admin bot"
        )
        await query.answer("✅ Mini App Catalog Synced!", show_alert=True)
        text, markup = build_miniapp_hub_view()
        await safe_edit_or_reply(query, sync_msg + "\n\n" + text, reply_markup=markup)
        return

    if data == "adm_miniapp_toggle_maint":
        current = models.get_setting("miniapp_maintenance_mode", "0") in ("1", "true", "True")
        new_val = "0" if current else "1"
        models.update_store_settings({"miniapp_maintenance_mode": new_val}, admin_id=user.id, admin_username=f"@{user.username}" if user.username else str(user.id))
        status_txt = "ENABLED (Maintenance Active)" if new_val == "1" else "DISABLED (Store Live)"
        await query.answer(f"Mini App Maintenance Mode {status_txt}", show_alert=True)
        text, markup = build_miniapp_hub_view()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data == "adm_miniapp_edit_banner":
        ADMIN_STATES[user.id] = {"state": "WAITING_MINIAPP_BANNER"}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_miniapp_hub")]])
        prompt = (
            "📢 <b>SET MINI APP PROMO BANNER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "👉 <b>Type the announcement text to display at the top of the Mini App:</b>\n\n"
            "<i>Examples:</i>\n"
            "• <code>🎉 Special Weekend Discount: 15% off all streaming plans!</code>\n"
            "• <code>⚡ Fast Automated Delivery is active 24/7 with Telebirr & CBE.</code>\n\n"
            "<i>Or send <code>OFF</code> to remove the banner.</i>"
        )
        await safe_edit_or_reply(query, prompt, reply_markup=cancel_markup)
        return

    if data == "adm_miniapp_clear_banner":
        models.update_store_settings({"miniapp_banner_active": "0", "miniapp_banner_text": ""}, admin_id=user.id, admin_username=f"@{user.username}" if user.username else str(user.id))
        await query.answer("Mini App banner cleared!", show_alert=True)
        text, markup = build_miniapp_hub_view()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data == "adm_miniapp_edit_url":
        ADMIN_STATES[user.id] = {"state": "WAITING_MINIAPP_URL"}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_miniapp_hub")]])
        prompt = (
            "🔗 <b>UPDATE MINI APP LIVE URL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "👉 <b>Enter your deployed Vercel or custom Mini App URL:</b>\n\n"
            "<i>Example:</i> <code>https://abdurea-store.vercel.app/miniapp</code>"
        )
        await safe_edit_or_reply(query, prompt, reply_markup=cancel_markup)
        return
        
    # --- Analytics ---
    if data.startswith("adm_analytics_"):
        tr = data.replace("adm_analytics_", "")
        text, markup = build_analytics_view(tr)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    # --- Orders Listing ---
    if data.startswith("adm_orders_"):
        parts = data.split("_")
        # format: adm_orders_{filter}_{offset}
        ftype = parts[2]
        offset = int(parts[3]) if len(parts) > 3 else 0
        text, markup = build_orders_list_view(ftype, offset)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    # --- Supabase Website Order Actions ---
    if data.startswith("sb_confirm_"):
        ref = data.replace("sb_confirm_", "")
        import supabase_bridge
        await query.answer("Confirming order...", show_alert=False)
        admin_name = f"@{user.username}" if user.username else f"Admin {user.id}"
        success = await supabase_bridge.update_supabase_order_status(
            order_id=ref,
            new_status="confirmed",
            admin_notes=f"Confirmed by {admin_name} via Telegram Admin Bot"
        )
        if success:
            await query.answer(f"✅ Supabase Order #{ref} Confirmed!", show_alert=True)
            new_kb = [
                [InlineKeyboardButton("📦 Complete Order", callback_data=f"sb_complete_{ref}")],
                [InlineKeyboardButton("❌ Cancel Order", callback_data=f"sb_cancel_{ref}")],
                [InlineKeyboardButton("📋 Order Details", callback_data=f"sb_details_{ref}")]
            ]
            try:
                curr_text = query.message.text or query.message.caption or ""
                updated_text = re.sub(r"Status:\s*\w+", "Status: Confirmed", curr_text, flags=re.IGNORECASE)
                if query.message.caption:
                    try:
                        await query.edit_message_caption(caption=updated_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(new_kb))
                    except Exception:
                        await query.edit_message_caption(caption=updated_text, reply_markup=InlineKeyboardMarkup(new_kb))
                else:
                    try:
                        await query.edit_message_text(text=updated_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(new_kb))
                    except Exception:
                        await query.edit_message_text(text=updated_text, reply_markup=InlineKeyboardMarkup(new_kb))
            except Exception as e:
                logger.debug(f"Message edit notice: {e}")
        else:
            await query.answer("⚠️ Could not update Supabase order. Check connection.", show_alert=True)
        return

    if data.startswith("sb_complete_"):
        ref = data.replace("sb_complete_", "")
        import supabase_bridge
        await query.answer("Completing order...", show_alert=False)
        admin_name = f"@{user.username}" if user.username else f"Admin {user.id}"
        success = await supabase_bridge.update_supabase_order_status(
            order_id=ref,
            new_status="completed",
            admin_notes=f"Completed by {admin_name} via Telegram Admin Bot"
        )
        if success:
            await query.answer(f"🎉 Supabase Order #{ref} Completed!", show_alert=True)
            new_kb = [
                [InlineKeyboardButton("📋 Order Details", callback_data=f"sb_details_{ref}")]
            ]
            try:
                curr_text = query.message.text or query.message.caption or ""
                updated_text = re.sub(r"Status:\s*\w+", "Status: Completed", curr_text, flags=re.IGNORECASE)
                if query.message.caption:
                    try:
                        await query.edit_message_caption(caption=updated_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(new_kb))
                    except Exception:
                        await query.edit_message_caption(caption=updated_text, reply_markup=InlineKeyboardMarkup(new_kb))
                else:
                    try:
                        await query.edit_message_text(text=updated_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(new_kb))
                    except Exception:
                        await query.edit_message_text(text=updated_text, reply_markup=InlineKeyboardMarkup(new_kb))
            except Exception as e:
                logger.debug(f"Message edit notice: {e}")
        else:
            await query.answer("⚠️ Could not update Supabase order. Check connection.", show_alert=True)
        return

    if data.startswith("sb_cancel_"):
        ref = data.replace("sb_cancel_", "")
        import supabase_bridge
        await query.answer("Cancelling order...", show_alert=False)
        admin_name = f"@{user.username}" if user.username else f"Admin {user.id}"
        success = await supabase_bridge.update_supabase_order_status(
            order_id=ref,
            new_status="cancelled",
            admin_notes=f"Cancelled by {admin_name} via Telegram Admin Bot"
        )
        if success:
            await query.answer(f"❌ Supabase Order #{ref} Cancelled!", show_alert=True)
            new_kb = [
                [InlineKeyboardButton("📋 Order Details", callback_data=f"sb_details_{ref}")]
            ]
            try:
                curr_text = query.message.text or query.message.caption or ""
                updated_text = re.sub(r"Status:\s*\w+", "Status: Cancelled", curr_text, flags=re.IGNORECASE)
                if query.message.caption:
                    try:
                        await query.edit_message_caption(caption=updated_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(new_kb))
                    except Exception:
                        await query.edit_message_caption(caption=updated_text, reply_markup=InlineKeyboardMarkup(new_kb))
                else:
                    try:
                        await query.edit_message_text(text=updated_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(new_kb))
                    except Exception:
                        await query.edit_message_text(text=updated_text, reply_markup=InlineKeyboardMarkup(new_kb))
            except Exception as e:
                logger.debug(f"Message edit notice: {e}")
        else:
            await query.answer("⚠️ Could not update Supabase order. Check connection.", show_alert=True)
        return

    if data.startswith("sb_details_"):
        ref = data.replace("sb_details_", "")
        import supabase_bridge
        await query.answer()
        order = await supabase_bridge.fetch_supabase_order_by_id(ref)
        if not order:
            order = resolve_order(ref, query)
            if order:
                text, markup = build_order_details_view(order["id"], query)
                await safe_edit_or_reply(query, text, reply_markup=markup)
                return
            await query.answer("Order details not found", show_alert=True)
            return

        order_num = html.escape(str(order.get("order_number") or ref))
        c_name = html.escape(str(order.get("customer_name") or "Customer"))
        phone = html.escape(str(order.get("customer_contact") or order.get("customer_phone") or "N/A"))
        email = html.escape(str(order.get("customer_email") or "N/A"))
        service = html.escape(str(order.get("service_name") or order.get("product_name") or "Digital Service"))
        plan = html.escape(str(order.get("plan_duration") or "Standard"))
        price = order.get("price") or order.get("amount") or 0.0
        currency = html.escape(str(order.get("currency") or "ETB"))
        payment = html.escape(str(order.get("payment_method") or "Telebirr"))
        status = html.escape(str(order.get("status") or "pending").capitalize())
        notes = html.escape(str(order.get("admin_notes") or "None"))
        receipt_url = order.get("receipt_url")

        details_text = (
            f"📋 <b>WEBSITE ORDER DETAILS — #{order_num}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛍️ <b>Service:</b> {service} ({plan})\n"
            f"💰 <b>Price:</b> <b>{float(price):,.0f} {currency}</b>\n"
            f"💳 <b>Payment Method:</b> {payment}\n"
            f"📊 <b>Status:</b> <b>{status}</b>\n\n"
            f"👤 <b>Customer Details:</b>\n"
            f"• Name: {c_name}\n"
            f"• Phone: <code>{phone}</code>\n"
            f"• Email: <code>{email}</code>\n"
            f"• Telegram User ID: <code>{order.get('telegram_user_id') or 'N/A'}</code>\n\n"
            f"📝 <b>Admin Notes:</b> {notes}\n"
            f"🌐 <b>Source:</b> Supabase Website Order\n"
        )
        if receipt_url:
            details_text += f"\n🧾 <b>Receipt:</b> <a href='{receipt_url}'>View Receipt Image</a>\n"

        back_kb = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"sb_confirm_{ref}"),
                InlineKeyboardButton("📦 Complete", callback_data=f"sb_complete_{ref}")
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data=f"sb_cancel_{ref}"),
                InlineKeyboardButton("🔙 Back", callback_data="adm_orders_all_0")
            ]
        ]
        await safe_edit_or_reply(query, details_text, reply_markup=InlineKeyboardMarkup(back_kb))
        return

    # --- View Order Details ---
    if data.startswith("adm_view_order_"):
        ref = data.replace("adm_view_order_", "")
        text, markup = build_order_details_view(ref, query)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    # --- 1-Click Approve Payment ---
    if data.startswith("adm_quick_approve_"):
        ref = data.replace("adm_quick_approve_", "")
        order = resolve_order(ref, query)
        if not order:
            await query.answer("Order not found", show_alert=True)
            return
            
        oid = order["id"]
        updated, tg_res = models.accept_order_payment(
            order_id=oid,
            admin_id=user.id,
            admin_username=f"@{user.username}" if user.username else str(user.id),
            admin_note="Approved via Telegram Admin Bot"
        )
        await query.answer(f"✅ Order #{order['order_number']} Approved!", show_alert=True)
        text, markup = build_order_details_view(oid, query)
        await safe_edit_or_reply(query, f"✅ <b>PAYMENT APPROVED</b> for Order #{order['order_number']}!\nCustomer notified.\n\n" + text, reply_markup=markup)
        return
        
    # --- Prompt Rejection Reason ---
    if data.startswith("adm_prompt_reject_"):
        ref = data.replace("adm_prompt_reject_", "")
        order = resolve_order(ref, query)
        if not order:
            await query.answer("Order not found", show_alert=True)
            return
            
        oid = order["id"]
        ADMIN_STATES[user.id] = {"state": "WAITING_REJECT_REASON", "order_id": oid}
        
        reject_keyboard = [
            [InlineKeyboardButton("📸 Invalid receipt screenshot", callback_data=f"adm_exec_reject_{oid}_invalid_receipt")],
            [InlineKeyboardButton("🔍 Transaction ID not found", callback_data=f"adm_exec_reject_{oid}_tx_not_found")],
            [InlineKeyboardButton("📉 Transfer amount mismatch", callback_data=f"adm_exec_reject_{oid}_amount_mismatch")],
            [InlineKeyboardButton("✍️ Type Custom Reason", callback_data=f"adm_type_reject_{oid}")],
            [InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_view_order_{oid}")]
        ]
        text = (
            f"❌ <b>REJECT PAYMENT — Order #{order['order_number']}</b>\n\n"
            f"Select a common rejection reason or choose 'Type Custom Reason':\n"
            f"<i>The customer will be notified with this exact explanation.</i>"
        )
        await safe_edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(reject_keyboard))
        return
        
    # --- Execute Preset Rejection ---
    if data.startswith("adm_exec_reject_"):
        parts = data.split("_")
        ref = parts[3]
        order = resolve_order(ref, query)
        if not order:
            await query.answer("Order not found", show_alert=True)
            return
            
        oid = order["id"]
        reason_code = "_".join(parts[4:])
        reason_map = {
            "invalid_receipt": "Receipt screenshot is illegible, cropped, or invalid. Please re-upload official transaction SMS or screenshot.",
            "tx_not_found": "Transaction reference could not be verified on our bank account statement. Please verify transfer.",
            "amount_mismatch": "Transfer amount received does not match the required plan price."
        }
        reason = reason_map.get(reason_code, "Payment verification could not be completed.")
        updated, tg_res = models.reject_order_payment(
            order_id=oid,
            reason=reason,
            admin_id=user.id,
            admin_username=f"@{user.username}" if user.username else str(user.id)
        )
        ADMIN_STATES[user.id] = None
        await query.answer("❌ Payment Rejected & Customer Notified", show_alert=True)
        text, markup = build_order_details_view(oid, query)
        await safe_edit_or_reply(query, f"❌ <b>PAYMENT REJECTED</b>: {reason}\n\n" + text, reply_markup=markup)
        return
        
    # --- Type Custom Rejection Reason ---
    if data.startswith("adm_type_reject_"):
        ref = data.replace("adm_type_reject_", "")
        order = resolve_order(ref, query)
        if not order:
            await query.answer("Order not found", show_alert=True)
            return
            
        oid = order["id"]
        ADMIN_STATES[user.id] = {"state": "WAITING_REJECT_REASON", "order_id": oid}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_view_order_{oid}")]])
        await safe_edit_or_reply(
            query,
            f"✍️ <b>Enter rejection reason for Order #{order['order_number']}:</b>\n\nType and send the explanation in your next message.",
            reply_markup=cancel_markup
        )
        return

    # --- Prompt Deliver Credentials ---
    if data.startswith("adm_prompt_deliver_"):
        ref = data.replace("adm_prompt_deliver_", "")
        order = resolve_order(ref, query)
        if not order:
            await query.answer("Order not found", show_alert=True)
            return
            
        oid = order["id"]
        ADMIN_STATES[user.id] = {"state": "WAITING_DELIVERY_TEXT", "order_id": oid}
        plan_dur = order.get('plan_duration') or '1 Month'
        text = (
            f"📦 <b>DELIVER SERVICE CREDENTIALS — #{order['order_number']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛍️ Service: <b>{order['product_name']}</b> ({plan_dur})\n"
            f"👤 Customer: <b>{order['customer_name']}</b> (@{order.get('customer_username') or 'none'})\n\n"
            f"👉 <b>Type or paste the credentials to deliver:</b>\n\n"
            f"<i>💡 Quick Copy-Paste Templates (tap code to copy):</i>\n\n"
            f"<b>Template A (Login Account):</b>\n"
            f"<code>📧 Email: account@domain.com\n"
            f"🔑 Pass: SecretPass123!\n"
            f"⏳ Plan: {plan_dur}\n"
            f"⚠️ Rules: Private profile. Do not alter info.</code>\n\n"
            f"<b>Template B (License / Key):</b>\n"
            f"<code>🔑 Key: XXXX-YYYY-ZZZZ-AAAA\n"
            f"📋 How to activate: Paste key in settings.</code>\n\n"
            f"<i>Once you send your message, the order will be marked <b>COMPLETED</b> and delivered instantly to the customer's Telegram chat!</i>"
        )
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_view_order_{oid}")]])
        await safe_edit_or_reply(query, text, reply_markup=cancel_markup)
        return

    # --- Show Receipt Photo ---
    if data.startswith("adm_show_receipt_"):
        ref = data.replace("adm_show_receipt_", "")
        order = resolve_order(ref, query)
        if not order:
            await query.answer("Order not found", show_alert=True)
            return
            
        oid = order["id"]
        payment = order.get("payment") or {}
        caption = f"🧾 <b>Receipt for Order #{order['order_number']}</b>\nAmount: {order['amount']} ETB | Method: {order['payment_method']}"
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Order Details", callback_data=f"adm_view_order_{oid}")]])

        # 1. Check local file on disk
        if payment.get("receipt_file"):
            rf = config.RECEIPTS_DIR / payment["receipt_file"]
            if rf.exists():
                try:
                    with open(rf, "rb") as f:
                        await query.message.reply_photo(photo=f, caption=caption, parse_mode="HTML", reply_markup=back_markup)
                    return
                except Exception as e:
                    logger.warning(f"Could not send receipt photo from disk: {e}")

        # 2. Check Telegram File ID
        tg_fid = payment.get("telegram_file_id")
        if not tg_fid and query.message and query.message.photo:
            tg_fid = query.message.photo[-1].file_id

        if tg_fid:
            try:
                await query.message.reply_photo(photo=tg_fid, caption=caption, parse_mode="HTML", reply_markup=back_markup)
                return
            except Exception as e:
                logger.warning(f"Could not send receipt photo via file_id: {e}")

        await query.answer("Receipt file not found on disk or Telegram", show_alert=True)
        return

    # --- Product Catalog & Full Editing ---
    if data == "adm_products_list":
        ADMIN_STATES[user.id] = None
        text, markup = build_products_list_view()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    if data.startswith("adm_prod_view_"):
        pid = data.replace("adm_prod_view_", "")
        ADMIN_STATES[user.id] = None
        text, markup = build_product_details_view(pid)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data.startswith("adm_ped_name_"):
        pid = data.replace("adm_ped_name_", "")
        ADMIN_STATES[user.id] = {"state": "WAITING_EDIT_PROD_NAME", "product_id": pid}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_prod_view_{pid}")]])
        await safe_edit_or_reply(query, "✏️ <b>Enter the new service name:</b>", reply_markup=cancel_markup)
        return

    if data.startswith("adm_ped_desc_"):
        pid = data.replace("adm_ped_desc_", "")
        ADMIN_STATES[user.id] = {"state": "WAITING_EDIT_PROD_DESC", "product_id": pid}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_prod_view_{pid}")]])
        await safe_edit_or_reply(query, "📝 <b>Enter the new service description:</b>", reply_markup=cancel_markup)
        return

    if data.startswith("adm_ped_cat_"):
        pid = data.replace("adm_ped_cat_", "")
        text, markup = build_category_picker_view(pid)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data.startswith("adm_ped_setcat_"):
        parts = data.replace("adm_ped_setcat_", "").split("_", 1)
        if len(parts) == 2:
            pid, cid = parts
            models.update_product_field(pid, "category_id", cid)
            await query.answer("Category updated", show_alert=False)
            text, markup = build_product_details_view(pid)
            await safe_edit_or_reply(query, text, reply_markup=markup)
            return

    if data.startswith("adm_ped_img_"):
        pid = data.replace("adm_ped_img_", "")
        ADMIN_STATES[user.id] = {"state": "WAITING_EDIT_PROD_IMG", "product_id": pid}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_prod_view_{pid}")]])
        await safe_edit_or_reply(
            query,
            "🖼️ <b>Send a new photo or paste an image URL for this service:</b>",
            reply_markup=cancel_markup
        )
        return

    if data.startswith("adm_prod_plans_"):
        pid = data.replace("adm_prod_plans_", "")
        ADMIN_STATES[user.id] = None
        text, markup = build_product_plans_view(pid)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data.startswith("adm_plan_manage_"):
        plan_id = data.replace("adm_plan_manage_", "")
        ADMIN_STATES[user.id] = None
        text, markup = build_single_plan_view(plan_id)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data.startswith("adm_plan_editprice_") or data.startswith("adm_plan_price_"):
        plan_id = data.replace("adm_plan_editprice_", "").replace("adm_plan_price_", "")
        plan = models.get_plan_by_id(plan_id)
        ADMIN_STATES[user.id] = {"state": "WAITING_EDIT_PLAN_PRICE", "plan_id": plan_id, "product_id": plan.get("product_id") if plan else None}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_plan_manage_{plan_id}")]])
        dur = plan.get('duration') if plan else 'Plan'
        await safe_edit_or_reply(query, f"✏️ <b>Enter new price in ETB for {dur}:</b>\n(e.g. <code>450</code>)", reply_markup=cancel_markup)
        return

    if data.startswith("adm_plan_editname_") or data.startswith("adm_plan_name_"):
        plan_id = data.replace("adm_plan_editname_", "").replace("adm_plan_name_", "")
        plan = models.get_plan_by_id(plan_id)
        ADMIN_STATES[user.id] = {"state": "WAITING_EDIT_PLAN_NAME", "plan_id": plan_id, "product_id": plan.get("product_id") if plan else None}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_plan_manage_{plan_id}")]])
        await safe_edit_or_reply(query, "✏️ <b>Enter new duration / name for this plan:</b>\n(e.g. <code>1 Month</code>, <code>3 Months</code>, <code>1 Year</code>)", reply_markup=cancel_markup)
        return

    if data.startswith("adm_plan_toggle_"):
        plan_id = data.replace("adm_plan_toggle_", "")
        plan = models.get_plan_by_id(plan_id)
        if plan:
            new_act = 0 if plan.get("active") else 1
            models.update_plan_details(plan_id, active=new_act)
            await query.answer("Plan status updated", show_alert=False)
        text, markup = build_single_plan_view(plan_id)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data.startswith("adm_plan_delconfirm_"):
        plan_id = data.replace("adm_plan_delconfirm_", "")
        plan = models.get_plan_by_id(plan_id)
        dur = plan.get('duration') if plan else 'this plan'
        confirm_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ Yes, Delete Plan", callback_data=f"adm_plan_delexec_{plan_id}")],
            [InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_plan_manage_{plan_id}")]
        ])
        await safe_edit_or_reply(query, f"⚠️ <b>Are you sure you want to delete {dur}?</b>", reply_markup=confirm_markup)
        return

    if data.startswith("adm_plan_delexec_"):
        plan_id = data.replace("adm_plan_delexec_", "")
        plan = models.get_plan_by_id(plan_id)
        pid = plan.get("product_id") if plan else ""
        models.delete_plan(plan_id)
        await query.answer("Plan deleted", show_alert=True)
        if pid:
            text, markup = build_product_plans_view(pid)
        else:
            text, markup = build_products_list_view()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data.startswith("adm_plan_add_"):
        pid = data.replace("adm_plan_add_", "")
        ADMIN_STATES[user.id] = {"state": "WAITING_ADD_PLAN_NAME", "product_id": pid, "data": {}}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_prod_plans_{pid}")]])
        await safe_edit_or_reply(
            query,
            "➕ <b>ADD NEW PLAN (Step 1/2)</b>\n\n👉 <b>Enter plan duration or name:</b>\n<i>(e.g. 1 Month, 3 Months, 1 Year, Lifetime)</i>",
            reply_markup=cancel_markup
        )
        return
        
    if data.startswith("adm_prod_toggle_"):
        pid = data.replace("adm_prod_toggle_", "")
        models.toggle_product_active(pid)
        await query.answer("Product status toggled", show_alert=False)
        text, markup = build_product_details_view(pid)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    if data.startswith("adm_prod_del_confirm_"):
        pid = data.replace("adm_prod_del_confirm_", "")
        prod = models.get_product_by_id(pid)
        name = prod.get("name") if prod else pid
        confirm_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ Yes, Delete Permanently", callback_data=f"adm_prod_del_exec_{pid}")],
            [InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_prod_view_{pid}")]
        ])
        await safe_edit_or_reply(
            query,
            f"⚠️ <b>Are you sure you want to permanently delete: {name}?</b>\nThis cannot be undone.",
            reply_markup=confirm_markup
        )
        return
        
    if data.startswith("adm_prod_del_exec_"):
        pid = data.replace("adm_prod_del_exec_", "")
        models.delete_product(pid)
        await query.answer("Product deleted", show_alert=True)
        text, markup = build_products_list_view()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    # --- Add New Product Wizard ---
    if data == "adm_prod_add_start":
        ADMIN_STATES[user.id] = {"state": "WAITING_NEW_PROD_NAME", "data": {}}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_products_list")]])
        await safe_edit_or_reply(
            query,
            "➕ <b>ADD NEW DIGITAL SERVICE (Step 1/5)</b>\n\n👉 <b>Enter the Service Name:</b>\n<i>(e.g. Netflix Premium 4K, ChatGPT Plus, Canva Pro)</i>",
            reply_markup=cancel_markup
        )
        return

    # --- Customers ---
    if data.startswith("adm_customers_"):
        offset = int(data.replace("adm_customers_", ""))
        text, markup = build_customers_list_view(offset)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    if data.startswith("adm_cust_"):
        cid = int(data.replace("adm_cust_", ""))
        text, markup = build_customer_profile_view(cid)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    if data.startswith("adm_prompt_msg_"):
        cid = int(data.replace("adm_prompt_msg_", ""))
        cust = models.get_user_by_telegram_id(cid)
        name = cust.get("full_name") if cust else f"User {cid}"
        ADMIN_STATES[user.id] = {"state": "WAITING_DIRECT_MSG", "target_id": cid}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_cust_{cid}")]])
        text = (
            f"💬 <b>DIRECT MESSAGE TO CUSTOMER: {name}</b>\n\n"
            f"👉 <b>Type your message below:</b>\n"
            f"It will be delivered directly into their Telegram chat with your store bot."
        )
        await safe_edit_or_reply(query, text, reply_markup=cancel_markup)
        return

    # --- Broadcast & Button Composer Callbacks ---
    if data == "adm_broadcast_menu":
        ADMIN_STATES[user.id] = None
        text, markup = build_broadcast_menu()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    if data.startswith("adm_bc_target_"):
        target_type = data.replace("adm_bc_target_", "")
        recipients = models.get_broadcast_recipients(target_type)
        count = len(recipients)
        
        ADMIN_STATES[user.id] = {
            "state": "WAITING_BROADCAST_TEXT",
            "target_type": target_type,
            "count": count,
            "buttons": []
        }
        target_title = "⭐ TELEGRAM PREMIUM MEMBERS" if target_type == "premium" else target_type.upper()
        text = (
            f"📢 <b>NEW BROADCAST ANNOUNCEMENT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Target Group: <b>{target_title}</b>\n"
            f"Audience Reach: <b>{count} recipient(s)</b>\n\n"
            f"👉 <b>Type your announcement text (or send a photo with caption).</b>\n\n"
            f"🎨 <b>BROADCAST WITH COLORED BUTTONS:</b>\n"
            f"• Tap any color button below to add a solid button to this broadcast right now!\n"
            f"• OR write buttons directly in your text: <code>[Label | link | green/blue/red]</code>"
        )
        quick_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🟢 Add Green Button", callback_data="adm_bc_pickcolor_green", api_kwargs={"style": "success"}),
                InlineKeyboardButton("🔵 Add Blue Button", callback_data="adm_bc_pickcolor_blue", api_kwargs={"style": "primary"})
            ],
            [
                InlineKeyboardButton("🔴 Add Red Button", callback_data="adm_bc_pickcolor_red", api_kwargs={"style": "danger"}),
                InlineKeyboardButton("⚪ Add Default Button", callback_data="adm_bc_pickcolor_default")
            ],
            [
                InlineKeyboardButton("🔙 Cancel Broadcast", callback_data="adm_broadcast_menu")
            ]
        ])
        await safe_edit_or_reply(query, text, reply_markup=quick_markup)
        return

    if data == "adm_bc_add_btn":
        state_info = ADMIN_STATES.get(user.id)
        if not state_info or state_info.get("state") not in ("CONFIRM_BROADCAST", "WAITING_BC_BUTTON_INPUT", "WAITING_BROADCAST_TEXT"):
            await query.answer("Broadcast session expired", show_alert=True)
            return
        text, markup = build_button_color_picker_view()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data.startswith("adm_bc_pickcolor_"):
        color = data.replace("adm_bc_pickcolor_", "")
        theme = BUTTON_STYLES.get(color, BUTTON_STYLES.get("default", {}))
        style = theme.get("style")
        state_info = ADMIN_STATES.get(user.id, {})
        if "data" in state_info:
            state_data = state_info["data"]
        else:
            state_data = {
                "target_type": state_info.get("target_type", "all"),
                "count": state_info.get("count", 0),
                "text": state_info.get("text", "📢 Announcement from Store"),
                "buttons": state_info.get("buttons", [])
            }

        ADMIN_STATES[user.id] = {
            "state": "WAITING_BC_BUTTON_INPUT",
            "data": state_data,
            "selected_color": color,
            "selected_style": style
        }
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_bc_preview_back")]])
        prompt = (
            f"🎨 <b>Selected Button Color:</b> <b>{theme.get('name', color)}</b> ({theme.get('badge', '')})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <b>Type the Button Label and Link URL:</b>\n\n"
            f"Format: <code>Button Text | https://example.com</code>\n"
            f"Example: <code>Unlimited Data & Voice Packages | https://t.me/trust994</code>\n\n"
            f"<i>• Solid background color ({theme.get('badge', '')}) will be applied to the button natively in Telegram!</i>\n"
            f"<i>• Clean text will be used without forced emojis.</i>"
        )
        await safe_edit_or_reply(query, prompt, reply_markup=cancel_markup)
        return

    if data == "adm_bc_preview_back":
        state_info = ADMIN_STATES.get(user.id)
        if state_info and "data" in state_info:
            ADMIN_STATES[user.id] = {"state": "CONFIRM_BROADCAST", "data": state_info["data"]}
            text, markup = build_broadcast_preview_view(state_info["data"])
            await safe_edit_or_reply(query, text, reply_markup=markup)
        else:
            text, markup = build_broadcast_menu()
            await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data == "adm_bc_clear_btns":
        state_info = ADMIN_STATES.get(user.id)
        if state_info and "data" in state_info:
            state_info["data"]["buttons"] = []
            ADMIN_STATES[user.id] = {"state": "CONFIRM_BROADCAST", "data": state_info["data"]}
            await query.answer("Buttons cleared", show_alert=False)
            text, markup = build_broadcast_preview_view(state_info["data"])
            await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    if data == "adm_bc_confirm_send":
        state_info = ADMIN_STATES.get(user.id)
        if not state_info or state_info.get("state") != "CONFIRM_BROADCAST":
            await query.answer("Broadcast session expired", show_alert=True)
            return
            
        bdata = state_info["data"]
        await safe_edit_or_reply(query, "🚀 <b>Dispatching broadcast messages in parallel... Please wait.</b>")
        
        buttons = bdata.get("buttons", [])
        reply_markup = None
        if buttons:
            btn_rows = []
            for b in buttons:
                btn_dict = {"text": b["text"], "url": b["url"]}
                if b.get("style"):
                    btn_dict["style"] = b["style"]
                btn_rows.append([btn_dict])
            reply_markup = {"inline_keyboard": btn_rows}

        res = models.send_broadcast_message(
            target_type=bdata.get("target_type", "all"),
            message_text=bdata.get("text", ""),
            media_path=bdata.get("media_path"),
            media_type=bdata.get("media_type", "text"),
            reply_markup=reply_markup,
            admin_id=user.id,
            admin_username=f"@{user.username}" if user.username else str(user.id)
        )
        ADMIN_STATES[user.id] = None
        
        result_text = (
            f"✅ <b>BROADCAST COMPLETED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Total Recipients: <b>{res.get('total', 0)}</b>\n"
            f"• Successfully Delivered: <b>{res.get('sent', 0)}</b>\n"
            f"• Failed / Blocked: <b>{res.get('failed', 0)}</b>\n"
        )
        if buttons:
            result_text += f"• Attached Buttons: <b>{len(buttons)}</b> with native solid colors\n"
        result_text += "\nYour broadcast report has been logged."
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")]])
        await safe_edit_or_reply(query, result_text, reply_markup=back_markup)
        return

    # --- Button Colors Hub & Live Demo ---
    if data == "adm_btn_colors":
        ADMIN_STATES[user.id] = None
        text, markup = build_button_colors_hub_view()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data == "adm_btn_noop":
        await query.answer("Native Telegram Button Background Color Active!", show_alert=False)
        return

    if data == "adm_test_btn_send_live":
        try:
            demo_payload = {
                "inline_keyboard": [
                    [{"text": "Unlimited Data & Voice Packages", "style": "success", "url": f"https://t.me/{config.DEFAULT_SUPPORT_USERNAME.replace('@', '')}"}],
                    [{"text": "↶ Back", "style": "primary", "callback_data": "adm_hub"}, {"text": "↻ Refresh", "style": "danger", "callback_data": "adm_hub"}]
                ]
            }
            res = bot_bridge.send_admin_telegram_message(
                "🎨 <b>LIVE TELEGRAM BUTTON COLOR DEMO:</b>\n\n"
                "Here are the native solid colored buttons rendered live in your chat:\n"
                "• 🟢 <b>Green (Success)</b>: <code>Unlimited Data & Voice Packages</code>\n"
                "• 🔵 <b>Blue (Primary)</b>: <code>Back</code>\n"
                "• 🔴 <b>Red (Danger)</b>: <code>Refresh</code>\n\n"
                "<i>This exact full-color access is active across your broadcasts and bot!</i>",
                reply_markup=demo_payload
            )
            if res.get("ok"):
                await query.answer("✅ Live colored buttons sent to your Telegram!", show_alert=True)
            else:
                await query.answer(f"Notice: {res.get('error', 'Sent')}", show_alert=True)
        except Exception as e:
            await query.answer(f"Demo notice: {e}", show_alert=True)
        return

    if data.startswith("adm_btncfg_"):
        key = data.replace("adm_btncfg_", "")
        if key == "reset_defaults":
            models.reset_all_button_colors(admin_id=user.id, admin_username=f"@{user.username}" if user.username else str(user.id))
            await query.answer("All button colors reset to defaults!", show_alert=True)
            text, markup = build_button_colors_hub_view()
            await safe_edit_or_reply(query, text, reply_markup=markup)
            return
        
        text, markup = build_single_button_color_picker(key)
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return

    if data.startswith("adm_setbtncolor_"):
        parts = data.replace("adm_setbtncolor_", "").rsplit("_", 1)
        if len(parts) == 2:
            btn_key, color_style = parts[0], parts[1]
            models.set_button_color(btn_key, color_style, admin_id=user.id, admin_username=f"@{user.username}" if user.username else str(user.id))
            btn_name = BUTTON_DISPLAY_NAMES.get(btn_key, btn_key)
            theme = BUTTON_STYLES.get(color_style, BUTTON_STYLES.get("default", {}))
            await query.answer(f"✅ {btn_name} set to {theme.get('badge', color_style)}!", show_alert=True)
            text, markup = build_button_colors_hub_view()
            await safe_edit_or_reply(query, text, reply_markup=markup)
            return

    # --- Store Settings ---
    if data == "adm_settings_view":
        ADMIN_STATES[user.id] = None
        text, markup = build_settings_view()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    if data.startswith("adm_edit_set_"):
        key = data.replace("adm_edit_set_", "")
        key_names = {
            "telebirr_account": "Telebirr Account Number",
            "telebirr_name": "Telebirr Account Holder Name",
            "cbe_birr_account": "CBE Account Number",
            "cbe_birr_name": "CBE Account Holder Name",
            "support_username": "Support Telegram Username (@username)",
            "store_name": "Store Display Name"
        }
        name = key_names.get(key, key)
        ADMIN_STATES[user.id] = {"state": "WAITING_SETTING_VALUE", "setting_key": key, "setting_name": name}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_settings_view")]])
        await safe_edit_or_reply(
            query,
            f"✏️ <b>Enter new value for: {name}</b>\n\nType the new value and send:",
            reply_markup=cancel_markup
        )
        return

    # --- Activity Logs ---
    if data == "adm_logs_view":
        ADMIN_STATES[user.id] = None
        text, markup = build_logs_view()
        await safe_edit_or_reply(query, text, reply_markup=markup)
        return
        
    # --- Search Prompts ---
    if data == "adm_order_search_prompt":
        ADMIN_STATES[user.id] = {"state": "WAITING_ORDER_SEARCH"}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_orders_all_0")]])
        await safe_edit_or_reply(query, "🔍 <b>Enter Order Number (#ABD-...) or Customer Name:</b>", reply_markup=cancel_markup)
        return
        
    if data == "adm_cust_search_prompt":
        ADMIN_STATES[user.id] = {"state": "WAITING_CUSTOMER_SEARCH"}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_customers_0")]])
        await safe_edit_or_reply(query, "🔍 <b>Enter Customer Username, Full Name, or Telegram ID:</b>", reply_markup=cancel_markup)
        return

    # --- Database Backup File Download ---
    if data == "adm_backup_download":
        await query.answer("Preparing database backup file...", show_alert=False)
        db_path = config.DB_PATH
        if db_path.exists():
            now_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            caption = (
                f"💾 <b>ABDUREA STORE — DATABASE BACKUP</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 <b>Date:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"📦 <b>Size:</b> {os.path.getsize(db_path) / 1024:,.1f} KB\n"
                f"🔒 <i>Complete live SQLite backup containing all products, orders, payments, customers, and store configuration.</i>"
            )
            try:
                with open(db_path, "rb") as f:
                    await query.message.reply_document(
                        document=f,
                        filename=f"abdurea_store_backup_{now_str}.db",
                        caption=caption,
                        parse_mode="HTML"
                    )
            except Exception as e:
                logger.error(f"Error sending backup document: {e}")
        else:
            await query.answer("Database file not found on disk", show_alert=True)
        return

# =====================================================================
# 10. TEXT & MESSAGE INPUT HANDLER (STATE MACHINE)
# =====================================================================

async def admin_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
        
    state_info = ADMIN_STATES.get(user.id)
    if not state_info:
        # No pending action -> show dashboard
        text, pending = build_dashboard_text(user)
        await update.message.reply_html(text, reply_markup=build_main_dashboard_keyboard(pending))
        return
        
    state = state_info.get("state")
    text_content = (update.message.text or update.message.caption or "").strip()
    
    # 1. Delivering Credentials
    if state == "WAITING_DELIVERY_TEXT":
        oid = state_info.get("order_id")
        delivery_content = text_content.strip()
        if not delivery_content:
            await update.message.reply_html("❌ Delivery content cannot be empty. Please enter credentials/account details:")
            return
            
        updated, tg_res = models.deliver_order(
            order_id=oid,
            delivery_content=delivery_content,
            admin_id=user.id,
            admin_username=f"@{user.username}" if user.username else str(user.id)
        )
        ADMIN_STATES[user.id] = None
        
        reply_text = (
            f"🎉 <b>ORDER #{updated['order_number']} DELIVERED & COMPLETED!</b>\n\n"
            f"The digital service credentials have been automatically dispatched to customer <b>{updated['customer_name']}</b> on Telegram."
        )
        back_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 View Order Details", callback_data=f"adm_view_order_{oid}")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")]
        ])
        await update.message.reply_html(reply_text, reply_markup=back_markup)
        return

    # 2. Custom Rejection Reason
    if state == "WAITING_REJECT_REASON":
        oid = state_info.get("order_id")
        reason = text_content.strip() or "Payment verification failed."
        updated, tg_res = models.reject_order_payment(
            order_id=oid,
            reason=reason,
            admin_id=user.id,
            admin_username=f"@{user.username}" if user.username else str(user.id)
        )
        ADMIN_STATES[user.id] = None
        reply_text = f"❌ <b>Payment Rejected for Order #{updated['order_number']}.</b>\nCustomer has been notified with your reason: <i>{reason}</i>"
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Orders", callback_data="adm_orders_all_0")]])
        await update.message.reply_html(reply_text, reply_markup=back_markup)
        return

    # 3. Direct Message to Customer
    if state == "WAITING_DIRECT_MSG":
        target_id = state_info.get("target_id")
        msg = text_content.strip()
        if not msg:
            await update.message.reply_html("❌ Message cannot be empty.")
            return
            
        res = bot_bridge.send_direct_customer_message(
            telegram_id=target_id,
            message_text=msg,
            admin_name=config.STORE_NAME
        )
        ADMIN_STATES[user.id] = None
        if res.get("ok"):
            await update.message.reply_html(
                f"✅ <b>Message successfully sent to customer!</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Customer Profile", callback_data=f"adm_cust_{target_id}")]])
            )
        else:
            await update.message.reply_html(
                f"⚠️ Could not deliver message: {res.get('error', 'Unknown error')}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Customer Profile", callback_data=f"adm_cust_{target_id}")]])
            )
        return

    # 4. Settings Update
    if state == "WAITING_SETTING_VALUE":
        key = state_info.get("setting_key")
        name = state_info.get("setting_name")
        val = text_content.strip()
        models.update_store_settings(
            {key: val},
            admin_id=user.id,
            admin_username=f"@{user.username}" if user.username else str(user.id)
        )
        ADMIN_STATES[user.id] = None
        await update.message.reply_html(
            f"✅ <b>{name} updated to:</b> <code>{val}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Settings", callback_data="adm_settings_view")]])
        )
        return

    # 5. Broadcast Text Input & Image Support
    if state == "WAITING_BROADCAST_TEXT":
        target_type = state_info.get("target_type")
        count = state_info.get("count")
        msg_text = (update.message.text or update.message.caption or "").strip()
        media_path = None
        media_type = "text"

        if update.message.photo:
            try:
                photo_file = await update.message.photo[-1].get_file()
                save_dir = config.RECEIPTS_DIR
                save_dir.mkdir(parents=True, exist_ok=True)
                local_path = save_dir / f"bc_{secrets.token_hex(6)}.jpg"
                await photo_file.download_to_drive(local_path)
                media_path = str(local_path)
                media_type = "photo"
            except Exception as e:
                logger.warning(f"Failed to download broadcast photo: {e}")

        if not msg_text and not media_path:
            await update.message.reply_html("❌ Please provide text or a photo with caption for the broadcast.")
            return

        # Check for pre-existing buttons or inline button tags [Button Name | URL | color]
        existing_buttons = state_info.get("buttons") or state_info.get("data", {}).get("buttons", [])
        import re
        btn_pattern = r'\[([^\|\]]+)\|([^\|\]]+)(?:\|([^\]]+))?\]'
        parsed_buttons = list(existing_buttons)
        if msg_text:
            for m in re.finditer(btn_pattern, msg_text):
                blabel = m.group(1).strip()
                burl = m.group(2).strip()
                bcolor = (m.group(3) or "default").strip().lower()
                theme = BUTTON_STYLES.get(bcolor, BUTTON_STYLES.get("default", {}))
                bstyle = theme.get("style")
                parsed_buttons.append({"text": blabel, "url": burl, "style": bstyle, "color": bcolor})

            clean_text = re.sub(btn_pattern, '', msg_text).strip()
            if clean_text:
                msg_text = clean_text

        bdata = {
            "target_type": target_type,
            "text": msg_text,
            "count": count,
            "media_path": media_path,
            "media_type": media_type,
            "buttons": parsed_buttons
        }
        ADMIN_STATES[user.id] = {"state": "CONFIRM_BROADCAST", "data": bdata}
        text, markup = build_broadcast_preview_view(bdata)
        await update.message.reply_html(text, reply_markup=markup)
        return

    # 5b. Broadcast Button Composer Input
    if state == "WAITING_BC_BUTTON_INPUT":
        bdata = state_info.get("data", {})
        color = state_info.get("selected_color", "default")
        theme = BUTTON_STYLES.get(color, BUTTON_STYLES.get("default", {}))
        style = state_info.get("selected_style") or theme.get("style")
        raw = text_content.strip()

        if "|" in raw:
            parts = [p.strip() for p in raw.split("|")]
            btn_label = parts[0]
            btn_url = parts[1]
            if len(parts) >= 3:
                c_override = parts[2].lower()
                if c_override in BUTTON_STYLES:
                    color = c_override
                    theme = BUTTON_STYLES[color]
                    style = theme.get("style")
        elif raw.startswith("http://") or raw.startswith("https://") or raw.startswith("t.me/"):
            btn_label = "Open Link"
            btn_url = raw
        else:
            btn_label = raw
            btn_url = f"https://t.me/{config.DEFAULT_SUPPORT_USERNAME.replace('@', '')}"

        if btn_url.startswith("t.me/"):
            btn_url = "https://" + btn_url
        elif not (btn_url.startswith("http://") or btn_url.startswith("https://") or btn_url.startswith("tg://")):
            btn_url = "https://" + btn_url

        # Store button with clean label and native style (NO forced emojis)
        if "buttons" not in bdata:
            bdata["buttons"] = []
        bdata["buttons"].append({
            "text": btn_label,
            "url": btn_url,
            "style": style,
            "color": color
        })

        ADMIN_STATES[user.id] = {"state": "CONFIRM_BROADCAST", "data": bdata}
        text, markup = build_broadcast_preview_view(bdata)
        style_name = theme.get("name", "Full Color")
        await update.message.reply_html(
            f"✅ <b>Button Added: [{btn_label}]</b> ({style_name})\n\n" + text,
            reply_markup=markup
        )
        return

    # 6. Add Product Wizard
    if state == "WAITING_NEW_PROD_NAME":
        p_name = text_content.strip()
        if not p_name:
            await update.message.reply_html("❌ Service name cannot be empty. Please enter the service name:")
            return
        state_info["data"]["name"] = p_name
        ADMIN_STATES[user.id] = {"state": "WAITING_NEW_PROD_DESC", "data": state_info["data"]}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_products_list")]])
        safe_p_name = html.escape(p_name)
        await update.message.reply_html(
            f"✅ Name: <b>{safe_p_name}</b>\n\n👉 <b>Enter Short Description (Step 2/5):</b>\n<i>(e.g. Ultra HD 4K, Private Account, 30 Days Warranty)</i>",
            reply_markup=cancel_markup
        )
        return
        
    if state == "WAITING_NEW_PROD_DESC":
        desc = text_content.strip()
        state_info["data"]["description"] = desc
        ADMIN_STATES[user.id] = {"state": "WAITING_NEW_PROD_IMAGE", "data": state_info["data"]}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_products_list")]])
        await update.message.reply_html(
            f"✅ Description saved.\n\n"
            f"📸 <b>UPLOAD PRODUCT BANNER / IMAGE (Step 3/5)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <b>Please upload or send a photo for this product banner</b> (or paste a direct image URL):\n\n"
            f"<i>• The uploaded image will be displayed on the customer product card.</i>\n"
            f"<i>• An image is required to complete adding this product.</i>",
            reply_markup=cancel_markup
        )
        return

    if state == "WAITING_NEW_PROD_IMAGE":
        msg = update.effective_message
        img_url = ""
        if msg and msg.photo:
            try:
                photo_file = await msg.photo[-1].get_file()
                photo_bytes = await photo_file.download_as_bytearray()
                img_url = process_image_to_base64(bytes(photo_bytes))
                try:
                    save_dir = config.PRODUCT_IMAGES_DIR
                    save_dir.mkdir(parents=True, exist_ok=True)
                    filename = f"prod_{secrets.token_hex(6)}.jpg"
                    (save_dir / filename).write_bytes(bytes(photo_bytes))
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Failed to download product photo: {e}")
        elif msg and msg.document:
            mime = (msg.document.mime_type or "").lower()
            fname = (msg.document.file_name or "").lower()
            if mime.startswith("image/") or fname.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")):
                try:
                    doc_file = await msg.document.get_file()
                    doc_bytes = await doc_file.download_as_bytearray()
                    img_url = process_image_to_base64(bytes(doc_bytes))
                    try:
                        save_dir = config.PRODUCT_IMAGES_DIR
                        save_dir.mkdir(parents=True, exist_ok=True)
                        filename = f"prod_{secrets.token_hex(6)}.jpg"
                        (save_dir / filename).write_bytes(bytes(doc_bytes))
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"Failed to download product document: {e}")
        elif text_content.startswith("http://") or text_content.startswith("https://"):
            img_url = text_content
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    resp = await client.get(img_url, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200 and resp.content:
                        img_url = process_image_to_base64(resp.content)
            except Exception as fe:
                logger.debug(f"Direct download fallback for URL: {fe}")

        if not img_url:
            cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_products_list")]])
            await update.message.reply_html(
                "❌ <b>Product image is required!</b>\n\n"
                "👉 Please upload a photo (send as image) or paste a valid image URL (e.g. <code>https://...</code>):",
                reply_markup=cancel_markup
            )
            return

        state_info["data"]["image_url"] = img_url
        ADMIN_STATES[user.id] = {"state": "WAITING_NEW_PROD_PLAN_DUR", "data": state_info["data"]}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_products_list")]])
        await update.message.reply_html(
            f"✅ <b>Image received and attached!</b>\n\n"
            f"👉 <b>Enter Plan Duration/Name (Step 4/5):</b>\n<i>(e.g. '1 Month', '3 Months', '1 Year', 'Lifetime')</i>",
            reply_markup=cancel_markup
        )
        return
        
    if state == "WAITING_NEW_PROD_PLAN_DUR":
        dur = text_content.strip()
        if not dur:
            await update.message.reply_html("❌ Duration cannot be empty. (e.g. '1 Month', '1 Year'):")
            return
        state_info["data"]["duration"] = dur
        ADMIN_STATES[user.id] = {"state": "WAITING_NEW_PROD_PRICE", "data": state_info["data"]}
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="adm_products_list")]])
        safe_dur = html.escape(dur)
        await update.message.reply_html(
            f"✅ Duration: <b>{safe_dur}</b>\n\n👉 <b>Enter Plan Price in ETB (Step 5/5)</b> (e.g. <code>450</code>):",
            reply_markup=cancel_markup
        )
        return
        
    if state == "WAITING_NEW_PROD_PRICE":
        try:
            price = float(text_content.strip().replace(",", ""))
        except ValueError:
            await update.message.reply_html("❌ Please enter a valid numeric price (e.g. <code>450</code>):")
            return
            
        pdata = state_info["data"]
        new_prod = models.create_product(
            name=pdata["name"],
            description=pdata["description"],
            image_url=pdata.get("image_url", ""),
            active=1,
            plans=[{"name": pdata["duration"], "duration": pdata["duration"], "price": price, "currency": "ETB", "active": 1}]
        )
        ADMIN_STATES[user.id] = None

        # Auto-broadcast new product announcement with image and full info to all customers
        try:
            all_users = models.get_broadcast_recipients("all") or []
            recipient_set = set(all_users)
            if user and user.id:
                recipient_set.add(user.id)
            if getattr(config, "ADMIN_TELEGRAM_ID", None):
                try:
                    recipient_set.add(int(config.ADMIN_TELEGRAM_ID))
                except Exception:
                    pass
            for aid in getattr(config, "ADMIN_TELEGRAM_IDS", []):
                try:
                    recipient_set.add(int(aid))
                except Exception:
                    pass

            ordered_recipients = []
            if user and user.id:
                ordered_recipients.append(user.id)
            if getattr(config, "ADMIN_TELEGRAM_ID", None):
                try:
                    aid = int(config.ADMIN_TELEGRAM_ID)
                    if aid not in ordered_recipients:
                        ordered_recipients.append(aid)
                except Exception:
                    pass
            for r in recipient_set:
                if r not in ordered_recipients:
                    ordered_recipients.append(r)

            if ordered_recipients:
                bc_text = (
                    f"🎉 <b>NEW SERVICE ADDED TO THE STORE!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ <b>{new_prod['name'].upper()}</b>\n\n"
                    f"📝 <b>Description:</b>\n{pdata.get('description', '')}\n\n"
                    f"💰 <b>Pricing:</b> {pdata['duration']} — <b>{price:,.0f} ETB</b>\n\n"
                    f"🔥 <i>Available right now in the store! Tap below to view and order:</i>"
                )
                cb_prod = new_prod['id'][5:] if new_prod['id'].startswith('prod_') else new_prod['id']
                bc_markup = {
                    "inline_keyboard": [
                        [{"text": f"🛒 View & Order {new_prod['name']}", "callback_data": f"prod_{cb_prod}", "style": "success"}],
                        [{"text": "🛍️ Open Catalog", "callback_data": "menu_services", "style": "primary"}]
                    ]
                }
                bc_media = pdata.get("image_url", "")
                import threading
                def _bg_broadcast():
                    bot_bridge.broadcast_to_users(
                        recipients=ordered_recipients,
                        message_text=bc_text,
                        media_path=bc_media if bc_media else None,
                        media_type="photo" if bc_media else "text",
                        reply_markup=bc_markup
                    )
                threading.Thread(target=_bg_broadcast, daemon=True).start()
        except Exception as be:
            logger.warning(f"New product broadcast notice: {be}")

        text = (
            f"🎉 <b>NEW SERVICE CREATED SUCCESSFULLY!</b>\n\n"
            f"• <b>Name:</b> {new_prod['name']}\n"
            f"• <b>Plan:</b> {pdata['duration']} — <b>{price:,.0f} ETB</b>\n"
            f"• <b>Image:</b> 🖼️ Attached & Live\n"
            f"• <b>Status:</b> 🟢 Active & Announced to Customers\n\n"
            f"This service is now live and available to customers in the catalog!"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ View in Catalog", callback_data=f"adm_prod_view_{new_prod['id']}")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="adm_hub")]
        ])
        await update.message.reply_html(text, reply_markup=markup)
        return

    # 7. Product & Plan Editing States
    if state == "WAITING_EDIT_PROD_NAME":
        pid = state_info.get("product_id")
        new_name = text_content.strip()
        if not new_name:
            await update.message.reply_html("❌ Service name cannot be empty.")
            return
        models.update_product_field(pid, "name", new_name)
        ADMIN_STATES[user.id] = None
        text, markup = build_product_details_view(pid)
        await update.message.reply_html(f"✅ <b>Service name updated to: {new_name}</b>\n\n" + text, reply_markup=markup)
        return

    if state == "WAITING_EDIT_PROD_DESC":
        pid = state_info.get("product_id")
        new_desc = text_content.strip()
        models.update_product_field(pid, "description", new_desc)
        ADMIN_STATES[user.id] = None
        text, markup = build_product_details_view(pid)
        await update.message.reply_html("✅ <b>Description updated!</b>\n\n" + text, reply_markup=markup)
        return

    if state == "WAITING_EDIT_PROD_IMG":
        pid = state_info.get("product_id")
        msg = update.effective_message
        img_val = text_content.strip()
        if msg and msg.photo:
            try:
                photo_file = await msg.photo[-1].get_file()
                photo_bytes = await photo_file.download_as_bytearray()
                img_val = process_image_to_base64(bytes(photo_bytes))
                try:
                    save_dir = config.PRODUCT_IMAGES_DIR
                    save_dir.mkdir(parents=True, exist_ok=True)
                    (save_dir / f"banner_{pid}.jpg").write_bytes(bytes(photo_bytes))
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Failed to process banner photo: {e}")
        elif msg and msg.document:
            mime = (msg.document.mime_type or "").lower()
            fname = (msg.document.file_name or "").lower()
            if mime.startswith("image/") or fname.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")):
                try:
                    doc_file = await msg.document.get_file()
                    doc_bytes = await doc_file.download_as_bytearray()
                    img_val = process_image_to_base64(bytes(doc_bytes))
                    try:
                        save_dir = config.PRODUCT_IMAGES_DIR
                        save_dir.mkdir(parents=True, exist_ok=True)
                        (save_dir / f"banner_{pid}.jpg").write_bytes(bytes(doc_bytes))
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"Failed to process banner document: {e}")
        elif img_val.startswith("http://") or img_val.startswith("https://"):
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    resp = await client.get(img_val, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200 and resp.content:
                        img_val = process_image_to_base64(resp.content)
            except Exception as fe:
                logger.debug(f"Direct download fallback for URL: {fe}")

        if not img_val:
            await update.message.reply_html("❌ Please send a photo or paste an image URL.")
            return
        models.update_product_field(pid, "image_url", img_val)
        ADMIN_STATES[user.id] = None
        text, markup = build_product_details_view(pid)
        await update.message.reply_html("✅ <b>Service image/banner updated!</b>\n\n" + text, reply_markup=markup)
        return

    if state == "WAITING_EDIT_PLAN_PRICE":
        try:
            new_price = float(text_content.strip().replace(",", ""))
        except ValueError:
            await update.message.reply_html("❌ Please enter a valid numeric price (e.g. <code>500</code>):")
            return
            
        plan_id = state_info.get("plan_id")
        models.update_plan_details(plan_id, price=new_price)
        ADMIN_STATES[user.id] = None
        text, markup = build_single_plan_view(plan_id)
        await update.message.reply_html(f"✅ <b>Price updated to {new_price:,.0f} ETB!</b>\n\n" + text, reply_markup=markup)
        return

    if state == "WAITING_EDIT_PLAN_NAME":
        new_name = text_content.strip()
        if not new_name:
            await update.message.reply_html("❌ Plan duration/name cannot be empty.")
            return
        plan_id = state_info.get("plan_id")
        models.update_plan_details(plan_id, name=new_name, duration=new_name)
        ADMIN_STATES[user.id] = None
        text, markup = build_single_plan_view(plan_id)
        await update.message.reply_html(f"✅ <b>Plan duration/name updated to {new_name}!</b>\n\n" + text, reply_markup=markup)
        return

    if state == "WAITING_ADD_PLAN_NAME":
        dur = text_content.strip()
        if not dur:
            await update.message.reply_html("❌ Plan duration cannot be empty.")
            return
        pid = state_info.get("product_id")
        ADMIN_STATES[user.id] = {
            "state": "WAITING_ADD_PLAN_PRICE",
            "product_id": pid,
            "data": {"duration": dur}
        }
        await update.message.reply_html(
            f"✅ Duration: <b>{dur}</b>\n\n👉 <b>Enter plan price in ETB (e.g. <code>450</code>):</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data=f"adm_prod_plans_{pid}")]])
        )
        return

    if state == "WAITING_ADD_PLAN_PRICE":
        try:
            price = float(text_content.strip().replace(",", ""))
        except ValueError:
            await update.message.reply_html("❌ Please enter a valid numeric price (e.g. <code>450</code>):")
            return
        pid = state_info.get("product_id")
        pdata = state_info.get("data", {})
        dur = pdata.get("duration", "Standard")
        models.add_product_plan(pid, name=dur, duration=dur, price=price)
        ADMIN_STATES[user.id] = None
        text, markup = build_product_plans_view(pid)
        await update.message.reply_html(
            f"🎉 <b>NEW PLAN ADDED!</b>\n• {dur} — <b>{price:,.0f} ETB</b>\n\n" + text,
            reply_markup=markup
        )
        return

    # 8. Order Search
    if state == "WAITING_ORDER_SEARCH":
        query_str = text_content.strip()
        orders = models.get_orders_list(search_query=query_str, limit=5)
        ADMIN_STATES[user.id] = None
        if not orders:
            await update.message.reply_html(
                f"❌ No orders matching '<b>{query_str}</b>'.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Orders", callback_data="adm_orders_all_0")]])
            )
            return
            
        keyboard = []
        for o in orders:
            keyboard.append([InlineKeyboardButton(f"#{o['order_number']} • {o['product_name']} ({o['amount']} ETB)", callback_data=f"adm_view_order_{o['id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Back to Orders", callback_data="adm_orders_all_0")])
        await update.message.reply_html(f"🔍 <b>Search results for '{query_str}':</b>", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 9. Customer Search
    if state == "WAITING_CUSTOMER_SEARCH":
        query_str = text_content.strip()
        custs = models.get_customers_list(search_query=query_str, limit=5)
        ADMIN_STATES[user.id] = None
        if not custs:
            await update.message.reply_html(
                f"❌ No customers matching '<b>{query_str}</b>'.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Directory", callback_data="adm_customers_0")]])
            )
            return
            
        keyboard = []
        for c in custs:
            name = c.get("full_name") or c.get("first_name") or f"User {c.get('telegram_id')}"
            keyboard.append([InlineKeyboardButton(f"👤 {name} (@{c.get('telegram_username') or 'none'})", callback_data=f"adm_cust_{c.get('telegram_id')}")])
        keyboard.append([InlineKeyboardButton("🔙 Back to Directory", callback_data="adm_customers_0")])
        await update.message.reply_html(f"🔍 <b>Found {len(custs)} customer(s):</b>", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 10. Mini App Banner Update
    if state == "WAITING_MINIAPP_BANNER":
        banner_text = text_content.strip()
        ADMIN_STATES[user.id] = None
        if banner_text.lower() in ("off", "none", "clear", "disable"):
            models.update_store_settings({"miniapp_banner_active": "0", "miniapp_banner_text": ""}, admin_id=user.id, admin_username=f"@{user.username}" if user.username else str(user.id))
            reply = "🗑️ <b>Mini App Announcement Banner has been disabled.</b>"
        else:
            models.update_store_settings({"miniapp_banner_active": "1", "miniapp_banner_text": banner_text}, admin_id=user.id, admin_username=f"@{user.username}" if user.username else str(user.id))
            reply = f"✅ <b>Mini App Announcement Banner updated & published live!</b>\n\n<i>\"{banner_text}\"</i>"

        text, markup = build_miniapp_hub_view()
        await update.message.reply_html(reply + "\n\n" + text, reply_markup=markup)
        return

    # 11. Mini App Live URL Update
    if state == "WAITING_MINIAPP_URL":
        new_url = text_content.strip()
        ADMIN_STATES[user.id] = None
        if not new_url.startswith("http://") and not new_url.startswith("https://"):
            new_url = "https://" + new_url
        models.update_store_settings({"miniapp_url": new_url}, admin_id=user.id, admin_username=f"@{user.username}" if user.username else str(user.id))
        setattr(config, "MINIAPP_URL", new_url)
        reply = f"✅ <b>Mini App Live URL updated to:</b>\n<code>{new_url}</code>"
        text, markup = build_miniapp_hub_view()
        await update.message.reply_html(reply + "\n\n" + text, reply_markup=markup)
        return

    # 12. General Setting Update
    if state == "WAITING_SETTING_VALUE":
        key = state_info.get("setting_key")
        sname = state_info.get("setting_name", key)
        val = text_content.strip()
        models.update_store_settings({key: val}, admin_id=user.id, admin_username=f"@{user.username}" if user.username else str(user.id))
        ADMIN_STATES[user.id] = None
        text, markup = build_settings_view()
        await update.message.reply_html(f"✅ <b>{sname} updated to:</b> <code>{val}</code>\n\n" + text, reply_markup=markup)
        return

# =====================================================================
# 11. COMMAND SHORTCUTS & MENU REGISTRATION
# =====================================================================

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    kpis = models.get_dashboard_kpis()
    pending = kpis.get("payments_under_review", 0)
    ftype = "pending" if pending > 0 else "all"
    text, markup = build_orders_list_view(ftype, 0)
    await update.message.reply_html(text, reply_markup=markup)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    text, markup = build_analytics_view("all")
    await update.message.reply_html(text, reply_markup=markup)

async def products_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    text, markup = build_products_list_view()
    await update.message.reply_html(text, reply_markup=markup)

async def customers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    text, markup = build_customers_list_view(0)
    await update.message.reply_html(text, reply_markup=markup)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    text, markup = build_broadcast_menu()
    await update.message.reply_html(text, reply_markup=markup)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    text, markup = build_settings_view()
    await update.message.reply_html(text, reply_markup=markup)

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    db_path = config.DB_PATH
    if not db_path.exists():
        await update.message.reply_html("❌ Database file not found on disk.")
        return
    now_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    caption = (
        f"💾 <b>ABDUREA STORE — DATABASE BACKUP</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 <b>Date:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"📦 <b>Size:</b> {os.path.getsize(db_path) / 1024:,.1f} KB\n"
        f"🔒 <i>Complete live SQLite backup containing all products, orders, payments, customers, and store configuration.</i>"
    )
    try:
        with open(db_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"abdurea_store_backup_{now_str}.db",
                caption=caption,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Error sending backup document: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    text = (
        f"👑 <b>ABDUREA STORE — ADMIN QUICK GUIDE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>Fast Command Shortcuts:</b>\n"
        f"• /admin or /start — Open Main Control Hub\n"
        f"• /orders — View Orders & Review Queue\n"
        f"• /stats — Financial Revenue & Analytics\n"
        f"• /products — Manage Catalog & Plan Prices\n"
        f"• /customers — Customer CRM & History\n"
        f"• /broadcast — Send Store-wide Announcements\n"
        f"• /backup — Download 1-Click Database File\n"
        f"• /settings — Official Bank Accounts & Config\n\n"
        f"🔍 <b>Direct Lookups:</b>\n"
        f"• Type <code>/order ABD-2026...</code> to view an order\n"
        f"• Type <code>/customer 6666443909</code> to view a customer\n\n"
        f"💡 <i>Tip: Tap the blue <b>[Menu]</b> button at the bottom-left anytime for instant shortcuts!</i>"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Open Dashboard", callback_data="adm_hub")]])
    await update.message.reply_html(text, reply_markup=markup)

async def direct_order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    args = context.args
    if not args:
        await update.message.reply_html("🔍 Usage: <code>/order ABD-20260924-001</code> or <code>/order 21</code>")
        return
    q = args[0].strip().replace("#", "")
    order = None
    if q.isdigit():
        order = models.get_order_by_id(int(q))
    if not order:
        order = models.get_order_by_number(q)
    if not order:
        res = models.get_orders_list(search_query=q, limit=1)
        if res:
            order = res[0]
    if order:
        text, markup = build_order_details_view(order["id"])
        await update.message.reply_html(text, reply_markup=markup)
    else:
        await update.message.reply_html(f"❌ Order '<b>{q}</b>' not found.")

async def direct_customer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    args = context.args
    if not args:
        await update.message.reply_html("🔍 Usage: <code>/customer 6666443909</code> or <code>/customer username</code>")
        return
    q = args[0].strip().replace("@", "")
    cust = None
    if q.isdigit():
        cust = models.get_user_by_telegram_id(int(q))
    if not cust:
        res = models.get_customers_list(search_query=q, limit=1)
        if res:
            cust = res[0]
    if cust:
        text, markup = build_customer_profile_view(cust["telegram_id"])
        await update.message.reply_html(text, reply_markup=markup)
    else:
        await update.message.reply_html(f"❌ Customer '<b>{q}</b>' not found.")

async def miniapp_command_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id, user.username):
        await update.message.reply_html(get_auth_denied_message())
        return
    text, markup = build_miniapp_hub_view()
    await update.message.reply_html(text, reply_markup=markup)

async def post_init_admin(application):
    try:
        commands = [
            BotCommand("admin", "👑 Main Control Hub"),
            BotCommand("miniapp", "📱 Mini App Control Hub"),
            BotCommand("orders", "📦 Orders & Review Queue"),
            BotCommand("stats", "📊 Financial Analytics"),
            BotCommand("products", "🛍️ Product Catalog & Prices"),
            BotCommand("customers", "👥 Customer CRM Directory"),
            BotCommand("broadcast", "📢 Broadcast Announcement"),
            BotCommand("backup", "💾 Download Database Backup"),
            BotCommand("settings", "⚙️ Store & Payment Accounts"),
            BotCommand("help", "❓ Admin Shortcuts & Help"),
        ]
        await application.bot.set_my_commands(commands)
        logger.info("Admin Bot Telegram commands menu registered successfully.")
    except Exception as e:
        logger.warning(f"Could not register admin commands menu: {e}")

# =====================================================================
# 12. APPLICATION BUILDER
# =====================================================================

def build_admin_bot_application():
    token = getattr(config, "ADMIN_BOT_TOKEN", "").strip()
    if not token or token.startswith("YOUR_"):
        raise ValueError("ADMIN_BOT_TOKEN is not configured in .env or config.py")
        
    req = HTTPXRequest(
        connection_pool_size=30,
        read_timeout=30.0,
        write_timeout=30.0,
        connect_timeout=20.0
    )
    
    app = (
        ApplicationBuilder()
        .token(token)
        .request(req)
        .post_init(post_init_admin)
        .build()
    )
    
    # Handlers
    app.add_handler(CommandHandler(["start", "admin", "dashboard", "menu"], start_admin))
    app.add_handler(CommandHandler(["miniapp", "app", "store_app"], miniapp_command_admin))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler(["stats", "analytics", "revenue"], stats_command))
    app.add_handler(CommandHandler(["products", "catalog", "services"], products_command))
    app.add_handler(CommandHandler(["customers", "users"], customers_command))
    app.add_handler(CommandHandler(["broadcast", "announce"], broadcast_command))
    app.add_handler(CommandHandler(["backup", "export_db"], backup_command))
    app.add_handler(CommandHandler(["settings", "config"], settings_command))
    app.add_handler(CommandHandler(["help", "guide"], help_command))
    app.add_handler(CommandHandler("order", direct_order_command))
    app.add_handler(CommandHandler("customer", direct_customer_command))
    
    app.add_handler(CallbackQueryHandler(admin_callback_router))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, admin_message_router))
    
    return app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logger.info("Starting ABDUREA STORE Telegram Admin Bot...")
    app = build_admin_bot_application()
    app.run_polling()
