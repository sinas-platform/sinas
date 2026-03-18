"""Shared test fixtures for SINAS backend tests."""
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.core.database import get_db
from app.models.user import Role, RolePermission, User, UserRole

# ---------------------------------------------------------------------------
# Database — connect directly to postgres (port 5433) instead of pgbouncer,
# since pgbouncer transaction pooling doesn't support nested savepoints.
# Engine is created per-fixture to avoid event loop conflicts.
# ---------------------------------------------------------------------------

_test_db_url = f"postgresql+asyncpg://{settings.database_user}:{settings.database_password}@localhost:5433/{settings.database_name}"


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a DB session that rolls back all changes after the test."""
    engine = create_async_engine(_test_db_url, echo=False)
    async with engine.connect() as conn:
        txn = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await txn.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def app(db: AsyncSession):
    """Create a FastAPI app with the test DB session injected."""
    from app.main import app as _app

    async def _override_get_db():
        yield db

    _app.dependency_overrides[get_db] = _override_get_db
    yield _app
    _app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client wired to the test app (no real server needed)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_role(db: AsyncSession) -> Role:
    """Create a test role with basic permissions."""
    role = Role(name=f"test-role-{uuid.uuid4().hex[:8]}", description="Test role")
    db.add(role)
    await db.flush()
    await db.refresh(role)

    for perm_key in [
        "sinas.agents.read:all",
        "sinas.agents.create:own",
        "sinas.agents.update:own",
        "sinas.agents.delete:own",
        "sinas.functions.read:all",
        "sinas.functions.create:own",
        "sinas.functions.update:own",
        "sinas.functions.delete:own",
        "sinas.queries.read:all",
        "sinas.queries.create:own",
        "sinas.queries.update:own",
        "sinas.queries.delete:own",
        "sinas.users.read:own",
    ]:
        db.add(RolePermission(role_id=role.id, permission_key=perm_key, permission_value=True))
    await db.flush()

    return role


@pytest_asyncio.fixture
async def admin_role(db: AsyncSession) -> Role:
    """Create an admin role with wildcard permissions."""
    role = Role(name=f"admin-role-{uuid.uuid4().hex[:8]}", description="Admin role")
    db.add(role)
    await db.flush()
    await db.refresh(role)

    db.add(RolePermission(role_id=role.id, permission_key="sinas.*:all", permission_value=True))
    await db.flush()

    return role


@pytest_asyncio.fixture
async def test_user(db: AsyncSession, test_role: Role) -> User:
    """Create a test user with the test role."""
    user = User(email=f"test-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    await db.flush()
    await db.refresh(user)

    db.add(UserRole(role_id=test_role.id, user_id=user.id, active=True))
    await db.flush()

    return user


@pytest_asyncio.fixture
async def admin_user(db: AsyncSession, admin_role: Role) -> User:
    """Create an admin user with wildcard permissions."""
    user = User(email=f"admin-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    await db.flush()
    await db.refresh(user)

    db.add(UserRole(role_id=admin_role.id, user_id=user.id, active=True))
    await db.flush()

    return user


def make_token(user: User) -> str:
    """Generate a valid JWT access token for a user."""
    from app.core.auth import create_access_token

    return create_access_token(user_id=str(user.id), email=user.email)


def auth_headers(user: User) -> dict[str, str]:
    """Return Authorization headers for a user."""
    return {"Authorization": f"Bearer {make_token(user)}"}
