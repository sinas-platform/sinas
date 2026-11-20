"""External OAuth tokens on the MCP gateway (RFC 9728 + bearer JWTs).

An MCP client that does not hold a Sinas API key goes through OAuth: it
reads the gateway's protected-resource metadata, finds the authorization
server(s), obtains an access token there and presents it as a bearer. The
authorization servers are the active identity providers (the same OIDC
providers SSO signs people in with); a token is accepted when its issuer
is one of them, its signature checks against that issuer's published
keys, it has not expired, and its audience names this gateway (the
resource URL, RFC 8707) or the provider's own client id (for issuers that
cannot mint resource-specific audiences).

The token's subject is then mapped to a Sinas user exactly as an SSO
sign-in would (link by subject, else by verified email, else provision
when the provider allows it), and the user's roles decide what the
gateway shows. Resolutions are cached per token for a short while so a
busy agent does not re-verify and re-sync on every call.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.identity_provider import IdentityProvider
from app.services import sso

logger = logging.getLogger(__name__)

ACCESS_TOKEN_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")
RESOLUTION_CACHE_SECONDS = 300


class GatewayAuthError(Exception):
    """The bearer token is not acceptable; the message is safe to return."""


def resource_url(namespace: str, name: str) -> str:
    """The gateway's resource identifier: what a token's `aud` must name."""
    return f"{settings.public_base_url}/mcp/{namespace}/{name}"


def metadata_url(namespace: str, name: str) -> str:
    return f"{settings.public_base_url}/.well-known/oauth-protected-resource/mcp/{namespace}/{name}"


async def protected_resource_metadata(db: AsyncSession, namespace: str, name: str, description: str | None) -> dict[str, Any]:
    """RFC 9728 document for one gateway."""
    providers = await sso.active_providers(db)
    doc: dict[str, Any] = {
        "resource": resource_url(namespace, name),
        "authorization_servers": sorted({p.issuer.rstrip("/") for p in providers}),
        "bearer_methods_supported": ["header"],
        "scopes_supported": [],
    }
    if description:
        doc["resource_name"] = description
    return doc


def _unverified_issuer(token: str) -> str | None:
    """The token's `iss`, read without verifying: it only selects which
    provider's keys to verify against."""
    try:
        claims = jwt.get_unverified_claims(token)
    except JWTError:
        return None
    iss = claims.get("iss")
    return iss.rstrip("/") if isinstance(iss, str) else None


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and _unverified_issuer(token) is not None


async def _verify(token: str, provider: IdentityProvider, audiences: list[str]) -> dict[str, Any]:
    doc = await sso.discovery(provider.issuer)
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise GatewayAuthError("Malformed bearer token") from e
    alg = header.get("alg")
    if alg not in ACCESS_TOKEN_ALGORITHMS:
        raise GatewayAuthError(f"Token algorithm {alg!r} is not accepted")
    kid = header.get("kid")
    key = sso._pick_key(await sso._jwks(doc["jwks_uri"]), kid, alg)
    if key is None:
        key = sso._pick_key(await sso._jwks(doc["jwks_uri"], refetch=True), kid, alg)
    if key is None:
        raise GatewayAuthError("Token is signed with a key the issuer does not publish")
    try:
        claims = jwt.decode(
            token, key, algorithms=[alg], issuer=doc["issuer"],
            options={"verify_aud": False, "leeway": sso.CLOCK_SKEW_SECONDS, "require_exp": True, "require_sub": True},
        )
    except ExpiredSignatureError as e:
        raise GatewayAuthError("Token has expired") from e
    except (JWTClaimsError, JWTError) as e:
        logger.info("Gateway: token from %s rejected: %s", provider.name, e)
        raise GatewayAuthError("Token could not be verified") from e
    aud = claims.get("aud")
    token_audiences = set(aud if isinstance(aud, list) else [aud] if aud else [])
    if not token_audiences & set(audiences):
        raise GatewayAuthError("Token was not issued for this gateway (audience)")
    return claims


@dataclass
class Resolution:
    user_id: str
    email: str
    expires_at: float


_cache: dict[str, Resolution] = {}


def clear_cache() -> None:
    _cache.clear()


async def resolve_bearer(db: AsyncSession, token: str, namespace: str, name: str) -> tuple[str, str]:
    """(user_id, email) for an external access token presented to the
    gateway `namespace/name`, or GatewayAuthError."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    cached = _cache.get(digest)
    if cached and cached.expires_at > time.monotonic():
        return cached.user_id, cached.email
    issuer = _unverified_issuer(token)
    if issuer is None:
        raise GatewayAuthError("Invalid or expired credentials")
    provider = next((p for p in await sso.active_providers(db) if p.issuer.rstrip("/") == issuer), None)
    if provider is None:
        raise GatewayAuthError("Token issuer is not a configured identity provider")
    claims = await _verify(token, provider, [resource_url(namespace, name), provider.client_id])
    if not sso.claim_values(claims, provider.email_claim):
        # Access tokens often leave the email out; userinfo has it.
        doc = await sso.discovery(provider.issuer)
        try:
            claims = await sso.with_userinfo(doc, claims, token)
        except sso.SsoError as e:
            raise GatewayAuthError(str(e)) from e
    try:
        signed_in = await sso.sign_in(db, provider, issuer, claims)
        await db.commit()
    except sso.SsoError as e:
        await db.rollback()
        raise GatewayAuthError(str(e)) from e
    user = signed_in.user
    ttl = min(RESOLUTION_CACHE_SECONDS, max(1, int(claims.get("exp", 0) - time.time())))
    if len(_cache) > 10_000:
        _cache.clear()
    _cache[digest] = Resolution(str(user.id), user.email, time.monotonic() + ttl)
    return str(user.id), user.email
