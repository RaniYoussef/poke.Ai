# poke.Ai
Your life partner that really knows and cares for you — reaches out before you even think about it.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env
```

Fill in `.env`:

```
TELEGRAM_BOT_TOKEN=       # from @BotFather on Telegram
MONGO_URL=mongodb://localhost:27017
MONGO_DB_NAME=poke_ai
GEMINI_API_KEY=           # from Google Cloud (Vertex AI)
```

Bot is live at [@pokeai_test1_bot](https://t.me/pokeai_test1_bot).

---

## MongoDB

```bash
# Install
curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | sudo gpg -o /usr/share/keyrings/mongodb-server-8.0.gpg --dearmor
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/8.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list
sudo apt update && sudo apt install -y mongodb-org

# Start
sudo systemctl start mongod
sudo systemctl enable mongod
```

---

## Running

Start all three in separate terminals from the project root:

```bash
# Terminal 1 — Backend API
uvicorn backend.app.main:app --reload

# Terminal 2 — Telegram bot
python -m telegram_bot.bot

# Terminal 3 — Background workers
python -m backend.app.workers.run_all_workers
```
