"""
proactive_worker.py
Background loop that finds due proactive tasks and sends warm messages
from poke.Ai to the user — without being asked.

Flow per tick:
  1. Find one pending task with scheduled_time <= now (atomic lock)
  2. Cooldown check: has the user received a proactive message recently?
  3. Build context with mode="proactive"
  4. Ask Gemini to write a natural, caring outbound message
  5. Send via Telegram bot API
  6. Save as assistant message in messages collection
  7. Mark task as sent (or failed)
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from bson import ObjectId

from db.mongodb import db
from backend.app.services.context_builder import build_user_context, format_context_for_prompt
from backend.app.services.gemini_client import generate_reply
from backend.app.services.telegram_sender import send_telegram_message

logger = logging.getLogger(__name__)

# How often the worker wakes up (seconds)
WORKER_INTERVAL_SECONDS = 30

# Cooldown thresholds by proactivity_level
_COOLDOWN_HOURS = {
    "low": 48,
    "medium": 24,
    "high": 8,
}

_PROACTIVE_PROMPT_TEMPLATE = """\
You are poke.Ai texting {name} first — they didn't message you.

Why you're reaching out:
{reason}

What you know about {name}:
{context}

Write one short Telegram message.

Rules:
- Sound like a close friend who remembered something
- Maximum 40 words
- No AI-like phrases, no assistant tone
- Max 1 emoji
- Do not mention scheduling or automation
- Do not over-explain
- Make it feel personal and real\
"""


async def proactive_worker_loop() -> None:
    """Infinite loop: every WORKER_INTERVAL_SECONDS, process one due task."""
    logger.info("Proactive worker started (interval: %ds)", WORKER_INTERVAL_SECONDS)
    while True:
        try:
            await _process_one_task()
        except Exception as e:
            logger.error("Worker tick error: %s", e)
        await asyncio.sleep(WORKER_INTERVAL_SECONDS)


async def _process_one_task() -> None:
    now = datetime.now(timezone.utc)

    # Atomically lock one pending task — prevents double-send under concurrent workers
    task = await db.proactive_tasks.find_one_and_update(
        {
            "status": "pending",
            "scheduled_time": {"$lte": now},
        },
        {"$set": {"status": "sending"}},
        sort=[("priority", -1), ("scheduled_time", 1)],
        return_document=True,  # return updated doc
    )

    if not task:
        return  # nothing due

    task_id = task["_id"]
    user_id = task["user_id"]
    telegram_id = task.get("telegram_id")
    reason = task.get("reason") or task.get("message_to_send") or "just checking in"

    try:
        # Load user to verify they still exist and get proactivity level
        user = await db.users.find_one({"_id": user_id})
        if not user or not user.get("telegram_id"):
            await _fail_task(task_id, "User not found or missing telegram_id")
            return

        # Cooldown check
        cooldown_result = await _check_cooldown(user_id, user)
        if cooldown_result["blocked"]:
            reschedule_at = cooldown_result["reschedule_at"]
            logger.info(
                "Task %s blocked by cooldown for user %s — rescheduling to %s",
                task_id, telegram_id, reschedule_at,
            )
            await db.proactive_tasks.update_one(
                {"_id": task_id},
                {"$set": {"status": "pending", "scheduled_time": reschedule_at}},
            )
            return

        # Build context for proactive mode
        context = await build_user_context(user_id, incoming_message=None, mode="proactive")
        context_str = format_context_for_prompt(context)
        name = user.get("first_name") or user.get("username") or "there"

        # Generate the outbound message
        prompt = _PROACTIVE_PROMPT_TEMPLATE.format(
            name=name,
            reason=reason,
            context=context_str,
        )
        message_text = await generate_reply(prompt)

        # Send via Telegram
        sent = await send_telegram_message(telegram_id, message_text)
        if not sent:
            await _fail_task(task_id, "Telegram send returned False")
            return

        # Persist as an assistant message so it's in conversation history
        sent_at = datetime.now(timezone.utc)
        await db.messages.insert_one({
            "user_id": user_id,
            "telegram_id": telegram_id,
            "telegram_message_id": None,
            "role": "assistant",
            "content": message_text,
            "message_type": "proactive",
            "created_at": sent_at,
        })

        # Mark task done
        await db.proactive_tasks.update_one(
            {"_id": task_id},
            {"$set": {"status": "sent", "sent_at": sent_at}},
        )
        logger.info("Proactive task %s sent to user %s", task_id, telegram_id)

    except Exception as e:
        logger.error("Failed to process task %s: %s", task_id, e)
        await _fail_task(task_id, str(e))


async def _check_cooldown(user_id: ObjectId, user: dict) -> dict:
    """
    Returns {"blocked": True/False, "reschedule_at": datetime | None}.
    Checks how recently the user received a proactive message and enforces limits.
    """
    level = user.get("proactivity_level", "medium")
    cooldown_hours = _COOLDOWN_HOURS.get(level, 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)

    last_proactive = await db.messages.find_one(
        {
            "user_id": user_id,
            "role": "assistant",
            "message_type": "proactive",
            "created_at": {"$gte": cutoff},
        },
        sort=[("created_at", -1)],
    )

    if last_proactive:
        last_sent = last_proactive["created_at"]
        reschedule_at = last_sent + timedelta(hours=cooldown_hours)
        return {"blocked": True, "reschedule_at": reschedule_at}

    return {"blocked": False, "reschedule_at": None}


async def _fail_task(task_id: ObjectId, error: str) -> None:
    await db.proactive_tasks.update_one(
        {"_id": task_id},
        {"$set": {"status": "failed", "error": error}},
    )
