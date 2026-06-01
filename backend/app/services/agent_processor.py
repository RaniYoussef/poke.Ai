from datetime import datetime, timezone

from db.mongodb import db
from backend.app.services.gemini_client import generate_reply, update_memory_summary


async def process_user_message(
    telegram_id: int,
    content: str,
    telegram_message_id: int | None = None,
):
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user:
        return {"success": False, "error": "User not found. Send /start first."}

    now = datetime.now(timezone.utc)

    # 1. Save user message
    await db.messages.insert_one({
        "user_id": user["_id"],
        "telegram_id": telegram_id,
        "telegram_message_id": telegram_message_id,
        "role": "user",
        "content": content,
        "message_type": "text",
        "created_at": now,
    })

    # 2. Load latest memory summary
    latest_memory = await db.memories.find_one(
        {"telegram_id": telegram_id, "memory_type": "conversation_summary"},
        sort=[("created_at", -1)],
    )
    memory_summary = latest_memory["content"] if latest_memory else ""

    # 3. Load last 10 messages for context (excluding the one just saved)
    cursor = db.messages.find(
        {"telegram_id": telegram_id, "content": {"$ne": content}}
    ).sort("created_at", -1).limit(10)

    recent = []
    async for msg in cursor:
        recent.append({"role": msg["role"], "content": msg["content"]})
    recent.reverse()

    # 4. Get AI response
    reply = await generate_reply(memory_summary, recent, content)

    # 5. Update memory summary
    new_summary = await update_memory_summary(memory_summary, content, reply)

    # 6. Save updated memory summary
    await db.memories.insert_one({
        "user_id": user["_id"],
        "telegram_id": telegram_id,
        "memory_type": "conversation_summary",
        "content": new_summary,
        "importance": 1.0,
        "confidence": 1.0,
        "source_message_id": None,
        "created_at": now,
        "last_used_at": now,
    })

    # 7. Save assistant response
    await db.messages.insert_one({
        "user_id": user["_id"],
        "telegram_id": telegram_id,
        "telegram_message_id": None,
        "role": "assistant",
        "content": reply,
        "message_type": "text",
        "created_at": datetime.now(timezone.utc),
    })

    return {"success": True, "response": reply}
