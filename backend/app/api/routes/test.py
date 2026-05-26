from datetime import datetime, timezone

from fastapi import APIRouter

from app.db.mongodb import db

router = APIRouter(prefix="/test", tags=["test"])


@router.post("/user")
async def create_test_user():
    now = datetime.now(timezone.utc)

    user = {
        "telegram_id": 123456789,
        "username": "test_user",
        "first_name": "Test",
        "last_name": "User",
        "language": "en",
        "timezone": "Africa/Cairo",
        "preferred_tone": "friendly",
        "voice_enabled": False,
        "created_at": now,
        "updated_at": now,
        "last_seen_at": now,
    }

    result = await db.users.insert_one(user)

    return {
        "message": "test user created",
        "user_id": str(result.inserted_id),
    }