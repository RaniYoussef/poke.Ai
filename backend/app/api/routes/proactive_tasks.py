from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.mongodb import db

router = APIRouter(prefix="/proactive-tasks", tags=["proactive tasks"])


class ProactiveTaskCreate(BaseModel):
    telegram_id: int
    event_id: Optional[str] = None
    task_type: str
    message_to_send: str
    scheduled_time: datetime


@router.post("")
async def create_proactive_task(task_data: ProactiveTaskCreate):
    user = await db.users.find_one({"telegram_id": task_data.telegram_id})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)

    task_doc = {
        "user_id": user["_id"],
        "telegram_id": task_data.telegram_id,
        "event_id": task_data.event_id,
        "task_type": task_data.task_type,
        "message_to_send": task_data.message_to_send,
        "scheduled_time": task_data.scheduled_time,
        "status": "pending",
        "sent_at": None,
        "created_at": now,
    }

    result = await db.proactive_tasks.insert_one(task_doc)

    return {
        "message": "proactive task saved",
        "task_id": str(result.inserted_id),
    }


@router.get("/pending")
async def get_pending_tasks():
    now = datetime.now(timezone.utc)

    cursor = db.proactive_tasks.find({
        "status": "pending",
        "scheduled_time": {"$lte": now},
    }).sort("scheduled_time", 1)

    tasks = []

    async for task in cursor:
        tasks.append({
            "id": str(task["_id"]),
            "telegram_id": task["telegram_id"],
            "task_type": task["task_type"],
            "message_to_send": task["message_to_send"],
            "scheduled_time": task["scheduled_time"],
            "status": task["status"],
        })

    return {
        "pending_tasks": tasks,
    }


@router.patch("/{task_id}/sent")
async def mark_task_as_sent(task_id: str):
    result = await db.proactive_tasks.update_one(
        {"_id": ObjectId(task_id)},
        {
            "$set": {
                "status": "sent",
                "sent_at": datetime.now(timezone.utc),
            }
        },
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "message": "task marked as sent",
        "task_id": task_id,
    }