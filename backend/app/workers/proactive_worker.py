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
from datetime import datetime, timezone

from bson import ObjectId

from db.mongodb import db
from backend.app.services.context_builder import build_user_context, format_context_for_prompt
from backend.app.services.gemini_client import generate_reply
from backend.app.services.proactive_decision_engine import (
    get_proactive_cooldown_state,
    get_quiet_hours_state,
)
from backend.app.services.telegram_sender import send_telegram_message

logger = logging.getLogger(__name__)

# How often the worker wakes up (seconds)
WORKER_INTERVAL_SECONDS = 30

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
            "$or": [
                {"scheduled_time": {"$lte": now}},
                {"scheduled_at": {"$lte": now}},
            ],
        },
        {"$set": {"status": "sending"}},
        sort=[("priority", -1), ("scheduled_time", 1), ("scheduled_at", 1)],
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

        telegram_id = telegram_id or user.get("telegram_id")

        quiet_state = get_quiet_hours_state(user)
        if quiet_state["blocked"]:
            reschedule_at = quiet_state["resume_at"]
            logger.info(
                "Task %s blocked by quiet hours for user %s - rescheduling to %s",
                task_id, telegram_id, reschedule_at,
            )
            await _reschedule_task(task_id, reschedule_at)
            return

        # Cooldown check
        cooldown_result = await get_proactive_cooldown_state(user_id, user)
        if cooldown_result["blocked"]:
            reschedule_at = cooldown_result["resume_at"]
            logger.info(
                "Task %s blocked by cooldown for user %s — rescheduling to %s",
                task_id, telegram_id, reschedule_at,
            )
            await _reschedule_task(task_id, reschedule_at)
            return

        message_text = _validated_message_override(task)
        if not message_text:
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
            "proactive_task_id": task_id,
            "proactive_type": task.get("task_type") or task.get("type"),
            "created_at": sent_at,
        })

        await db.users.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "last_proactive_message_at": sent_at,
                    "updated_at": sent_at,
                }
            },
        )

        await _mark_related_event_followed_up(task, sent_at)

        # Mark task done
        await db.proactive_tasks.update_one(
            {"_id": task_id},
            {
                "$set": {
                    "status": "sent",
                    "sent_at": sent_at,
                    "message_sent": message_text,
                }
            },
        )
        logger.info("Proactive task %s sent to user %s", task_id, telegram_id)

    except Exception as e:
        logger.error("Failed to process task %s: %s", task_id, e)
        await _fail_task(task_id, str(e))


def _validated_message_override(task: dict) -> str | None:
    message = (task.get("message_override") or "").strip()
    if not message:
        return None

    message = " ".join(message.split())
    if len(message.split()) > 45:
        logger.warning("Task %s message_override is too long; regenerating", task.get("_id"))
        return None

    return message[:500]


async def _reschedule_task(task_id: ObjectId, reschedule_at: datetime | None) -> None:
    reschedule_at = reschedule_at or datetime.now(timezone.utc)
    await db.proactive_tasks.update_one(
        {"_id": task_id},
        {
            "$set": {
                "status": "pending",
                "scheduled_time": reschedule_at,
                "scheduled_at": reschedule_at,
            }
        },
    )


async def _mark_related_event_followed_up(task: dict, sent_at: datetime) -> None:
    task_type = task.get("task_type") or task.get("type")
    if task_type != "event_followup" or not task.get("event_id"):
        return

    try:
        event_id = ObjectId(str(task["event_id"]))
    except Exception:
        logger.warning("Task %s has invalid event_id %s", task.get("_id"), task.get("event_id"))
        return

    await db.events.update_one(
        {"_id": event_id},
        {
            "$set": {
                "follow_up_done": True,
                "followed_up_at": sent_at,
            }
        },
    )


async def _fail_task(task_id: ObjectId, error: str) -> None:
    await db.proactive_tasks.update_one(
        {"_id": task_id},
        {"$set": {"status": "failed", "error": error}},
    )
