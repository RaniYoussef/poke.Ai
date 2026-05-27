from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.mongodb import db

router = APIRouter(prefix="/messages", tags=["messages"])


class MessageCreate(BaseModel):
    telegram_id: int
    role: str
    content: str
    telegram_message_id: Optional[int] = None
    message_type: str = "text"


@router.post("")
async def create_message(message_data: MessageCreate):
    user = await db.users.find_one({"telegram_id": message_data.telegram_id})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)

    message_doc = {
        "user_id": user["_id"],
        "telegram_id": message_data.telegram_id,
        "telegram_message_id": message_data.telegram_message_id,
        "role": message_data.role,
        "content": message_data.content,
        "message_type": message_data.message_type,
        "created_at": now,
    }

    result = await db.messages.insert_one(message_doc)

    return {
        "message": "message saved",
        "message_id": str(result.inserted_id),
    }


@router.get("/{telegram_id}")
async def get_user_messages(telegram_id: int, limit: int = 20):
    messages_cursor = (
        db.messages
        .find({"telegram_id": telegram_id})
        .sort("created_at", -1)
        .limit(limit)
    )

    messages = []

    async for message in messages_cursor:
        messages.append({
            "id": str(message["_id"]),
            "telegram_id": message["telegram_id"],
            "role": message["role"],
            "content": message["content"],
            "message_type": message.get("message_type", "text"),
            "created_at": message["created_at"],
        })

    return {
        "telegram_id": telegram_id,
        "messages": messages,
    }