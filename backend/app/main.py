from fastapi import FastAPI

from app.api.routes import test, users, messages, memories, events, proactive_tasks
from app.db.mongodb import ping_mongo

app = FastAPI(
    title="Poke.AI Backend",
    description="Backend API for the proactive Telegram AI friend",
    version="0.1.0",
)

app.include_router(test.router)
app.include_router(users.router)
app.include_router(messages.router)
app.include_router(memories.router)
app.include_router(events.router)
app.include_router(proactive_tasks.router)

@app.on_event("startup")
async def startup_event():
    await ping_mongo()
    print("Connected to MongoDB")


@app.get("/")
def root():
    return {"message": "Poke.AI backend is running"}


@app.get("/health")
async def health_check():
    await ping_mongo()
    return {
        "status": "ok",
        "database": "mongodb connected",
    }