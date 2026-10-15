# -*- coding: utf-8 -*-
"""
Production-Ready Telegram Commerce Bot for ABDUREA STORE.
Implements the exact sequential payment flow:
/start -> Welcome -> Browse Services -> Select Service -> Select Plan -> Confirm Order
-> Choose Payment Method -> Payment Instructions -> Complete Payment -> Upload Receipt
-> Customer Identity & Delivery Info -> Review Submitted Info -> Create Order
-> Unique Order ID (#ABD-YYYYMMDD-XXX) -> Customer Confirmation -> Admin Notification -> Review
Includes restart-resilient state persistence in SQLite, update idempotency, and friendly error UX.
"""
import os
import io
import json
import base64
import logging
import datetime
import asyncio
import httpx
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    MenuButtonDefault,
    BotCommand,
    InputMediaPhoto
)
from telegram.constants import ChatMemberStatus
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
import i18n

logger = logging.getLogger("telegram_bot")

# Status badge mapping for My Orders
STATUS_BADGES = {
    "NEW": "🟡 NEW",
    "AWAITING_PAYMENT": "🟡 AWAITING PAYMENT",
    "PENDING_PAYMENT": "🟡 AWAITING PAYMENT",
    "PAYMENT_SUBMITTED": "🟠 UNDER REVIEW",
    "UNDER_REVIEW": "🟠 UNDER REVIEW",
    "PAYMENT_REVIEW": "🟠 UNDER REVIEW",
    "PAYMENT_APPROVED": "🔵 PAYMENT APPROVED",
    "ACCEPTED": "🔵 PAYMENT APPROVED",
    "PROCESSING": "🔵 PROCESSING",
    "PAYMENT_REJECTED": "🔴 REJECTED",
    "REJECTED": "🔴 REJECTED",
    "COMPLETED": "🟢 COMPLETED",
    "CANCELLED": "⚫ CANCELLED"
}

# ----------------- INLINE KEYBOARD BUILDERS -----------------

_CHAR_VISUAL_WEIGHTS = {
    'i': 3.5, 'j': 3.5, 'l': 3.5, '!': 3.5, '|': 3.5, '.': 3.5, ':': 3.5, ';': 3.5, '\'': 3.5, '`': 3.5,
    'f': 4.5, 't': 4.5, 'I': 4.5, ' ': 4.0,
    'r': 5.5, '-': 5.5, 's': 6.5, 'c': 6.5, 'z': 6.5,
    'a': 7.5, 'b': 7.5, 'd': 7.5, 'e': 7.5, 'g': 7.5, 'h': 7.5, 'k': 7.5, 'n': 7.5, 'o': 7.5, 'p': 7.5, 'q': 7.5, 'u': 7.5, 'v': 7.5, 'x': 7.5, 'y': 7.5,
    '+': 8.5, '0': 8.5, '1': 8.5, '2': 8.5, '3': 8.5, '4': 8.5, '5': 8.5, '6': 8.5, '7': 8.5, '8': 8.5, '9': 8.5,
    'w': 10.5, 'm': 11.5,
    'A': 8.5, 'B': 8.5, 'C': 8.5, 'D': 9.0, 'E': 8.5, 'F': 8.0, 'G': 9.5, 'H': 9.5, 'J': 6.5, 'K': 8.5, 'L': 7.5, 'N': 9.5, 'O': 9.5, 'P': 8.5, 'Q': 9.5, 'R': 9.0, 'S': 8.5, 'T': 8.5, 'U': 9.5, 'V': 8.5, 'X': 8.5, 'Y': 8.5, 'Z': 8.5,
    'M': 12.0, 'W': 13.0
}

def estimate_product_name_width(text: str) -> float:
    """Estimates visual pixel width of a product name string."""
    return sum(_CHAR_VISUAL_WEIGHTS.get(c, 8.0) for c in text)

def format_aligned_product_button(name: str, target_width: float = 140.0, icon: str = "👉 ") -> str:
    """
    Formats product button text so that all buttons align in a straight vertical column (ቀጥ ባለ መደዳ / Left-justified).
    Normalizes visual pixel width across all product buttons using invisible Braille blanks (\u2800 = 9px),
    ensuring Telegram's client-side centering places every product's left edge at the exact same horizontal coordinate.
    """
    clean_name = name.strip()
    w = estimate_product_name_width(clean_name)
    needed = max(0, int(round((target_width - w) / 9.0)))
    return f"{icon}{clean_name}" + ("\u2800" * needed)

def get_main_menu_keyboard(lang="en"):
    return i18n.get_main_menu_keyboard(lang)

def get_navigation_buttons(back_callback="menu_services", lang="en"):
    return i18n.get_navigation_buttons(back_callback, lang)

def get_channel_join_keyboard(lang="en"):
    """
    Returns the two required buttons for channel membership verification:
    1. '📢 Join Channel' (URL button pointing to https://t.me/abdurea1)
    2. '✅ Check Membership' (Callback button to trigger verification)
    """
    channel_url = getattr(config, "OFFICIAL_CHANNEL_URL", "https://t.me/abdurea1")
    btn_join = i18n.t("btn_join_channel", lang) or "📢 Join Channel"
    btn_check = i18n.t("btn_check_membership", lang) or "✅ Check Membership"
    keyboard = [
        [InlineKeyboardButton(btn_join, url=channel_url)],
        [InlineKeyboardButton(btn_check, callback_data="check_channel_membership")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_channel_join_text(lang="en"):
    return i18n.t("must_join_channel_prompt", lang)

async def is_user_channel_member(bot, user_id: int) -> tuple[bool, str]:
    """
    Verifies if user is a member of the official Telegram channel (@abdurea1).
    Returns (is_member: bool, reason: str).
    """
    if not getattr(config, "REQUIRE_CHANNEL_MEMBERSHIP", True):
        return True, "not_required"

    channel_username = getattr(config, "OFFICIAL_CHANNEL_USERNAME", "@abdurea1")
    channel_id = getattr(config, "OFFICIAL_CHANNEL_ID", "-1002546804176")
    targets = [channel_username, channel_id]

    last_error = ""
    for target in targets:
        if not target:
            continue
        try:
            member = await bot.get_chat_member(chat_id=target, user_id=user_id)
            status = getattr(member, "status", "")
            # Telegram statuses: 'creator', 'administrator', 'member', 'restricted', 'left', 'kicked'
            if status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER):
                return True, "member"
            elif status == ChatMemberStatus.RESTRICTED:
                if getattr(member, "is_member", True):
                    return True, "restricted_member"
                else:
                    return False, "restricted_not_member"
            elif status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                return False, "not_member"
            else:
                return True, "member"
        except Exception as e:
            last_error = str(e)
            err_lower = last_error.lower()
            if "member list is inaccessible" in err_lower:
                logger.warning(f"Bot is not an administrator in {target}. Inaccessible member list.")
                return False, "bot_not_admin"
            elif "user not found" in err_lower:
                return False, "user_not_found"
            elif "chat not found" in err_lower:
                continue
            else:
                logger.warning(f"Error checking membership on {target}: {e}")
                continue

    return False, last_error or "error"

async def ensure_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Guard function to ensure customer has joined official channel before using any bot feature.
    """
    user = update.effective_user
    if not user:
        return True

    if not getattr(config, "REQUIRE_CHANNEL_MEMBERSHIP", True):
        return True

    if not models.is_channel_verified(user.id):
        lang = models.get_user_language(user.id) or "en"
        text = get_channel_join_text(lang)
        reply_markup = get_channel_join_keyboard(lang)
        if update.callback_query:
            if update.callback_query.data != "check_channel_membership":
                try:
                    await update.callback_query.answer(i18n.t("alert_must_join_channel", lang), show_alert=True)
                except Exception:
                    pass
            await edit_or_reply(update.callback_query, text, reply_markup=reply_markup)
        elif update.message:
            await update.message.reply_html(text, reply_markup=reply_markup)
        return False

    # Live check
    is_member, reason = await is_user_channel_member(context.bot, user.id)
    if not is_member:
        models.set_channel_verified(user.id, False)
        lang = models.get_user_language(user.id) or "en"
        text = get_channel_join_text(lang)
        reply_markup = get_channel_join_keyboard(lang)
        if update.callback_query:
            if update.callback_query.data != "check_channel_membership":
                try:
                    await update.callback_query.answer(i18n.t("alert_must_join_channel", lang), show_alert=True)
                except Exception:
                    pass
            await edit_or_reply(update.callback_query, text, reply_markup=reply_markup)
        elif update.message:
            await update.message.reply_html(text, reply_markup=reply_markup)
        return False

    return True

async def edit_or_reply(query, text, reply_markup=None, parse_mode="HTML"):
    """
    Safely edits the callback query message in place.
    If the current message is a photo message and cannot be edited as text,
    it cleanly deletes the photo message and sends the text message.
    """
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return
        if "no text in the message" in err:
            try:
                await query.message.delete()
            except Exception:
                pass
            try:
                await query.message.reply_html(text, reply_markup=reply_markup)
                return
            except Exception as e2:
                logger.debug(f"reply_html after delete notice: {e2}")
        logger.debug(f"edit_or_reply fallback notice: {e}")

async def load_photo_input(img_url):
    """
    Resolves product image to an open file object, io.BytesIO, or valid Telegram reference.
    Guarantees Telegram NEVER fails with 'Failed to get HTTP URL content'.
    Supports:
    1. Base64 Data URIs (data:image/jpeg;base64,...)
    2. Local file paths and /static/uploads/...
    3. HTTP / HTTPS URLs (downloads directly into io.BytesIO in-memory)
    4. Telegram file_id (if available)
    """
    if not img_url:
        return None

    img_str = str(img_url).strip()

    # 1. Base64 Data URI
    if img_str.startswith("data:image/") and ";base64," in img_str:
        try:
            b64_part = img_str.split(";base64,")[1]
            raw_bytes = base64.b64decode(b64_part)
            bio = io.BytesIO(raw_bytes)
            bio.name = "product.jpg"
            return bio
        except Exception as e:
            logger.warning(f"Failed to decode base64 photo: {e}")

    # 2. Check exact local path
    if os.path.exists(img_str) and not os.path.isdir(img_str):
        try:
            return open(img_str, "rb")
        except Exception:
            pass

    # 3. Check relative /static/ path
    if img_str.startswith("/static/") or img_str.startswith("static/"):
        local_p = config.BASE_DIR / img_str.lstrip("/")
        if local_p.exists() and not local_p.is_dir():
            try:
                return open(local_p, "rb")
            except Exception:
                pass

    # 4. Check filename inside PRODUCT_IMAGES_DIR
    clean_url = img_str.split("?")[0]
    fname = os.path.basename(clean_url)
    if fname:
        p_cand = config.PRODUCT_IMAGES_DIR / fname
        if p_cand.exists() and not p_cand.is_dir():
            try:
                return open(p_cand, "rb")
            except Exception:
                pass

    # 5. If HTTP/HTTPS URL, download bytes in Python (handles Telegram CDN & Base44 webp)
    if img_str.startswith("http://") or img_str.startswith("https://"):
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(img_str, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200 and resp.content:
                    bio = io.BytesIO(resp.content)
                    bio.name = "product.jpg"
                    return bio
        except Exception as e:
            logger.warning(f"Failed to fetch photo from {img_str}: {e}")

    # 6. Telegram file_id (if alphanumeric)
    if not "/" in img_str and not "\\" in img_str and len(img_str) > 10:
        return img_str

    return None

# ----------------- COMMAND HANDLERS -----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /start command:
    Verifies channel membership in @abdurea1 (https://t.me/abdurea1).
    If user has not joined yet, sends channel link with two buttons:
    1. '📢 Join Channel'
    2. '✅ Check Membership'
    If user is a verified member, proceeds to store welcome & language selection.
    """
    user = update.effective_user
    is_prem = 1 if getattr(user, "is_premium", False) else 0
    models.get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_premium=is_prem
    )
    lang = models.get_user_language(user.id) or "en"

    if update.message:
        models.log_conversation_message(
            telegram_id=user.id,
            direction="INCOMING",
            message_type="command",
            message_text="/start",
            message_id=update.message.message_id
        )

    # 1. Check official channel membership (@abdurea1)
    if not models.is_channel_verified(user.id):
        text = get_channel_join_text(lang)
        reply_markup = get_channel_join_keyboard(lang)
        if update.message:
            await update.message.reply_html(text, reply_markup=reply_markup)
        elif update.callback_query:
            await edit_or_reply(update.callback_query, text, reply_markup=reply_markup)
        return

    # If previously verified, also confirm live that user has not left the channel
    is_member, reason = await is_user_channel_member(context.bot, user.id)
    if not is_member:
        models.set_channel_verified(user.id, False)
        text = get_channel_join_text(lang)
        reply_markup = get_channel_join_keyboard(lang)
        if update.message:
            await update.message.reply_html(text, reply_markup=reply_markup)
        elif update.callback_query:
            await edit_or_reply(update.callback_query, text, reply_markup=reply_markup)
        return

    # 2. Member verified -> allow access to store with 1 single clean message
    models.set_channel_verified(user.id, True)
    welcome_text = i18n.t("welcome_text", lang, store_name=config.STORE_NAME, name=user.first_name)
    if getattr(user, "is_premium", False):
        welcome_text += "\n\n⭐ <b>VIP Telegram Premium Member</b> 🌟\n<i>Enjoy high-priority VIP order processing and exclusive digital access!</i>"

    if update.message:
        await update.message.reply_html(
            welcome_text,
            reply_markup=get_main_menu_keyboard(lang)
        )
    elif update.callback_query:
        await edit_or_reply(update.callback_query, welcome_text, reply_markup=get_main_menu_keyboard(lang))


async def services_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays available digital services directly."""
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    lang = models.get_user_language(user.id)
    products = models.get_all_products(include_inactive=False)

    if not products:
        text = i18n.t("no_products", lang)
        if update.message:
            await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_main", lang)))
        elif update.callback_query:
            await edit_or_reply(update.callback_query, text, reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_main", lang)))
        return

    buttons = []
    # 3-color sequence cycling through Green (success), Red (danger), and Blue (primary) like Photo 2
    color_cycle = ["success", "danger", "primary"]
    max_w = max((estimate_product_name_width(p["name"]) for p in products), default=115.0)
    target_w = max(max_w + 22.0, 140.0)

    for idx, p in enumerate(products):
        cb_id = p["id"][5:] if p["id"].startswith("prod_") else p["id"]
        btn_label = format_aligned_product_button(p["name"], target_width=target_w)
        btn_style = color_cycle[idx % len(color_cycle)]
        buttons.append([InlineKeyboardButton(btn_label, callback_data=f"prod_{cb_id}", api_kwargs={"style": btn_style})])

    buttons.extend(get_navigation_buttons("menu_main", lang))
    text = i18n.t("catalog_title", lang)
    if update.message:
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(buttons))
    elif update.callback_query:
        await edit_or_reply(update.callback_query, text, reply_markup=InlineKeyboardMarkup(buttons))

async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays user's orders."""
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    lang = models.get_user_language(user.id)
    orders = models.get_user_orders(user.id, limit=15)

    st_ref = models.get_button_color("btn_color_refresh")
    ref_kwargs = {"callback_data": "menu_orders"}
    if st_ref in ("success", "primary", "danger"):
        ref_kwargs["api_kwargs"] = {"style": st_ref}

    st_back = models.get_button_color("btn_color_back")
    back_kwargs = {"callback_data": "menu_main"}
    if st_back in ("success", "primary", "danger"):
        back_kwargs["api_kwargs"] = {"style": st_back}

    st_browse = models.get_button_color("btn_color_browse")
    browse_kwargs = {"callback_data": "menu_services"}
    if st_browse in ("success", "primary", "danger"):
        browse_kwargs["api_kwargs"] = {"style": st_browse}

    if not orders:
        text = i18n.t("my_orders_empty", lang)
        buttons = [
            [InlineKeyboardButton(i18n.t("btn_browse", lang), **browse_kwargs)],
            [
                InlineKeyboardButton(i18n.t("btn_back", lang), **back_kwargs),
                InlineKeyboardButton("🔄 Refresh", **ref_kwargs)
            ]
        ]
        if update.message:
            await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(buttons))
        elif update.callback_query:
            await edit_or_reply(update.callback_query, text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    buttons = []
    for o in orders:
        badge = STATUS_BADGES.get(o["status"], o["status"])
        buttons.append([
            InlineKeyboardButton(
                f"#{o['order_number']} — {o['product_name']} ({badge})",
                callback_data=f"view_order_{o['id']}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(i18n.t("btn_back", lang), **back_kwargs),
        InlineKeyboardButton("🔄 Refresh", **ref_kwargs)
    ])

    is_prem = bool(getattr(user, "is_premium", False))
    prem_tag = " ⭐ [VIP MEMBER]" if is_prem else ""
    title = ("📦 <b>MY ORDERS</b>" if lang == "en" else ("📦 <b>የትዕዛዞቼ ዝርዝር</b>" if lang == "am" else "📦 <b>طلباتي</b>")) + prem_tag
    subtitle = "Select an order below to inspect details:" if lang == "en" else ("ዝርዝሩን ለማየት ከታች አንዱን ትዕዛዝ ይጫኑ:" if lang == "am" else "اختر طلباً من القائمة أدناه لمعرفة التفاصيل:")
    text = f"{title} ({len(orders)})\n\n{subtitle}"
    if update.message:
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(buttons))
    elif update.callback_query:
        await edit_or_reply(update.callback_query, text, reply_markup=InlineKeyboardMarkup(buttons))

async def payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays payment instructions and accounts."""
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    lang = models.get_user_language(user.id)
    telebirr_acc = models.get_setting("telebirr_account", config.DEFAULT_TELEBIRR_ACCOUNT)
    telebirr_name = models.get_setting("telebirr_name", config.DEFAULT_TELEBIRR_NAME)
    cbe_acc = models.get_setting("cbe_birr_account", config.DEFAULT_CBE_BIRR_ACCOUNT)
    cbe_name = models.get_setting("cbe_birr_name", config.DEFAULT_CBE_BIRR_NAME)

    if lang == "am":
        text = (
            f"💳 <b>የክፍያ መመሪያ እና አካውንቶች</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>ቴሌብር (TELEBIRR)</b>\n"
            f"የሂሳብ ቁጥር: <code>{telebirr_acc}</code>\n"
            f"ስም: <b>{telebirr_name}</b>\n\n"
            f"<b>ንግድ ባንክ (CBE BIRR)</b>\n"
            f"የሂሳብ ቁጥር: <code>{cbe_acc}</code>\n"
            f"ስም: <b>{cbe_name}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>የክፍያ ሂደቱ:</b>\n"
            f"• ክፍያውን ከላይ ባሉት አካውንቶች ይፈጽሙ።\n"
            f"• ክፍያውን ከፈጸሙ በኋላ ደረሰኙን በቦቱ ይላኩ።\n"
            f"• አስተዳዳሪው ሲያረጋግጥ አገልግሎትዎ ወዲያውኑ ይላክልዎታል።"
        )
    elif lang == "ar":
        text = (
            f"💳 <b>تعليمات وطرق الدفع</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>تليبر (TELEBIRR)</b>\n"
            f"رقم الحساب: <code>{telebirr_acc}</code>\n"
            f"الاسم: <b>{telebirr_name}</b>\n\n"
            f"<b>البنك التجاري (CBE BIRR)</b>\n"
            f"رقم الحساب: <code>{cbe_acc}</code>\n"
            f"الاسم: <b>{cbe_name}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>إرشادات الدفع:</b>\n"
            f"• قم بالتحويل إلى أحد الحسابات أعلاه.\n"
            f"• بعد الدفع، أرسل صورة الإيصال عبر البوت للمراجعة.\n"
            f"• يتم تفعيل الخدمة مباشرة بعد تأكيد التحويل."
        )
    else:
        text = (
            f"💳 <b>PAYMENT INSTRUCTIONS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>TELEBIRR</b>\n"
            f"Account: <code>{telebirr_acc}</code>\n"
            f"Name: <b>{telebirr_name}</b>\n\n"
            f"<b>CBE BIRR</b>\n"
            f"Account: <code>{cbe_acc}</code>\n"
            f"Name: <b>{cbe_name}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Payment Guidelines:</b>\n"
            f"• Please complete the payment using the account above.\n"
            f"• After payment, upload your payment receipt/screenshot in the order process.\n"
            f"• Orders are verified manually by our administrator before delivery.\n"
            f"• The bot will never auto-mark orders as paid without proof verification."
        )
    buttons = [
        [InlineKeyboardButton(i18n.t("btn_browse", lang), callback_data="menu_services")],
        [InlineKeyboardButton(i18n.t("btn_main_menu", lang), callback_data="menu_main")]
    ]
    if update.message:
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(buttons))
    elif update.callback_query:
        await edit_or_reply(update.callback_query, text, reply_markup=InlineKeyboardMarkup(buttons))

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays customer support info."""
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    lang = models.get_user_language(user.id)
    supp_user = models.get_setting("support_username", config.DEFAULT_SUPPORT_USERNAME).replace("@", "").strip()
    if lang == "am":
        text = (
            f"📞 <b>የደንበኞች ድጋፍ</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ቴሌግራም: <b>@{supp_user}</b>\n\n"
            f"ስለ ትዕዛዝዎ፣ ክፍያ ወይም ተጨማሪ መረጃ ለመጠየቅ ከታች ያለውን ይጫኑ:"
        )
    elif lang == "ar":
        text = (
            f"📞 <b>خدمة العملاء والدعم الفني</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"تيليجرام: <b>@{supp_user}</b>\n\n"
            f"للاستفسار عن طلبك أو طرق الدفع أو التجديد، يرجى التواصل مع الإدارة:"
        )
    else:
        text = (
            f"📞 <b>CONTACT SUPPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Telegram: <b>@{supp_user}</b>\n\n"
            f"Have questions about your order, renewal, custom plan, or payment?\n"
            f"Click the button below to contact the administrator directly:"
        )
    buttons = [
        [InlineKeyboardButton(i18n.t("btn_contact_admin", lang), url=f"https://t.me/{supp_user}")],
        [InlineKeyboardButton(i18n.t("btn_main_menu", lang), callback_data="menu_main")]
    ]
    if update.message:
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(buttons))
    elif update.callback_query:
        await edit_or_reply(update.callback_query, text, reply_markup=InlineKeyboardMarkup(buttons))

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays language selection options."""
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    lang = models.get_user_language(user.id)
    text = (
        "🌐 <b>Choose Language / ቋንቋ ይምረጡ / اختر اللغة:</b>\n\n"
        f"Current / አሁን ያለው / الحالي: <b>{i18n.LANGUAGES.get(lang, {}).get('flag', '')} {i18n.LANGUAGES.get(lang, {}).get('native', 'English')}</b>\n\n"
        "Select your preferred language below:"
    )
    kb = [
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
            InlineKeyboardButton("🇪🇹 አማርኛ", callback_data="setlang_am"),
            InlineKeyboardButton("🇸🇦 العربية", callback_data="setlang_ar")
        ],
        [InlineKeyboardButton(i18n.t("btn_back", lang), callback_data="menu_main")]
    ]
    if update.message:
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(kb))
    elif update.callback_query:
        await edit_or_reply(update.callback_query, text, reply_markup=InlineKeyboardMarkup(kb))

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays about store info."""
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    lang = models.get_user_language(user.id)
    if lang == "am":
        text = (
            f"ℹ️ <b>ስለ {config.STORE_NAME}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"በኢትዮጵያ ውስጥ ለፕሪሚየም ዲጂታል አገልግሎቶች፣ AI መተግበሪያዎች፣ ፊልሞችና የቢዝነስ አካውንቶች ተመራጭ መደብር።\n\n"
            f"⚡ <b>ፈጣን አቅርቦት:</b> በቀጥታ በቴሌግራም የሚደርስ\n"
            f"💳 <b>ፈጣን የሀገር ውስጥ ክፍያዎች:</b> ቴሌብር እና ንግድ ባንክ (CBE Birr)\n"
            f"🛡️ <b>100% ዋስትና:</b> ሙሉ ጊዜ ዋስትና እና የ24/7 ድጋፍ\n"
            f"📞 <b>24/7 ድጋፍ:</b> በቀጥታ ከአስተዳዳሪው ጋር\n\n"
            f"🌐 <b>ድረ-ገጽ:</b> https://abdureastore.base44.app/\n"
            f"📞 <b>ድጋፍ:</b> {config.DEFAULT_SUPPORT_USERNAME}"
        )
    elif lang == "ar":
        text = (
            f"ℹ️ <b>حول متجر {config.STORE_NAME}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"وجهتك الأولى الموثوقة للاشتراكات والخدمات الرقمية وحسابات الذكاء الاصطناعي في إثيوبيا.\n\n"
            f"⚡ <b>تسليم فوري:</b> مباشرة عبر تيليجرام\n"
            f"💳 <b>طرق دفع سريعة:</b> تليبر والبنك التجاري\n"
            f"🛡️ <b>ضمان كامل 100%:</b> ضمان طوال مدة الاشتراك\n"
            f"📞 <b>دعم متواصل:</b> تواصل مباشر مع الإدارة\n\n"
            f"🌐 <b>الموقع:</b> https://abdureastore.base44.app/\n"
            f"📞 <b>الدعم:</b> {config.DEFAULT_SUPPORT_USERNAME}"
        )
    else:
        text = (
            f"ℹ️ <b>ABOUT ABDUREA STORE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Your #1 trusted destination for premium digital services, AI tools, streaming, and productivity accounts in Ethiopia.\n\n"
            f"⚡ <b>Instant Delivery:</b> Direct credentials & instructions via Telegram\n"
            f"💳 <b>Fast Local Payments:</b> Telebirr & CBE Birr supported\n"
            f"🛡️ <b>100% Guaranteed:</b> Full duration replacement warranty\n"
            f"📞 <b>24/7 Dedicated Support:</b> Direct admin contact\n\n"
            f"🌐 <b>Website:</b> https://abdureastore.base44.app/\n"
            f"📞 <b>Support:</b> {config.DEFAULT_SUPPORT_USERNAME}"
        )
    buttons = [
        [InlineKeyboardButton(i18n.t("btn_browse", lang), callback_data="menu_services")],
        [InlineKeyboardButton(i18n.t("btn_main_menu", lang), callback_data="menu_main")]
    ]
    if update.message:
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(buttons))
    elif update.callback_query:
        await edit_or_reply(update.callback_query, text, reply_markup=InlineKeyboardMarkup(buttons))

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /cancel command to safely cancel active wizard and return to main menu."""
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    lang = models.get_user_language(user.id)
    models.clear_user_state(user.id)
    models.log_conversation_message(
        telegram_id=user.id,
        direction="INCOMING",
        message_type="command",
        message_text="/cancel",
        message_id=update.message.message_id if update.message else None
    )

    if lang == "am":
        text = (
            "❌ <b>ትዕዛዙ ተሰርዟል</b>\n\n"
            "ያሉበት ሂደት ተቋርጧል። ዝርዝር አገልግሎቶችን ለማየት ከታች ያሉትን አማራጮች ይጠቀሙ:"
        )
    elif lang == "ar":
        text = (
            "❌ <b>تم إلغاء العملية</b>\n\n"
            "تم إلغاء طلبك بنجاح. يمكنك تصفح الخدمات أو البدء متى شئت:"
        )
    else:
        text = (
            "❌ <b>Interaction Cancelled</b>\n\n"
            "Your current order process has been cancelled safely. "
            "You can browse services or start whenever you're ready:"
        )

    if update.message:
        await update.message.reply_html(text, reply_markup=i18n.get_reply_keyboard(lang))
    elif update.callback_query:
        await edit_or_reply(update.callback_query, text, reply_markup=get_main_menu_keyboard(lang))

# ----------------- CALLBACK QUERY HANDLER -----------------

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user

    # Keep user profile updated with Telegram Premium detection
    is_prem = 1 if getattr(user, "is_premium", False) else 0
    models.get_or_create_user(user.id, user.username, user.first_name, user.last_name, is_premium=is_prem)
    lang = models.get_user_language(user.id) or "en"

    # Channel Membership Verification Button
    if data == "check_channel_membership":
        is_member, reason = await is_user_channel_member(context.bot, user.id)
        if is_member:
            models.set_channel_verified(user.id, True)
            try:
                await query.answer(i18n.t("alert_membership_verified", lang), show_alert=False)
            except Exception:
                pass
            welcome_text = i18n.t("welcome_text", lang, store_name=config.STORE_NAME, name=user.first_name)
            if getattr(user, "is_premium", False):
                welcome_text += "\n\n⭐ <b>VIP Telegram Premium Member</b> 🌟\n<i>Enjoy high-priority VIP order processing and exclusive digital access!</i>"
            await edit_or_reply(query, welcome_text, reply_markup=get_main_menu_keyboard(lang))
            return
        else:
            models.set_channel_verified(user.id, False)
            if reason == "bot_not_admin":
                if user.id in getattr(config, "ADMIN_TELEGRAM_IDS", []):
                    await query.answer("⚠️ Admin Notice: Bot is not an Admin in @abdurea1 yet. Please add @abdurea2_bot as an Administrator to @abdurea1 in Telegram.", show_alert=True)
                    models.set_channel_verified(user.id, True)
                    welcome_text = i18n.t("welcome_text", lang, store_name=config.STORE_NAME, name=user.first_name)
                    await edit_or_reply(query, welcome_text, reply_markup=get_main_menu_keyboard(lang))
                    return
                else:
                    await query.answer("⚠️ Please make sure you joined @abdurea1, then click Check Membership again!", show_alert=True)
                    return
            else:
                await query.answer(i18n.t("alert_must_join_channel", lang), show_alert=True)
                return

    # Guard: Require verified channel membership for all other interactions
    if not await ensure_channel_membership(update, context):
        return

    try:
        await query.answer()
    except Exception:
        pass

    # 0. Language Selection
    if data.startswith("setlang_"):
        new_lang = data.replace("setlang_", "")
        if new_lang in i18n.LANGUAGES:
            lang = new_lang
            models.set_user_language(user.id, new_lang)

        models.clear_user_state(user.id)
        welcome_text = i18n.t("welcome_text", lang, store_name=config.STORE_NAME, name=user.first_name)
        if getattr(user, "is_premium", False):
            welcome_text += "\n\n⭐ <b>VIP Telegram Premium Member</b> 🌟\n<i>Enjoy high-priority VIP order processing and exclusive digital access!</i>"
        try:
            await query.answer()
        except Exception:
            pass
        await edit_or_reply(query, welcome_text, reply_markup=i18n.get_main_menu_keyboard(lang))
        return

    # Language Switcher Menu
    if data == "menu_language":
        text = (
            "🌐 <b>Choose Language / ቋንቋ ይምረጡ / اختر اللغة:</b>\n\n"
            f"Current / አሁን ያለው / الحالي: <b>{i18n.LANGUAGES.get(lang, {}).get('flag', '')} {i18n.LANGUAGES.get(lang, {}).get('native', 'English')}</b>\n\n"
            "Select your preferred language below:"
        )
        kb = [
            [
                InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
                InlineKeyboardButton("🇪🇹 አማርኛ", callback_data="setlang_am"),
                InlineKeyboardButton("🇸🇦 العربية", callback_data="setlang_ar")
            ],
            [InlineKeyboardButton(i18n.t("btn_back", lang), callback_data="menu_main")]
        ]
        await edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(kb))
        return

    # 1. Main Menu
    if data == "menu_main":
        models.clear_user_state(user.id)
        welcome_text = i18n.t("welcome_text", lang, store_name=config.STORE_NAME, name=user.first_name)
        if getattr(user, "is_premium", False):
            welcome_text += "\n\n⭐ <b>VIP Telegram Premium Member</b> 🌟\n<i>Enjoy high-priority VIP order processing and exclusive digital access!</i>"
        await edit_or_reply(query, welcome_text, reply_markup=get_main_menu_keyboard(lang))
        return

    # 2. Browse Services / Products (Direct Services List, No Categories)
    if data == "menu_services" or data == "menu_browse":
        products = models.get_all_products(include_inactive=False)

        if not products:
            text = i18n.t("no_products", lang)
            await edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_main", lang)))
            return

        buttons = []
        # 3-color sequence cycling through Green (success), Red (danger), and Blue (primary) like Photo 2
        color_cycle = ["success", "danger", "primary"]
        max_w = max((estimate_product_name_width(p["name"]) for p in products), default=115.0)
        target_w = max(max_w + 22.0, 140.0)

        # Direct service list buttons (names only, prices shown after selecting)
        for idx, p in enumerate(products):
            cb_id = p["id"][5:] if p["id"].startswith("prod_") else p["id"]
            btn_label = format_aligned_product_button(p["name"], target_width=target_w)
            btn_style = color_cycle[idx % len(color_cycle)]
            buttons.append([InlineKeyboardButton(btn_label, callback_data=f"prod_{cb_id}", api_kwargs={"style": btn_style})])

        buttons.extend(get_navigation_buttons("menu_main", lang))
        text = i18n.t("catalog_title", lang)
        await edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # 4. View Service Details & Plan Selection
    if data.startswith("prod_"):
        prod_id = data[5:]
        product = models.get_product_by_id(prod_id)
        if not product:
            await edit_or_reply(query, "Service not found.", reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_services", lang)))
            return

        plans = models.get_plans_by_product(product["id"])
        desc = product.get("description", "").strip() or "Premium digital service subscription."

        features_list = []
        try:
            features_list = json.loads(product.get("features") or "[]")
        except Exception:
            pass

        reqs_list = []
        try:
            reqs_list = json.loads(product.get("requirements") or "[]")
        except Exception:
            pass

        lbl_desc = "Description" if lang == "en" else ("መግለጫ" if lang == "am" else "الوصف")
        lbl_feat = "Key Features" if lang == "en" else ("ዋና ዋና ጥቅሞች" if lang == "am" else "المزايا الرئيسية")
        lbl_req = "Requirements" if lang == "en" else ("የሚያስፈልጉ መረጃዎች" if lang == "am" else "المتطلبات")

        text = (
            f"⚡ <b>{product['name'].upper()}</b>\n\n"
            f"<b>{lbl_desc}:</b>\n{desc}\n\n"
        )

        if features_list:
            text += f"<b>{lbl_feat}:</b>\n" + "\n".join([f"• {f}" for f in features_list[:4]]) + "\n\n"

        if reqs_list:
            text += f"<b>{lbl_req}:</b>\n" + "\n".join([f"• {r}" for r in reqs_list[:3]]) + "\n\n"

        text += i18n.t("available_plans", lang)
        if not plans:
            text += "<i>Currently out of stock. Contact support for inquiries.</i>\n"

        buttons = []
        for pl in plans:
            label = f"🛒 {pl['duration']} — {pl['price']:,.0f} {pl.get('currency', 'ETB')}"
            cb_prod = product['id'][5:] if product['id'].startswith('prod_') else product['id']
            cb_plan = pl['id'][5:] if pl['id'].startswith('plan_') else pl['id']
            buttons.append([InlineKeyboardButton(label, callback_data=f"plan_{cb_prod}_{cb_plan}")])

        buttons.extend(get_navigation_buttons("menu_services", lang))
        reply_markup = InlineKeyboardMarkup(buttons)

        # Truncate caption if needed to stay safely within Telegram's 1024-char limit
        caption_text = text if len(text) <= 1020 else text[:1015] + "..."

        # Display product details with real photo
        img_url = str(product.get("image_url") or "").strip()
        photo_sent = False

        if img_url:
            photo_obj = await load_photo_input(img_url)
            if photo_obj:
                # 1. If existing message is already a photo, smoothly edit the media
                if getattr(query.message, "photo", None):
                    try:
                        await query.edit_message_media(
                            media=InputMediaPhoto(media=photo_obj, caption=caption_text, parse_mode="HTML"),
                            reply_markup=reply_markup
                        )
                        photo_sent = True
                    except Exception as me:
                        logger.debug(f"edit_message_media notice: {me}")
                        photo_obj = await load_photo_input(img_url)

                # 2. Otherwise send native photo and delete previous list message
                if not photo_sent and photo_obj:
                    try:
                        await context.bot.send_photo(
                            chat_id=query.message.chat_id,
                            photo=photo_obj,
                            caption=caption_text,
                            parse_mode="HTML",
                            reply_markup=reply_markup
                        )
                        photo_sent = True
                        try:
                            await query.message.delete()
                        except Exception:
                            pass
                    except Exception as pe:
                        logger.warning(f"Failed to send product photo ({img_url}): {pe}")
                        # Auto-retry with clean plain text caption in case HTML entity tags caused failure
                        try:
                            import re
                            plain_cap = re.sub(r'<[^>]+>', '', caption_text)
                            if len(plain_cap) > 1020:
                                plain_cap = plain_cap[:1015] + "..."
                            retry_photo = await load_photo_input(img_url)
                            if retry_photo:
                                await context.bot.send_photo(
                                    chat_id=query.message.chat_id,
                                    photo=retry_photo,
                                    caption=plain_cap,
                                    reply_markup=reply_markup
                                )
                                photo_sent = True
                                try:
                                    await query.message.delete()
                                except Exception:
                                    pass
                        except Exception as pe2:
                            logger.warning(f"Retry send_photo also failed: {pe2}")

        # 3. Fallback to clean HTML text card if no photo or sending photo failed
        if not photo_sent:
            await edit_or_reply(query, text, reply_markup=reply_markup)
        return

    # 5. Selected Plan -> Confirm Order Summary Screen
    if data.startswith("plan_"):
        rest = data[5:]
        if "_" in rest:
            prod_id, plan_id = rest.rsplit("_", 1)
        else:
            prod_id = rest
            plan_id = ""
        product = models.get_product_by_id(prod_id)
        plan = models.get_plan_by_id(plan_id)

        if not product or not plan:
            await edit_or_reply(query, "Invalid product or plan.", reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_services", lang)))
            return

        cust_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Customer"
        username_display = f"@{user.username}" if user.username else "N/A"

        confirm_text = (
            f"{i18n.t('order_confirmation_title', lang)}\n\n"
            f"{i18n.t('order_conf_service', lang, service=product['name'])}\n"
            f"{i18n.t('order_conf_duration', lang, duration=plan['duration'])}\n"
            f"{i18n.t('order_conf_amount', lang, amount=plan['price'], currency=plan.get('currency', 'ETB'))}\n\n"
            f"{i18n.t('order_conf_customer', lang, name=cust_name, username=username_display, telegram_id=user.id)}"
        )

        # Save active checkout selection in user state
        models.save_user_state(user.id, "CONFIRM_ORDER", {
            "prod_id": prod_id,
            "plan_id": plan_id
        })

        cb_back = product['id'][5:] if product['id'].startswith('prod_') else product['id']
        buttons = [
            [InlineKeyboardButton(i18n.t("btn_confirm_order", lang), callback_data="btn_choosepay")],
            [InlineKeyboardButton(i18n.t("btn_back_to_plans", lang), callback_data=f"prod_{cb_back}")],
            [InlineKeyboardButton(i18n.t("btn_main_menu", lang), callback_data="menu_main")]
        ]

        await edit_or_reply(query, confirm_text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # 6. Choose Payment Method
    if data == "btn_choosepay" or data.startswith("choosepay_"):
        prod_id = None
        plan_id = None
        if data.startswith("choosepay_"):
            rest = data[10:]
            if "_" in rest:
                prod_id, plan_id = rest.rsplit("_", 1)

        if not prod_id or not plan_id:
            state = models.get_user_state(user.id)
            if state and state.get("data"):
                prod_id = state["data"].get("prod_id")
                plan_id = state["data"].get("plan_id")

        product = models.get_product_by_id(prod_id) if prod_id else None
        plan = models.get_plan_by_id(plan_id) if plan_id else None

        if not product or not plan:
            await edit_or_reply(query, "Invalid selection. Please browse our catalog again.", reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_services", lang)))
            return

        # Update state with confirmed plan
        models.save_user_state(user.id, "CHOOSING_PAYMENT", {
            "prod_id": prod_id,
            "plan_id": plan_id
        })

        text = i18n.t(
            "select_payment_title", lang,
            service=product['name'],
            duration=plan['duration'],
            amount=plan['price'],
            currency=plan.get('currency', 'ETB')
        )

        cb_back = product['id'][5:] if product['id'].startswith('prod_') else product['id']
        buttons = [
            [InlineKeyboardButton("📱 TELEBIRR", callback_data="pay_telebirr")],
            [InlineKeyboardButton("🏦 CBE BIRR", callback_data="pay_cbe")],
            [InlineKeyboardButton(i18n.t("btn_back", lang), callback_data=f"prod_{cb_back}")],
            [InlineKeyboardButton(i18n.t("btn_cancel_order", lang), callback_data="menu_main")]
        ]
        await edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # 7. Show Payment Instructions & Ask for Receipt
    if data in ("pay_telebirr", "pay_cbe") or data.startswith("payinfo_"):
        prod_id = None
        plan_id = None
        method_key = "telebirr" if "telebirr" in data else "cbe"

        if data.startswith("payinfo_"):
            rest = data[8:]
            parts = rest.split("_")
            if len(parts) >= 3:
                method_key = parts[-1].lower()
                plan_id = parts[-2]
                prod_id = "_".join(parts[:-2])

        if not prod_id or not plan_id:
            state = models.get_user_state(user.id)
            if state and state.get("data"):
                prod_id = state["data"].get("prod_id")
                plan_id = state["data"].get("plan_id")

        product = models.get_product_by_id(prod_id) if prod_id else None
        plan = models.get_plan_by_id(plan_id) if plan_id else None

        if not product or not plan:
            await edit_or_reply(query, "Session expired. Please browse services to start an order.", reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_services", lang)))
            return

        telebirr_acc = models.get_setting("telebirr_account", config.DEFAULT_TELEBIRR_ACCOUNT)
        telebirr_name = models.get_setting("telebirr_name", config.DEFAULT_TELEBIRR_NAME)
        cbe_acc = models.get_setting("cbe_birr_account", config.DEFAULT_CBE_BIRR_ACCOUNT)
        cbe_name = models.get_setting("cbe_birr_name", config.DEFAULT_CBE_BIRR_NAME)

        lbl_acc = "Account Number" if lang == "en" else ("የሂሳብ ቁጥር" if lang == "am" else "رقم الحساب")
        lbl_nm = "Account Name" if lang == "en" else ("የስም ባለቤት" if lang == "am" else "اسم الحساب")

        if method_key == "telebirr":
            selected_method = "Telebirr"
            account_display = f"📱 <b>TELEBIRR</b>\n{lbl_acc}: <code>{telebirr_acc}</code>\n{lbl_nm}: <b>{telebirr_name}</b>"
        else:
            selected_method = "CBE Birr"
            account_display = f"🏦 <b>CBE BIRR</b>\n{lbl_acc}: <code>{cbe_acc}</code>\n{lbl_nm}: <b>{cbe_name}</b>"

        # Persist conversation state to SQLite
        state_data = {
            "prod_id": prod_id,
            "plan_id": plan_id,
            "prod_name": product["name"],
            "plan_duration": plan["duration"],
            "amount": plan["price"],
            "currency": plan.get("currency", "ETB"),
            "payment_method": selected_method
        }
        models.save_user_state(user.id, "WAITING_RECEIPT", state_data)

        instruction_text = i18n.t(
            "payment_instructions_header", lang,
            method=selected_method.upper(),
            service=product['name'],
            duration=plan['duration'],
            amount=plan['price'],
            currency=plan.get('currency', 'ETB'),
            account_display=account_display
        )

        buttons = [
            [InlineKeyboardButton(i18n.t("btn_change_payment", lang), callback_data="btn_choosepay")],
            [InlineKeyboardButton(i18n.t("btn_cancel_order", lang), callback_data="menu_main")]
        ]
        await edit_or_reply(query, instruction_text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # 8. Final Submit Order Callback (after reviewing submitted information)
    if data == "submit_final_order":
        state_info = models.get_user_state(user.id)
        if not state_info or state_info.get("state") != "REVIEW_SUBMITTED_INFO":
            await edit_or_reply(
                query,
                "❌ Session expired or order already submitted. Please start from the menu.",
                reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_services", lang))
            )
            return

        order_data = state_info.get("data", {})
        try:
            is_prem = bool(getattr(user, "is_premium", False))
            cust_notes = order_data.get("customer_notes") or ""
            if is_prem and "⭐ [VIP Telegram Premium Member]" not in cust_notes:
                cust_notes = f"⭐ [VIP Telegram Premium Member] {cust_notes}".strip()

            # 1. Create order
            order = models.create_order(
                telegram_id=user.id,
                product_id=order_data["prod_id"],
                plan_id=order_data["plan_id"],
                customer_name=order_data["customer_name"],
                customer_username=user.username,
                customer_phone=order_data.get("customer_phone"),
                customer_delivery_info=order_data.get("customer_delivery_info"),
                customer_notes=cust_notes,
                payment_method=order_data["payment_method"],
                status=models.STATUS_PAYMENT_SUBMITTED
            )

            # 2. Record payment with receipt metadata
            payment = models.record_payment(
                order_id=order["id"],
                payment_method=order_data["payment_method"],
                payment_sender_name=order_data["customer_name"],
                receipt_file=order_data["receipt_file"],
                telegram_file_id=order_data.get("telegram_file_id"),
                file_unique_id=order_data.get("file_unique_id"),
                media_type=order_data.get("media_type", "photo"),
                message_id=order_data.get("receipt_msg_id"),
                amount=order_data["amount"]
            )

            # Instantly alert the Dedicated Telegram Admin Bot with receipt photo & 1-click actions
            try:
                bot_bridge.notify_admin_new_order(order, payment)
            except Exception as ex_admin:
                logger.warning(f"Could not alert admin bot: {ex_admin}")

            # Clear state
            models.clear_user_state(user.id)

            # 3. Customer Confirmation Message
            confirm_text = i18n.t(
                "receipt_success_msg", lang,
                order_number=order['order_number'],
                service=order['product_name'],
                duration=order['plan_duration'],
                amount=order['amount'],
                currency=order.get('currency', 'ETB'),
                method=order['payment_method'],
                store_name=config.STORE_NAME
            )
            if is_prem:
                confirm_text += "\n\n⭐ <b>VIP Order Priority: High Priority Processing for Telegram Premium Member</b>"

            buttons = [
                [InlineKeyboardButton(i18n.t("btn_track_order", lang), callback_data=f"view_order_{order['id']}")],
                [InlineKeyboardButton(i18n.t("btn_main_menu", lang), callback_data="menu_main")]
            ]
            await edit_or_reply(query, confirm_text, reply_markup=InlineKeyboardMarkup(buttons))
            return

        except Exception as e:
            logger.exception("Error submitting final order")
            await edit_or_reply(
                query,
                "❌ Could not complete order submission at this moment. Please try again from the menu.",
                reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_services", lang))
            )
            return

    # 9. My Orders Menu
    if data == "menu_orders":
        orders = models.get_user_orders(user.id, limit=15)
        if not orders:
            await edit_or_reply(
                query,
                i18n.t("my_orders_empty", lang),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(i18n.t("btn_browse", lang), callback_data="menu_services")],
                    [InlineKeyboardButton(i18n.t("btn_main_menu", lang), callback_data="menu_main")]
                ])
            )
            return

        buttons = []
        for o in orders:
            badge = STATUS_BADGES.get(o["status"], o["status"])
            buttons.append([
                InlineKeyboardButton(
                    f"#{o['order_number']} — {o['product_name']} ({badge})",
                    callback_data=f"view_order_{o['id']}"
                )
            ])
        buttons.extend(get_navigation_buttons("menu_main", lang))

        title = "📦 <b>MY ORDERS</b>" if lang == "en" else ("📦 <b>የትዕዛዞቼ ዝርዝር</b>" if lang == "am" else "📦 <b>طلباتي</b>")
        subtitle = "Select an order below to inspect details:" if lang == "en" else ("ዝርዝሩን ለማየት ከታች አንዱን ትዕዛዝ ይጫኑ:" if lang == "am" else "اختر طلباً من القائمة أدناه لمعرفة التفاصيل:")
        text = f"{title} ({len(orders)})\n\n{subtitle}"
        await edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # 10. View Single Order
    if data.startswith("view_order_"):
        order_id = int(data.replace("view_order_", ""))
        order = models.get_order_by_id(order_id)
        if not order or order["telegram_id"] != user.id:
            await edit_or_reply(query, "Order not found.", reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_orders", lang)))
            return

        badge = STATUS_BADGES.get(order["status"], order["status"])
        payment = models.get_payment_by_order_id(order_id)
        delivery = models.get_delivery_by_order_id(order_id)

        text = (
            f"📄 <b>ORDER DETAILS: #{order['order_number']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🛍️ <b>Service:</b> {order['product_name']} ({order.get('plan_duration', '')})\n"
            f"💰 <b>Amount:</b> {order['amount']:,.0f} {order.get('currency', 'ETB')}\n"
            f"📅 <b>Date:</b> {order['created_at'][:16]}\n"
            f"📊 <b>Status:</b> {badge}\n"
        )

        if payment:
            text += (
                f"\n💳 <b>Payment Information:</b>\n"
                f"• Method: {payment.get('payment_method')}\n"
                f"• Sender Name: {payment.get('payment_sender_name') or 'Customer'}\n"
                f"• Review Status: {payment.get('review_status')}\n"
            )

        if order.get("rejection_reason"):
            text += f"\n❌ <b>Rejection Notice:</b>\n<i>{order['rejection_reason']}</i>\n"

        if order.get("admin_notes"):
            text += f"\n📝 <b>Admin Note:</b> {order['admin_notes']}\n"

        if delivery:
            text += (
                f"\n━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎉 <b>DELIVERED CREDENTIALS / INSTRUCTIONS:</b>\n\n"
                f"{delivery['content']}\n"
            )

        buttons = []
        if order["status"] in ("NEW", "AWAITING_PAYMENT", "PENDING_PAYMENT"):
            buttons.append([InlineKeyboardButton("💳 Complete Payment", callback_data=f"choosepay_{order['product_id']}_{order['plan_id']}")])
        buttons.append([InlineKeyboardButton("🔙 Back to Orders", callback_data="menu_orders")])
        buttons.append([InlineKeyboardButton(i18n.t("btn_main_menu", lang), callback_data="menu_main")])

        await edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # 11. Payment Instructions
    if data == "menu_payment_info":
        telebirr_acc = models.get_setting("telebirr_account", config.DEFAULT_TELEBIRR_ACCOUNT)
        telebirr_name = models.get_setting("telebirr_name", config.DEFAULT_TELEBIRR_NAME)
        cbe_acc = models.get_setting("cbe_birr_account", config.DEFAULT_CBE_BIRR_ACCOUNT)
        cbe_name = models.get_setting("cbe_birr_name", config.DEFAULT_CBE_BIRR_NAME)

        if lang == "am":
            text = (
                f"💳 <b>የክፍያ መመሪያ እና አካውንቶች</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>ቴሌብር (TELEBIRR)</b>\n"
                f"የሂሳብ ቁጥር: <code>{telebirr_acc}</code>\n"
                f"ስም: <b>{telebirr_name}</b>\n\n"
                f"<b>ንግድ ባንክ (CBE BIRR)</b>\n"
                f"የሂሳብ ቁጥር: <code>{cbe_acc}</code>\n"
                f"ስም: <b>{cbe_name}</b>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 <b>የክፍያ ሂደቱ:</b>\n"
                f"• ክፍያውን ከላይ ባሉት አካውንቶች ይፈጽሙ።\n"
                f"• ክፍያውን ከፈጸሙ በኋላ የከፈሉበትን ደረሰኝ በቦቱ ይላኩ።\n"
                f"• አስተዳዳሪው ደረሰኙን ሲያረጋግጥ አገልግሎትዎ ወዲያውኑ ይላክልዎታል።"
            )
        elif lang == "ar":
            text = (
                f"💳 <b>تعليمات وطرق الدفع</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>تليبر (TELEBIRR)</b>\n"
                f"رقم الحساب: <code>{telebirr_acc}</code>\n"
                f"الاسم: <b>{telebirr_name}</b>\n\n"
                f"<b>البنك التجاري (CBE BIRR)</b>\n"
                f"رقم الحساب: <code>{cbe_acc}</code>\n"
                f"الاسم: <b>{cbe_name}</b>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 <b>إرشادات الدفع:</b>\n"
                f"• قم بالتحويل إلى أحد الحسابات أعلاه.\n"
                f"• بعد الدفع، أرسل صورة الإيصال عبر البوت للمراجعة.\n"
                f"• يتم تفعيل الخدمة مباشرة بعد تأكيد التحويل."
            )
        else:
            text = (
                f"💳 <b>PAYMENT INSTRUCTIONS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>TELEBIRR</b>\n"
                f"Account: <code>{telebirr_acc}</code>\n"
                f"Name: <b>{telebirr_name}</b>\n\n"
                f"<b>CBE BIRR</b>\n"
                f"Account: <code>{cbe_acc}</code>\n"
                f"Name: <b>{cbe_name}</b>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 <b>Payment Guidelines:</b>\n"
                f"• Please complete the payment using the account above.\n"
                f"• After payment, upload your payment receipt/screenshot in the order process.\n"
                f"• Orders are verified manually by our administrator before delivery.\n"
                f"• The bot will never auto-mark orders as paid without proof verification."
            )
        buttons = [
            [InlineKeyboardButton(i18n.t("btn_browse", lang), callback_data="menu_services")],
            [InlineKeyboardButton(i18n.t("btn_main_menu", lang), callback_data="menu_main")]
        ]
        await edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # 12. Support Info
    if data == "menu_support":
        supp_user = models.get_setting("support_username", config.DEFAULT_SUPPORT_USERNAME).replace("@", "").strip()
        if lang == "am":
            text = (
                f"📞 <b>የደንበኞች ድጋፍ</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"ቴሌግራም: <b>@{supp_user}</b>\n\n"
                f"ስለ ትዕዛዝዎ፣ ክፍያ ወይም ተጨማሪ መረጃ ለመጠየቅ ከታች ያለውን ይጫኑ:"
            )
        elif lang == "ar":
            text = (
                f"📞 <b>خدمة العملاء والدعم الفني</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"تيليجرام: <b>@{supp_user}</b>\n\n"
                f"للاستفسار عن طلبك أو طرق الدفع أو التجديد، يرجى التواصل مع الإدارة:"
            )
        else:
            text = (
                f"📞 <b>CONTACT SUPPORT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Telegram: <b>@{supp_user}</b>\n\n"
                f"Have questions about your order, renewal, custom plan, or payment?\n"
                f"Click the button below to contact the administrator directly:"
            )
        buttons = [
            [InlineKeyboardButton(i18n.t("btn_contact_admin", lang), url=f"https://t.me/{supp_user}")],
            [InlineKeyboardButton(i18n.t("btn_main_menu", lang), callback_data="menu_main")]
        ]
        await edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # 13. About Store / Help
    if data == "menu_about" or data == "menu_help":
        if lang == "am":
            text = (
                f"ℹ️ <b>ስለ {config.STORE_NAME}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"በኢትዮጵያ ውስጥ ለፕሪሚየም ዲጂታል አገልግሎቶች፣ AI መተግበሪያዎች፣ ፊልሞችና የቢዝነስ አካውንቶች ተመራጭ መደብር።\n\n"
                f"⚡ <b>ፈጣን አቅርቦት:</b> በቀጥታ በቴሌግራም የሚደርስ\n"
                f"💳 <b>ፈጣን የሀገር ውስጥ ክፍያዎች:</b> ቴሌብር እና ንግድ ባንክ (CBE Birr)\n"
                f"🛡️ <b>100% ዋስትና:</b> ሙሉ ጊዜ ዋስትና እና የ24/7 ድጋፍ\n"
                f"📞 <b>24/7 ድጋፍ:</b> በቀጥታ ከአስተዳዳሪው ጋር\n\n"
                f"🌐 <b>ድረ-ገጽ:</b> https://abdureastore.base44.app/\n"
                f"📞 <b>ድጋፍ:</b> {config.DEFAULT_SUPPORT_USERNAME}"
            )
        elif lang == "ar":
            text = (
                f"ℹ️ <b>حول متجر {config.STORE_NAME}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"وجهتك الأولى الموثوقة للاشتراكات والخدمات الرقمية وحسابات الذكاء الاصطناعي في إثيوبيا.\n\n"
                f"⚡ <b>تسليم فوري:</b> مباشرة عبر تيليجرام\n"
                f"💳 <b>طرق دفع سريعة:</b> تليبر والبنك التجاري\n"
                f"🛡️ <b>ضمان كامل 100%:</b> ضمان طوال مدة الاشتراك\n"
                f"📞 <b>دعم متواصل:</b> تواصل مباشر مع الإدارة\n\n"
                f"🌐 <b>الموقع:</b> https://abdureastore.base44.app/\n"
                f"📞 <b>الدعم:</b> {config.DEFAULT_SUPPORT_USERNAME}"
            )
        else:
            text = (
                f"ℹ️ <b>ABOUT ABDUREA STORE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Your #1 trusted destination for premium digital services, AI tools, streaming, and productivity accounts in Ethiopia.\n\n"
                f"⚡ <b>Instant Delivery:</b> Direct credentials & instructions via Telegram\n"
                f"💳 <b>Fast Local Payments:</b> Telebirr & CBE Birr supported\n"
                f"🛡️ <b>100% Guaranteed:</b> Full duration replacement warranty\n"
                f"📞 <b>24/7 Dedicated Support:</b> Direct admin contact\n\n"
                f"🌐 <b>Website:</b> https://abdureastore.base44.app/\n"
                f"📞 <b>Support:</b> {config.DEFAULT_SUPPORT_USERNAME}"
            )
        buttons = [
            [InlineKeyboardButton(i18n.t("btn_browse", lang), callback_data="menu_services")],
            [InlineKeyboardButton(i18n.t("btn_main_menu", lang), callback_data="menu_main")]
        ]
        await edit_or_reply(query, text, reply_markup=InlineKeyboardMarkup(buttons))
        return

# ----------------- RECEIPT UPLOAD (MEDIA) HANDLER -----------------

async def handle_user_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles payment receipt uploads (Photo or Document/PDF).
    Step in flow: customer uploads receipt before identity details.
    """
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    lang = models.get_user_language(user.id)
    state_info = models.get_user_state(user.id)

    # Check if user is in WAITING_RECEIPT state
    if not state_info or state_info.get("state") != "WAITING_RECEIPT":
        await update.message.reply_html(
            "Please select a service and payment method from the menu before sending a receipt:" if lang == "en" else ("እባክዎ ደረሰኝ ከመላክዎ በፊት አገልግሎት እና የክፍያ ዘዴ ይምረጡ:" if lang == "am" else "يرجى اختيار الخدمة وطريقة الدفع من القائمة قبل إرسال الإيصال:"),
            reply_markup=get_main_menu_keyboard(lang)
        )
        return

    state_data = state_info.get("data", {})
    file_id = None
    file_unique_id = None
    media_type = "photo"
    ext = ".jpg"

    if update.message.photo:
        photo = update.message.photo[-1]
        file_id = photo.file_id
        file_unique_id = photo.file_unique_id
        media_type = "photo"
        ext = ".jpg"
    elif update.message.document:
        doc = update.message.document
        file_id = doc.file_id
        file_unique_id = doc.file_unique_id
        media_type = "document"
        ext = Path(doc.file_name or "receipt.jpg").suffix or ".jpg"
    else:
        await update.message.reply_html("❌ Please upload an image screenshot or document of your receipt.")
        return

    # Duplicate receipt protection check
    with models.db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, order_id FROM payments WHERE file_unique_id = ?", (file_unique_id,))
        if cursor.fetchone():
            await update.message.reply_html(
                "⚠️ This receipt has already been submitted for an existing order. Please submit a valid, unique receipt for this purchase." if lang == "en" else ("⚠️ ይህ ደረሰኝ ከዚህ በፊት ለሌላ ትዕዛዝ ገብቷል። እባክዎ አዲስና ትክክለኛ ደረሰኝ ያስገቡ።" if lang == "am" else "⚠️ تم استخدام هذا الإيصال لطلب سابق. يرجى إرسال إيصال جديد وصحيح."),
                reply_markup=InlineKeyboardMarkup(get_navigation_buttons("menu_services", lang))
            )
            return

    # Save receipt file locally
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"receipt_{user.id}_{timestamp}{ext}"
    dest_path = config.RECEIPTS_DIR / filename

    # Update state data
    state_data["receipt_file"] = filename
    state_data["telegram_file_id"] = file_id
    state_data["file_unique_id"] = file_unique_id
    state_data["media_type"] = media_type
    state_data["receipt_msg_id"] = update.message.message_id

    # Advance state to WAITING_IDENTITY_DETAILS
    models.save_user_state(user.id, "WAITING_IDENTITY_DETAILS", state_data)

    models.log_conversation_message(
        telegram_id=user.id,
        direction="INCOMING",
        message_type=media_type,
        message_text=update.message.caption or "[Receipt Uploaded]",
        media_ref=filename,
        message_id=update.message.message_id
    )

    # Download file in background
    async def _download():
        try:
            tg_file = await context.bot.get_file(file_id)
            await tg_file.download_to_drive(custom_path=dest_path)
            logger.info(f"Downloaded receipt to {dest_path}")
        except Exception as err:
            logger.warning(f"Background receipt download notice: {err}")

    asyncio.create_task(_download())

    # Prompt customer for Identity Details
    product_reqs = []
    try:
        p = models.get_product_by_id(state_data.get("prod_id"))
        if p:
            product_reqs = json.loads(p.get("requirements") or "[]")
    except Exception:
        pass

    req_hint = ""
    if product_reqs:
        req_hint = "\n<b>Product Specific Requirements:</b>\n" + "\n".join([f"• {r}" for r in product_reqs]) + "\n"

    cust_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Customer"

    if lang == "am":
        ask_details_text = (
            f"✅ <b>ደረሰኙ ደርሷል!</b>\n\n"
            f"<b>መረጃዎን ይላኩ:</b>\n"
            f"አካውንቱ የሚላክበትን ስም፣ ስልክ እና ኢሜይል ይላኩ:\n"
            f"{req_hint}"
            f"<code>ምሳሌ: {cust_name}, 0911223344, email@gmail.com</code>"
        )
    elif lang == "ar":
        ask_details_text = (
            f"✅ <b>تم استلام الإيصال!</b>\n\n"
            f"<b>أرسል بياناتك:</b>\n"
            f"أرسل الاسم، الهاتف، والبريد الإلكتروني لاستلام الحساب:\n"
            f"{req_hint}"
            f"<code>مثال: {cust_name}, 0911223344, email@gmail.com</code>"
        )
    else:
        ask_details_text = (
            f"✅ <b>RECEIPT RECEIVED!</b>\n\n"
            f"<b>SEND YOUR DETAILS:</b>\n"
            f"Send your Name, Phone, and Email to deliver your account:\n"
            f"{req_hint}"
            f"<code>Example: {cust_name}, 0911223344, email@gmail.com</code>"
        )

    buttons = [
        [InlineKeyboardButton(i18n.t("btn_cancel_order", lang), callback_data="menu_main")]
    ]
    await update.message.reply_html(ask_details_text, reply_markup=InlineKeyboardMarkup(buttons))

# ----------------- TEXT MESSAGE HANDLER -----------------

async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text input from customer (Identity details, notes, etc.)."""
    if not await ensure_channel_membership(update, context):
        return
    user = update.effective_user
    lang = models.get_user_language(user.id)
    text = update.message.text.strip()
    models.log_conversation_message(
        telegram_id=user.id,
        direction="INCOMING",
        message_type="text",
        message_text=text,
        message_id=update.message.message_id
    )

    # Check bottom reply keyboard button clicks across English, Amharic, and Arabic
    lower_text = text.lower()
    if text in i18n.MENU_ACTION_MATCHERS["browse"] or lower_text in ("browse", "products", "services"):
        models.clear_user_state(user.id)
        await services_command(update, context)
        return

    if text in i18n.MENU_ACTION_MATCHERS["orders"] or lower_text in ("orders", "my orders"):
        models.clear_user_state(user.id)
        await orders_command(update, context)
        return

    if text in i18n.MENU_ACTION_MATCHERS["payment"] or lower_text in ("payment", "payments"):
        models.clear_user_state(user.id)
        await payment_command(update, context)
        return

    if text in i18n.MENU_ACTION_MATCHERS["support"] or lower_text in ("support", "help", "admin"):
        models.clear_user_state(user.id)
        await support_command(update, context)
        return

    if text in i18n.MENU_ACTION_MATCHERS["about"] or lower_text in ("about", "info"):
        models.clear_user_state(user.id)
        await about_command(update, context)
        return

    if text in i18n.MENU_ACTION_MATCHERS["language"] or lower_text in ("language", "lang"):
        models.clear_user_state(user.id)
        await language_command(update, context)
        return

    if text in i18n.MENU_ACTION_MATCHERS["main_menu"] or lower_text in ("menu", "main menu"):
        models.clear_user_state(user.id)
        welcome_text = i18n.t("welcome_text", lang, store_name=config.STORE_NAME, name=user.first_name)
        await update.message.reply_html(
            welcome_text,
            reply_markup=get_main_menu_keyboard(lang)
        )
        return

    state_info = models.get_user_state(user.id)
    if not state_info:
        welcome_text = i18n.t("welcome_text", lang, store_name=config.STORE_NAME, name=user.first_name)
        await update.message.reply_html(
            welcome_text,
            reply_markup=get_main_menu_keyboard(lang)
        )
        return

    state = state_info.get("state")
    state_data = state_info.get("data", {})

    # Flow Step: Receiving Customer Identity Details
    if state == "WAITING_IDENTITY_DETAILS":
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) >= 2:
            cust_name = parts[0]
            phone = parts[1] if len(parts) > 1 and any(char.isdigit() for char in parts[1]) else None
            delivery_info = parts[2] if len(parts) > 2 else (parts[1] if not phone else text)
        else:
            cust_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or parts[0]
            phone = None
            delivery_info = text

        state_data["customer_name"] = cust_name
        state_data["customer_phone"] = phone
        state_data["customer_delivery_info"] = delivery_info
        state_data["customer_notes"] = f"Submitted info: {text}"

        # Advance to REVIEW_SUBMITTED_INFO
        models.save_user_state(user.id, "REVIEW_SUBMITTED_INFO", state_data)

        # Show Order Review screen
        if lang == "am":
            lbl_rev_title = "📋 <b>ትዕዛዝዎን ያረጋግጡ</b>"
            lbl_rev_service = f"• <b>አገልግሎት:</b> {state_data['prod_name']} ({state_data['plan_duration']})"
            lbl_rev_amount = f"• <b>ዋጋ:</b> {state_data['amount']:,.0f} {state_data.get('currency', 'ETB')} ({state_data['payment_method']})"
            lbl_rev_receipt = "• <b>ደረሰኝ:</b> ✅ ተያይዟል"
            lbl_rev_cust = f"• <b>መላኪያ:</b> <code>{delivery_info}</code>"
            lbl_rev_foot = "<i>ትዕዛዙን ለመጨረስ ከታች ይጫኑ:</i>"
            btn_submit_label = "🚀 ትዕዛዙን ላክ"
        elif lang == "ar":
            lbl_rev_title = "📋 <b>تأكيد الطلب</b>"
            lbl_rev_service = f"• <b>الخدمة:</b> {state_data['prod_name']} ({state_data['plan_duration']})"
            lbl_rev_amount = f"• <b>المبلغ:</b> {state_data['amount']:,.0f} {state_data.get('currency', 'ETB')} ({state_data['payment_method']})"
            lbl_rev_receipt = "• <b>الإيصال:</b> ✅ مرفق"
            lbl_rev_cust = f"• <b>بيانات الاستلام:</b> <code>{delivery_info}</code>"
            lbl_rev_foot = "<i>اضغط أدناه لإكمال الطلب:</i>"
            btn_submit_label = "🚀 إرسال الطلب"
        else:
            lbl_rev_title = "📋 <b>CONFIRM ORDER</b>"
            lbl_rev_service = f"• <b>Service:</b> {state_data['prod_name']} ({state_data['plan_duration']})"
            lbl_rev_amount = f"• <b>Amount:</b> {state_data['amount']:,.0f} {state_data.get('currency', 'ETB')} ({state_data['payment_method']})"
            lbl_rev_receipt = "• <b>Receipt:</b> ✅ Attached"
            lbl_rev_cust = f"• <b>Delivery:</b> <code>{delivery_info}</code>"
            lbl_rev_foot = "<i>Click below to finish:</i>"
            btn_submit_label = "🚀 SUBMIT ORDER"

        review_text = (
            f"{lbl_rev_title}\n\n"
            f"{lbl_rev_service}\n"
            f"{lbl_rev_amount}\n"
            f"{lbl_rev_receipt}\n"
            f"{lbl_rev_cust}\n\n"
            f"{lbl_rev_foot}"
        )

        buttons = [
            [InlineKeyboardButton(btn_submit_label, callback_data="submit_final_order")],
            [InlineKeyboardButton(i18n.t("btn_cancel_order", lang), callback_data="menu_main")]
        ]
        await update.message.reply_html(review_text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    # Fallback for unexpected messages
    await update.message.reply_html(
        "👋 Please use the interactive menu buttons to navigate:" if lang == "en" else ("👋 እባክዎ አማራጮችን ለመምረጥ የቦቱን ቁልፎች ይጠቀሙ:" if lang == "am" else "👋 يرجى استخدام أزرار القائمة التفاعلية للتنقل:"),
        reply_markup=get_main_menu_keyboard(lang)
    )

# ----------------- GLOBAL ERROR & IDEMPOTENCY HANDLER -----------------

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Logs unexpected exceptions to server logs without sending error alerts into customer chat."""
    err = str(context.error) if context.error else ""
    if "not modified" in err.lower() or "query is too old" in err.lower() or "conflict" in err.lower():
        logger.debug(f"Harmless Telegram update notice: {err}")
        return
    logger.error(f"Telegram Bot Exception: {context.error}", exc_info=True)

# ----------------- APP FACTORY -----------------

def build_telegram_app():
    """Builds and returns the python-telegram-bot Application instance."""
    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN.startswith("YOUR_"):
        logger.warning("No valid TELEGRAM_BOT_TOKEN provided in configuration. Telegram bot loop disabled.")
        return None

    request_config = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=60.0,
        read_timeout=90.0,
        write_timeout=60.0,
        pool_timeout=60.0
    )

    async def post_init(application):
        try:
            customer_commands = [
                BotCommand("start", "👋 Start Store & Select Language"),
                BotCommand("services", "🛍️ Browse Products & Plans"),
                BotCommand("orders", "📦 Track My Orders & Credentials"),
                BotCommand("payment", "💳 Payment Accounts & Instructions"),
                BotCommand("support", "💬 Contact Store Administrator"),
                BotCommand("about", "ℹ️ About Our Store & Guarantees"),
                BotCommand("language", "🌐 Switch Language (EN/AM/OM)"),
            ]
            await application.bot.set_my_commands(customer_commands)
            logger.info("Customer Bot Telegram commands menu registered successfully.")
        except Exception as e:
            logger.warning(f"Could not register customer commands: {e}")

    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .request(request_config)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("services", services_command))
    app.add_handler(CommandHandler("products", services_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("payment", payment_command))
    app.add_handler(CommandHandler("support", support_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("help", about_command))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_user_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text))
    app.add_error_handler(global_error_handler)

    return app
