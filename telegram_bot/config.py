from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    BACKEND_URL: str = "http://localhost:8000"
    PROXY_URL: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
