import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "poke_ai")

client = AsyncIOMotorClient(MONGO_URL)
db = client[MONGO_DB_NAME]


async def ping_mongo():
    await client.admin.command("ping")
    return True
