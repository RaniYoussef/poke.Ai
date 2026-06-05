from pathlib import Path

from pydantic_settings import BaseSettings


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _PROJECT_ROOT / "backend"


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    BACKEND_URL: str = "http://localhost:8000"
    PROXY_URL: str = ""

    class Config:
        env_file = (
            _PROJECT_ROOT / ".env",
            _BACKEND_DIR / ".env",
        )
        extra = "ignore"


settings = Settings()
