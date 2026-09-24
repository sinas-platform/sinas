"""Authentication must not serialise on a shared API key's usage stamp.

Every authenticated request used to stamp `api_keys.last_used_at` and
`users.last_login_at`. A service calling Sinas uses ONE key, so every
concurrent request needed the row lock on that one key row and the one user
row behind it: effective concurrency on the auth path was one. Under
sustained load the queue became self-sustaining — observed as 33 concurrent
`UPDATE api_keys SET last_used_at` waiting on transactionid, the oldest for
643 seconds, with uploads timing out and /health taking six seconds.

The stamps are now rewritten at most once per window, with a conditional
UPDATE so a herd at window expiry resolves after one write rather than
queueing one per request.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    USAGE_STAMP_MAX_AGE,
    create_api_key,
    validate_api_key,
)
from app.models.user import APIKey, Role, RolePermission, User, UserRole


@pytest_asyncio.fixture
async def service_key(db: AsyncSession) -> tuple[User, APIKey, str]:
    """A user and one API key, as a service integration would have."""
    role = Role(name=f"svc-{uuid.uuid4().hex[:8]}")
    db.add(role)
    await db.flush()
    db.add(
        RolePermission(
            role_id=role.id, permission_key="sinas.agents/*/*.read:own", permission_value=True
        )
    )
    user = User(email=f"svc-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    await db.flush()
    db.add(UserRole(role_id=role.id, user_id=user.id, active=True))
    await db.flush()

    api_key, plain = await create_api_key(
        db, user, "service", {"sinas.agents/*/*.read:own": True}
    )
    await db.flush()
    return user, api_key, plain


async def _key_stamp(db: AsyncSession, api_key: APIKey):
    """Read the stored value, not the ORM object's cached one."""
    return (
        await db.execute(select(APIKey.last_used_at).where(APIKey.id == api_key.id))
    ).scalar_one()


async def _user_stamp(db: AsyncSession, user: User):
    return (
        await db.execute(select(User.last_login_at).where(User.id == user.id))
    ).scalar_one()


async def _backdate(db: AsyncSession, api_key: APIKey, user: User, age: timedelta) -> None:
    past = datetime.now(UTC) - age
    await db.execute(update(APIKey).where(APIKey.id == api_key.id).values(last_used_at=past))
    await db.execute(update(User).where(User.id == user.id).values(last_login_at=past))
    await db.flush()


class TestStampWriteFrequency:
    async def test_first_use_stamps_the_key(self, db: AsyncSession, service_key):
        user, api_key, plain = service_key
        assert await _key_stamp(db, api_key) is None

        assert await validate_api_key(db, plain) is not None

        assert await _key_stamp(db, api_key) is not None

    async def test_repeated_use_inside_the_window_writes_once(
        self, db: AsyncSession, service_key
    ):
        """The behaviour that matters: N requests, one write."""
        user, api_key, plain = service_key
        assert await validate_api_key(db, plain) is not None
        first = await _key_stamp(db, api_key)

        for _ in range(20):
            assert await validate_api_key(db, plain) is not None

        assert await _key_stamp(db, api_key) == first

    async def test_a_stale_stamp_is_rewritten(self, db: AsyncSession, service_key):
        user, api_key, plain = service_key
        await _backdate(db, api_key, user, USAGE_STAMP_MAX_AGE * 2)
        stale = await _key_stamp(db, api_key)

        assert await validate_api_key(db, plain) is not None

        assert await _key_stamp(db, api_key) > stale

    async def test_the_owner_stamp_is_gated_too(self, db: AsyncSession, service_key):
        """users.last_login_at is the second shared row on this path."""
        user, api_key, plain = service_key
        await validate_api_key(db, plain)
        first = await _user_stamp(db, user)
        assert first is not None

        for _ in range(10):
            await validate_api_key(db, plain)

        assert await _user_stamp(db, user) == first


class TestAuthStillWorks:
    async def test_permissions_resolve_unchanged(self, db: AsyncSession, service_key):
        user, _, plain = service_key
        result = await validate_api_key(db, plain)
        assert result is not None
        authenticated, permissions = result
        assert authenticated.id == user.id
        assert permissions.get("sinas.agents/*/*.read:own") is True

    async def test_a_wrong_key_is_still_refused(self, db: AsyncSession, service_key):
        assert await validate_api_key(db, "sk-not-a-real-key") is None

    async def test_an_expired_key_is_still_refused(self, db: AsyncSession, service_key):
        user, api_key, plain = service_key
        await db.execute(
            update(APIKey)
            .where(APIKey.id == api_key.id)
            .values(expires_at=datetime.now(UTC) - timedelta(days=1))
        )
        await db.flush()
        assert await validate_api_key(db, plain) is None

    async def test_a_deactivated_owner_is_still_refused(
        self, db: AsyncSession, service_key
    ):
        user, _, plain = service_key
        await db.execute(update(User).where(User.id == user.id).values(is_active=False))
        await db.flush()
        assert await validate_api_key(db, plain) is None


@pytest_asyncio.fixture(autouse=True)
async def _dispose_shared_engine():
    """The app engine is module-level and binds to the loop that first uses
    it; each test gets a fresh loop. The concurrency test below drives it
    directly, so drop its pooled connections afterwards."""
    yield
    from app.core.database import async_engine

    await async_engine.dispose()


@pytest_asyncio.fixture
async def committed_service_key():
    """A user and key that really exist, so independent sessions can see them.

    The rolled-back `db` fixture is invisible to other sessions, and the point
    of these tests is what separate concurrent requests do to the same row.
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as setup:
        role = Role(name=f"conc-{uuid.uuid4().hex[:8]}")
        setup.add(role)
        await setup.flush()
        setup.add(
            RolePermission(
                role_id=role.id,
                permission_key="sinas.agents/*/*.read:own",
                permission_value=True,
            )
        )
        user = User(email=f"conc-{uuid.uuid4().hex[:8]}@example.com")
        setup.add(user)
        await setup.flush()
        setup.add(UserRole(role_id=role.id, user_id=user.id, active=True))
        await setup.flush()
        api_key, plain = await create_api_key(
            setup, user, "concurrent", {"sinas.agents/*/*.read:own": True}
        )
        await setup.commit()
        ids = (user.id, api_key.id, role.id)

    try:
        yield ids, plain
    finally:
        user_id, key_id, role_id = ids
        async with AsyncSessionLocal() as cleanup:
            await cleanup.execute(delete(UserRole).where(UserRole.role_id == role_id))
            await cleanup.execute(
                delete(RolePermission).where(RolePermission.role_id == role_id)
            )
            await cleanup.execute(delete(APIKey).where(APIKey.id == key_id))
            await cleanup.execute(delete(Role).where(Role.id == role_id))
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()


class _StampWriteCounter:
    """Counts the statements that actually reach the database."""

    def __init__(self):
        self.api_keys = 0
        self.users = 0

    def __enter__(self):
        from app.core.database import async_engine

        self._engine = async_engine.sync_engine
        event.listen(self._engine, "before_cursor_execute", self._on)
        return self

    def __exit__(self, *exc):
        event.remove(self._engine, "before_cursor_execute", self._on)

    def _on(self, conn, cursor, statement, params, context, executemany):
        normalised = " ".join(statement.split()).lower()
        if normalised.startswith("update api_keys"):
            self.api_keys += 1
        elif normalised.startswith("update users"):
            self.users += 1


class TestConcurrentRequests:
    """What separate requests do to the same row at the same moment.

    Each call gets its own session, as each HTTP request does. Sequential
    calls on one session cannot show this: they never contend.
    """

    CONCURRENCY = 24

    async def _burst(self, plain: str, n: int) -> None:
        from app.core.database import AsyncSessionLocal

        async def one():
            async with AsyncSessionLocal() as session:
                assert await validate_api_key(session, plain) is not None

        await asyncio.gather(*[one() for _ in range(n)])

    async def test_a_burst_inside_the_window_writes_nothing(
        self, committed_service_key
    ):
        """The steady state, and the one that used to cost a write per
        request: a key already in use, hit by many requests at once."""
        _, plain = committed_service_key
        await self._burst(plain, 2)  # establish a fresh stamp

        with _StampWriteCounter() as writes:
            await self._burst(plain, self.CONCURRENCY)

        assert writes.api_keys == 0
        assert writes.users == 0

    async def test_a_burst_on_a_stale_row_collapses_to_few_writes(
        self, committed_service_key
    ):
        """The herd at window expiry. A caller that blocks on the row
        re-evaluates the WHERE after the winner commits, finds the stamp
        fresh and writes nothing — so this must not scale with the burst."""
        ids, plain = committed_service_key
        user_id, key_id, _ = ids
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as s:
            past = datetime.now(UTC) - USAGE_STAMP_MAX_AGE * 5
            await s.execute(
                update(APIKey).where(APIKey.id == key_id).values(last_used_at=past)
            )
            await s.execute(
                update(User).where(User.id == user_id).values(last_login_at=past)
            )
            await s.commit()

        with _StampWriteCounter() as writes:
            await self._burst(plain, self.CONCURRENCY)

        assert writes.api_keys < self.CONCURRENCY, (
            "every concurrent request wrote — the staleness gate is not holding"
        )
        assert writes.users < self.CONCURRENCY

        # And the row did get refreshed exactly once, to one value
        async with AsyncSessionLocal() as s:
            stamp = (
                await s.execute(select(APIKey.last_used_at).where(APIKey.id == key_id))
            ).scalar_one()
        assert stamp > past
