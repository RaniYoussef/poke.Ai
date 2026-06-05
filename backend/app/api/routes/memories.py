from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db.mongodb import db

router = APIRouter(prefix="/memories", tags=["memories"])


class MemoryCreate(BaseModel):
    telegram_id: int
    memory_type: str
    content: str
    importance: float = 0.5
    confidence: float = 0.8
    source_message_id: Optional[str] = None


@router.post("")
async def create_memory(memory_data: MemoryCreate):
    user = await db.users.find_one({"telegram_id": memory_data.telegram_id})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)

    memory_doc = {
        "user_id": user["_id"],
        "telegram_id": memory_data.telegram_id,
        "memory_type": memory_data.memory_type,
        "content": memory_data.content,
        "importance": memory_data.importance,
        "confidence": memory_data.confidence,
        "source_message_id": memory_data.source_message_id,
        "created_at": now,
        "last_used_at": None,
    }

    result = await db.memories.insert_one(memory_doc)

    return {
        "message": "memory saved",
        "memory_id": str(result.inserted_id),
    }


@router.get("/{telegram_id}")
async def get_user_memories(telegram_id: int, limit: int = 20):
    cursor = (
        db.memories
        .find({"telegram_id": telegram_id})
        .sort("importance", -1)
        .limit(limit)
    )

    memories = []

    async for memory in cursor:
        memories.append({
            "id": str(memory["_id"]),
            "telegram_id": memory["telegram_id"],
            "memory_type": memory["memory_type"],
            "content": memory["content"],
            "importance": memory["importance"],
            "confidence": memory["confidence"],
            "created_at": memory["created_at"],
        })

    return {
        "telegram_id": telegram_id,
        "memories": memories,
    }