from datetime import datetime, timedelta, timezone
import re

from app.db.mongodb import db


async def process_user_message(
    telegram_id: int,
    content: str,
    telegram_message_id: int | None = None,
):
    """
    MVP agent processor.

    Current behavior:
    1. Saves the user message.
    2. Detects simple exam messages.
    3. Creates a memory.
    4. Creates an event.
    5. Creates a proactive follow-up task.

    Later we replace the rule-based extraction with an LLM.
    """

    user = await db.users.find_one({"telegram_id": telegram_id})

    if not user:
        return {
            "success": False,
            "error": "User not found. Create the Telegram user first.",
        }

    now = datetime.now(timezone.utc)

    # 1. Save message
    message_doc = {
        "user_id": user["_id"],
        "telegram_id": telegram_id,
        "telegram_message_id": telegram_message_id,
        "role": "user",
        "content": content,
        "message_type": "text",
        "created_at": now,
    }

    message_result = await db.messages.insert_one(message_doc)
    message_id = str(message_result.inserted_id)

    created_items = {
        "message_id": message_id,
        "memory_id": None,
        "event_id": None,
        "proactive_task_id": None,
    }

    lower_content = content.lower()

    # 2. Simple rule: detect exam
    if "exam" in lower_content:
        memory_doc = {
            "user_id": user["_id"],
            "telegram_id": telegram_id,
            "memory_type": "important_event",
            "content": content,
            "importance": 0.9,
            "confidence": 0.8,
            "source_message_id": message_id,
            "created_at": now,
            "last_used_at": None,
        }

        memory_result = await db.memories.insert_one(memory_doc)
        created_items["memory_id"] = str(memory_result.inserted_id)

        # 3. Try to extract time like "2pm to 4pm"
        start_hour = 14
        end_hour = 16

        time_match = re.search(
            r"(\d{1,2})\s*(am|pm)?\s*(to|-)\s*(\d{1,2})\s*(am|pm)?",
            lower_content,
        )

        if time_match:
            start_hour = int(time_match.group(1))
            start_ampm = time_match.group(2)
            end_hour = int(time_match.group(4))
            end_ampm = time_match.group(5)

            if start_ampm == "pm" and start_hour != 12:
                start_hour += 12
            if end_ampm == "pm" and end_hour != 12:
                end_hour += 12

        # 4. Simple date rule: tomorrow
        event_date = now.date()

        if "tomorrow" in lower_content:
            event_date = (now + timedelta(days=1)).date()

        start_time = datetime(
            event_date.year,
            event_date.month,
            event_date.day,
            start_hour,
            0,
            tzinfo=timezone.utc,
        )

        end_time = datetime(
            event_date.year,
            event_date.month,
            event_date.day,
            end_hour,
            0,
            tzinfo=timezone.utc,
        )

        event_doc = {
            "user_id": user["_id"],
            "telegram_id": telegram_id,
            "title": "Exam",
            "description": content,
            "event_type": "exam",
            "start_time": start_time,
            "end_time": end_time,
            "follow_up_needed": True,
            "follow_up_done": False,
            "created_at": now,
        }

        event_result = await db.events.insert_one(event_doc)
        event_id = str(event_result.inserted_id)
        created_items["event_id"] = event_id

        # 5. Create proactive task 5 minutes after the event ends
        scheduled_time = end_time + timedelta(minutes=5)

        task_doc = {
            "user_id": user["_id"],
            "telegram_id": telegram_id,
            "event_id": event_id,
            "task_type": "follow_up",
            "message_to_send": "How did your exam go?",
            "scheduled_time": scheduled_time,
            "status": "pending",
            "sent_at": None,
            "created_at": now,
        }

        task_result = await db.proactive_tasks.insert_one(task_doc)
        created_items["proactive_task_id"] = str(task_result.inserted_id)

    return {
        "success": True,
        "input": content,
        "created": created_items,
    }