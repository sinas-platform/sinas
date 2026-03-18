"""Tests for the SINAS authentication flow."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    create_access_token,
    create_otp_session,
    create_refresh_token,
    verify_otp_code,
)
from app.core.config import settings
from app.models import OTPSession, User
from tests.conftest import auth_headers, make_token


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def test_login_existing_user(client: AsyncClient, test_user: User):
    """POST /auth/login creates an OTP session for an existing user."""
    with patch("app.api.runtime.endpoints.authentication.create_otp_session") as mock_create:
        fake_session = AsyncMock()
        fake_session.id = uuid.uuid4()
        mock_create.return_value = fake_session

        resp = await client.post("/auth/login", json={"email": test_user.email})

    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "OTP sent to your email"
    assert "session_id" in data


async def test_login_nonexistent_user(client: AsyncClient):
    """POST /auth/login returns 403 for an unknown email."""
    resp = await client.post("/auth/login", json={"email": "nobody@example.com"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# OTP verification
# ---------------------------------------------------------------------------


async def test_verify_otp_correct_code(client: AsyncClient, db: AsyncSession, test_user: User):
    """Correct OTP code returns access and refresh tokens."""
    otp_session = OTPSession(
        email=test_user.email,
        otp_code="123456",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db.add(otp_session)
    await db.flush()
    await db.refresh(otp_session)

    resp = await client.post(
        "/auth/verify-otp",
        json={"session_id": str(otp_session.id), "otp_code": "123456"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == test_user.email


async def test_verify_otp_wrong_code(client: AsyncClient, db: AsyncSession, test_user: User):
    """Wrong OTP code returns 400."""
    otp_session = OTPSession(
        email=test_user.email,
        otp_code="123456",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db.add(otp_session)
    await db.flush()
    await db.refresh(otp_session)

    resp = await client.post(
        "/auth/verify-otp",
        json={"session_id": str(otp_session.id), "otp_code": "000000"},
    )

    assert resp.status_code == 400
    assert "Invalid or expired OTP" in resp.json()["detail"]


async def test_verify_otp_max_attempts(client: AsyncClient, db: AsyncSession, test_user: User):
    """After otp_max_attempts wrong attempts the OTP is invalidated."""
    otp_session = OTPSession(
        email=test_user.email,
        otp_code="123456",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db.add(otp_session)
    await db.flush()
    await db.refresh(otp_session)

    # Exhaust all attempts with wrong codes
    for _ in range(settings.otp_max_attempts):
        resp = await client.post(
            "/auth/verify-otp",
            json={"session_id": str(otp_session.id), "otp_code": "000000"},
        )
        assert resp.status_code == 400

    # Even the correct code should now fail
    resp = await client.post(
        "/auth/verify-otp",
        json={"session_id": str(otp_session.id), "otp_code": "123456"},
    )
    assert resp.status_code == 400


async def test_verify_otp_expired_session(client: AsyncClient, db: AsyncSession, test_user: User):
    """Expired OTP session returns 400."""
    otp_session = OTPSession(
        email=test_user.email,
        otp_code="123456",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db.add(otp_session)
    await db.flush()
    await db.refresh(otp_session)

    resp = await client.post(
        "/auth/verify-otp",
        json={"session_id": str(otp_session.id), "otp_code": "123456"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


async def test_refresh_valid_token(client: AsyncClient, db: AsyncSession, test_user: User):
    """Valid refresh token returns a new access token."""
    plain_token, _ = await create_refresh_token(db, str(test_user.id))

    resp = await client.post("/auth/refresh", json={"refresh_token": plain_token})

    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_refresh_invalid_token(client: AsyncClient):
    """Invalid refresh token returns 401."""
    resp = await client.post("/auth/refresh", json={"refresh_token": "bogus-token"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


async def test_logout_revokes_token(client: AsyncClient, db: AsyncSession, test_user: User):
    """Logout revokes the refresh token so it can no longer be used."""
    plain_token, _ = await create_refresh_token(db, str(test_user.id))

    resp = await client.post("/auth/logout", json={"refresh_token": plain_token})
    assert resp.status_code == 204

    # Subsequent refresh should fail
    resp = await client.post("/auth/refresh", json={"refresh_token": plain_token})
    assert resp.status_code == 401


async def test_logout_unknown_token(client: AsyncClient):
    """Logout with an unknown token returns 404."""
    resp = await client.post("/auth/logout", json={"refresh_token": "no-such-token"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


async def test_me_authenticated(client: AsyncClient, test_user: User):
    """GET /auth/me returns user info for an authenticated user."""
    resp = await client.get("/auth/me", headers=auth_headers(test_user))

    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == test_user.email
    assert data["id"] == str(test_user.id)


async def test_me_no_token(client: AsyncClient):
    """GET /auth/me without a token returns 401/403."""
    resp = await client.get("/auth/me")
    assert resp.status_code in (401, 403)


async def test_me_invalid_token(client: AsyncClient):
    """GET /auth/me with a garbage token returns 401."""
    resp = await client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def test_login_rate_limit(client: AsyncClient, test_user: User):
    """Too many login requests trigger a 429 response."""
    from app.core.redis import get_redis

    # Clear any existing rate limit keys for this test
    redis = await get_redis()
    async for key in redis.scan_iter("sinas:ratelimit:login:*"):
        await redis.delete(key)

    # Patch create_otp_session to avoid email sending
    with patch("app.api.runtime.endpoints.authentication.create_otp_session") as mock_create:
        fake_session = AsyncMock()
        fake_session.id = uuid.uuid4()
        mock_create.return_value = fake_session

        # Exceed the per-email rate limit (5 by default)
        for i in range(settings.rate_limit_login_email_max + 1):
            resp = await client.post("/auth/login", json={"email": test_user.email})

        assert resp.status_code == 429

    # Clean up rate limit keys
    async for key in redis.scan_iter("sinas:ratelimit:login:*"):
        await redis.delete(key)


# ---------------------------------------------------------------------------
# Check permissions
# ---------------------------------------------------------------------------


async def test_check_permissions_with_valid_perms(client: AsyncClient, test_user: User):
    """check-permissions returns correct results for the user's permissions."""
    resp = await client.post(
        "/auth/check-permissions",
        json={"permissions": ["sinas.functions.read:all"], "logic": "AND"},
        headers=auth_headers(test_user),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"] is True


async def test_check_permissions_missing_perm(client: AsyncClient, test_user: User):
    """check-permissions returns false for a permission the user does not have."""
    resp = await client.post(
        "/auth/check-permissions",
        json={"permissions": ["sinas.admin.nuke:all"], "logic": "AND"},
        headers=auth_headers(test_user),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"] is False


async def test_check_permissions_admin_wildcard(client: AsyncClient, admin_user: User):
    """Admin with wildcard permission passes any check."""
    resp = await client.post(
        "/auth/check-permissions",
        json={"permissions": ["sinas.anything.here:all"], "logic": "AND"},
        headers=auth_headers(admin_user),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"] is True
