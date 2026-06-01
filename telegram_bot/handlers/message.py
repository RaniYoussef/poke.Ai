from datetime import datetime, timezone

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from db.mongodb import db
from telegram_bot.config import settings


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = update.message.text
    now = datetime.now(timezone.utc)

    # ensure user exists before backend processes the message
    await db.users.update_one(
        {"telegram_id": user.id},
        {
            "$set": {
                "telegram_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language": user.language_code or "en",
                "last_seen_at": now,
                "updated_at": now,
            },
            "$setOnInsert": {
                "preferred_tone": "friendly",
                "voice_enabled": False,
                "created_at": now,
            },
        },
        upsert=True,
    )

    # call backend agent (handles saving messages + generating AI reply)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.BACKEND_URL}/agent/process-message",
                json={
                    "telegram_id": user.id,
                    "content": text,
                    "telegram_message_id": update.message.message_id,
                },
                timeout=30.0,
            )
            data = resp.json()
        reply = data.get("response", "Sorry, something went wrong.")
    except Exception:
        reply = "Sorry, I'm having trouble responding right now. Try again in a moment."

    await update.message.reply_text(reply)
