"""
Combined service entrypoint: admin dashboard (HTTP, $PORT) + 15m scheduler loop.

Single process so both share the same local data_store on Railway.
Dashboard runs in a daemon thread; the scheduler loop runs in main.
"""
import os
import threading

from src.scheduler import BotScheduler
from src.dashboard import run as run_dashboard
from src.utils.logger import get_logger

logger = get_logger(__name__)


def main():
    port = int(os.getenv("PORT", "8080"))
    interval = int(os.getenv("SCAN_INTERVAL_SECONDS", "900"))

    thread = threading.Thread(
        target=run_dashboard, kwargs={"port": port}, daemon=True, name="dashboard"
    )
    thread.start()
    logger.info(f"Dashboard thread started on port {port}; starting scheduler loop...")

    BotScheduler().start_loop(interval_seconds=interval)


if __name__ == "__main__":
    main()
