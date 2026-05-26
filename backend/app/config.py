from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    MONGO_URL: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "poke_ai"

    class Config:
        env_file = ".env"


settings = Settings()