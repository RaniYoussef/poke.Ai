import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pymongo import ASCENDING, DESCENDING

from backend.app.api.routes import (
    test,
    users,
    messages,
    memories,
    events,
    proactive_tasks,
    agent,
)
from backend.app.config import settings
from db.mongodb import db, ping_mongo

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ── startup ──────────────────────────────────────────────────────────────
    await ping_mongo()
    logger.info("Connected to MongoDB")
    await _ensure_indexes()

    if settings.RUN_PROACTIVE_WORKER_IN_API:
        from backend.app.workers.proactive_worker import proactive_worker_loop
        logger.info("Starting proactive worker inside FastAPI (dev mode)")
        asyncio.create_task(proactive_worker_loop())

    if settings.RUN_PROACTIVE_DECISION_WORKER_IN_API:
        from backend.app.workers.proactive_decision_worker import proactive_decision_worker_loop
        logger.info("Starting proactive decision worker inside FastAPI (dev mode)")
        asyncio.create_task(proactive_decision_worker_loop())

    yield
    # ── shutdown (nothing to clean up for now) ────────────────────────────────


app = FastAPI(
    title="Poke.AI Backend",
    description="Backend API for the proactive Telegram AI friend",
    version="0.2.0",
    lifespan=lifespan,
)

# ─── Routes ──────────────────────────────────────────────────────────────────
app.include_router(test.router)
app.include_router(users.router)
app.include_router(messages.router)
app.include_router(memories.router)
app.include_router(events.router)
app.include_router(proactive_tasks.router)
app.include_router(agent.router)


async def _ensure_indexes():
    """Create MongoDB indexes on startup if they don't exist yet."""
    try:
        # messages: fast lookup by user + time
        await db.messages.create_index([("user_id", ASCENDING), ("created_at", ASCENDING)])

        # memories: lookup by user, sorted by importance or tags
        await db.memories.create_index([("user_id", ASCENDING), ("importance", DESCENDING)])
        await db.memories.create_index([("user_id", ASCENDING), ("tags", ASCENDING)])
        # fast lookup for rolling summary
        await db.memories.create_index([
            ("user_id", ASCENDING),
            ("memory_type", ASCENDING),
            ("created_at", DESCENDING),
        ])

        # events: open events per user
        await db.events.create_index([
            ("user_id", ASCENDING),
            ("follow_up_done", ASCENDING),
            ("start_time", ASCENDING),
        ])
        await db.events.create_index([
            ("user_id", ASCENDING),
            ("followed_up", ASCENDING),
            ("followup_at", ASCENDING),
        ])
        await db.events.create_index([
            ("user_id", ASCENDING),
            ("follow_up_done", ASCENDING),
            ("followup_at", ASCENDING),
        ])

        # proactive_tasks: worker query (status + scheduled_time is the hot path)
        await db.proactive_tasks.create_index([("status", ASCENDING), ("scheduled_time", ASCENDING)])
        await db.proactive_tasks.create_index([("status", ASCENDING), ("scheduled_at", ASCENDING)])
        await db.proactive_tasks.create_index([("user_id", ASCENDING), ("status", ASCENDING)])
        await db.proactive_tasks.create_index([
            ("user_id", ASCENDING),
            ("cooldown_key", ASCENDING),
            ("status", ASCENDING),
            ("created_at", DESCENDING),
        ])

        # users: decision worker scan + proactive cooldown metadata
        await db.users.create_index([("telegram_id", ASCENDING)])
        await db.users.create_index([
            ("proactive_enabled", ASCENDING),
            ("last_proactive_decision_at", ASCENDING),
        ])

        # memory_profiles: one profile per user
        await db.memory_profiles.create_index([("user_id", ASCENDING)], unique=True)

        logger.info("MongoDB indexes ensured")
    except Exception as e:
        # Non-fatal: indexes are optimizations, not requirements
        logger.warning("Index creation warning: %s", e)


# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"message": "Poke.AI backend is running"}


@app.get("/health")
async def health_check():
    await ping_mongo()
    return {
        "status": "ok",
        "database": "mongodb connected",
        "version": "0.2.0",
    }
