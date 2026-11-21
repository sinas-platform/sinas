"""Liveness heartbeat file for the processes without an HTTP server.

The queue workers, the scheduler and the CDC worker have no port a
Kubernetes probe could call. Instead each one touches a file every
`SINAS_HEARTBEAT_INTERVAL` seconds (default 10) from a task on its own event
loop, and the chart's exec probes check the file's age with `stat`:

- the file appears once the process is up (the arq poll loop has started,
  the scheduler / CDC service is running), so readiness means "started";
- it stays fresh for as long as the event loop runs, so liveness means
  "the event loop is not stuck".

It deliberately says nothing about Redis or Postgres: a worker survives a
full or unreachable Redis on its own (app/queue/arq_worker.py), and an
outage of either must not turn into a restart storm of every worker.

Only on when `SINAS_HEARTBEAT_FILE` is set (the Helm chart sets it, on an
emptyDir mount); Docker Compose and the all-in-one profile leave it unset,
so nothing is written there. A stale file from a previous container of the
same pod (the emptyDir outlives a container restart) is removed at start,
so a restarted process isn't "ready" before it is up again.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

HEARTBEAT_FILE_ENV = "SINAS_HEARTBEAT_FILE"
HEARTBEAT_INTERVAL_ENV = "SINAS_HEARTBEAT_INTERVAL"
DEFAULT_INTERVAL_SECONDS = 10.0


class Heartbeat:
    """Touches `path` every `interval` seconds once `mark_ready()` was called."""

    def __init__(self, path: Path, interval: float = DEFAULT_INTERVAL_SECONDS) -> None:
        self.path = path
        self.interval = max(1.0, interval)
        self._ready = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._write_failed = False

    def start(self) -> None:
        """Remove a stale file and start the loop (it writes after mark_ready)."""
        self._remove()
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="sinas-heartbeat")

    def mark_ready(self) -> None:
        if not self._ready.is_set():
            self._ready.set()
            self.touch()

    def touch(self) -> None:
        try:
            self.path.touch()  # creates it, or updates its mtime
            if self._write_failed:
                logger.info(f"Heartbeat file {self.path} writable again")
            self._write_failed = False
        except OSError as e:
            if not self._write_failed:
                logger.error(
                    f"Cannot write the heartbeat file {self.path} ({e}): the liveness "
                    "probe will fail. Mount a writable volume there or unset "
                    f"{HEARTBEAT_FILE_ENV}."
                )
            self._write_failed = True

    async def _run(self) -> None:
        await self._ready.wait()
        while True:
            self.touch()
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Not ready any more: a terminating pod leaves the rotation at once.
        self._remove()

    def _remove(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Could not remove the heartbeat file {self.path}: {e}")


_current: Heartbeat | None = None


def from_env() -> Heartbeat | None:
    """A Heartbeat from SINAS_HEARTBEAT_FILE / _INTERVAL, None when unset."""
    path = os.environ.get(HEARTBEAT_FILE_ENV, "").strip()
    if not path:
        return None
    try:
        interval = float(os.environ.get(HEARTBEAT_INTERVAL_ENV) or DEFAULT_INTERVAL_SECONDS)
    except ValueError:
        interval = DEFAULT_INTERVAL_SECONDS
    return Heartbeat(Path(path), interval)


def start() -> Heartbeat | None:
    """Start this process's heartbeat (call from inside its event loop).
    No-op returning None when SINAS_HEARTBEAT_FILE is unset."""
    global _current
    if _current is None:
        _current = from_env()
        if _current is not None:
            _current.start()
    return _current


def mark_ready() -> None:
    """The process is up: start touching the file. No-op without start()."""
    if _current is not None:
        _current.mark_ready()


async def stop() -> None:
    global _current
    if _current is not None:
        hb, _current = _current, None
        await hb.stop()
