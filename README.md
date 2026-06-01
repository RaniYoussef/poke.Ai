# poke.Ai
your life partner that really knows and cares for you, that reaches out before you even think about it.

---

## Project Structure

```
poke.Ai/
  backend/        # FastAPI REST API
  telegram_bot/   # Telegram bot
  db/             # Shared MongoDB connection
  requirements.txt
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Environment variables

Copy `.env.example` and fill in your values:

```bash
cp .env.example .env
```

```
TELEGRAM_BOT_TOKEN=your_token_here
MONGO_URL=mongodb://localhost:27017
MONGO_DB_NAME=poke_ai
```

Get your bot token from [@BotFather](https://t.me/BotFather) on Telegram.

The bot is live at [@pokeai_test1_bot](https://t.me/pokeai_test1_bot).

> **TODO:** Gemini API key is required for the agent to read and respond to messages.
> Get one from [aistudio.google.com](https://aistudio.google.com) and add it to `.env` as `GEMINI_API_KEY`.

---

## MongoDB

### Install

```bash
# Import MongoDB GPG key
curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | sudo gpg -o /usr/share/keyrings/mongodb-server-8.0.gpg --dearmor

# Add repository
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/8.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list

# Install
sudo apt update && sudo apt install -y mongodb-org
```

### Start

```bash
sudo systemctl start mongod
sudo systemctl enable mongod   # auto-start on boot
```

### Verify

```bash
sudo systemctl status mongod
```

---

## MongoDB GUI (Compass)

### Install

```bash
wget https://downloads.mongodb.com/compass/mongodb-compass_1.44.4_amd64.deb
sudo dpkg -i mongodb-compass_1.44.4_amd64.deb
```

### Open

```bash
mongodb-compass
```

Connect using: `mongodb://localhost:27017`

Browse the `poke_ai` database to see `users`, `messages`, `memories`, `events`, and `proactive_tasks` collections.

---

## Running

Start all three in separate terminals from the project root:

```bash
# 1. MongoDB (if not running)
sudo systemctl start mongod

# 2. Backend API
uvicorn backend.app.main:app --reload

# 3. Telegram bot
python -m telegram_bot.bot
```
