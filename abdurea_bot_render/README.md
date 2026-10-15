# 🚀 ABDUREA STORE — Dual Telegram Bot System

A high-performance, production-ready Dual-Bot Commerce & Administration ecosystem for **ABDUREA STORE**.

- **Customer Bot**: Full digital store where customers can browse categories, select subscription plans, make payments (Telebirr & CBE Birr), upload receipt screenshots, and receive automated delivery of accounts/credentials.
- **Dedicated Admin Bot**: Complete administrative control center accessible exclusively by the store owner (`@trust994` / `ID: 6666443909`). Features 100% parity with web dashboards: instant receipt review, 1-click approvals, 1-click delivery, product catalog editor, financial analytics, customer directory, and mass broadcasts.

---

## 🌟 Architecture & Features

### 🛍️ Customer Bot (`telegram_bot.py`)
- **Multilingual Support**: English (EN), Amharic (AM), and Oromo (OM).
- **Dynamic Catalog**: Products and plans loaded live from SQLite database (`store.db`).
- **Sequential Ordering Flow**:
  `/start` ➔ Category ➔ Service ➔ Plan ➔ Payment Method (Telebirr / CBE) ➔ Payment Guide ➔ Receipt Screenshot ➔ Customer Name & Details ➔ Confirmation `#ABD-YYYYMMDD-XXX`.
- **Duplicate Receipt Shield**: Uses Telegram unique file hashes to block replay attacks.
- **Instant Credentials Delivery**: Dispatches accounts/passwords/licenses directly into customer's chat upon admin completion.

### 👑 Dedicated Admin Bot (`admin_bot.py`)
- **Strict Authorization**: Only Admin Telegram ID `6666443909` (`@trust994`) can access.
- **Real-Time Push Alerts**: Receipt uploads immediately notify the admin with receipt photos and quick buttons.
- **1-Click Approvals**: Approve orders instantly; customer is notified automatically.
- **Reject with Explanation**: Preset reason buttons or custom reason typed in chat.
- **1-Click Digital Delivery**: Paste credentials or keys; bot marks order completed and dispatches to customer.
- **Live Financial Analytics**: Today, 7-Day, Month, Year, All-Time revenue, AOV, and top services.
- **Product & Plan Manager**: Add new products via 4-step wizard, edit plan prices in ETB, toggle active/inactive.
- **Customer Management & 1-on-1 Chat**: View customer spend history and send direct messages.
- **Store-Wide Broadcast**: Send announcements with photos to All, Completed, or Pending customers.
- **Live Settings Editor**: Change Telebirr or CBE account numbers anytime directly in Telegram.

---

## 📁 Repository Structure

```
├── admin_bot.py              # Dedicated Telegram Admin Dashboard Bot
├── telegram_bot.py           # Customer Commerce Bot
├── run_bots.py               # Unified async multi-bot runner
├── bot_bridge.py             # Cross-bot messaging & real-time alerts
├── models.py                 # SQLite Data Access Layer & Business Logic
├── database.py               # Database schemas, migrations, WAL mode
├── config.py                 # Configuration loader with environment variables
├── i18n.py                   # Multi-language translations (EN, AM, OM)
├── main.py                   # Production deployment entrypoint
├── run.py                    # Local CLI runner
├── start_bot.bat             # 1-Click Windows startup launcher
├── store.db                  # Local SQLite database with products and orders
├── .env.example              # Environment variables template
├── requirements.txt          # Python dependencies
├── Dockerfile                # Docker container configuration
├── Procfile                  # Cloud process file (Heroku / Railway / Render)
├── render.yaml               # 1-Click Render Blueprint deployment
└── README.md                 # Project documentation
```

---

## ⚙️ Environment Configuration (`.env`)

```ini
# Bot Tokens from @BotFather
MAIN_BOT_TOKEN=YOUR_CUSTOMER_BOT_TOKEN
ADMIN_BOT_TOKEN=8920397884:AAHOFpkA-IhcBcCVE5MibMAdj8a6WMfm8gA

# Admin Security
ADMIN_TELEGRAM_ID=6666443909
ADMIN_TELEGRAM_USERNAME=@trust994

# Payment Accounts
TELEBIRR_ACCOUNT=0992558519
TELEBIRR_NAME=𝙁𝘼𝙍𝙃𝘼𝙉
CBE_BIRR_ACCOUNT=1000572238575
CBE_BIRR_NAME=𝙁𝘼𝙍𝙃𝘼𝙉
SUPPORT_USERNAME=@trust994
```

---

## 🚀 How to Upload to GitHub

1. Open your browser and go to [github.com/new](https://github.com/new).
2. Create a new repository (e.g. `abdurea-store-bot`).
3. Click **"uploading an existing file"**.
4. Open the folder **`ABDUREA_BOT_GITHUB_UPLOAD`** on your Desktop:
   - Drag all the files and folders from that folder directly into GitHub.
5. Click **Commit changes**.

---

## 🌐 24/7 Cloud Deployment (Free & Easy)

### Option A: Deploy on Render.com (Recommended - 100% Free)
1. Go to [render.com](https://render.com) and sign in with GitHub.
2. Click **New +** ➔ **Web Service** (or **Blueprint** to use `render.yaml` automatically).
3. Connect your new GitHub repository.
4. Set the following settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python run_bots.py`
   - **Instance Type**: `Free`
5. In **Environment Variables**, add:
   - `MAIN_BOT_TOKEN`: `8848082794:AAEzNGEQYHD9dpTqe9kbUFBJhJ9n5OK4NIA`
   - `ADMIN_BOT_TOKEN`: `8920397884:AAHOFpkA-IhcBcCVE5MibMAdj8a6WMfm8gA`
   - `ADMIN_TELEGRAM_ID`: `6666443909`
   - `ADMIN_TELEGRAM_IDS`: `6666443909,2145055956`
   - `TELEBIRR_ACCOUNT`: `0992558519`
   - `TELEBIRR_NAME`: `𝙁𝘼𝙍𝙃𝘼𝙉`
   - `CBE_BIRR_ACCOUNT`: `1000572238575`
   - `CBE_BIRR_NAME`: `𝙁𝘼𝙍𝙃𝘼𝙉`
   - `SUPPORT_USERNAME`: `@trust994`
6. Click **Deploy Web Service**. Your dual bots and web health server are now online 24/7!

### Option B: Deploy on Railway.app
1. Go to [railway.app](https://railway.app) ➔ **New Project** ➔ **Deploy from GitHub repo**.
2. Select your repository.
3. In Variables, add your `.env` values.
4. Railway will deploy automatically from the `Procfile`!

### Option C: Run Locally on Windows
Double-click **`start_bot.bat`** or run:
```bash
python run_bots.py
```
