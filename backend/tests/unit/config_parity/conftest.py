"""Shared fixtures for the config-parity suites (one per migrated kind)."""

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.function import Function


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture
def published(monkeypatch):
    """Capture what reaches Redis; everything else is a harmless no-op."""
    sent: list[tuple[str, dict]] = []

    class _Redis:
        async def publish(self, channel, payload):
            sent.append((channel, json.loads(payload)))

        def __getattr__(self, _name):
            async def _noop(*args, **kwargs):
                return None

            return _noop

    async def fake_get_redis():
        return _Redis()

    monkeypatch.setattr("app.core.redis.get_redis", fake_get_redis)
    return sent


@pytest_asyncio.fixture
async def fn(db: AsyncSession, admin_user) -> Function:
    function = Function(
        user_id=admin_user.id,
        namespace=f"ns{_uid()}",
        name="nightly",
        code="def handler(input, context):\n    return {}",
        input_schema={},
        output_schema={},
    )
    db.add(function)
    await db.flush()
    return function


@pytest_asyncio.fixture
async def agent(db: AsyncSession, admin_user) -> Agent:
    row = Agent(
        user_id=admin_user.id,
        namespace=f"ns{_uid()}",
        name="digest",
        system_prompt="Summarise.",
    )
    db.add(row)
    await db.flush()
    return row
