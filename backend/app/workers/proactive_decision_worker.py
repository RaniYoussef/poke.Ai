"""
proactive_decision_worker.py

Background loop that periodically asks the proactive decision engine whether
Poke.AI should create a new outbound message task for active Telegram users.
It creates tasks only; the existing proactive_worker remains responsible for
sending messages.
"""
import asyncio
import logging
from datetime import datetime, timezone

from backend.app.config import settings
from backend.app.services.proactive_decision_engine import (
    create_proactive_task_from_decision,
    evaluate_user_for_proactive_message,
)
from db.mongodb import db

logger = logging.getLogger(__name__)


async def proactive_decision_worker_loop() -> None:
    interval = settings.PROACTIVE_DECISION_WORKER_INTERVAL_SECONDS
    logger.info("Proactive decision worker started (interval: %ds)", interval)

    while True:
        try:
            result = await run_proactive_decision_batch()
            logger.info(
                "Decision batch complete: scanned=%s created=%s skipped=%s",
                result["scanned"],
                result["created"],
                result["skipped"],
            )
        except Exception as e:
            logger.error("Decision worker tick error: %s", e)

        await asyncio.sleep(interval)


async def run_proactive_decision_batch() -> dict:
    """Scan one batch of users and create proactive tasks where appropriate."""
    now = datetime.now(timezone.utc)
    batch_size = settings.PROACTIVE_DECISION_BATCH_SIZE
    scanned = 0
    created = 0
    skipped = 0

    cursor = (
        db.users
        .find({
            "telegram_id": {"$exists": True, "$ne": None},
            "proactive_enabled": {"$ne": False},
        })
        .sort([
            ("last_proactive_decision_at", 1),
            ("last_seen_at", -1),
        ])
        .limit(batch_size)
    )

    async for user in cursor:
        scanned += 1
        user_id = str(user["_id"])

        try:
            decision = await evaluate_user_for_proactive_message(user_id)

            await db.users.update_one(
                {"_id": user["_id"]},
                {"$set": {"last_proactive_decision_at": now}},
            )

            if not decision.get("should_message"):
                skipped += 1
                continue

            task_result = await create_proactive_task_from_decision(
                user_id,
                decision,
                created_by="proactive_decision_worker",
            )

            if task_result.get("created"):
                created += 1
                logger.info(
                    "Created proactive task for telegram_id=%s type=%s cooldown_key=%s",
                    user.get("telegram_id"),
                    decision.get("message_type"),
                    task_result.get("cooldown_key"),
                )
            else:
                skipped += 1

        except Exception as e:
            skipped += 1
            logger.error("Decision failed for user_id=%s: %s", user_id, e)

    return {"scanned": scanned, "created": created, "skipped": skipped}
