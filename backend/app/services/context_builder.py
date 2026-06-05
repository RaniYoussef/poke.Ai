"""
context_builder.py
Builds a structured context object for a user from MongoDB and formats it
into a clean prompt string for Gemini — no raw DB dumps.
"""
import logging
from datetime import datetime, timezone

from bson import ObjectId

from db.mongodb import db

logger = logging.getLogger(__name__)


async def build_user_context(
    user_id: ObjectId,
    incoming_message: str | None = None,
    mode: str = "reactive",
) -> dict:
    """
    Reads DB collections for one user and returns a structured context dict.

    mode:
      "reactive"  — responding to a user message
      "proactive" — bot is reaching out first (no incoming_message)
    """
    user = await db.users.find_one({"_id": user_id})
    if not user:
        logger.warning("build_user_context: user %s not found", user_id)
        return {}

    # Rolling conversation summary (most recent wins)
    summary_doc = await db.memories.find_one(
        {"user_id": user_id, "memory_type": "conversation_summary"},
        sort=[("created_at", -1)],
    )
    summary = summary_doc["content"] if summary_doc else ""

    # Memory profile: likes, dislikes, goals, emotional notes
    profile_doc = await db.memory_profiles.find_one({"user_id": user_id}) or {}

    # Top 8 important memories (excluding the rolling summary)
    important_memories = []
    mem_cursor = (
        db.memories
        .find({"user_id": user_id, "memory_type": {"$ne": "conversation_summary"}})
        .sort("importance", -1)
        .limit(8)
    )
    async for m in mem_cursor:
        important_memories.append({
            "type": m.get("memory_type", "fact"),
            "text": m["content"],
            "importance": m.get("importance", 0.5),
            "tags": m.get("tags", []),
        })

    # Last 10 messages (oldest first for natural conversation order)
    recent_messages = []
    msg_cursor = (
        db.messages
        .find({"user_id": user_id})
        .sort("created_at", -1)
        .limit(10)
    )
    async for msg in msg_cursor:
        recent_messages.append({"role": msg["role"], "content": msg["content"]})
    recent_messages.reverse()

    # Open events (follow-up not yet done)
    open_events = []
    ev_cursor = (
        db.events
        .find({"user_id": user_id, "follow_up_done": False})
        .sort("start_time", 1)
        .limit(5)
    )
    async for ev in ev_cursor:
        open_events.append({
            "title": ev["title"],
            "description": ev.get("description", ""),
            "event_type": ev.get("event_type", ""),
            "start_time": ev.get("start_time"),
            "end_time": ev.get("end_time"),
        })

    # Pending proactive tasks for this user (up to 3)
    pending_tasks = []
    task_cursor = (
        db.proactive_tasks
        .find({"user_id": user_id, "status": "pending"})
        .sort("scheduled_time", 1)
        .limit(3)
    )
    async for t in task_cursor:
        pending_tasks.append({
            "type": t.get("task_type", ""),
            "reason": t.get("reason") or t.get("message_to_send", ""),
            "scheduled_at": t.get("scheduled_time"),
        })

    return {
        "mode": mode,
        "user": {
            "user_id": str(user_id),
            "telegram_id": user.get("telegram_id"),
            "first_name": user.get("first_name", ""),
            "username": user.get("username", ""),
            "language": user.get("language", "en"),
            "timezone": user.get("timezone", "UTC"),
            "preferred_tone": user.get("preferred_tone", "friendly"),
            "proactivity_level": user.get("proactivity_level", "medium"),
        },
        "summary": summary,
        "profile": profile_doc,
        "important_memories": important_memories,
        "recent_messages": recent_messages,
        "open_events": open_events,
        "pending_tasks": pending_tasks,
        "incoming_message": incoming_message,
    }


def format_context_for_prompt(context: dict) -> str:
    """
    Converts the context dict into a concise, structured prompt string.
    Only includes sections that have actual content.
    """
    parts = []
    mode = context.get("mode", "reactive")
    user = context.get("user", {})
    name = user.get("first_name") or user.get("username") or "the user"

    parts.append(f"[Mode: {mode}]")
    parts.append(f"[User: {name}]")

    summary = context.get("summary", "").strip()
    if summary:
        parts.append(f"\n[Long-term summary]\n{summary}")

    profile = context.get("profile", {})
    if profile:
        if profile.get("likes"):
            parts.append(f"[Likes] {', '.join(profile['likes'])}")
        if profile.get("dislikes"):
            parts.append(f"[Dislikes] {', '.join(profile['dislikes'])}")
        if profile.get("goals"):
            parts.append(f"[Goals] {', '.join(profile['goals'])}")
        if profile.get("emotional_notes"):
            parts.append(f"[Emotional notes] {profile['emotional_notes']}")
        if profile.get("communication_style"):
            parts.append(f"[Communication style] {profile['communication_style']}")

    memories = context.get("important_memories", [])
    if memories:
        parts.append("\n[Important memories]")
        for m in memories:
            tag_str = f" [{', '.join(m['tags'])}]" if m.get("tags") else ""
            parts.append(f"- ({m['type']}){tag_str}: {m['text']}")

    events = context.get("open_events", [])
    if events:
        parts.append("\n[Events to follow up on]")
        for ev in events:
            start = ev.get("start_time")
            time_str = ""
            if start:
                if hasattr(start, "strftime"):
                    time_str = f" at {start.strftime('%Y-%m-%d %H:%M UTC')}"
                else:
                    time_str = f" at {start}"
            desc = f": {ev['description']}" if ev.get("description") else ""
            parts.append(f"- {ev['title']}{time_str}{desc}")

    recent = context.get("recent_messages", [])
    if recent:
        parts.append("\n[Recent conversation]")
        for msg in recent:
            label = name if msg["role"] == "user" else "poke.Ai"
            parts.append(f"{label}: {msg['content']}")

    incoming = context.get("incoming_message", "")
    if incoming:
        parts.append(f"\n{name}: {incoming}")

    return "\n".join(parts)
