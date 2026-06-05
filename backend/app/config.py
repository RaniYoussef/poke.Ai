from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    MONGO_URL: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "poke_ai"
    GOOGLE_CLOUD_PROJECT: str = "project-722768a1-c5f8-4666-b5d"
    GEMINI_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    # Set to true to run the proactive worker inside FastAPI (dev mode).
    # In production, run the worker separately: python -m app.workers.run_worker
    RUN_PROACTIVE_WORKER_IN_API: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()