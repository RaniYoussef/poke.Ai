from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.services.agent_processor import process_user_message
from backend.app.services.proactive_decision_engine import (
    create_proactive_task_from_decision,
    evaluate_user_for_proactive_message,
)
from db.mongodb import db

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentProcessMessageRequest(BaseModel):
    telegram_id: int
    content: str
    telegram_message_id: Optional[int] = None


@router.post("/process-message")
async def process_message(request: AgentProcessMessageRequest):
    result = await process_user_message(
        telegram_id=request.telegram_id,
        content=request.content,
        telegram_message_id=request.telegram_message_id,
    )

    return result


@router.post("/evaluate-proactive/{telegram_id}")
async def evaluate_proactive(telegram_id: int):
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return await evaluate_user_for_proactive_message(str(user["_id"]))


@router.post("/force-proactive/{telegram_id}")
async def force_proactive(telegram_id: int):
    user = await db.users.find_one({"telegram_id": telegram_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    decision = await evaluate_user_for_proactive_message(str(user["_id"]))
    if not decision.get("should_message"):
        return {
            "created": False,
            "decision": decision,
        }

    task_result = await create_proactive_task_from_decision(
        str(user["_id"]),
        decision,
        created_by="force_proactive_route",
    )

    return {
        "created": task_result.get("created", False),
        "decision": decision,
        "task_result": task_result,
    }
