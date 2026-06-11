"""
memory_extractor.py
After each conversation turn, asks Gemini to extract structured information
and persists it: new memories, profile updates, events, proactive tasks, mood.
Designed to run as a background asyncio task — errors are logged, not raised.
"""
import logging
from datetime import datetime, timezone, timedelta

from bson import ObjectId

from db.mongodb import db
from backend.app.services.gemini_client import generate_json

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
Analyze this conversation exchange and extract structured information about the user.

User message: {user_message}
Assistant reply: {ai_response}
Current summary context: {summary}

Return ONLY a valid JSON object — no explanation, no markdown, no fences.

{{
  "new_memories": [
    {{
      "type": "preference | goal | personal_fact | emotional_pattern | relationship | project | event_hint",
      "text": "Concise, useful memory text for future conversations",
      "importance": 0.8,
      "confidence": 0.9,
      "tags": ["tag1", "tag2"]
    }}
  ],
  "profile_updates": {{
    "likes": [],
    "dislikes": [],
    "goals": [],
    "communication_style": "",
    "emotional_notes": "",
    "attachment_style": null,
    "neurodivergence": [],
    "social_battery": null,
    "stress_coping": null
  }},
  "events": [
    {{
      "title": "Short event title",
      "description": "What happens",
      "event_type": "exam | meeting | deadline | trip | appointment | other",
      "event_start": null,
      "event_end": null
    }}
  ],
  "proactive_tasks": [
    {{
      "type": "event_followup | check_in | motivation | reminder | daily_touch",
      "reason": "Why poke.Ai should reach out — specific and personal",
      "message": "Optional exact short Telegram message to send",
      "scheduled_in_minutes": null,
      "scheduled_in_hours": 24,
      "priority": 0.7,
      "user_requested": false
    }}
  ],
  "mood": "happy | stressed | sad | excited | neutral | unknown"
}}

Rules:
- Only extract memories genuinely useful for future conversations. Skip trivial details.
- If the user mentions an exam, meeting, deadline, trip, or important task — create an event AND a proactive_task to follow up after it.
- scheduled_in_hours should be a number (e.g. 24 means "check in tomorrow").
- If the user asks for a reminder in minutes, set scheduled_in_minutes to that exact number.
- If the user directly asks Poke.AI to remind/message them, set type to "reminder", user_requested to true, and include a short message.
- If nothing meaningful to extract, return empty arrays and "unknown" mood.
- Return valid JSON only.

Profile field rules (only fill when clearly evidenced — leave null/empty otherwise):
- attachment_style: "anxious" if they show strong need for reassurance, apologize for texting too much, fear being ignored, or ask if they're bothering you. "avoidant" if they mention needing space, pulling away under stress, or disliking frequent messages. "secure" if they're comfortable and balanced. "fearful_avoidant" if they show both extremes. null if unclear.
- neurodivergence: ONLY if the user explicitly states a diagnosis or strongly self-identifies (e.g. "I have ADHD", "my OCD is bad today", "I'm autistic"). Never infer or guess. Use their exact term. Valid values: ADHD, OCD, autism, anxiety_disorder, depression, BPD, dyslexia, sensory_processing, PTSD, bipolar.
- social_battery: "introvert" if they recharge alone and find people draining. "extrovert" if energized by socializing. "ambivert" if clearly both. null if unclear.
- stress_coping: "isolates" if they go quiet or pull away when stressed. "reaches_out" if they talk to people when stressed. "overthinks" if they spiral or ruminate. "shuts_down" if they become non-functional. null if unclear.\
"""


async def extract_and_save(
    user_id: ObjectId,
    telegram_id: int,
    user_message: str,
    ai_response: str,
    summary: str = "",
) -> None:
    """
    Extract structured data from one conversation turn and persist it.
    Safe to run as an asyncio background task — never raises.
    """
    try:
        prompt = _EXTRACTION_PROMPT.format(
            user_message=user_message,
            ai_response=ai_response,
            summary=(summary[:400] if summary else "none"),
        )

        data = await generate_json(prompt)
        if not data:
            return

        now = datetime.now(timezone.utc)

        await _save_memories(user_id, telegram_id, data.get("new_memories", []), now)
        await _save_profile(user_id, telegram_id, data.get("profile_updates", {}), now)
        await _save_events(user_id, telegram_id, data.get("events", []), now)
        await _save_tasks(user_id, telegram_id, data.get("proactive_tasks", []), now)
        await _save_mood(user_id, telegram_id, data.get("mood", "unknown"), now)

    except Exception as e:
        logger.error("extract_and_save failed for user %s: %s", telegram_id, e)


# ─── private helpers ────────────────────────────────────────────────────────


async def _save_memories(user_id, telegram_id, memories: list, now: datetime) -> None:
    for mem in memories:
        text = (mem.get("text") or "").strip()
        if not text:
            continue
        await db.memories.insert_one({
            "user_id": user_id,
            "telegram_id": telegram_id,
            "memory_type": mem.get("type", "personal_fact"),
            "content": text,
            "importance": _clamp(mem.get("importance", 0.5)),
            "confidence": _clamp(mem.get("confidence", 0.8)),
            "tags": mem.get("tags", []),
            "source_message_id": None,
            "created_at": now,
            "last_used_at": None,
        })


async def _save_profile(user_id, telegram_id, updates: dict, now: datetime) -> None:
    if not updates:
        return

    list_fields = {
        k: [i for i in updates.get(k, []) if i]
        for k in ("likes", "dislikes", "goals", "neurodivergence")
    }
    scalar_fields = {
        k: v
        for k in ("communication_style", "emotional_notes")
        if (v := (updates.get(k) or "").strip())
    }
    # Personality scalars — only set when Gemini returned a non-null value
    _ATTACHMENT_VALID = {"anxious", "avoidant", "secure", "fearful_avoidant"}
    _SOCIAL_VALID = {"introvert", "extrovert", "ambivert"}
    _STRESS_VALID = {"isolates", "reaches_out", "overthinks", "shuts_down"}
    for field, valid_set in (
        ("attachment_style", _ATTACHMENT_VALID),
        ("social_battery", _SOCIAL_VALID),
        ("stress_coping", _STRESS_VALID),
    ):
        raw = (updates.get(field) or "").strip().lower()
        if raw in valid_set:
            scalar_fields[field] = raw

    has_list = any(list_fields.values())
    has_scalar = bool(scalar_fields)

    if not has_list and not has_scalar:
        return

    update_op: dict = {
        "$setOnInsert": {"user_id": user_id, "telegram_id": telegram_id, "created_at": now},
        "$set": {"updated_at": now},
    }
    if has_scalar:
        update_op["$set"].update(scalar_fields)
    if has_list:
        update_op["$addToSet"] = {
            field: {"$each": items}
            for field, items in list_fields.items()
            if items
        }

    await db.memory_profiles.update_one(
        {"user_id": user_id},
        update_op,
        upsert=True,
    )


async def _save_events(user_id, telegram_id, events: list, now: datetime) -> None:
    for ev in events:
        title = (ev.get("title") or "").strip()
        if not title:
            continue
        start_time = _parse_dt(ev.get("event_start")) or now
        end_time = _parse_dt(ev.get("event_end"))
        await db.events.insert_one({
            "user_id": user_id,
            "telegram_id": telegram_id,
            "title": title,
            "description": ev.get("description", ""),
            "event_type": ev.get("event_type", "other"),
            "start_time": start_time,
            "end_time": end_time,
            "followup_at": end_time or start_time,
            "follow_up_needed": True,
            "follow_up_done": False,
            "created_at": now,
        })


async def _save_tasks(user_id, telegram_id, tasks: list, now: datetime) -> None:
    for task in tasks:
        reason = (task.get("reason") or "").strip()
        if not reason:
            continue

        task_type = (task.get("type") or "check_in").strip() or "check_in"
        user_requested = bool(task.get("user_requested")) or task_type == "reminder"
        scheduled_time = _scheduled_time_for_task(task, now)
        message_override = _clean_message_override(task.get("message"))
        if user_requested and not message_override:
            message_override = _reminder_message_from_reason(reason)
        cooldown_key = _cooldown_key_for_task(task_type, reason, scheduled_time)

        await db.proactive_tasks.insert_one({
            "user_id": user_id,
            "telegram_id": telegram_id,
            "task_type": task_type,
            "type": task_type,
            "reason": reason,
            "message_to_send": message_override,
            "message_override": message_override,
            "scheduled_time": scheduled_time,
            "scheduled_at": scheduled_time,
            "status": "pending",
            "priority": _clamp(task.get("priority", 0.5)),
            "cooldown_key": cooldown_key,
            "user_requested": user_requested,
            "created_by": "memory_extractor",
            "created_at": now,
            "sent_at": None,
            "error": None,
        })


async def _save_mood(user_id, telegram_id, mood: str, now: datetime) -> None:
    if not mood or mood in ("unknown", "neutral"):
        return
    await db.memories.insert_one({
        "user_id": user_id,
        "telegram_id": telegram_id,
        "memory_type": "emotional_pattern",
        "content": f"User felt {mood} on {now.strftime('%Y-%m-%d')}.",
        "importance": 0.4,
        "confidence": 0.7,
        "tags": ["mood", mood],
        "source_message_id": None,
        "created_at": now,
        "last_used_at": None,
    })


def _clamp(val, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(val)))
    except (TypeError, ValueError):
        return 0.5


def _scheduled_time_for_task(task: dict, now: datetime) -> datetime:
    minutes = task.get("scheduled_in_minutes")
    if _is_number(minutes):
        return now + timedelta(minutes=max(1.0, float(minutes)))

    hours = task.get("scheduled_in_hours")
    if _is_number(hours):
        return now + timedelta(hours=max(0.05, float(hours)))

    # Default: check in after 24 hours when no time was extracted.
    return now + timedelta(hours=24)


def _is_number(value) -> bool:
    if value is None or value == "":
        return False
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _clean_message_override(value) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text[:500]


def _reminder_message_from_reason(reason: str) -> str:
    cleaned = " ".join(reason.split())
    if not cleaned:
        return "Hey, small reminder."
    return f"Hey, reminder: {cleaned[:220]}"


def _cooldown_key_for_task(task_type: str, reason: str, scheduled_time: datetime) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in reason).strip("_")
    slug = "_".join(part for part in slug.split("_") if part)[:48] or "general"
    minute_key = scheduled_time.strftime("%Y_%m_%d_%H_%M")
    return f"{task_type}_{slug}_{minute_key}"


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
