from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.services.agent_processor import process_user_message

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