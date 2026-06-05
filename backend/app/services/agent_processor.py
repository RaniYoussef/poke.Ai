"""
agent_processor.py
Handles the full reactive message flow:
  user message → context → Gemini reply → save → background extraction
"""
import asyncio
import logging
from datetime import datetime, timezone

from db.mongodb import db
from backend.app.services.context_builder import build_user_context, format_context_for_prompt
from backend.app.services.gemini_client import generate_reply, update_memory_summary
from backend.app.services.memory_extractor import extract_and_save

logger = logging.getLogger(__name__)


async def process_user_message(
    telegram_id: int,
    content: str,
    telegram_message_id: int | None = None,
) -> dict:
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user:
        return {"success": False, "error": "User not found. Send /start first."}

    user_id = user["_id"]
    now = datetime.now(timezone.utc)

    # 1. Save incoming user message
    await db.messages.insert_one({
        "user_id": user_id,
        "telegram_id": telegram_id,
        "telegram_message_id": telegram_message_id,
        "role": "user",
        "content": content,
        "message_type": "text",
        "created_at": now,
    })

    # 2. Build context (user profile, memories, recent messages, events, tasks)
    context = await build_user_context(user_id, incoming_message=content, mode="reactive")

    # 3. Format context into a Gemini-ready prompt
    prompt = format_context_for_prompt(context)

    # 4. Generate AI reply
    try:
        reply = await generate_reply(prompt)
    except Exception as e:
        logger.error("Gemini reply failed for telegram_id %s: %s", telegram_id, e)
        reply = "Hey, I'm having a tiny glitch right now — give me a moment and try again!"

    # 5. Save assistant reply
    await db.messages.insert_one({
        "user_id": user_id,
        "telegram_id": telegram_id,
        "telegram_message_id": None,
        "role": "assistant",
        "content": reply,
        "message_type": "text",
        "created_at": datetime.now(timezone.utc),
    })

    # 6. Background tasks — don't block the response
    summary = context.get("summary", "")
    asyncio.create_task(_run_post_processing(user_id, telegram_id, content, reply, summary))

    return {"success": True, "response": reply}


async def _run_post_processing(
    user_id,
    telegram_id: int,
    user_message: str,
    ai_response: str,
    summary: str,
) -> None:
    """
    Runs after the reply is sent:
    - Updates the rolling conversation summary
    - Extracts structured memories, events, and proactive tasks
    """
    try:
        # Update rolling summary (needed for next message's context)
        new_summary = await update_memory_summary(summary, user_message, ai_response)
        now = datetime.now(timezone.utc)
        await db.memories.insert_one({
            "user_id": user_id,
            "telegram_id": telegram_id,
            "memory_type": "conversation_summary",
            "content": new_summary,
            "importance": 1.0,
            "confidence": 1.0,
            "source_message_id": None,
            "created_at": now,
            "last_used_at": now,
        })
    except Exception as e:
        logger.error("Summary update failed for telegram_id %s: %s", telegram_id, e)

    # Extract structured memories, events, proactive tasks from this turn
    await extract_and_save(user_id, telegram_id, user_message, ai_response, summary)
