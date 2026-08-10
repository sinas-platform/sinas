"""OIDC-compatible token verification (#101): RS256 signing, JWKS, userinfo.

The signing context is cached per process, so every test that flips
JWT_ALGORITHM goes through the `rs256` / `hs256` fixtures, which reset the
cache on both entry and exit.
"""

import os
import uuid

import pytest
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.token_signing import (
    _generate_private_pem,
    _public_jwk,
    _public_pem_from_private,
    decode_access_token,
    get_signing_context,
    jwks,
    load_or_create_db_key,
    reset_signing_context,
)
from tests.conftest import auth_headers, make_token

# One keypair for the whole module — RSA generation is the slow part.
_PRIVATE_PEM = _generate_private_pem()
_PUBLIC_PEM = _public_pem_from_private(_PRIVATE_PEM)


@pytest.fixture
def rs256(monkeypatch):
    monkeypatch.setattr(settings, "jwt_algorithm", "RS256")
    monkeypatch.setattr(settings, "jwt_private_key", _PRIVATE_PEM)
    reset_signing_context()
    yield
    reset_signing_context()


@pytest.fixture
def hs256():
    reset_signing_context()
    yield
    reset_signing_context()


# ---------------------------------------------------------------------------
# HS256 default: byte-for-byte today's behavior
# ---------------------------------------------------------------------------


class TestHS256Default:
    def test_token_has_no_oidc_claims(self, hs256):
        """Adding aud to HS256 tokens would break existing consumers whose
        JWT library auto-verifies aud whenever the claim is present."""
        from app.core.auth import create_access_token

        token = create_access_token(str(uuid.uuid4()), "u@example.com")
        payload = decode_access_token(token)
        assert "aud" not in payload
        assert "iss" not in payload
        assert jose_jwt.get_unverified_header(token) == {"alg": "HS256", "typ": "JWT"}

    def test_jwks_is_none(self, hs256):
        assert jwks() is None

    async def test_jwks_endpoint_404(self, hs256, client):
        resp = await client.get("/.well-known/jwks.json")
        assert resp.status_code == 404
        assert "RS256" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# RS256: mint, publish, verify offline
# ---------------------------------------------------------------------------


class TestRS256:
    def test_token_carries_iss_aud_kid(self, rs256):
        from app.core.auth import create_access_token

        token = create_access_token(str(uuid.uuid4()), "u@example.com")
        header = jose_jwt.get_unverified_header(token)
        assert header["alg"] == "RS256"
        assert header["kid"] == get_signing_context().kid

        payload = decode_access_token(token)
        assert payload["iss"] == settings.token_issuer
        assert payload["aud"] == settings.jwt_audience

    def test_third_party_verifies_with_jwks_only(self, rs256):
        """The whole point: verification using nothing but the published JWK."""
        from app.core.auth import create_access_token

        user_id = str(uuid.uuid4())
        token = create_access_token(user_id, "u@example.com")

        key_set = jwks()
        assert len(key_set["keys"]) == 1
        payload = jose_jwt.decode(
            token,
            key_set["keys"][0],
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.token_issuer,
        )
        assert payload["sub"] == user_id

    def test_wrong_audience_rejected(self, rs256):
        from app.core.auth import create_access_token

        token = create_access_token(str(uuid.uuid4()), "u@example.com")
        with pytest.raises(jose_jwt.JWTError):
            jose_jwt.decode(
                token,
                jwks()["keys"][0],
                algorithms=["RS256"],
                audience="someone-else",
                issuer=settings.token_issuer,
            )

    def test_hs256_token_rejected_under_rs256(self, rs256):
        """A token forged with the (compromised or guessed) HMAC secret must
        not pass once the deployment runs RS256 — no algorithm-confusion."""
        forged = jose_jwt.encode(
            {"sub": str(uuid.uuid4()), "email": "x@example.com"},
            settings.secret_key,
            algorithm="HS256",
        )
        with pytest.raises(jose_jwt.JWTError):
            decode_access_token(forged)

    def test_kid_is_rfc7638_thumbprint(self, rs256):
        """Deterministic: same key → same kid, independently reproducible."""
        import base64
        import hashlib
        import json

        jwk_dict = _public_jwk(_PUBLIC_PEM)
        canonical = json.dumps(
            {"e": jwk_dict["e"], "kty": "RSA", "n": jwk_dict["n"]},
            separators=(",", ":"),
            sort_keys=True,
        )
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert jwk_dict["kid"] == expected

    async def test_full_auth_path_accepts_rs256_token(self, rs256, client, test_user):
        """verify_jwt_or_api_key (used by every endpoint) round-trips RS256."""
        resp = await client.get("/auth/me", headers=auth_headers(test_user))
        assert resp.status_code == 200
        assert resp.json()["email"] == test_user.email

    async def test_jwks_endpoint_serves_key(self, rs256, client):
        resp = await client.get("/.well-known/jwks.json")
        assert resp.status_code == 200
        keys = resp.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["kty"] == "RSA"
        assert keys[0]["use"] == "sig"
        assert set(keys[0]) >= {"kid", "n", "e", "alg"}

    def test_private_key_file_source(self, tmp_path, monkeypatch):
        keyfile = tmp_path / "jwt.pem"
        keyfile.write_text(_PRIVATE_PEM)
        monkeypatch.setattr(settings, "jwt_algorithm", "RS256")
        monkeypatch.setattr(settings, "jwt_private_key", "")
        monkeypatch.setattr(settings, "jwt_private_key_file", str(keyfile))
        reset_signing_context()
        try:
            ctx = get_signing_context()
            assert ctx.algorithm == "RS256"
            assert ctx.kid == _public_jwk(_PUBLIC_PEM)["kid"]
        finally:
            reset_signing_context()

    def test_unsupported_algorithm_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "jwt_algorithm", "ES512")
        reset_signing_context()
        try:
            with pytest.raises(ValueError, match="ES512"):
                get_signing_context()
        finally:
            reset_signing_context()


# ---------------------------------------------------------------------------
# userinfo endpoint
# ---------------------------------------------------------------------------


class TestUserinfo:
    async def test_get_and_post_return_oidc_claims(self, client, db, test_user):
        test_user.custom_fields = {"department": "engineering", "employee_id": 42}
        await db.flush()

        for method in ("get", "post"):
            resp = await getattr(client, method)(
                "/userinfo", headers=auth_headers(test_user)
            )
            assert resp.status_code == 200, resp.text
            claims = resp.json()
            assert claims["sub"] == str(test_user.id)
            assert claims["email"] == test_user.email
            assert isinstance(claims["roles"], list) and claims["roles"]
            assert claims["department"] == "engineering"
            assert claims["employee_id"] == 42

    async def test_custom_fields_cannot_shadow_standard_claims(
        self, client, db, test_user
    ):
        """A token-exchange partner writes custom_fields; a field named "sub"
        must not let it rewrite the identity seen by downstream services."""
        test_user.custom_fields = {"sub": "spoofed", "email": "spoofed@evil"}
        await db.flush()

        resp = await client.get("/userinfo", headers=auth_headers(test_user))
        claims = resp.json()
        assert claims["sub"] == str(test_user.id)
        assert claims["email"] == test_user.email

    async def test_requires_auth(self, client):
        resp = await client.get("/userinfo")
        assert resp.status_code in (401, 403)

    async def test_works_under_rs256(self, rs256, client, test_user):
        resp = await client.get("/userinfo", headers=auth_headers(test_user))
        assert resp.status_code == 200
        assert resp.json()["sub"] == str(test_user.id)


# ---------------------------------------------------------------------------
# DB-persisted keypair (no env key): load-or-create against the real table
# ---------------------------------------------------------------------------


class TestDBKeyPersistence:
    @pytest.fixture
    async def pg(self):
        import asyncpg

        conn = await asyncpg.connect(
            host="localhost",
            port=int(os.environ.get("TEST_DATABASE_PORT", "5433")),
            user=settings.database_user,
            password=settings.database_password,
            database=settings.database_name,
        )
        # Isolate from any real persisted key, and clean up after ourselves.
        await conn.execute("DELETE FROM jwt_signing_keys WHERE purpose = 'access-token'")
        yield conn
        await conn.execute("DELETE FROM jwt_signing_keys WHERE purpose = 'access-token'")
        await conn.close()

    async def test_first_boot_generates_then_reuses(self, pg):
        pem1 = await load_or_create_db_key(pg)
        pem2 = await load_or_create_db_key(pg)
        assert pem1 == pem2
        assert "PRIVATE KEY" in pem1

        row = await pg.fetchrow(
            "SELECT algorithm, kid, public_key_pem, private_key_encrypted "
            "FROM jwt_signing_keys WHERE purpose = 'access-token'"
        )
        assert row["algorithm"] == "RS256"
        assert row["kid"] == _public_jwk(row["public_key_pem"])["kid"]
        # Never stored in the clear
        assert "PRIVATE KEY" not in row["private_key_encrypted"]

    async def test_concurrent_generation_converges(self, pg):
        """Two processes racing on first boot must end up with the same key
        (ON CONFLICT DO NOTHING + re-select)."""
        import asyncpg

        conn2 = await asyncpg.connect(
            host="localhost",
            port=int(os.environ.get("TEST_DATABASE_PORT", "5433")),
            user=settings.database_user,
            password=settings.database_password,
            database=settings.database_name,
        )
        try:
            import asyncio

            pem_a, pem_b = await asyncio.gather(
                load_or_create_db_key(pg), load_or_create_db_key(conn2)
            )
            assert pem_a == pem_b
        finally:
            await conn2.close()
