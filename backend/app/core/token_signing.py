"""Access-token signing context: HS256 (default) or RS256 with published JWKS.

HS256 keeps today's behavior byte-for-byte: tokens signed with the shared
``secret_key``, no extra claims. RS256 (``JWT_ALGORITHM=RS256``) signs access
tokens with an RSA keypair and adds ``iss``/``aud`` claims + a ``kid`` header,
so any standard resource-server library can verify Sinas tokens offline via
``GET /.well-known/jwks.json`` — no shared secret, no per-request introspection.

Private key resolution order (RS256):
  1. ``JWT_PRIVATE_KEY``       — PEM content in the environment
  2. ``JWT_PRIVATE_KEY_FILE``  — path to a PEM file
  3. auto-generated once and persisted (encrypted) in ``jwt_signing_keys`` so
     every backend/worker/scheduler process signs and verifies with the same
     key. Concurrent first boots race safely via ON CONFLICT DO NOTHING.

Internal purpose tokens (file serve, component render, content refresh) are
deliberately NOT part of this: they stay HS256 with ``secret_key`` — they are
consumed only by Sinas itself and third parties should never verify them.
"""

import asyncio
import base64
import concurrent.futures
import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Optional

from jose import jwt
from jose import jwk as jose_jwk

from app.core.config import settings

logger = logging.getLogger(__name__)

# Single row per purpose; today only access tokens have a managed keypair.
_KEY_PURPOSE = "access-token"


@dataclass(frozen=True)
class SigningContext:
    algorithm: str
    sign_key: str  # HS256: shared secret; RS256: private PEM
    verify_key: str  # HS256: shared secret; RS256: public PEM
    kid: Optional[str] = None  # RS256 only
    public_jwk: Optional[dict[str, Any]] = None  # RS256 only


_context: Optional[SigningContext] = None
_context_lock = threading.Lock()


def reset_signing_context() -> None:
    """Drop the cached context (tests / key rotation followed by restart)."""
    global _context
    with _context_lock:
        _context = None


def get_signing_context() -> SigningContext:
    """Resolve (once per process) and return the signing context."""
    global _context
    if _context is not None:
        return _context
    with _context_lock:
        if _context is None:
            _context = _build_context()
    return _context


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify an access token with the configured algorithm.

    Central choke point: under RS256 this also enforces ``aud`` and ``iss``,
    which per-callsite ``jwt.decode`` calls would silently skip. Raises
    ``jose.JWTError`` (or subclasses) on any failure, same contract callers
    already handle.
    """
    ctx = get_signing_context()
    if ctx.algorithm == "RS256":
        return jwt.decode(
            token,
            ctx.verify_key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.token_issuer,
        )
    return jwt.decode(token, ctx.verify_key, algorithms=[ctx.algorithm])


def jwks() -> Optional[dict[str, Any]]:
    """The published key set, or None when HS256 (nothing safe to publish)."""
    ctx = get_signing_context()
    if ctx.public_jwk is None:
        return None
    return {"keys": [ctx.public_jwk]}


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------


def _build_context() -> SigningContext:
    algorithm = settings.jwt_algorithm.strip().upper() or "HS256"

    if algorithm == "HS256":
        return SigningContext(
            algorithm="HS256",
            sign_key=settings.secret_key,
            verify_key=settings.secret_key,
        )

    if algorithm != "RS256":
        raise ValueError(
            f"Unsupported JWT_ALGORITHM {algorithm!r} — use HS256 or RS256"
        )

    private_pem = _resolve_private_key()
    public_pem = _public_pem_from_private(private_pem)
    public_jwk = _public_jwk(public_pem)
    kid = public_jwk["kid"]

    logger.info(f"Access tokens signed with RS256 (kid={kid})")
    return SigningContext(
        algorithm="RS256",
        sign_key=private_pem,
        verify_key=public_pem,
        kid=kid,
        public_jwk=public_jwk,
    )


def _resolve_private_key() -> str:
    if settings.jwt_private_key.strip():
        # Env vars flatten newlines easily; accept literal "\n" sequences.
        return settings.jwt_private_key.replace("\\n", "\n").strip() + "\n"

    if settings.jwt_private_key_file.strip():
        with open(settings.jwt_private_key_file.strip()) as f:
            return f.read()

    return _load_or_create_db_key_sync()


def _generate_private_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _public_pem_from_private(private_pem: str) -> str:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


def _public_jwk(public_pem: str) -> dict[str, Any]:
    """Standard RSA JWK with an RFC 7638 thumbprint as the kid."""
    jwk_dict = jose_jwk.construct(public_pem, "RS256").to_dict()
    # RFC 7638: SHA-256 over the required members only, lexicographic order,
    # no whitespace — gives a deterministic kid any tooling can reproduce.
    canonical = json.dumps(
        {"e": jwk_dict["e"], "kty": jwk_dict["kty"], "n": jwk_dict["n"]},
        separators=(",", ":"),
        sort_keys=True,
    )
    thumbprint = base64.urlsafe_b64encode(
        hashlib.sha256(canonical.encode()).digest()
    ).rstrip(b"=").decode()
    return {
        "kty": jwk_dict["kty"],
        "use": "sig",
        "alg": "RS256",
        "kid": thumbprint,
        "n": jwk_dict["n"],
        "e": jwk_dict["e"],
    }


# ---------------------------------------------------------------------------
# DB-persisted keypair (no env key provided)
# ---------------------------------------------------------------------------


def _load_or_create_db_key_sync() -> str:
    """Run the async DB loader from sync code, whatever the calling context.

    ``create_access_token`` is sync and called from inside running event loops
    (request handlers, workers), where ``asyncio.run`` would fail — so the
    loader always runs in a fresh loop on a dedicated thread. One-time cost
    per process; the result is cached in the signing context.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, _load_or_create_db_key()).result()


async def _load_or_create_db_key() -> str:
    import asyncpg

    conn = await asyncpg.connect(
        host=settings.database_host,
        port=int(settings.database_port),
        user=settings.database_user,
        password=settings.database_password,
        database=settings.database_name,
    )
    try:
        return await load_or_create_db_key(conn)
    finally:
        await conn.close()


async def load_or_create_db_key(conn) -> str:
    """Load the persisted private key, generating it on first ever boot.

    Takes an asyncpg connection so tests can supply their own. Concurrent
    processes may both generate a candidate key; ON CONFLICT DO NOTHING +
    re-select guarantees they all converge on whichever insert won.
    """
    import asyncpg

    from app.core.encryption import encryption_service

    try:
        row = await conn.fetchrow(
            "SELECT private_key_encrypted FROM jwt_signing_keys WHERE purpose = $1",
            _KEY_PURPOSE,
        )
    except asyncpg.exceptions.UndefinedTableError:
        raise RuntimeError(
            "jwt_signing_keys table missing — run `alembic upgrade head` "
            "before enabling JWT_ALGORITHM=RS256"
        )

    if row is None:
        private_pem = _generate_private_pem()
        public_pem = _public_pem_from_private(private_pem)
        kid = _public_jwk(public_pem)["kid"]
        await conn.execute(
            """
            INSERT INTO jwt_signing_keys
                (purpose, algorithm, kid, private_key_encrypted, public_key_pem)
            VALUES ($1, 'RS256', $2, $3, $4)
            ON CONFLICT (purpose) DO NOTHING
            """,
            _KEY_PURPOSE,
            kid,
            encryption_service.encrypt(private_pem),
            public_pem,
        )
        row = await conn.fetchrow(
            "SELECT private_key_encrypted FROM jwt_signing_keys WHERE purpose = $1",
            _KEY_PURPOSE,
        )
        logger.info("Generated and persisted RS256 signing keypair")

    return encryption_service.decrypt(row["private_key_encrypted"])
