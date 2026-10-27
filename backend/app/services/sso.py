"""SSO login via OpenID Connect (docs/design/oidc-sso.md).

Authorization Code + PKCE against any OIDC identity provider found by
discovery. This module holds everything but the HTTP endpoints: discovery
and JWKS (cached), the code exchange, ID-token verification, claims, who the
person is in Sinas (link, link by verified email, provision), role sync,
per-domain enforcement, and the short-lived login state in Redis.

Messages of SsoError are shown to the person signing in: they name what
went wrong, never a token, a claim dump or a secret.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlsplit

import httpx
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_user_by_email, normalize_email
from app.core.config import settings
from app.core.redis import get_redis
from app.models.identity_provider import IdentityProvider
from app.models.user import RefreshToken, Role, User, UserIdentity, UserRole
from app.services.user_identity import get_user_by_identity, link_user_identity

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 600  # to get through the IdP's login page
CODE_TTL_SECONDS = 60  # from the redirect to the console's exchange
BIND_COOKIE_NAME = "sinas_sso"
BIND_COOKIE_PATH = "/auth/oidc"
CLOCK_SKEW_SECONDS = 60
CACHE_SECONDS = 3600
JWKS_REFETCH_SECONDS = 60  # at most this often for a kid not in the cached set
HTTP_TIMEOUT_SECONDS = 10
# Asymmetric only: never "none", never HMAC keyed with the client secret.
ID_TOKEN_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")

_STATE_PREFIX = "sso_state:"
_CODE_PREFIX = "sso_code:"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class SsoError(Exception):
    """A sign-in refused; the message is for the person signing in."""


# ------------------------------------------------------------------ URLs


def redirect_uri(name: str) -> str:
    """What the IdP redirects back to (and what admins register there)."""
    return f"{settings.public_base_url}/auth/oidc/{quote(name, safe='')}/callback"


def console_callback_url() -> str:
    """Where the backend sends the browser after the IdP: always this, never
    a URL from the request."""
    return f"{settings.public_console_url}/sso/callback"


def safe_return_to(value: Optional[str]) -> str:
    """A path inside the console to continue on, or "/". Absolute URLs,
    scheme-relative ones (//host) and backslash tricks are dropped."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(ch) < 0x20 for ch in value) or len(value) > 2048:
        return "/"
    return value


def source_tag(provider_name: str) -> str:
    """user_roles.source for memberships this provider's mapping granted."""
    return f"idp:{provider_name}"


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _usable_url(url: Any, what: str) -> str:
    """An endpoint discovery handed us: https, or http where the issuer
    itself may be (localhost, or OIDC_ALLOW_HTTP)."""
    if not isinstance(url, str):
        raise SsoError(f"The identity provider's discovery document has no {what}.")
    parts = urlsplit(url)
    if parts.scheme == "https" or (
        parts.scheme == "http" and (parts.hostname in _LOCAL_HOSTS or settings.oidc_allow_http)
    ):
        if parts.hostname:
            return url
    raise SsoError(f"The identity provider's {what} is not a usable https URL.")


# ------------------------------------------------------------------ HTTP


def _client() -> httpx.AsyncClient:
    # Redirects aren't followed: an IdP endpoint answers itself.
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False)


async def _get_json(url: str, what: str, headers: Optional[dict] = None) -> dict:
    try:
        async with _client() as client:
            response = await client.get(url, headers={"Accept": "application/json", **(headers or {})})
    except httpx.HTTPError as e:
        logger.warning(f"SSO: fetching the {what} from {url} failed: {e!r}")
        raise SsoError(f"The identity provider could not be reached ({what}).") from e
    if response.status_code != 200:
        logger.warning(f"SSO: the {what} at {url} answered {response.status_code}")
        raise SsoError(f"The identity provider's {what} answered {response.status_code}.")
    try:
        body = response.json()
    except ValueError as e:
        raise SsoError(f"The identity provider's {what} is not JSON.") from e
    if not isinstance(body, dict):
        raise SsoError(f"The identity provider's {what} is not a JSON object.")
    return body


# ------------------------------------------------------------------ discovery + JWKS

_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, list[dict]]] = {}  # uri -> (fetched at, keys)


def clear_caches() -> None:
    _discovery_cache.clear()
    _jwks_cache.clear()


async def discovery(issuer: str) -> dict:
    """The provider's metadata (OpenID Connect Discovery 1.0), cached."""
    cached = _discovery_cache.get(issuer)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    doc = await _get_json(f"{issuer.rstrip('/')}/.well-known/openid-configuration", "discovery document")
    # The document must be about this issuer (§4.3): only a trailing slash may differ.
    if not isinstance(doc.get("issuer"), str) or doc["issuer"].rstrip("/") != issuer.rstrip("/"):
        raise SsoError("The identity provider's discovery document is for a different issuer.")
    for what, key in (("authorization endpoint", "authorization_endpoint"),
                      ("token endpoint", "token_endpoint"), ("key set", "jwks_uri")):
        _usable_url(doc.get(key), what)
    if doc.get("userinfo_endpoint") is not None:
        _usable_url(doc["userinfo_endpoint"], "userinfo endpoint")
    _discovery_cache[issuer] = (time.monotonic() + CACHE_SECONDS, doc)
    return doc


async def _jwks(jwks_uri: str, *, refetch: bool = False) -> list[dict]:
    """The provider's signing keys, cached. `refetch` (a kid not in the
    cached set: the IdP rotated) fetches them again, unless that happened
    under a minute ago."""
    cached = _jwks_cache.get(jwks_uri)
    now = time.monotonic()
    if cached and now - cached[0] < (JWKS_REFETCH_SECONDS if refetch else CACHE_SECONDS):
        return cached[1]
    body = await _get_json(jwks_uri, "key set")
    keys = [k for k in body.get("keys") or [] if isinstance(k, dict)]
    _jwks_cache[jwks_uri] = (now, keys)
    return keys


def _pick_key(keys: list[dict], kid: Optional[str], alg: str) -> Optional[dict]:
    kty = "EC" if alg.startswith("ES") else "RSA"
    usable = [
        k for k in keys
        if k.get("kty") == kty and k.get("use", "sig") == "sig" and k.get("alg") in (None, alg)
    ]
    if kid is not None:
        return next((k for k in usable if k.get("kid") == kid), None)
    return usable[0] if len(usable) == 1 else None


# ------------------------------------------------------------------ the code exchange


@dataclass
class TokenSet:
    id_token: str
    access_token: Optional[str]


async def exchange_code(
    db: AsyncSession, provider: IdentityProvider, doc: dict, code: str, code_verifier: str
) -> TokenSet:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(provider.name),
        "code_verifier": code_verifier,
    }
    headers = {"Accept": "application/json"}
    secret = await client_secret(db, provider)
    if secret is None:
        data["client_id"] = provider.client_id  # a public client
    else:
        methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
        if "client_secret_basic" in methods:
            # RFC 6749 §2.3.1: form-encode both parts before base64.
            pair = f"{quote(provider.client_id, safe='')}:{quote(secret, safe='')}"
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        else:
            data["client_id"] = provider.client_id
            data["client_secret"] = secret
    try:
        async with _client() as client:
            response = await client.post(doc["token_endpoint"], data=data, headers=headers)
    except httpx.HTTPError as e:
        logger.warning(f"SSO {provider.name}: token request failed: {e!r}")
        raise SsoError("The identity provider could not be reached to finish signing in.") from e
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or not isinstance(body, dict):
        # The error code only: descriptions can echo request details.
        error = body.get("error") if isinstance(body, dict) else None
        logger.warning(f"SSO {provider.name}: token endpoint answered {response.status_code} ({error})")
        raise SsoError("The identity provider refused to finish the sign-in. Try again.")
    if not isinstance(body.get("id_token"), str):
        raise SsoError("The identity provider returned no ID token (is 'openid' among the scopes?).")
    access = body.get("access_token")
    return TokenSet(id_token=body["id_token"], access_token=access if isinstance(access, str) else None)


async def client_secret(db: AsyncSession, provider: IdentityProvider) -> Optional[str]:
    """The client secret from the shared Secret the provider names."""
    if not provider.client_secret:
        return None
    from app.services.connector_service import connector_service

    value = await connector_service._resolve_secret_value(db, provider.client_secret, None)
    if value is None:
        from app.core.secret_refs import describe_secret_ref

        logger.warning(f"SSO {provider.name}: shared {describe_secret_ref(provider.client_secret)} not found")
        raise SsoError("Single sign-on isn't fully set up (its client secret is missing). Tell an administrator.")
    return value


# ------------------------------------------------------------------ the ID token


async def verify_id_token(
    token: str, *, doc: dict, client_id: str, nonce: str, access_token: Optional[str]
) -> dict:
    """The ID token's claims, once its signature (the IdP's published keys),
    issuer, audience, expiry, nonce and at_hash check out."""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise SsoError("The identity provider's ID token is malformed.") from e
    alg = header.get("alg")
    if alg not in ID_TOKEN_ALGORITHMS:
        raise SsoError(f"The ID token is signed with an algorithm Sinas doesn't accept ({alg}).")
    kid = header.get("kid")
    key = _pick_key(await _jwks(doc["jwks_uri"]), kid, alg)
    if key is None:  # rotated: fetch the set again, once
        key = _pick_key(await _jwks(doc["jwks_uri"], refetch=True), kid, alg)
    if key is None:
        raise SsoError("The ID token is signed with a key the identity provider doesn't publish.")
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=client_id,
            issuer=doc["issuer"],
            access_token=access_token,
            options={
                "leeway": CLOCK_SKEW_SECONDS,
                "require_exp": True,
                "require_iat": True,
                "require_iss": True,
                "require_aud": True,
                "require_sub": True,
            },
        )
    except ExpiredSignatureError as e:
        raise SsoError("The identity provider's ID token has expired. Try again.") from e
    except JWTClaimsError as e:
        logger.warning(f"SSO: ID token claims rejected: {e}")
        raise SsoError("The identity provider's ID token isn't meant for this Sinas (issuer or audience).") from e
    except JWTError as e:
        logger.warning(f"SSO: ID token rejected: {e}")
        raise SsoError("The identity provider's ID token could not be verified.") from e
    audiences = claims["aud"] if isinstance(claims.get("aud"), list) else [claims.get("aud")]
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise SsoError("The ID token was issued to another client.")
    if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"], nonce):
        raise SsoError("The ID token doesn't belong to this sign-in (nonce). Try again.")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise SsoError("The identity provider's ID token names no subject.")
    return claims


async def with_userinfo(doc: dict, claims: dict, access_token: Optional[str]) -> dict:
    """Fill in claims the ID token left out from the userinfo endpoint. The
    ID token's own claims win; userinfo for another subject is refused."""
    if not access_token or not doc.get("userinfo_endpoint"):
        return claims
    info = await _get_json(doc["userinfo_endpoint"], "userinfo endpoint", {"Authorization": f"Bearer {access_token}"})
    if info.get("sub") != claims["sub"]:
        raise SsoError("The identity provider's userinfo is for someone else.")
    return _fill_missing(claims, info)


def _fill_missing(claims: dict, extra: dict) -> dict:
    """`claims` with what only `extra` has added, at every depth: an ID
    token holding realm_access without roles still gets userinfo's
    realm_access.roles. A value the ID token has is never replaced."""
    merged = dict(claims)
    for key, value in extra.items():
        if key not in merged:
            merged[key] = value
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _fill_missing(merged[key], value)
    return merged


# ------------------------------------------------------------------ claims

_MISSING = object()


def _lookup(value: Any, segments: list[str]) -> Any:
    """Walk `segments` into nested objects, taking the longest run of
    segments that is a key at each level first, so keys holding dots
    (https://example.com/roles) need no escaping."""
    if not segments:
        return value
    if not isinstance(value, dict):
        return _MISSING
    for end in range(len(segments), 0, -1):
        key = ".".join(segments[:end])
        if key in value:
            found = _lookup(value[key], segments[end:])
            if found is not _MISSING:
                return found
    return _MISSING


def claim_values(claims: dict, path: str) -> Optional[list[str]]:
    """The strings at a claim path: a list's string items, or a string as a
    one-element list. None when the path isn't there at all."""
    found = _lookup(claims, path.split("."))
    if found is _MISSING or found is None:
        return None
    if isinstance(found, str):
        return [found]
    if isinstance(found, list):
        return [v for v in found if isinstance(v, str)]
    return []


def email_verified(claims: dict) -> Optional[bool]:
    """True / False as the IdP says (some send the strings), None if it
    doesn't say."""
    value = claims.get("email_verified")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _has_group_rules(provider: IdentityProvider) -> bool:
    return any(rule.get("group") is not None for rule in provider.role_mappings or [])


def needs_userinfo(provider: IdentityProvider, claims: dict) -> bool:
    return (
        claim_values(claims, provider.email_claim) is None
        or (_has_group_rules(provider) and claim_values(claims, provider.groups_claim) is None)
        or any(
            _lookup(claims, str(m.get("claim") or "").split(".")) is _MISSING
            for m in provider.claim_mappings or []
        )
    )


# ------------------------------------------------------------------ claim mappings

# A copied string longer than this, or a list longer than MAX_CLAIM_ITEMS, is
# not copied (never cut: a cut value is a wrong value).
MAX_CLAIM_VALUE_LENGTH = 1024
MAX_CLAIM_ITEMS = 50
_SCALAR = (str, int, float, bool)


def mapped_claim_value(claims: dict, path: str) -> Any:
    """The value a claim mapping copies: a string, number or boolean; a list
    of them (a one-item list as its item, so a single `organization` in a
    list claim reads as that organization). None — the field is removed —
    when the claim is absent, empty, an object, or too long."""
    found = _lookup(claims, path.split("."))
    if found is _MISSING or found is None:
        return None
    if isinstance(found, list):
        if not found or len(found) > MAX_CLAIM_ITEMS or not all(isinstance(v, _SCALAR) for v in found):
            return None
        items = [_scalar(v) for v in found]
        if any(v is None for v in items):
            return None
        return items[0] if len(items) == 1 else items
    return _scalar(found) if isinstance(found, _SCALAR) else None


def _scalar(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value if value and len(value) <= MAX_CLAIM_VALUE_LENGTH else None
    return value


def session_context(provider: IdentityProvider, claims: dict) -> dict[str, Any]:
    """The session context this login's `target: session` claim mappings
    give: each mapped claim's value (a string, number or boolean; a one-item
    list as its item), for the tokens of this login only."""
    from app.providers.attribution import validate_context

    context: dict[str, Any] = {}
    for mapping in provider.claim_mappings or []:
        if mapping.get("target") != "session" or not mapping.get("claim") or not mapping.get("field"):
            continue
        value = mapped_claim_value(claims, mapping["claim"])
        found = _lookup(claims, mapping["claim"].split("."))
        present = found is not _MISSING and not (found is None or (isinstance(found, (str, list)) and not found))
        if value is None and present:
            # Sent, but not as a usable value (an object, an overlong string):
            # refused like an invalid one, never read as absent.
            raise SsoError(
                f"{provider.display_name} sent {mapping['claim']}, which says what this session "
                "is for, in a form Sinas can't use."
            )
        if isinstance(value, list):
            raise SsoError(
                f"{provider.display_name} sent several values for {mapping['claim']}, which says "
                "what this session is for; it must be one."
            )
        if value is not None:
            context[mapping["field"]] = value
    # A value that can't be a context is refused, never dropped: dropping it
    # would sign the session in for no organization, or another one.
    try:
        return validate_context(context)
    except ValueError as e:
        raise SsoError(f"{provider.display_name} sent a session claim Sinas can't use: {e}")


def sync_custom_fields(
    user: User, provider: IdentityProvider, claims: dict, previous: Optional[dict] = None
) -> dict[str, Any]:
    """Make the custom fields this provider maps match the claims of this login.

    A mapped field is the identity provider's: set to the claim's value, and
    removed when the claim is absent, so a value the IdP no longer sends
    doesn't linger. Fields no mapping names are never touched, whoever set
    them (an admin, token exchange). A field this provider set at its last
    login (`previous`, kept on the identity) whose mapping was removed since
    is removed too, unless its value changed since (then someone else owns
    it now). Returns what this login set, to keep as the next `previous`."""
    mappings = [
        m for m in provider.claim_mappings or []
        if m.get("claim") and m.get("field") and m.get("target") != "session"
    ]
    if not mappings and not previous:
        return {}
    current = dict(user.custom_fields or {})
    fields = dict(current)
    now: dict[str, Any] = {}
    for mapping in mappings:
        value = mapped_claim_value(claims, mapping["claim"])
        if value is None:
            fields.pop(mapping["field"], None)
        else:
            fields[mapping["field"]] = now[mapping["field"]] = value
    mapped = {m["field"] for m in mappings}
    for field, value in (previous or {}).items():
        if field not in mapped and field in fields and fields[field] == value:
            del fields[field]
    if fields != current:
        user.custom_fields = fields
        changed = sorted(k for k in set(fields) | set(current) if fields.get(k) != current.get(k))
        logger.info(f"SSO {provider.name}: custom fields of user {user.id} synced: {changed}")
    return now


# ------------------------------------------------------------------ who signs in


@dataclass
class SignIn:
    user: User
    provisioned: bool = False
    linked_by_email: bool = False
    roles_added: list[str] = field(default_factory=list)
    roles_removed: list[str] = field(default_factory=list)
    # The session context of this login (session claim mappings).
    context: dict[str, Any] = field(default_factory=dict)


def is_superadmin(email: Optional[str]) -> bool:
    return bool(email and settings.superadmin_email) and normalize_email(email) == normalize_email(
        settings.superadmin_email
    )


def _domain(email: Optional[str]) -> Optional[str]:
    return email.rsplit("@", 1)[1] if email and "@" in email else None


async def sign_in(db: AsyncSession, provider: IdentityProvider, issuer: str, claims: dict) -> SignIn:
    """Find (or create) the Sinas user these verified claims describe, link
    the identity, and sync the roles this provider grants. In the caller's
    transaction; nothing commits here."""
    subject = claims["sub"]
    name = provider.display_name
    values = claim_values(claims, provider.email_claim) or []
    raw_email = next((v for v in values if "@" in v), None)
    email = normalize_email(raw_email) if raw_email else None
    verified = email_verified(claims)
    # An email the IdP itself calls unverified proves nothing: it doesn't
    # satisfy allowedDomains or match an emailDomain rule.
    trusted_domain = _domain(email) if verified is not False else None

    allowed = provider.allowed_domains or []
    if allowed and trusted_domain not in allowed:
        if email and verified is False:
            raise SsoError(f"{name} says your email address isn't verified, so it can't be used here.")
        raise SsoError(f"Signing in with {name} isn't open to your email domain.")

    provisioned = linked_by_email = False
    user = await get_user_by_identity(db, issuer, subject)
    if user is not None:
        if not user.is_active:
            raise SsoError("This account is deactivated. Contact your administrator.")
    elif not email:
        raise SsoError(f"{name} didn't share an email address, so no Sinas account can be found for you.")
    else:
        existing = await get_user_by_email(db, email)
        if existing is not None:
            if not existing.is_active:
                raise SsoError("This account is deactivated. Contact your administrator.")
            if verified is not True:
                # The account-takeover hole this closes: anyone able to put
                # this address on an IdP account would get the Sinas one.
                raise SsoError(
                    f"A Sinas account with this email exists, but {name} doesn't confirm the address "
                    "is verified, so they can't be linked. Ask an administrator to link your account."
                )
            if is_superadmin(email):
                raise SsoError(
                    "The superadmin account is never linked by email. Link its identity explicitly, "
                    "or sign in with a password or code."
                )
            # Locked until the caller commits: two first sign-ins with
            # different subjects for this address would otherwise both find
            # no identity at this issuer and both link. The second waits
            # here, then sees the first's identity.
            await db.execute(
                select(User.id).where(User.id == existing.id).with_for_update()
            )
            other = (
                await db.execute(
                    select(UserIdentity.id).where(
                        UserIdentity.user_id == existing.id, UserIdentity.provider == issuer
                    )
                )
            ).first()
            if other is not None:
                raise SsoError(f"This Sinas account is already linked to another {name} identity.")
            user, linked_by_email = existing, True
        else:
            if not provider.auto_provision:
                raise SsoError("There is no Sinas account for your email address. Ask an administrator for access.")
            if verified is False:
                raise SsoError(f"{name} says your email address isn't verified, so no account is created for it.")
            user = User(email=email)
            db.add(user)
            await db.flush()
            provisioned = True
            if provider.default_role:
                role = await Role.get_by_name(db, provider.default_role)
                if role is None:
                    logger.warning(f"SSO {provider.name}: default role '{provider.default_role}' not found")
                else:
                    # Given once, like a role granted by hand: no sync takes it away.
                    db.add(UserRole(role_id=role.id, user_id=user.id, active=True))

    previous_metadata = (
        await db.execute(
            select(UserIdentity.identity_metadata).where(
                UserIdentity.provider == issuer, UserIdentity.subject == subject
            )
        )
    ).scalar_one_or_none() or {}
    previous_fields = previous_metadata.get("claim_fields") if isinstance(previous_metadata, dict) else None
    claim_fields = sync_custom_fields(
        user, provider, claims, previous_fields if isinstance(previous_fields, dict) else None
    )
    await link_user_identity(
        db, user, issuer, subject,
        {
            "identity_provider": provider.name,
            "email": email,
            "email_verified": verified,
            **{k: claims[k] for k in ("name", "preferred_username") if isinstance(claims.get(k), str)},
            # What the claim mappings set at this login (sync_custom_fields).
            **({"claim_fields": claim_fields} if claim_fields else {}),
        },
    )
    groups = set(claim_values(claims, provider.groups_claim) or [])
    added, removed = await sync_roles(db, user, provider, groups, trusted_domain)

    from app.providers.attribution import membership_problem

    context = session_context(provider, claims)
    problem = membership_problem(context, user.custom_fields)
    if problem:
        raise SsoError(f"{problem}, so {name} can't sign you in for it.")

    user.last_login_at = datetime.now(UTC)
    await db.flush()
    return SignIn(user, provisioned, linked_by_email, added, removed, context)


def mapped_role_names(provider: IdentityProvider, groups: set[str], email_domain: Optional[str]) -> set[str]:
    names: set[str] = set()
    for rule in provider.role_mappings or []:
        group, domain = rule.get("group"), rule.get("email_domain")
        if (group is not None and group in groups) or (domain is not None and domain == email_domain):
            names.update(rule.get("roles") or [])
    return names


async def sync_roles(
    db: AsyncSession, user: User, provider: IdentityProvider, groups: set[str], email_domain: Optional[str]
) -> tuple[list[str], list[str]]:
    """Make the memberships this provider granted match what it maps now.

    Grants each mapped role the user doesn't hold actively (tagged with the
    provider), and ends the provider's own memberships that no longer map.
    A membership given by hand, by config or by another provider is never
    touched. Returns (added, removed) role names."""
    tag = source_tag(provider.name)
    wanted = mapped_role_names(provider, groups, email_domain)
    roles = {}
    if wanted:
        roles = {
            role_id: role_name
            for role_id, role_name in (
                await db.execute(select(Role.id, Role.name).where(Role.name.in_(wanted)))
            ).all()
        }
        missing = wanted - set(roles.values())
        if missing:
            logger.warning(f"SSO {provider.name}: mapped roles not found, skipped: {sorted(missing)}")

    # One sync per user at a time: two logins at once would both see a role
    # missing and both add it.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    memberships = (
        await db.execute(select(UserRole).where(UserRole.user_id == user.id))
    ).scalars().all()
    now = datetime.now(UTC)
    added: list[str] = []
    removed: list[str] = []
    for role_id, role_name in roles.items():
        held = [m for m in memberships if m.role_id == role_id]
        if any(m.active for m in held):
            continue  # held already, whoever granted it
        if held:  # ended before: reactivate the most recent row, as this provider's
            latest = max(held, key=lambda m: m.added_at or now)
            latest.active, latest.removed_at, latest.removed_by = True, None, None
            latest.added_by, latest.source = None, tag
        else:
            db.add(UserRole(user_id=user.id, role_id=role_id, active=True, source=tag))
        added.append(role_name)
    ended = [m for m in memberships if m.active and m.source == tag and m.role_id not in roles]
    if ended:
        names = dict(
            (await db.execute(select(Role.id, Role.name).where(Role.id.in_([m.role_id for m in ended])))).all()
        )
        for membership in ended:
            membership.active, membership.removed_at = False, now
            removed.append(names.get(membership.role_id, str(membership.role_id)))
    if added or removed:
        logger.info(
            f"SSO {provider.name}: roles of user {user.id} synced: added {sorted(added)}, removed {sorted(removed)}"
        )
    return sorted(added), sorted(removed)


# ------------------------------------------------------------------ enforcement


async def active_providers(db: AsyncSession) -> list[IdentityProvider]:
    return list(
        (
            await db.execute(
                select(IdentityProvider).where(IdentityProvider.is_active.is_(True)).order_by(IdentityProvider.name)
            )
        ).scalars()
    )


async def enforcing_provider(db: AsyncSession, email: Optional[str]) -> Optional[IdentityProvider]:
    """The active provider this email's domain must sign in with, if any.
    Never for the superadmin: the operator can't be locked out."""
    domain = _domain(normalize_email(email)) if email else None
    if domain is None or is_superadmin(email):
        return None
    for provider in await active_providers(db):
        if domain in (provider.enforce_for_domains or []):
            return provider
    return None


def enforced_message(provider: IdentityProvider, email: str) -> str:
    return f"Accounts at {_domain(normalize_email(email))} sign in with {provider.display_name}."


@dataclass(frozen=True)
class RefreshSession:
    """What a refresh token says about its session."""

    identity_provider: Optional[str]  # the SSO login that issued it, if any
    expires_at: datetime  # for an SSO session: its hard end
    context: Optional[dict] = None  # the session's attribution context


async def refresh_session(db: AsyncSession, plain_token: str) -> Optional[RefreshSession]:
    token_hash = hashlib.sha256(plain_token.encode()).hexdigest()
    row = (
        await db.execute(
            select(RefreshToken.identity_provider, RefreshToken.expires_at, RefreshToken.context).where(
                RefreshToken.token_hash == token_hash
            )
        )
    ).first()
    return RefreshSession(row[0], row[1], row[2]) if row else None


async def refresh_blocked(db: AsyncSession, email: str, session: Optional[RefreshSession]) -> Optional[str]:
    """Why a refresh token may no longer be used: it came from a password or
    code login, and the user's domain now has to use SSO."""
    provider = await enforcing_provider(db, email)
    if provider is None:
        return None
    return None if session and session.identity_provider else enforced_message(provider, email)


def access_token_lifetime(session: Optional[RefreshSession]) -> Optional[timedelta]:
    """An access token minted for an SSO session ends with the session: a
    refresh just before the deadline must not stretch it by a full access
    token lifetime. None: the usual lifetime."""
    if session is None or not session.identity_provider:
        return None
    usual = timedelta(minutes=settings.access_token_expire_minutes)
    left = session.expires_at - datetime.now(UTC)
    return max(left, timedelta(seconds=1)) if left < usual else None


# ------------------------------------------------------------------ login state (Redis)


@dataclass
class LoginState:
    provider: str
    nonce: str
    code_verifier: str
    browser_nonce: str
    console_challenge: str
    return_to: str


async def store_state(state: LoginState) -> str:
    """Keep the flow's secrets under a fresh opaque state; returns it."""
    key = secrets.token_urlsafe(32)
    redis = await get_redis()
    await redis.set(_STATE_PREFIX + key, json.dumps(state.__dict__), ex=STATE_TTL_SECONDS)
    return key


async def consume_state(key: str) -> Optional[LoginState]:
    """The flow for a state, once: GETDEL makes it single-use even when two
    callbacks race."""
    if not key or len(key) > 128:
        return None
    raw = await (await get_redis()).getdel(_STATE_PREFIX + key)
    if raw is None:
        return None
    try:
        return LoginState(**json.loads(raw))
    except (ValueError, TypeError):
        return None


async def store_code(
    *, user_id: str, provider: str, session_hours: int, console_challenge: str, return_to: str,
    context: Optional[dict] = None,
) -> str:
    """The one-time code the console trades for tokens. Only who signed in
    (and the session context of the login) is kept; the tokens are minted at
    the exchange."""
    code = secrets.token_urlsafe(32)
    payload = {
        "user_id": user_id, "provider": provider, "session_hours": session_hours,
        "console_challenge": console_challenge, "return_to": return_to,
        "context": context or None,
    }
    await (await get_redis()).set(_CODE_PREFIX + code, json.dumps(payload), ex=CODE_TTL_SECONDS)
    return code


async def consume_code(code: str, code_verifier: str) -> Optional[dict]:
    """The sign-in for a one-time code, once, and only for the console tab
    holding the verifier of the challenge it started with."""
    raw = await (await get_redis()).getdel(_CODE_PREFIX + code)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not secrets.compare_digest(pkce_challenge(code_verifier), payload.get("console_challenge") or ""):
        return None
    return payload


_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


def valid_challenge(value: Optional[str]) -> bool:
    return bool(value and _CHALLENGE.match(value))


def authorization_url(provider: IdentityProvider, doc: dict, *, state: str, nonce: str, code_verifier: str) -> str:
    params = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri(provider.name),
        "scope": " ".join(provider.scopes or ["openid"]),
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    endpoint = doc["authorization_endpoint"]
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{urlencode(params)}"
