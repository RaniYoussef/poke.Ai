"""
memory_extractor.py
After each conversation turn, asks Gemini to extract structured information
and persists it: new memories, profile updates, events, proactive tasks, mood.
Designed to run as a background asyncio task — errors are logged, not raised.
"""
import json
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

Memories already saved (do NOT re-extract these — only update if the user clearly corrects or changes one):
{existing_memories}

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
  "memory_updates": [
    {{
      "id": "exact_id_from_existing_memories_list",
      "new_text": "Corrected or updated memory text",
      "new_importance": 0.8
    }}
  ],
  "profile_updates": {{
    "likes": [],
    "dislikes": [],
    "goals": [],
    "hobbies": [],
    "personality_traits": [],
    "communication_style": "",
    "emotional_notes": "",
    "job": "",
    "company": "",
    "city": "",
    "study_field": "",
    "family_situation": ""
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
      "scheduled_in_hours": 24,
      "priority": 0.7
    }}
  ],
  "mood": "happy | stressed | sad | excited | neutral | unknown"
}}

Rules:
- Only extract memories genuinely useful for future conversations. Skip trivial details.
- Do NOT re-add something already in the existing_memories list — skip it.
- memory_updates: only use when the user explicitly corrects a fact (e.g. "actually I changed jobs"). Max 2 updates. Use the exact id string from the list.
- If the user mentions an exam, meeting, deadline, trip, or important task — create an event AND a proactive_task to follow up after it.
- scheduled_in_hours should be a number (e.g. 24 means "check in tomorrow").
- If nothing meaningful to extract, return empty arrays and "unknown" mood.
- Return valid JSON only.\
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
        # Fetch top existing memories so Gemini knows what's already saved
        existing_mems = []
        cursor = (
            db.memories
            .find({"user_id": user_id, "memory_type": {"$ne": "conversation_summary"}})
            .sort("importance", -1)
            .limit(10)
        )
        async for m in cursor:
            existing_mems.append({"id": str(m["_id"]), "type": m.get("memory_type"), "text": m["content"]})

        prompt = _EXTRACTION_PROMPT.format(
            user_message=user_message,
            ai_response=ai_response,
            summary=(summary[:400] if summary else "none"),
            existing_memories=json.dumps(existing_mems, ensure_ascii=False) if existing_mems else "None yet.",
        )

        data = await generate_json(prompt)
        if not data:
            return

        now = datetime.now(timezone.utc)

        await _save_memories(user_id, telegram_id, data.get("new_memories", []), now)
        await _apply_memory_updates(user_id, data.get("memory_updates", []), existing_mems)
        await _save_profile(user_id, telegram_id, data.get("profile_updates", {}), now)
        await _save_events(user_id, telegram_id, data.get("events", []), now)
        await _save_tasks(user_id, telegram_id, data.get("proactive_tasks", []), now)
        await _save_mood(user_id, telegram_id, data.get("mood", "unknown"), now)

    except Exception as e:
        logger.error("extract_and_save failed for user %s: %s", telegram_id, e)


# ─── private helpers ────────────────────────────────────────────────────────


async def _apply_memory_updates(user_id: ObjectId, updates: list, existing_mems: list) -> None:
    if not updates:
        return
    valid_ids = {m["id"] for m in existing_mems}
    applied = 0
    for upd in updates[:2]:  # hard cap: max 2 updates per turn
        mem_id_str = (upd.get("id") or "").strip()
        new_text = (upd.get("new_text") or "").strip()
        if not mem_id_str or not new_text or mem_id_str not in valid_ids:
            continue
        try:
            oid = ObjectId(mem_id_str)
        except Exception:
            continue
        await db.memories.update_one(
            {"_id": oid, "user_id": user_id},  # user_id guard prevents cross-user edits
            {"$set": {
                "content": new_text,
                "importance": _clamp(upd.get("new_importance", 0.7)),
                "last_used_at": datetime.now(timezone.utc),
            }},
        )
        applied += 1
    if applied:
        logger.info("Applied %d memory update(s) for user %s", applied, user_id)


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
        for k in ("likes", "dislikes", "goals", "hobbies", "personality_traits")
    }
    scalar_fields = {
        k: v
        for k in ("communication_style", "emotional_notes", "job", "company", "city", "study_field", "family_situation")
        if (v := (updates.get(k) or "").strip())
    }

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
        await db.events.insert_one({
            "user_id": user_id,
            "telegram_id": telegram_id,
            "title": title,
            "description": ev.get("description", ""),
            "event_type": ev.get("event_type", "other"),
            "start_time": _parse_dt(ev.get("event_start")) or now,
            "end_time": _parse_dt(ev.get("event_end")),
            "follow_up_needed": True,
            "follow_up_done": False,
            "created_at": now,
        })


async def _save_tasks(user_id, telegram_id, tasks: list, now: datetime) -> None:
    for task in tasks:
        reason = (task.get("reason") or "").strip()
        if not reason:
            continue
        hours = task.get("scheduled_in_hours")
        if hours and str(hours).replace(".", "").isdigit():
            scheduled_time = now + timedelta(hours=float(hours))
        else:
            # Default: check in after 24 hours
            scheduled_time = now + timedelta(hours=24)

        await db.proactive_tasks.insert_one({
            "user_id": user_id,
            "telegram_id": telegram_id,
            "task_type": task.get("type", "check_in"),
            "reason": reason,
            "message_to_send": None,  # generated by worker at send time
            "scheduled_time": scheduled_time,
            "status": "pending",
            "priority": _clamp(task.get("priority", 0.5)),
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


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
