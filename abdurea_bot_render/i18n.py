# -*- coding: utf-8 -*-
"""
Multi-Language (Internationalization) Module for ABDUREA STORE Telegram Bot.
Supports:
- English (en)
- Amharic / አማርኛ (am)
- Arabic / العربية (ar)
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

LANGUAGES = {
    "en": {"name": "English", "flag": "🇬🇧", "native": "English"},
    "am": {"name": "Amharic", "flag": "🇪🇹", "native": "አማርኛ"},
    "ar": {"name": "Arabic", "flag": "🇸🇦", "native": "العربية"}
}

DEFAULT_LANGUAGE = "en"

STRINGS = {
    # Language Selection Prompt
    "choose_language": {
        "en": "🌐 <b>Please choose your preferred language:</b>\n\nSelect a language to continue:",
        "am": "🌐 <b>እባክዎ የሚፈልጉትን ቋንቋ ይምረጡ:</b>\n\nለመቀጠል አንዱን ይጫኑ:",
        "ar": "🌐 <b>الرجاء اختيار لغتك المفضلة:</b>\n\nاختر لغة للمتابعة:"
    },

    # Mandatory Channel Join Requirement
    "must_join_channel_prompt": {
        "en": (
            "👋 <b>WELCOME TO ABDUREA STORE</b>\n\n"
            "📢 <b>To use this bot, you must first join our official Telegram channel:</b>\n"
            "👉 <b>@abdurea1</b>\n\n"
            "1️⃣ Click <b>📢 Join Channel</b> below to join.\n"
            "2️⃣ Once you have joined, click <b>✅ Check Membership</b> to unlock the store!"
        ),
        "am": (
            "👋 <b>WELCOME TO ABDUREA STORE</b>\n\n"
            "📢 <b>ይህንን ቦት ለመጠቀም እባክዎ መጀመሪያ ኦፊሴላዊ የቴሌግራም ቻናላችንን ይቀላቀሉ:</b>\n"
            "👉 <b>@abdurea1</b>\n\n"
            "1️⃣ ከታች <b>📢 ቻናሉን ይቀላቀሉ</b> የሚለውን ተጭነው ይቀላቀሉ።\n"
            "2️⃣ እንደተቀላቀሉ <b>✅ አባልነትን ያረጋግጡ</b> የሚለውን ተጭነው ቦቱን ይጠቀሙ!"
        ),
        "ar": (
            "👋 <b>WELCOME TO ABDUREA STORE</b>\n\n"
            "📢 <b>لاستخدام هذا البوت، يجب أولاً الانضمام إلى قناتنا الرسمية:</b>\n"
            "👉 <b>@abdurea1</b>\n\n"
            "1️⃣ اضغط على <b>📢 انضم للقناة</b> أدناه للانضمام.\n"
            "2️⃣ بعد الانضمام، اضغط على <b>✅ التحقق من الاشتراك</b> لبدء استخدام البوت!"
        )
    },
    "btn_join_channel": {
        "en": "📢 Join Channel",
        "am": "📢 ቻናሉን ይቀላቀሉ",
        "ar": "📢 انضم للقناة"
    },
    "btn_check_membership": {
        "en": "✅ Check Membership",
        "am": "✅ አባልነትን ያረጋግጡ",
        "ar": "✅ التحقق من الاشتراك"
    },
    "alert_must_join_channel": {
        "en": "⚠️ You have not joined our channel yet! Please join @abdurea1 first and click 'Check Membership'.",
        "am": "⚠️ እስካሁን ቻናሉን አልተቀላቀሉም! እባክዎ መጀመሪያ @abdurea1 ይቀላቀሉ እና 'አባልነትን ያረጋግጡ' የሚለውን ይጫኑ።",
        "ar": "⚠️ لم تنضم إلى القناة بعد! يرجى الانضمام إلى @abdurea1 أولاً ثم الضغط على 'التحقق من الاشتراك'."
    },
    "alert_membership_verified": {
        "en": "✅ Channel membership verified! Welcome to ABDUREA STORE!",
        "am": "✅ አባልነትዎ ተረጋግጧል! እንኳን ወደ ABDUREA STORE በደህና መጡ!",
        "ar": "✅ تم التحقق من الاشتراك بنجاح! أهلاً بك في متجر ABDUREA STORE!"
    },

    # Main Menu Buttons
    "btn_browse": {
        "en": "🛍️ Browse Products",
        "am": "🛍️ ምርቶችን ይመልከቱ",
        "ar": "🛍️ تصفح المنتجات"
    },
    "btn_orders": {
        "en": "📦 My Orders",
        "am": "📦 ትዕዛዞቼ",
        "ar": "📦 طلباتي"
    },
    "btn_payment_info": {
        "en": "💳 Payment Instructions",
        "am": "💳 የክፍያ መመሪያ",
        "ar": "💳 تعليمات الدفع"
    },
    "btn_support": {
        "en": "📞 Contact Support",
        "am": "📞 የደንበኞች ድጋፍ",
        "ar": "📞 خدمة العملاء"
    },
    "btn_about": {
        "en": "ℹ️ About Store",
        "am": "ℹ️ ስለ መደብሩ",
        "ar": "ℹ️ حول المتجر"
    },
    "btn_change_lang": {
        "en": "🌐 Language / ቋንቋ",
        "am": "🌐 ቋንቋ ቀይር",
        "ar": "🌐 تغيير اللغة"
    },

    # Navigation Buttons
    "btn_back": {
        "en": "🔙 Back",
        "am": "🔙 ተመለስ",
        "ar": "🔙 رجوع"
    },
    "btn_main_menu": {
        "en": "🏠 Main Menu",
        "am": "🏠 ዋና ማውጫ",
        "ar": "🏠 القائمة الرئيسية"
    },
    "btn_cancel_order": {
        "en": "❌ Cancel Order",
        "am": "❌ ትዕዛዝ ሰርዝ",
        "ar": "❌ إلغاء الطلب"
    },
    "btn_change_payment": {
        "en": "🔄 Change Payment Method",
        "am": "🔄 የመክፈያ ዘዴ ቀይር",
        "ar": "🔄 تغيير طريقة الدفع"
    },
    "btn_track_order": {
        "en": "📦 Track Order Status",
        "am": "📦 የትዕዛዝ ሁኔታ ተከታተል",
        "ar": "📦 تتبع حالة الطلب"
    },
    "btn_contact_admin": {
        "en": "💬 CONTACT ADMIN",
        "am": "💬 ከአስተዳዳሪው ጋር ይገናኙ",
        "ar": "💬 محادثة الإدارة"
    },
    "btn_confirm_order": {
        "en": "✅ CONFIRM ORDER & PAY",
        "am": "✅ ትዕዛዙን አረጋግጥ እና ክፈል",
        "ar": "✅ تأكيد الطلب واختيار الدفع"
    },
    "btn_back_to_plans": {
        "en": "🔙 Back to Plans",
        "am": "🔙 ወደ ፕላኖች ተመለስ",
        "ar": "🔙 العودة للخطط"
    },

    # Welcome Message
    "welcome_text": {
        "en": (
            "👋 <b>WELCOME TO ABDUREA STORE</b>\n\n"
            "Hey <b>{name}</b>! Browse our digital products and services below. All files are delivered instantly after purchase."
        ),
        "am": (
            "👋 <b>WELCOME TO ABDUREA STORE</b>\n\n"
            "ሰላም <b>{name}</b>! ከዚህ በታች ያሉትን የዲጂታል ምርቶቻችንን እና አገልግሎቶቻችንን ይመልከቱ። ሁሉም እቃዎች ከክፍያ በኋላ ወዲያውኑ ይላካሉ።"
        ),
        "ar": (
            "👋 <b>WELCOME TO ABDUREA STORE</b>\n\n"
            "أهلاً <b>{name}</b>! تصفح خدماتنا ومنتجاتنا الرقمية أدناه. يتم تسليم جميع الطلبات فوراً بعد إتمام الشراء."
        )
    },

    # Catalog & Products
    "catalog_title": {
        "en": "🛍️ <b>SELECT A SERVICE</b>\n\nTap any service below to view plans and prices:",
        "am": "🛍️ <b>አገልግሎት ይምረጡ</b>\n\nዋጋዎችንና ፕላኖችን ለማየት አንዱን ይጫኑ:",
        "ar": "🛍️ <b>اختر الخدمة</b>\n\nاضغط على أي خدمة لعرض الخطط والأسعار:"
    },
    "no_products": {
        "en": "❌ No services are currently available. Please check back soon!",
        "am": "❌ በአሁኑ ጊዜ ምንም አገልግሎት አልተገኘም። እባክዎ ትንሽ ቆይተው እንደገና ይመልከቱ!",
        "ar": "❌ لا توجد خدمات متاحة حالياً. يرجى المحاولة لاحقاً!"
    },
    "from_price": {
        "en": " — from {price:,.0f} {currency}",
        "am": " — ከ {price:,.0f} {currency}",
        "ar": " — ابتداءً من {price:,.0f} {currency}"
    },
    "available_plans": {
        "en": "<b>Available Plans:</b>\n",
        "am": "<b>ያሉ ፕላኖች:</b>\n",
        "ar": "<b>الخطط المتاحة:</b>\n"
    },

    # Order Confirmation
    "order_confirmation_title": {
        "en": "📋 <b>ORDER SUMMARY</b>",
        "am": "📋 <b>የትዕዛዝ ማጠቃለያ</b>",
        "ar": "📋 <b>ملخص الطلب</b>"
    },
    "order_conf_service": {
        "en": "🛍️ <b>Service:</b> {service}",
        "am": "🛍️ <b>አገልግሎት:</b> {service}",
        "ar": "🛍️ <b>الخدمة:</b> {service}"
    },
    "order_conf_duration": {
        "en": "⏳ <b>Plan:</b> {duration}",
        "am": "⏳ <b>ፕላን:</b> {duration}",
        "ar": "⏳ <b>الخطة:</b> {duration}"
    },
    "order_conf_amount": {
        "en": "💰 <b>Price:</b> <b>{amount:,.0f} {currency}</b>",
        "am": "💰 <b>ዋጋ:</b> <b>{amount:,.0f} {currency}</b>",
        "ar": "💰 <b>المبلغ:</b> <b>{amount:,.0f} {currency}</b>"
    },
    "order_conf_customer": {
        "en": (
            "👤 <b>Customer:</b> {name} ({username})\n\n"
            "<i>Click below to continue to payment:</i>"
        ),
        "am": (
            "👤 <b>ደንበኛ:</b> {name} ({username})\n\n"
            "<i>ክፍያ ለመምረጥ ከታች ያለውን ይጫኑ:</i>"
        ),
        "ar": (
            "👤 <b>العميل:</b> {name} ({username})\n\n"
            "<i>اضغط أدناه لاختيار طريقة الدفع:</i>"
        )
    },

    # Payment Selection
    "select_payment_title": {
        "en": (
            "💳 <b>CHOOSE PAYMENT METHOD</b>\n\n"
            "<b>{service}</b> ({duration})\n"
            "<b>Total:</b> <b>{amount:,.0f} {currency}</b>\n\n"
            "Choose Telebirr or CBE Birr below:"
        ),
        "am": (
            "💳 <b>የመክፈያ ዘዴ ይምረጡ</b>\n\n"
            "<b>{service}</b> ({duration})\n"
            "<b>ጠቅላላ ዋጋ:</b> <b>{amount:,.0f} {currency}</b>\n\n"
            "በቴሌብር ወይስ በንግድ ባንክ መክፈል ይፈልጋሉ?"
        ),
        "ar": (
            "💳 <b>اختر طريقة الدفع</b>\n\n"
            "<b>{service}</b> ({duration})\n"
            "<b>المبلغ الإجمالي:</b> <b>{amount:,.0f} {currency}</b>\n\n"
            "اختر طريقة الدفع أدناه:"
        )
    },

    # Payment Instructions
    "payment_instructions_header": {
        "en": (
            "💳 <b>{method} PAYMENT</b>\n\n"
            "<b>Service:</b> {service} ({duration})\n"
            "<b>Amount:</b> <b>{amount:,.0f} {currency}</b>\n\n"
            "{account_display}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📸 <b>UPLOAD THE RECEIPT</b>\n"
            "<i>Send your receipt screenshot or photo here now:</i>"
        ),
        "am": (
            "💳 <b>{method} ክፍያ</b>\n\n"
            "<b>አገልግሎት:</b> {service} ({duration})\n"
            "<b>የሚከፈለው:</b> <b>{amount:,.0f} {currency}</b>\n\n"
            "{account_display}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📸 <b>ደረሰኙን ይላኩ (UPLOAD THE RECEIPT)</b>\n"
            "<i>የከፈሉበትን ደረሰኝ ስክሪንሾት ወይም ፎቶ እዚህ ይላኩ:</i>"
        ),
        "ar": (
            "💳 <b>دفع {method}</b>\n\n"
            "<b>الخدمة:</b> {service} ({duration})\n"
            "<b>المبلغ:</b> <b>{amount:,.0f} {currency}</b>\n\n"
            "{account_display}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📸 <b>ارفع الإيصال (UPLOAD THE RECEIPT)</b>\n"
            "<i>أرسل لقطة الشاشة أو صورة الإيصال هنا الآن:</i>"
        )
    },

    # Receipt Received & Review
    "receipt_success_msg": {
        "en": (
            "✅ <b>Order Placed! (#{order_number})</b>\n\n"
            "<b>Service:</b> {service} ({duration})\n"
            "<b>Amount:</b> {amount:,.0f} {currency} ({method})\n"
            "<b>Status:</b> 🟠 Payment Under Review\n\n"
            "We are checking your receipt. Your account will be sent right here soon!\n\n"
            "🙏 <i>Thank you for choosing {store_name}!</i>"
        ),
        "am": (
            "✅ <b>ትዕዛዝዎ ተመዝግቧል! (#{order_number})</b>\n\n"
            "<b>አገልግሎት:</b> {service} ({duration})\n"
            "<b>ዋጋ:</b> {amount:,.0f} {currency} ({method})\n"
            "<b>ሁኔታ:</b> 🟠 ክፍያው እየተረጋገጠ ነው\n\n"
            "ደረሰኝዎ እየተጣራ ነው። ልክ እንደተረጋገጠ አካውንትዎ እዚሁ ይላካል!\n\n"
            "🙏 <i>{store_name}ን ስለመረጡ እናመሰግናለን!</i>"
        ),
        "ar": (
            "✅ <b>تم استلام طلبك! (#{order_number})</b>\n\n"
            "<b>الخدمة:</b> {service} ({duration})\n"
            "<b>المبلغ:</b> {amount:,.0f} {currency} ({method})\n"
            "<b>الحالة:</b> 🟠 قيد المراجعة\n\n"
            "جاري فحص الإيصال. سيتم تسليم الحساب هنا فور التأكيد!\n\n"
            "🙏 <i>شكراً لاختيارك {store_name}!</i>"
        )
    },

    # Customer Identity Prompt
    "ask_customer_name": {
        "en": "👤 <b>Please send your Full Name:</b>",
        "am": "👤 <b>እባክዎ ሙሉ ስምዎን ይላኩ:</b>",
        "ar": "👤 <b>الرجاء إرسال اسمك الكامل:</b>"
    },

    # General Payment Info Menu
    "general_payment_info": {
        "en": (
            "💳 <b>PAYMENT ACCOUNTS</b>\n\n"
            "📱 <b>TELEBIRR</b>\n"
            "Account: <code>{telebirr_acc}</code>\n"
            "Name: <b>{telebirr_name}</b>\n\n"
            "🏦 <b>CBE BIRR</b>\n"
            "Account: <code>{cbe_acc}</code>\n"
            "Name: <b>{cbe_name}</b>\n\n"
            "<i>Pay to either account above and upload the receipt when ordering.</i>"
        ),
        "am": (
            "💳 <b>የክፍያ አካውንቶች</b>\n\n"
            "📱 <b>ቴሌብር</b>\n"
            "አካውንት: <code>{telebirr_acc}</code>\n"
            "ስም: <b>{telebirr_name}</b>\n\n"
            "🏦 <b>ንግድ ባንክ (CBE)</b>\n"
            "አካውንት: <code>{cbe_acc}</code>\n"
            "ስም: <b>{cbe_name}</b>\n\n"
            "<i>በፈለጉት ከፍለው ደረሰኙን ሲያዙ ይላኩ።</i>"
        ),
        "ar": (
            "💳 <b>حسابات الدفع</b>\n\n"
            "📱 <b>تليبر (TELEBIRR)</b>\n"
            "الحساب: <code>{telebirr_acc}</code>\n"
            "الاسم: <b>{telebirr_name}</b>\n\n"
            "🏦 <b>البنك التجاري (CBE)</b>\n"
            "الحساب: <code>{cbe_acc}</code>\n"
            "الاسم: <b>{cbe_name}</b>\n\n"
            "<i>حول لأي من الحسابين وأرسل الإيصال عند الطلب.</i>"
        )
    },

    # Contact Support Menu
    "support_info": {
        "en": (
            "📞 <b>SUPPORT</b>\n\n"
            "Admin: <b>@{support_user}</b>\n\n"
            "<i>Click below to message support:</i>"
        ),
        "am": (
            "📞 <b>የደንበኞች ድጋፍ</b>\n\n"
            "አስተዳዳሪ: <b>@{support_user}</b>\n\n"
            "<i>ከአስተዳዳሪው ጋር ለመነጋገር ከታች ይጫኑ:</i>"
        ),
        "ar": (
            "📞 <b>خدمة العملاء</b>\n\n"
            "المسؤول: <b>@{support_user}</b>\n\n"
            "<i>اضغط أدناه لمراسلة الإدارة:</i>"
        )
    },

    # About Store Menu
    "about_store": {
        "en": (
            "ℹ️ <b>ABOUT ABDUREA STORE</b>\n\n"
            "Premium digital subscriptions in Ethiopia.\n"
            "• Instant Telegram delivery\n"
            "• Telebirr & CBE Birr payments\n"
            "• 100% replacement warranty\n\n"
            "🌐 <b>Website:</b> https://abdureastore.base44.app/\n"
            "📞 <b>Support:</b> @{support_user}"
        ),
        "am": (
            "ℹ️ <b>ስለ ABDUREA STORE</b>\n\n"
            "ተመራጭ ዲጂታል አገልግሎቶች በኢትዮጵያ።\n"
            "• ፈጣን አቅርቦት በቴሌግራም\n"
            "• በቴሌብር እና በንግድ ባንክ\n"
            "• 100% ሙሉ ዋስትና\n\n"
            "🌐 <b>ድረ-ገጽ:</b> https://abdureastore.base44.app/\n"
            "📞 <b>ድጋፍ:</b> @{support_user}"
        ),
        "ar": (
            "ℹ️ <b>حول ABDUREA STORE</b>\n\n"
            "الاشتراكات والخدمات الرقمية في إثيوبيا.\n"
            "• تسليم فوري عبر تيليجرام\n"
            "• دفع عبر تليبر والبنك التجاري\n"
            "• ضمان استبدال 100%\n\n"
            "🌐 <b>الموقع:</b> https://abdureastore.base44.app/\n"
            "📞 <b>الدعم:</b> @{support_user}"
        )
    },

    # My Orders Menu
    "no_orders_msg": {
        "en": "📦 <b>MY ORDERS</b>\n\nYou haven't placed any orders yet.\nBrowse our services to make your first purchase!",
        "am": "📦 <b>የትዕዛዞቼ ዝርዝር</b>\n\nእስካሁን ምንም ትዕዛዝ አላስገቡም።\nምርቶቻችንን በመመልከት የመጀመሪያ ትዕዛዝዎን ይፈጽሙ!",
        "ar": "📦 <b>طلباتي</b>\n\nلم تقم بإنشاء أي طلبات بعد.\nتصفح الخدمات والمنتجات لإجراء أول عملية شراء!"
    },

    # Language Switch Confirmation
    "lang_switched": {
        "en": "✅ Language changed to <b>English</b>!",
        "am": "✅ ቋንቋው ወደ <b>አማርኛ</b> ተቀይሯል!",
        "ar": "✅ تم تغيير اللغة إلى <b>العربية</b>!"
    }
}


def t(key: str, lang: str = "en", **kwargs) -> str:
    """Retrieves the localized text for the given key and language, formatting kwargs."""
    if lang not in LANGUAGES:
        lang = DEFAULT_LANGUAGE

    string_dict = STRINGS.get(key, {})
    template = string_dict.get(lang) or string_dict.get(DEFAULT_LANGUAGE) or key

    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template


def get_language_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for choosing language."""
    keyboard = [
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
            InlineKeyboardButton("🇪🇹 አማርኛ", callback_data="setlang_am"),
            InlineKeyboardButton("🇸🇦 العربية", callback_data="setlang_ar")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_main_menu_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Builds the main menu inline keyboard with admin-configured button colors."""
    try:
        import models
        def _btn(text, cb, key):
            st = models.get_button_color(key)
            kwargs = {"callback_data": cb}
            if st in ("success", "primary", "danger"):
                kwargs["api_kwargs"] = {"style": st}
            return InlineKeyboardButton(text, **kwargs)
    except Exception:
        def _btn(text, cb, key):
            return InlineKeyboardButton(text, callback_data=cb)

    keyboard = [
        [_btn(t("btn_browse", lang), "menu_services", "btn_color_browse")],
        [_btn(t("btn_orders", lang), "menu_orders", "btn_color_orders")],
        [_btn(t("btn_payment_info", lang), "menu_payment_info", "btn_color_payment")],
        [_btn(t("btn_support", lang), "menu_support", "btn_color_support")],
        [_btn(t("btn_about", lang), "menu_about", "btn_color_about")],
        [_btn(t("btn_change_lang", lang), "menu_language", "btn_color_language")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_navigation_buttons(back_callback: str = "menu_services", lang: str = "en"):
    """Localized back and main menu buttons with admin-configured colors."""
    try:
        import models
        st_back = models.get_button_color("btn_color_back")
    except Exception:
        st_back = "primary"

    back_kwargs = {"callback_data": back_callback}
    if st_back in ("success", "primary", "danger"):
        back_kwargs["api_kwargs"] = {"style": st_back}

    return [
        [
            InlineKeyboardButton(t("btn_back", lang), **back_kwargs),
            InlineKeyboardButton(t("btn_main_menu", lang), callback_data="menu_main")
        ]
    ]


def get_reply_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    """
    Builds bottom Reply Keyboard for the user's selected language.
    Enables the 4-dots keyboard icon in the Telegram chat bar.
    Product button is full-width (single item in top row).
    """
    keyboard = [
        [KeyboardButton(t("btn_browse", lang))],
        [KeyboardButton(t("btn_orders", lang)), KeyboardButton(t("btn_payment_info", lang))],
        [KeyboardButton(t("btn_support", lang)), KeyboardButton(t("btn_about", lang))]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


MENU_ACTION_MATCHERS = {
    "browse": {"🛍️ Browse Products", "🛍️ ምርቶችን ይመልከቱ", "🛍️ تصفح المنتجات", "browse", "products", "services"},
    "orders": {"📦 My Orders", "📦 ትዕዛዞቼ", "📦 طلباتي", "orders", "my orders"},
    "payment": {"💳 Payment Instructions", "💳 የክፍያ መመሪያ", "💳 تعليمات الدفع", "payment", "payments"},
    "support": {"📞 Contact Support", "📞 የደንበኞች ድጋፍ", "📞 خدمة العملاء", "support", "admin"},
    "about": {"ℹ️ About Store", "ℹ️ ስለ መደብሩ", "ℹ️ حول المتجر", "about", "info"},
    "language": {"🌐 Language / ቋንቋ", "🌐 ቋንቋ ቀይር", "🌐 تغيير اللغة", "language", "lang"},
    "main_menu": {"🏠 Main Menu", "🏠 ዋና ማውጫ", "🏠 القائمة الرئيسية", "menu", "main menu"}
}
