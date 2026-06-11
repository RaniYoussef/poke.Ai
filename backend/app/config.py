from pathlib import Path

from pydantic_settings import BaseSettings


_BACKEND_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    MONGO_URL: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "poke_ai"
    GOOGLE_CLOUD_PROJECT: str = "project-722768a1-c5f8-4666-b5d"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    TELEGRAM_BOT_TOKEN: str = ""

    GOOGLE_CLOUD_LOCATION: str = "us-central1"
    # Set to true to run the proactive worker inside FastAPI (dev mode).
    # In production, run the worker separately: python -m app.workers.run_worker
    RUN_PROACTIVE_WORKER_IN_API: bool = False
    PROACTIVE_DECISION_WORKER_INTERVAL_SECONDS: int = 300
    PROACTIVE_DECISION_BATCH_SIZE: int = 50
    PROACTIVE_INACTIVITY_HOURS: int = 48
    PROACTIVE_DEFAULT_LEVEL: str = "medium"
    PROACTIVE_LOW_COOLDOWN_HOURS: int = 72
    PROACTIVE_MEDIUM_COOLDOWN_HOURS: int = 24
    PROACTIVE_HIGH_COOLDOWN_HOURS: int = 6
    RUN_PROACTIVE_DECISION_WORKER_IN_API: bool = False
    DEFAULT_TIMEZONE: str = "Africa/Cairo"
    DEFAULT_QUIET_HOURS_START: str = "23:00"
    DEFAULT_QUIET_HOURS_END: str = "09:00"

    class Config:
        env_file = (
            _PROJECT_ROOT / ".env",
            _BACKEND_DIR / ".env",
        )
        extra = "ignore"


settings = Settings()
