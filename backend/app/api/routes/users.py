from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.db.mongodb import db

router = APIRouter(prefix="/users", tags=["users"])


class TelegramUserCreate(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language: Optional[str] = "en"
    timezone: Optional[str] = "Africa/Cairo"


@router.post("/telegram")
async def upsert_telegram_user(user_data: TelegramUserCreate):
    now = datetime.now(timezone.utc)

    user_doc = {
        "telegram_id": user_data.telegram_id,
        "username": user_data.username,
        "first_name": user_data.first_name,
        "last_name": user_data.last_name,
        "language": user_data.language,
        "timezone": user_data.timezone,
        "preferred_tone": "friendly",
        "voice_enabled": False,
        "updated_at": now,
        "last_seen_at": now,
    }

    result = await db.users.update_one(
        {"telegram_id": user_data.telegram_id},
        {
            "$set": user_doc,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    saved_user = await db.users.find_one({"telegram_id": user_data.telegram_id})

    return {
        "message": "telegram user saved",
        "created": result.upserted_id is not None,
        "user_id": str(saved_user["_id"]),
        "telegram_id": saved_user["telegram_id"],
    }