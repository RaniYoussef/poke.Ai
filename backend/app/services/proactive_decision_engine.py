"""
proactive_decision_engine.py

Decides whether Poke.AI should create a proactive message task for a user.
The engine is intentionally conservative: cheap rules filter users first, then
Gemini is used only when there is a real candidate reason to reach out.
"""
import json
import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bson import ObjectId

from backend.app.config import settings
from backend.app.services.gemini_client import generate_json
from db.mongodb import db

logger = logging.getLogger(__name__)

DECISION_MESSAGE_TYPES = {
    "inactivity_checkin",
    "event_followup",
    "goal_support",
    "emotional_checkin",
    "daily_touch",
    "motivation",
    "celebration",
    "reminder",
}

_EMOTIONAL_KEYWORDS = {
    "stress": [
        "stress",
        "stressed",
        "nervous",
        "anxious",
        "anxiety",
        "pressure",
        "panic",
        "overwhelmed",
        "heavy",
        "burned out",
        "burnt out",
    ],
    "sadness": ["sad", "down", "lonely", "empty", "cry", "hopeless"],
}

_DECISION_PROMPT = """\
You are Poke.AI's proactive decision brain.

You decide whether Poke.AI should message the user first.

Poke.AI should feel like a close human friend/partner, not an assistant.

Rules:
- Do not spam.
- Do not message just because the system is running.
- Only message if there is a real human reason.
- Do not be creepy.
- Do not mention database, memory, scheduling, or analysis.
- Do not say "as an AI".
- Do not sound like a notification.
- Prefer specific, natural messages based on what the user cares about.
- The final message must be short, human, caring, and natural for Telegram.
- Maximum 40 words.
- No more than 1 emoji.
- Not romantic unless the user clearly uses that tone first.

Candidate trigger:
{candidate_json}

User snapshot:
{snapshot_json}

Return JSON only:
{{
  "should_message": true/false,
  "reason": "",
  "message_type": "",
  "priority": 0.0,
  "suggested_message": "",
  "cooldown_key": "",
  "event_id": null,
  "reschedule_after_minutes": null
}}\
"""


async def evaluate_user_for_proactive_message(user_id: str) -> dict:
    """
    Evaluate one user and return a structured proactive decision.

    This does not create a task and does not send a Telegram message. Workers
    and debug routes can call this safely to inspect the decision.
    """
    user_oid = _to_object_id(user_id)
    if not user_oid:
        return _no("Invalid user_id.", reschedule_after_minutes=120)

    user = await db.users.find_one({"_id": user_oid})
    if not user:
        return _no("User not found.", reschedule_after_minutes=120)

    if not user.get("telegram_id"):
        return _no("User has no telegram_id.", reschedule_after_minutes=240)

    if user.get("proactive_enabled") is False:
        return _no("User disabled proactive messages.", reschedule_after_minutes=720)

    now = datetime.now(timezone.utc)

    quiet_state = get_quiet_hours_state(user, now)
    if quiet_state["blocked"]:
        return _no(
            "User is inside quiet hours.",
            reschedule_after_minutes=quiet_state["reschedule_after_minutes"],
        )

    cooldown_state = await get_proactive_cooldown_state(user_oid, user, now)
    if cooldown_state["blocked"]:
        return _no(
            cooldown_state["reason"],
            reschedule_after_minutes=cooldown_state["reschedule_after_minutes"],
        )

    snapshot = await _load_user_snapshot(user_oid, user, now)
    candidates = _detect_candidates(snapshot, now)

    if not candidates:
        return _no("No strong proactive reason found.", reschedule_after_minutes=120)

    candidates.sort(key=lambda item: item["priority"], reverse=True)

    selected_candidate = None
    for candidate in candidates:
        duplicate = await has_recent_duplicate_task(
            user_oid,
            candidate.get("cooldown_key"),
            candidate.get("message_type"),
            user,
            now,
        )
        if not duplicate:
            selected_candidate = candidate
            break

    if not selected_candidate:
        return _no("A similar proactive task already exists recently.", reschedule_after_minutes=240)

    if not settings.GEMINI_API_KEY:
        return _decision_from_candidate(selected_candidate)

    decision = await _ask_gemini_for_decision(snapshot, selected_candidate, now)

    if not decision.get("should_message"):
        return decision

    duplicate = await has_recent_duplicate_task(
        user_oid,
        decision.get("cooldown_key"),
        decision.get("message_type"),
        user,
        now,
    )
    if duplicate:
        return _no("A similar proactive task already exists recently.", reschedule_after_minutes=240)

    return decision


async def create_proactive_task_from_decision(
    user_id: str,
    decision: dict,
    created_by: str = "proactive_decision_worker",
) -> dict:
    """
    Convert a positive decision into a pending proactive task.

    The task includes both existing field names (`task_type`, `scheduled_time`)
    and newer decision-worker fields (`type`, `scheduled_at`) so old and new
    worker code can read it.
    """
    if not decision.get("should_message"):
        return {"created": False, "reason": "Decision said not to message."}

    user_oid = _to_object_id(user_id)
    if not user_oid:
        return {"created": False, "reason": "Invalid user_id."}

    user = await db.users.find_one({"_id": user_oid})
    if not user or not user.get("telegram_id"):
        return {"created": False, "reason": "User not found or missing telegram_id."}

    now = datetime.now(timezone.utc)
    message_type = _normalize_message_type(decision.get("message_type"))
    cooldown_key = (decision.get("cooldown_key") or "").strip() or _make_cooldown_key(
        message_type,
        decision.get("reason") or message_type,
    )

    duplicate = await has_recent_duplicate_task(
        user_oid,
        cooldown_key,
        message_type,
        user,
        now,
    )
    if duplicate:
        return {
            "created": False,
            "reason": "Duplicate proactive task already exists recently.",
            "cooldown_key": cooldown_key,
        }

    task_doc = {
        "user_id": user_oid,
        "telegram_id": user["telegram_id"],
        "event_id": decision.get("event_id"),
        "task_type": message_type,
        "type": message_type,
        "reason": (decision.get("reason") or "").strip(),
        "message_to_send": (decision.get("suggested_message") or "").strip() or None,
        "message_override": (decision.get("suggested_message") or "").strip() or None,
        "scheduled_time": now,
        "scheduled_at": now,
        "status": "pending",
        "priority": _clamp(decision.get("priority", 0.5)),
        "cooldown_key": cooldown_key,
        "created_by": created_by,
        "created_at": now,
        "sent_at": None,
        "error": None,
    }

    result = await db.proactive_tasks.insert_one(task_doc)
    return {
        "created": True,
        "task_id": str(result.inserted_id),
        "cooldown_key": cooldown_key,
        "task": _serialize_task(task_doc, result.inserted_id),
    }


async def has_recent_duplicate_task(
    user_id: ObjectId,
    cooldown_key: str | None,
    message_type: str | None,
    user: dict,
    now: datetime | None = None,
) -> bool:
    """Return True if a similar active task exists or was sent recently."""
    if not cooldown_key:
        return False

    now = now or datetime.now(timezone.utc)
    window_hours = _duplicate_window_hours(message_type, user)
    cutoff = now - timedelta(hours=window_hours)

    active_duplicate = await db.proactive_tasks.find_one({
        "user_id": user_id,
        "cooldown_key": cooldown_key,
        "status": {"$in": ["pending", "sending"]},
    })
    if active_duplicate:
        return True

    recent_sent = await db.proactive_tasks.find_one({
        "user_id": user_id,
        "cooldown_key": cooldown_key,
        "status": "sent",
        "$or": [
            {"sent_at": {"$gte": cutoff}},
            {"created_at": {"$gte": cutoff}},
        ],
    })

    return recent_sent is not None


def get_quiet_hours_state(user: dict, now: datetime | None = None) -> dict:
    """Check user/default quiet hours and return a block/reschedule state."""
    now = now or datetime.now(timezone.utc)
    quiet_hours = _quiet_hours_for_user(user)
    if not quiet_hours:
        return {"blocked": False, "reschedule_after_minutes": None, "resume_at": None}

    start = _parse_clock_time(quiet_hours.get("start"))
    end = _parse_clock_time(quiet_hours.get("end"))
    if not start or not end:
        return {"blocked": False, "reschedule_after_minutes": None, "resume_at": None}

    local_now = _to_user_timezone(now, user)
    local_time = local_now.time().replace(second=0, microsecond=0)

    if start < end:
        blocked = start <= local_time < end
        resume_local = local_now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    else:
        blocked = local_time >= start or local_time < end
        if local_time >= start:
            resume_local = (local_now + timedelta(days=1)).replace(
                hour=end.hour,
                minute=end.minute,
                second=0,
                microsecond=0,
            )
        else:
            resume_local = local_now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)

    if not blocked:
        return {"blocked": False, "reschedule_after_minutes": None, "resume_at": None}

    resume_at = resume_local.astimezone(timezone.utc)
    minutes = max(1, int((resume_at - now).total_seconds() // 60))
    return {"blocked": True, "reschedule_after_minutes": minutes, "resume_at": resume_at}


def _quiet_hours_for_user(user: dict) -> dict | None:
    quiet_hours = user.get("quiet_hours")
    if isinstance(quiet_hours, dict):
        if quiet_hours.get("enabled") is False:
            return None
        return {
            "start": quiet_hours.get("start")
            or quiet_hours.get("start_time")
            or settings.DEFAULT_QUIET_HOURS_START,
            "end": quiet_hours.get("end")
            or quiet_hours.get("end_time")
            or settings.DEFAULT_QUIET_HOURS_END,
        }

    return {
        "start": user.get("quiet_hours_start") or settings.DEFAULT_QUIET_HOURS_START,
        "end": user.get("quiet_hours_end") or settings.DEFAULT_QUIET_HOURS_END,
    }


async def get_proactive_cooldown_state(
    user_id: ObjectId,
    user: dict,
    now: datetime | None = None,
) -> dict:
    """Apply per-user proactivity cooldown rules."""
    now = now or datetime.now(timezone.utc)
    level = _normalize_proactivity_level(user.get("proactivity_level"))
    cooldown_hours = _cooldown_hours(level)
    last_proactive_at = await _last_proactive_message_at(user_id, user)

    if last_proactive_at:
        elapsed = now - last_proactive_at
        if elapsed < timedelta(hours=cooldown_hours):
            resume_at = last_proactive_at + timedelta(hours=cooldown_hours)
            minutes = max(1, int((resume_at - now).total_seconds() // 60))
            return {
                "blocked": True,
                "reason": f"Proactivity cooldown is active for {level} level.",
                "reschedule_after_minutes": minutes,
                "resume_at": resume_at,
            }

    if level == "high":
        local_now = _to_user_timezone(now, user)
        day_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_local = day_start_local + timedelta(days=1)
        day_start_utc = day_start_local.astimezone(timezone.utc)
        day_end_utc = day_end_local.astimezone(timezone.utc)

        sent_today = await db.messages.count_documents({
            "user_id": user_id,
            "role": "assistant",
            "message_type": "proactive",
            "created_at": {"$gte": day_start_utc, "$lt": day_end_utc},
        })

        if sent_today >= 2:
            minutes = max(1, int((day_end_utc - now).total_seconds() // 60))
            return {
                "blocked": True,
                "reason": "High proactivity daily limit already reached.",
                "reschedule_after_minutes": minutes,
                "resume_at": day_end_utc,
            }

    return {"blocked": False, "reason": None, "reschedule_after_minutes": None, "resume_at": None}


async def _load_user_snapshot(user_id: ObjectId, user: dict, now: datetime) -> dict:
    profile = await db.memory_profiles.find_one({"user_id": user_id}) or {}

    important_memories = []
    memory_cursor = (
        db.memories
        .find({"user_id": user_id, "memory_type": {"$ne": "conversation_summary"}})
        .sort("importance", -1)
        .limit(10)
    )
    async for memory in memory_cursor:
        important_memories.append(_clean_memory(memory))

    recent_messages = []
    message_cursor = (
        db.messages
        .find({"user_id": user_id})
        .sort("created_at", -1)
        .limit(12)
    )
    async for message in message_cursor:
        recent_messages.append(_clean_message(message))
    recent_messages.reverse()

    last_user_message = await db.messages.find_one(
        {"user_id": user_id, "role": "user"},
        sort=[("created_at", -1)],
    )

    open_events = []
    event_cursor = (
        db.events
        .find({
            "user_id": user_id,
            "follow_up_done": {"$ne": True},
            "followed_up": {"$ne": True},
        })
        .sort("start_time", 1)
        .limit(10)
    )
    async for event in event_cursor:
        open_events.append(_clean_event(event))

    proactive_tasks = []
    task_cursor = (
        db.proactive_tasks
        .find({"user_id": user_id})
        .sort("created_at", -1)
        .limit(12)
    )
    async for task in task_cursor:
        proactive_tasks.append(_clean_task(task))

    emotional_memories = []
    emotional_cursor = (
        db.memories
        .find({"user_id": user_id, "memory_type": "emotional_pattern"})
        .sort("created_at", -1)
        .limit(5)
    )
    async for memory in emotional_cursor:
        emotional_memories.append(_clean_memory(memory))

    last_user_message_at = _as_utc(user.get("last_user_message_at"))
    if not last_user_message_at and last_user_message:
        last_user_message_at = _as_utc(last_user_message.get("created_at"))
    last_seen_at = _as_utc(user.get("last_seen_at")) or last_user_message_at

    inactivity_hours = None
    if last_user_message_at:
        inactivity_hours = max(0.0, (now - last_user_message_at).total_seconds() / 3600)

    return {
        "now": now,
        "user": {
            "user_id": str(user_id),
            "telegram_id": user.get("telegram_id"),
            "first_name": user.get("first_name") or "",
            "username": user.get("username") or "",
            "timezone": user.get("timezone") or settings.DEFAULT_TIMEZONE,
            "proactivity_level": _normalize_proactivity_level(user.get("proactivity_level")),
            "proactive_enabled": user.get("proactive_enabled", True),
        },
        "profile": _clean_profile(profile),
        "important_memories": important_memories,
        "recent_messages": recent_messages,
        "open_events": open_events,
        "proactive_tasks": proactive_tasks,
        "emotional_memories": emotional_memories,
        "last_user_message_at": last_user_message_at,
        "last_seen_at": last_seen_at,
        "inactivity_hours": inactivity_hours,
    }


def _detect_candidates(snapshot: dict, now: datetime) -> list[dict]:
    candidates: list[dict] = []
    user = snapshot["user"]
    name = user.get("first_name") or user.get("username") or "there"
    level = user.get("proactivity_level", "medium")
    inactivity_hours = snapshot.get("inactivity_hours")

    for event in snapshot.get("open_events", []):
        candidate = _event_candidate(event, now)
        if candidate:
            candidates.append(candidate)

    emotional_state = _detect_emotional_state(snapshot, now)
    if emotional_state and (inactivity_hours is None or inactivity_hours >= 6):
        candidates.append({
            "should_message": True,
            "reason": f"Recent conversation or memory suggests {emotional_state}, with enough space for a caring check-in.",
            "message_type": "emotional_checkin",
            "priority": 0.76,
            "suggested_message": "Hey. Yesterday sounded heavy. Just checking on you - you okay?",
            "cooldown_key": f"emotional_checkin_{_slug(emotional_state)}_recent",
            "event_id": None,
        })

    goal_text = _extract_goal_text(snapshot)
    if goal_text and (inactivity_hours is not None and inactivity_hours >= settings.PROACTIVE_INACTIVITY_HOURS):
        goal_label = _short_goal_label(goal_text)
        candidates.append({
            "should_message": True,
            "reason": f"User has been inactive and has an active goal/project: {goal_label}.",
            "message_type": "goal_support",
            "priority": 0.68,
            "suggested_message": f"Small poke for {goal_label} today: one clean step. Not everything. Just one thing that works.",
            "cooldown_key": f"goal_support_{_slug(goal_label)}",
            "event_id": None,
        })

    if inactivity_hours is not None and inactivity_hours >= settings.PROACTIVE_INACTIVITY_HOURS:
        candidates.append({
            "should_message": True,
            "reason": f"User has been inactive for about {int(inactivity_hours)} hours.",
            "message_type": "inactivity_checkin",
            "priority": 0.58,
            "suggested_message": f"Hey {name}, haven't heard from you in a bit. You okay today?",
            "cooldown_key": "inactivity_checkin_user_48h",
            "event_id": None,
        })

    if level == "high" and (inactivity_hours is None or inactivity_hours >= 20):
        local_date = _to_user_timezone(now, {"timezone": user.get("timezone")}).strftime("%Y_%m_%d")
        candidates.append({
            "should_message": True,
            "reason": "High proactivity user is eligible for a light daily touch.",
            "message_type": "daily_touch",
            "priority": 0.35,
            "suggested_message": "Morning. What's the one thing you want to win today?",
            "cooldown_key": f"daily_touch_{local_date}",
            "event_id": None,
        })

    return candidates


def _event_candidate(event: dict, now: datetime) -> dict | None:
    if event.get("follow_up_needed") is False:
        return None
    if event.get("follow_up_done") is True or event.get("followed_up") is True:
        return None

    followup_at = (
        _as_utc(event.get("followup_at"))
        or _as_utc(event.get("follow_up_at"))
        or _as_utc(event.get("end_time"))
        or _as_utc(event.get("start_time"))
    )
    if not followup_at or followup_at > now:
        return None

    age_hours = (now - followup_at).total_seconds() / 3600
    if age_hours > 24 * 7:
        return None

    title = event.get("title") or "that thing"
    event_type = (event.get("event_type") or "").lower()
    text = f"{title} {event_type}".lower()
    date_key = followup_at.strftime("%Y_%m_%d")
    label = _slug(event_type or title)

    if "exam" in text:
        message = "So... how did the exam go? Was it better than your stress was saying?"
        reason = f"User had an exam that is ready for follow-up: {title}."
        priority = 0.92
    elif any(word in text for word in ["deadline", "meeting", "appointment", "trip"]):
        message = f"How did {title} go? Been thinking about it a bit."
        reason = f"User had an important event that is ready for follow-up: {title}."
        priority = 0.86
    else:
        message = f"How did {title} go?"
        reason = f"An open event is due for follow-up: {title}."
        priority = 0.78

    return {
        "should_message": True,
        "reason": reason,
        "message_type": "event_followup",
        "priority": priority,
        "suggested_message": message,
        "cooldown_key": f"event_followup_{label}_{date_key}",
        "event_id": event.get("id"),
    }


async def _ask_gemini_for_decision(snapshot: dict, candidate: dict, now: datetime) -> dict:
    prompt = _DECISION_PROMPT.format(
        candidate_json=json.dumps(_json_safe(candidate), ensure_ascii=True, indent=2),
        snapshot_json=json.dumps(_json_safe(_snapshot_for_prompt(snapshot, now)), ensure_ascii=True, indent=2),
    )

    data = await generate_json(prompt)
    if not data:
        return _decision_from_candidate(candidate)

    return _normalize_decision(data, candidate)


def _decision_from_candidate(candidate: dict) -> dict:
    return {
        "should_message": True,
        "reason": candidate.get("reason") or "Rule-based proactive trigger matched.",
        "message_type": _normalize_message_type(candidate.get("message_type")),
        "priority": _clamp(candidate.get("priority", 0.5)),
        "suggested_message": _clean_suggested_message(candidate.get("suggested_message") or ""),
        "cooldown_key": candidate.get("cooldown_key") or _make_cooldown_key(
            candidate.get("message_type"),
            candidate.get("reason") or "",
        ),
        "reschedule_after_minutes": None,
        "event_id": candidate.get("event_id"),
    }


def _normalize_decision(data: dict, fallback: dict) -> dict:
    should_message = bool(data.get("should_message"))
    if not should_message:
        return _no(
            (data.get("reason") or "Gemini decided not to message.").strip(),
            message_type=None,
            reschedule_after_minutes=data.get("reschedule_after_minutes") or 120,
        )

    message_type = _normalize_message_type(data.get("message_type") or fallback.get("message_type"))
    suggested = _clean_suggested_message(data.get("suggested_message") or "")
    fallback_suggested = _clean_suggested_message(fallback.get("suggested_message") or "")

    if not suggested or len(suggested.split()) > 45:
        suggested = fallback_suggested

    if not suggested:
        return _no("No usable suggested message was produced.", reschedule_after_minutes=120)

    cooldown_key = (data.get("cooldown_key") or fallback.get("cooldown_key") or "").strip()
    if not cooldown_key:
        cooldown_key = _make_cooldown_key(message_type, data.get("reason") or fallback.get("reason") or "")

    return {
        "should_message": True,
        "reason": (data.get("reason") or fallback.get("reason") or "").strip(),
        "message_type": message_type,
        "priority": _clamp(data.get("priority", fallback.get("priority", 0.5))),
        "suggested_message": suggested,
        "cooldown_key": cooldown_key,
        "reschedule_after_minutes": data.get("reschedule_after_minutes"),
        "event_id": fallback.get("event_id") or data.get("event_id"),
    }


def _no(
    reason: str,
    message_type: str | None = None,
    reschedule_after_minutes: int | None = None,
) -> dict:
    return {
        "should_message": False,
        "reason": reason,
        "message_type": message_type,
        "priority": 0,
        "suggested_message": None,
        "cooldown_key": None,
        "event_id": None,
        "reschedule_after_minutes": reschedule_after_minutes,
    }


def _snapshot_for_prompt(snapshot: dict, now: datetime) -> dict:
    return {
        "now_utc": now.isoformat(),
        "user": snapshot.get("user"),
        "inactivity_hours": snapshot.get("inactivity_hours"),
        "profile": snapshot.get("profile"),
        "important_memories": snapshot.get("important_memories", [])[:8],
        "emotional_memories": snapshot.get("emotional_memories", [])[:4],
        "recent_messages": snapshot.get("recent_messages", [])[-8:],
        "open_events": snapshot.get("open_events", [])[:6],
        "recent_proactive_tasks": snapshot.get("proactive_tasks", [])[:8],
    }


def _detect_emotional_state(snapshot: dict, now: datetime) -> str | None:
    recent_texts: list[str] = []
    cutoff = now - timedelta(hours=72)

    for message in snapshot.get("recent_messages", []):
        created_at = _as_utc(message.get("created_at"))
        if message.get("role") == "user" and (not created_at or created_at >= cutoff):
            recent_texts.append(message.get("content", ""))

    for memory in snapshot.get("emotional_memories", []):
        created_at = _as_utc(memory.get("created_at"))
        if not created_at or created_at >= cutoff:
            recent_texts.append(memory.get("content", ""))

    combined = " ".join(recent_texts).lower()
    if not combined:
        return None

    for state, keywords in _EMOTIONAL_KEYWORDS.items():
        if any(keyword in combined for keyword in keywords):
            return state

    return None


def _extract_goal_text(snapshot: dict) -> str | None:
    profile = snapshot.get("profile", {})
    for goal in profile.get("goals", []):
        if goal:
            return str(goal)

    for memory in snapshot.get("important_memories", []):
        memory_type = (memory.get("memory_type") or memory.get("type") or "").lower()
        tags = " ".join(memory.get("tags", [])).lower()
        if memory_type in {"goal", "project"} or "goal" in tags or "project" in tags:
            return memory.get("content")

    return None


def _short_goal_label(goal_text: str) -> str:
    cleaned = re.sub(r"\s+", " ", goal_text).strip()
    if not cleaned:
        return "your goal"
    if "poke" in cleaned.lower():
        return "Poke.AI"
    words = cleaned.split()
    return " ".join(words[:5])


def _duplicate_window_hours(message_type: str | None, user: dict) -> int:
    cooldown_hours = _cooldown_hours(_normalize_proactivity_level(user.get("proactivity_level")))
    by_type = {
        "event_followup": 24 * 30,
        "celebration": 24 * 30,
        "inactivity_checkin": 72,
        "emotional_checkin": 72,
        "goal_support": 72,
        "daily_touch": 24,
        "motivation": 48,
        "reminder": 24,
    }
    return max(cooldown_hours, by_type.get(message_type or "", cooldown_hours))


async def _last_proactive_message_at(user_id: ObjectId, user: dict) -> datetime | None:
    dates = []
    user_last = _as_utc(user.get("last_proactive_message_at"))
    if user_last:
        dates.append(user_last)

    last_message = await db.messages.find_one(
        {"user_id": user_id, "role": "assistant", "message_type": "proactive"},
        sort=[("created_at", -1)],
    )
    if last_message:
        message_last = _as_utc(last_message.get("created_at"))
        if message_last:
            dates.append(message_last)

    return max(dates) if dates else None


def _normalize_proactivity_level(level: Any) -> str:
    level = (str(level or settings.PROACTIVE_DEFAULT_LEVEL).lower()).strip()
    if level not in {"low", "medium", "high"}:
        return settings.PROACTIVE_DEFAULT_LEVEL
    return level


def _cooldown_hours(level: str) -> int:
    level = _normalize_proactivity_level(level)
    if level == "low":
        return settings.PROACTIVE_LOW_COOLDOWN_HOURS
    if level == "high":
        return settings.PROACTIVE_HIGH_COOLDOWN_HOURS
    return settings.PROACTIVE_MEDIUM_COOLDOWN_HOURS


def _to_object_id(value: Any) -> ObjectId | None:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _as_utc(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _to_user_timezone(value: datetime, user: dict) -> datetime:
    tz_name = user.get("timezone") or settings.DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return value.astimezone(tz)


def _parse_clock_time(value: Any) -> time | None:
    if not value:
        return None
    try:
        hour, minute = str(value).split(":", 1)
        return time(hour=int(hour), minute=int(minute[:2]))
    except Exception:
        return None


def _clean_profile(profile: dict) -> dict:
    if not profile:
        return {}
    return {
        "likes": profile.get("likes", []),
        "dislikes": profile.get("dislikes", []),
        "goals": profile.get("goals", []),
        "communication_style": profile.get("communication_style", ""),
        "emotional_notes": profile.get("emotional_notes", ""),
        "attachment_style": profile.get("attachment_style"),
        "neurodivergence": profile.get("neurodivergence", []),
        "social_battery": profile.get("social_battery"),
        "stress_coping": profile.get("stress_coping"),
    }


def _clean_memory(memory: dict) -> dict:
    return {
        "id": str(memory.get("_id")),
        "memory_type": memory.get("memory_type", "fact"),
        "content": memory.get("content", ""),
        "importance": memory.get("importance", 0.5),
        "confidence": memory.get("confidence", 0.8),
        "tags": memory.get("tags", []),
        "created_at": _as_utc(memory.get("created_at")),
    }


def _clean_message(message: dict) -> dict:
    return {
        "id": str(message.get("_id")),
        "role": message.get("role", ""),
        "content": message.get("content", ""),
        "message_type": message.get("message_type", "text"),
        "created_at": _as_utc(message.get("created_at")),
    }


def _clean_event(event: dict) -> dict:
    return {
        "id": str(event.get("_id")),
        "title": event.get("title", ""),
        "description": event.get("description", ""),
        "event_type": event.get("event_type", "other"),
        "start_time": _as_utc(event.get("start_time")),
        "end_time": _as_utc(event.get("end_time")),
        "followup_at": _as_utc(event.get("followup_at")),
        "follow_up_at": _as_utc(event.get("follow_up_at")),
        "follow_up_needed": event.get("follow_up_needed", True),
        "follow_up_done": event.get("follow_up_done", False),
        "followed_up": event.get("followed_up", False),
    }


def _clean_task(task: dict) -> dict:
    return {
        "id": str(task.get("_id")),
        "task_type": task.get("task_type") or task.get("type"),
        "status": task.get("status"),
        "reason": task.get("reason") or task.get("message_to_send"),
        "cooldown_key": task.get("cooldown_key"),
        "scheduled_time": _as_utc(task.get("scheduled_time") or task.get("scheduled_at")),
        "created_at": _as_utc(task.get("created_at")),
        "sent_at": _as_utc(task.get("sent_at")),
    }


def _serialize_task(task: dict, inserted_id: ObjectId) -> dict:
    return {
        "id": str(inserted_id),
        "telegram_id": task.get("telegram_id"),
        "task_type": task.get("task_type"),
        "reason": task.get("reason"),
        "message_override": task.get("message_override"),
        "scheduled_time": _json_safe(task.get("scheduled_time")),
        "status": task.get("status"),
        "priority": task.get("priority"),
        "cooldown_key": task.get("cooldown_key"),
        "created_by": task.get("created_by"),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _clean_suggested_message(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "")).strip()
    return value[:500]


def _normalize_message_type(value: Any) -> str:
    value = (str(value or "daily_touch").strip() or "daily_touch")
    return value if value in DECISION_MESSAGE_TYPES else "daily_touch"


def _make_cooldown_key(message_type: str | None, seed: str) -> str:
    return f"{_normalize_message_type(message_type)}_{_slug(seed)[:60]}"


def _slug(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return slug or "general"


def _clamp(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return 0.5
