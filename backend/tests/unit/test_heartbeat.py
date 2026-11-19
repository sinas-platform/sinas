"""The liveness heartbeat file of the processes without an HTTP server.

The chart's exec probes check the file's age; the file must appear only once
the process is up, stay fresh while its event loop runs (Redis or no Redis),
be gone after shutdown, and never be written where SINAS_HEARTBEAT_FILE is
unset (Docker Compose, the all-in-one profile).
"""

import asyncio
import os
import time

import pytest

from app.core import heartbeat


@pytest.fixture(autouse=True)
def _no_global_heartbeat():
    heartbeat._current = None
    yield
    heartbeat._current = None


async def test_off_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv(heartbeat.HEARTBEAT_FILE_ENV, raising=False)
    assert heartbeat.start() is None
    heartbeat.mark_ready()  # no-op
    await heartbeat.stop()
    assert list(tmp_path.iterdir()) == []


async def test_written_after_ready_refreshed_and_removed_on_stop(monkeypatch, tmp_path):
    path = tmp_path / "heartbeat"
    path.write_text("")  # a previous container's file (the emptyDir survives restarts)
    old = time.time() - 3600
    os.utime(path, (old, old))
    monkeypatch.setenv(heartbeat.HEARTBEAT_FILE_ENV, str(path))
    monkeypatch.setenv(heartbeat.HEARTBEAT_INTERVAL_ENV, "1")

    hb = heartbeat.start()
    assert hb is not None and hb.interval == 1
    await asyncio.sleep(0)
    assert not path.exists()  # stale file gone: not ready until up again

    heartbeat.mark_ready()
    assert path.exists()
    first = path.stat().st_mtime
    assert time.time() - first < 5

    await asyncio.sleep(1.2)  # the loop touched it again
    assert path.stat().st_mtime > first

    await heartbeat.stop()
    assert not path.exists()


async def test_unwritable_path_logs_and_keeps_running(monkeypatch, tmp_path, caplog):
    path = tmp_path / "missing-dir" / "heartbeat"
    monkeypatch.setenv(heartbeat.HEARTBEAT_FILE_ENV, str(path))
    heartbeat.start()
    heartbeat.mark_ready()
    heartbeat.mark_ready()
    assert "Cannot write the heartbeat file" in caplog.text
    assert caplog.text.count("Cannot write the heartbeat file") == 1
    await heartbeat.stop()


async def test_bad_interval_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv(heartbeat.HEARTBEAT_FILE_ENV, str(tmp_path / "hb"))
    monkeypatch.setenv(heartbeat.HEARTBEAT_INTERVAL_ENV, "soon")
    hb = heartbeat.from_env()
    assert hb.interval == heartbeat.DEFAULT_INTERVAL_SECONDS


async def test_arq_worker_ready_once_polling(monkeypatch, tmp_path):
    """SinasWorker starts the heartbeat in main() and marks ready on its
    first poll iteration (after on_startup)."""
    from arq.worker import Worker

    from app.queue.arq_worker import SinasWorker

    path = tmp_path / "hb"
    monkeypatch.setenv(heartbeat.HEARTBEAT_FILE_ENV, str(path))

    async def fake_main(self):
        assert heartbeat._current is not None and not path.exists()
        await self._poll_iteration()

    async def fake_poll(self):
        return None

    monkeypatch.setattr(Worker, "main", fake_main)
    monkeypatch.setattr(Worker, "_poll_iteration", fake_poll)
    worker = SinasWorker.__new__(SinasWorker)
    worker._oom_until = 0.0
    await worker.main()
    assert path.exists()
    await heartbeat.stop()
