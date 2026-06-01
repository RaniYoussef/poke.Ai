from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    MONGO_URL: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "poke_ai"
    GOOGLE_CLOUD_PROJECT: str = "project-722768a1-c5f8-4666-b5d"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()