"""All-in-one ("lite") runtime: queue workers, scheduler and CDC inside the API process.

Started from the FastAPI lifespan when ALL_IN_ONE=true. Each embedded service
is the same code that runs as a dedicated container in the full profile —
arq workers built from the existing WorkerSettings classes, and the
scheduler/CDC service bodies factored into run(stop_event) coroutines.

Constraints (enforced by deployment, documented here):
- Single uvicorn worker (UVICORN_WORKERS=1) and a single backend replica —
  the scheduler and CDC loops are singletons.
- Redis and Postgres are still external processes; only the five sinas
  Python processes collapse into one.
"""
import asyncio
import logging

from arq.worker import Worker, create_worker

from app.core.config import settings

logger = logging.getLogger(__name__)


class AllInOneRuntime:
    """Owns the embedded service tasks for the all-in-one profile."""

    def __init__(self) -> None:
        self._workers: list[Worker] = []
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        from app.cdc import service as cdc_service
        from app.queue.worker import (
            AgentWorkerSettings,
            PipelineWorkerSettings,
            SubAgentWorkerSettings,
            WorkerSettings,
        )
        from app.scheduler import service as scheduler_service

        # Scheduler first: it owns config apply, sandbox image pre-build and
        # pool creation. The tasks still start concurrently, but the pool
        # singletons are per-process — with docker_pool the workers' startup
        # discovery shares this process's pool object, so the cross-process
        # empty-pool race of the full profile cannot occur here.
        self._tasks.append(
            asyncio.create_task(
                self._supervise("scheduler", scheduler_service.run(self._stop_event))
            )
        )
        self._tasks.append(
            asyncio.create_task(
                self._supervise("cdc", cdc_service.run(self._stop_event))
            )
        )

        worker_settings: list[type] = [
            WorkerSettings,
            AgentWorkerSettings,
            PipelineWorkerSettings,
        ]
        if settings.agent_subagent_queue:
            worker_settings.append(SubAgentWorkerSettings)

        for settings_cls in worker_settings:
            # handle_signals=False: signal handling belongs to uvicorn here.
            worker = create_worker(settings_cls, handle_signals=False)
            self._workers.append(worker)
            self._tasks.append(
                asyncio.create_task(
                    self._supervise(settings_cls.__name__, worker.async_run())
                )
            )

        logger.info(
            "All-in-one runtime started: scheduler, cdc and %d queue workers "
            "embedded in the API process",
            len(self._workers),
        )

    async def _supervise(self, name: str, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            # An embedded service dying must be loud: in the full profile the
            # container would crash and restart. Here we log and let the
            # remaining services run; the operator's health signal is the log.
            logger.exception("Embedded service %r crashed", name)

    async def stop(self) -> None:
        self._stop_event.set()  # scheduler + cdc shut down gracefully

        for worker in self._workers:
            try:
                await worker.close()  # waits for in-flight jobs, runs on_shutdown
            except Exception:
                logger.exception("Error closing embedded arq worker")

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._workers.clear()
        self._tasks.clear()
        logger.info("All-in-one runtime stopped")


runtime = AllInOneRuntime()
