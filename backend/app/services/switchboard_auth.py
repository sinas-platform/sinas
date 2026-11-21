"""External OAuth tokens on the switchboard (RFC 9728 + bearer JWTs).

An MCP client that does not hold a Sinas API key goes through OAuth: it
reads the switchboard's protected-resource metadata, finds the authorization
server(s), obtains an access token there and presents it as a bearer. The
authorization servers are the active identity providers (the same OIDC
providers SSO signs people in with); a token is accepted when its issuer
is one of them, its signature checks against that issuer's published
keys, it has not expired, and its audience names this switchboard (the
resource URL, RFC 8707) or the provider's own client id (for issuers that
cannot mint resource-specific audiences).

The token's subject is then mapped to a Sinas user exactly as an SSO
sign-in would (link by subject, else by verified email, else provision
when the provider allows it), and the user's roles decide what the
switchboard shows. Resolutions are cached per token for a short while so a
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


class SwitchboardAuthError(Exception):
    """The bearer token is not acceptable; the message is safe to return."""


def resource_url(namespace: str, name: str) -> str:
    """The switchboard's resource identifier: what a token's `aud` must name."""
    return f"{settings.public_base_url}/mcp/{namespace}/{name}"


def metadata_url(namespace: str, name: str) -> str:
    return f"{settings.public_base_url}/.well-known/oauth-protected-resource/mcp/{namespace}/{name}"


async def protected_resource_metadata(db: AsyncSession, namespace: str, name: str, description: str | None) -> dict[str, Any]:
    """RFC 9728 document for one switchboard."""
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


async def _discovery(provider: IdentityProvider) -> dict:
    """The provider's metadata; a provider that is down or misconfigured is
    an authentication failure, not a server error."""
    try:
        return await sso.discovery(provider.issuer)
    except sso.SsoError as e:
        raise SwitchboardAuthError(f"Identity provider unavailable: {e}") from e


async def _verify(token: str, provider: IdentityProvider, audiences: list[str]) -> dict[str, Any]:
    doc = await _discovery(provider)
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise SwitchboardAuthError("Malformed bearer token") from e
    alg = header.get("alg")
    if alg not in ACCESS_TOKEN_ALGORITHMS:
        raise SwitchboardAuthError(f"Token algorithm {alg!r} is not accepted")
    kid = header.get("kid")
    try:
        key = sso._pick_key(await sso._jwks(doc["jwks_uri"]), kid, alg)
        if key is None:
            key = sso._pick_key(await sso._jwks(doc["jwks_uri"], refetch=True), kid, alg)
    except sso.SsoError as e:
        raise SwitchboardAuthError(f"Identity provider unavailable: {e}") from e
    if key is None:
        raise SwitchboardAuthError("Token is signed with a key the issuer does not publish")
    try:
        claims = jwt.decode(
            token, key, algorithms=[alg], issuer=doc["issuer"],
            options={"verify_aud": False, "leeway": sso.CLOCK_SKEW_SECONDS, "require_exp": True, "require_sub": True},
        )
    except ExpiredSignatureError as e:
        raise SwitchboardAuthError("Token has expired") from e
    except (JWTClaimsError, JWTError) as e:
        logger.info("Switchboard: token from %s rejected: %s", provider.name, e)
        raise SwitchboardAuthError("Token could not be verified") from e
    aud = claims.get("aud")
    token_audiences = set(aud if isinstance(aud, list) else [aud] if aud else [])
    resource, client_id = audiences[0], audiences[1:]
    if resource in token_audiences:
        return claims
    if token_audiences & set(client_id):
        # The provider's own client id is also what its SSO ID tokens carry.
        # Only a token the provider marked as an access token (RFC 9068,
        # typ at+jwt) is taken on that audience; an ID token (a nonce, no
        # such type) authorizes a login, not API calls.
        typ = str(header.get("typ") or "").lower()
        if typ in ("at+jwt", "application/at+jwt") and "nonce" not in claims:
            return claims
        raise SwitchboardAuthError("Token is an ID token, not an access token for this switchboard")
    raise SwitchboardAuthError("Token was not issued for this switchboard (audience)")


async def _sign_in(db: AsyncSession, provider: IdentityProvider, issuer: str, claims: dict[str, Any]):
    """sign_in, committed. Two first sign-ins of one new identity can race
    into the same insert; the loser rolls back and finds the winner's row."""
    from sqlalchemy.exc import IntegrityError

    for attempt in (1, 2):
        try:
            signed_in = await sso.sign_in(db, provider, issuer, claims)
            await db.commit()
            return signed_in.user
        except sso.SsoError as e:
            await db.rollback()
            raise SwitchboardAuthError(str(e)) from e
        except IntegrityError:
            await db.rollback()
            if attempt == 2:
                raise SwitchboardAuthError("Sign-in collided with another request; retry") from None
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass
class Resolution:
    user_id: str
    email: str
    provider_id: Any
    expires_at: float


_cache: dict[str, Resolution] = {}


def clear_cache() -> None:
    _cache.clear()


async def resolve_bearer(db: AsyncSession, token: str, namespace: str, name: str) -> tuple[str, str]:
    """(user_id, email) for an external access token presented to the
    switchboard `namespace/name`, or SwitchboardAuthError."""
    # Cached per token AND switchboard: a token issued for switchboard A must still
    # fail B's audience check, cache or no cache.
    digest = hashlib.sha256(f"{namespace}/{name}\n{token}".encode()).hexdigest()
    cached = _cache.get(digest)
    if cached and cached.expires_at > time.monotonic():
        # The provider that vouched for this token must still be there and
        # active: disabling or deleting it ends its tokens' access at once,
        # cache or no cache.
        if await _provider_active(db, cached.provider_id):
            return cached.user_id, cached.email
        _cache.pop(digest, None)
        raise SwitchboardAuthError("Token issuer is not a configured identity provider")
    issuer = _unverified_issuer(token)
    if issuer is None:
        raise SwitchboardAuthError("Invalid or expired credentials")
    provider = next((p for p in await sso.active_providers(db) if p.issuer.rstrip("/") == issuer), None)
    if provider is None:
        raise SwitchboardAuthError("Token issuer is not a configured identity provider")
    claims = await _verify(token, provider, [resource_url(namespace, name), provider.client_id])
    # Access tokens often leave the email, the groups or a mapped claim to
    # userinfo. Ask for it whenever the SSO callback would: signing in with
    # groups missing would end the roles this provider granted earlier.
    if (
        not sso.claim_values(claims, provider.email_claim)
        or sso.email_verified(claims) is None  # linking by email needs the proof
        or sso.needs_userinfo(provider, claims)
    ):
        try:
            claims = await sso.with_userinfo(await _discovery(provider), claims, token)
        except sso.SsoError as e:
            raise SwitchboardAuthError(str(e)) from e
    user = await _sign_in(db, provider, issuer, claims)
    ttl = min(RESOLUTION_CACHE_SECONDS, max(1, int(claims.get("exp", 0) - time.time())))
    if len(_cache) > 10_000:
        _cache.clear()
    _cache[digest] = Resolution(str(user.id), user.email, provider.id, time.monotonic() + ttl)
    return str(user.id), user.email


async def _provider_active(db: AsyncSession, provider_id: Any) -> bool:
    from sqlalchemy import select

    return (
        await db.execute(
            select(IdentityProvider.id).where(IdentityProvider.id == provider_id, IdentityProvider.is_active.is_(True))
        )
    ).scalar_one_or_none() is not None


def header_safe(text: str, limit: int = 200) -> str:
    """A string fit for a quoted header parameter: no quotes, backslashes or
    control characters, bounded in length."""
    cleaned = "".join(ch for ch in text if 32 <= ord(ch) < 127 and ch not in '"\\')
    return cleaned[:limit]
