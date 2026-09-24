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
from sqlalchemy import select, update
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
