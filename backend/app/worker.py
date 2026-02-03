"""
Shared worker process for executing trusted functions.

This process keeps the worker container alive and ready to receive
execution requests via docker exec_run.
"""
import asyncio
import logging
import signal
import sys

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def worker_main():
    """Main worker loop - keeps container alive and ready."""
    logger.info("🚀 Worker started and ready to receive execution requests")

    # Keep worker alive indefinitely
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logger.info("Worker shutting down...")
        raise


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)


if __name__ == "__main__":
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Run worker
    try:
        asyncio.run(worker_main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.error(f"Worker crashed: {e}", exc_info=True)
        sys.exit(1)
