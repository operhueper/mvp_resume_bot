# Resume Bot

A Telegram bot that helps candidates build a professional resume through a conversational interview. The bot guides users through onboarding, collects work experience, skills, and education via a structured interview, then generates a polished resume using OpenAI and lets users export it.

## What it does

1. **Onboarding** — greets the user, collects basic contact info (name, email, phone, city, desired position).
2. **Interview** — asks targeted questions about work history, achievements, skills, and education.
3. **Draft** — generates a structured resume using GPT, stores it in Supabase.
4. **Export** — sends the finished resume to the user as a formatted message or file.

All user data and analytics events are stored in a Supabase (PostgreSQL) database. An admin dashboard at `admin/index.html` shows a conversion funnel and recent events.

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A Telegram bot token (create one via [@BotFather](https://t.me/BotFather))
- An OpenAI API key
- A [Supabase](https://supabase.com) project

### 2. Clone and install

```bash
git clone <repo-url>
cd "Бот поиска работы"
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in all values:

| Variable              | Description                                        |
|-----------------------|----------------------------------------------------|
| `TELEGRAM_BOT_TOKEN`  | Token from @BotFather                              |
| `OPENAI_API_KEY`      | Your OpenAI secret key                             |
| `ADMIN_TELEGRAM_ID`   | Your Telegram numeric user ID (for admin commands) |
| `SUPABASE_URL`        | Project URL from Supabase dashboard                |
| `SUPABASE_ANON_KEY`   | Anon/public key from Supabase dashboard            |
| `SUPABASE_SERVICE_KEY`| Service role key (for server-side writes)          |
| `ADMIN_SECRET`        | Arbitrary password for the admin dashboard         |

### 4. Run the database migration

In the Supabase dashboard, open the **SQL Editor** and run the contents of:

```
migrations/001_initial.sql
```

This creates all tables and indexes.

### 5. Run locally

```bash
python -m bot.main
```

The bot will start polling Telegram. Send `/start` to your bot to test it.

---

## Admin dashboard

Open `admin/index.html` in a browser (or serve it as a static file).

Before opening, replace the two placeholder strings in the file with your actual Supabase credentials, or use the deploy script which does it automatically via `sed`:

```bash
sed -i \
  "s|__SUPABASE_URL__|$SUPABASE_URL|g; \
   s|__SUPABASE_ANON_KEY__|$SUPABASE_ANON_KEY|g" \
  admin/index.html
```

When the page loads it will prompt for `ADMIN_SECRET`. After login it shows:
- Total users, DAU, conversion rate, export count
- Per-stage user counts (Onboarding / Interview / Draft / Exported)
- Conversion funnel chart
- Last 50 analytics events
- Auto-refreshes every 30 seconds

---

## Deploy to Render

1. Push the repository to GitHub.
2. In [Render](https://render.com), create a new **Web Service** and connect the repo.
3. Render will detect `render.yaml` automatically and configure the service.
4. In the Render dashboard, set all environment variables listed in `.env.example` under **Environment → Secret Files** or **Environment Variables**.
5. Click **Deploy**. Render will install dependencies and start the bot.

To redeploy after changes, push to the connected branch — Render will auto-deploy.

### Docker (optional)

```bash
docker build -t resume-bot .
docker run --env-file .env resume-bot
```

---

## Project structure

```
.
├── bot/                  # Bot source code
│   └── main.py           # Entry point
├── migrations/
│   └── 001_initial.sql   # Full database schema
├── admin/
│   └── index.html        # Single-file admin dashboard
├── requirements.txt
├── Dockerfile
├── render.yaml
└── .env.example
```
