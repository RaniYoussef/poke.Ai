"""
run_worker.py
Standalone entry point for the proactive worker process.

Run from the backend/ directory:
    python -m app.workers.run_worker
"""
import asyncio
import logging
import sys
from pathlib import Path

# Ensure the project root (poke.Ai/) is on sys.path so both
# 'db' and 'backend' packages resolve correctly when running
# from the backend/ subdirectory.
_project_root = Path(__file__).resolve().parents[3]  # .../poke.Ai/
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from db.mongodb import ping_mongo
from backend.app.workers.proactive_worker import proactive_worker_loop

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Connecting to MongoDB...")
    await ping_mongo()
    logger.info("MongoDB connected. Starting proactive worker.")
    await proactive_worker_loop()


if __name__ == "__main__":
    asyncio.run(main())
