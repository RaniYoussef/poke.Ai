from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.mongodb import db

router = APIRouter(prefix="/events", tags=["events"])


class EventCreate(BaseModel):
    telegram_id: int
    title: str
    description: Optional[str] = None
    event_type: str
    start_time: datetime
    end_time: Optional[datetime] = None
    follow_up_needed: bool = True


@router.post("")
async def create_event(event_data: EventCreate):
    user = await db.users.find_one({"telegram_id": event_data.telegram_id})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)

    event_doc = {
        "user_id": user["_id"],
        "telegram_id": event_data.telegram_id,
        "title": event_data.title,
        "description": event_data.description,
        "event_type": event_data.event_type,
        "start_time": event_data.start_time,
        "end_time": event_data.end_time,
        "follow_up_needed": event_data.follow_up_needed,
        "follow_up_done": False,
        "created_at": now,
    }

    result = await db.events.insert_one(event_doc)

    return {
        "message": "event saved",
        "event_id": str(result.inserted_id),
    }


@router.get("/{telegram_id}")
async def get_user_events(telegram_id: int, limit: int = 20):
    cursor = (
        db.events
        .find({"telegram_id": telegram_id})
        .sort("start_time", -1)
        .limit(limit)
    )

    events = []

    async for event in cursor:
        events.append({
            "id": str(event["_id"]),
            "telegram_id": event["telegram_id"],
            "title": event["title"],
            "description": event.get("description"),
            "event_type": event["event_type"],
            "start_time": event["start_time"],
            "end_time": event.get("end_time"),
            "follow_up_needed": event["follow_up_needed"],
            "follow_up_done": event["follow_up_done"],
        })

    return {
        "telegram_id": telegram_id,
        "events": events,
    }