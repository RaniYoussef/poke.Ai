"""
run_all_workers.py
Development entry point that runs both proactive workers in one process.

Run from the backend/ directory:
    python -m app.workers.run_all_workers
"""
import asyncio
import logging
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from backend.app.workers.proactive_decision_worker import proactive_decision_worker_loop
from backend.app.workers.proactive_worker import proactive_worker_loop
from db.mongodb import ping_mongo

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Connecting to MongoDB...")
    await ping_mongo()
    logger.info("MongoDB connected. Starting proactive workers.")
    await asyncio.gather(
        proactive_decision_worker_loop(),
        proactive_worker_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
