from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from db.mongodb import db


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    now = datetime.now(timezone.utc)

    await db.users.update_one(
        {"telegram_id": user.id},
        {
            "$set": {
                "telegram_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language": user.language_code or "en",
                "preferred_tone": "friendly",
                "voice_enabled": False,
                "updated_at": now,
                "last_seen_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
                "proactive_enabled": True,
                "proactivity_level": "medium",
            },
        },
        upsert=True,
    )

    await update.message.reply_text(
        f"Hey {user.first_name}. I'm Poke.AI.\n\nText me anything. I'll keep track of what matters."
    )
