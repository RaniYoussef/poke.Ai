from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings


client = AsyncIOMotorClient(settings.MONGO_URL)

db = client[settings.MONGO_DB_NAME]


async def ping_mongo():
    await client.admin.command("ping")
    return True