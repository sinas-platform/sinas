"""OIDC-compatible verification endpoints: JWKS + userinfo (#101).

Not a full OIDC IdP — no authorize/consent/client registration. Just enough
for external resource servers to verify Sinas access tokens with standard
JWT middleware: publish the RS256 public key set, and expose the user's
claims under OIDC-standard names.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import set_permission_used, verify_jwt_or_api_key
from app.core.database import get_db
from app.core.token_signing import jwks
from app.models import User
from app.models.user import Role, UserRole

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/.well-known/jwks.json", include_in_schema=False)
async def jwks_endpoint():
    """Published signing keys (RFC 7517). Unauthenticated by design.

    Only meaningful under JWT_ALGORITHM=RS256 — an HMAC secret must never be
    published, so HS256 deployments get a 404 with a pointer instead of an
    empty key set that standard libraries would treat as "no valid keys".
    """
    key_set = jwks()
    if key_set is None:
        raise HTTPException(
            status_code=404,
            detail="JWKS is only published when JWT_ALGORITHM=RS256",
        )
    return key_set


async def _userinfo_claims(user_id: str, db: AsyncSession) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")

    roles_result = await db.execute(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id, UserRole.active == True)
    )

    claims = {
        "sub": str(user.id),
        "email": user.email,
        "roles": [r[0] for r in roles_result.all()],
    }
    # Org-specific profile data becomes plain claims, but must never shadow
    # the standard ones — a custom field named "sub" would otherwise let a
    # token-exchange partner rewrite the user's identity as seen downstream.
    for key, value in (user.custom_fields or {}).items():
        claims.setdefault(key, value)
    return claims


@router.get("/userinfo")
@router.post("/userinfo")
async def userinfo(
    request: Request,
    auth_data: tuple = Depends(verify_jwt_or_api_key),
    db: AsyncSession = Depends(get_db),
):
    """OIDC-style userinfo: same data as /auth/me, standard claim names.

    GET and POST both accepted per OIDC Core 5.3.1. Bearer token in the
    Authorization header (API keys work too, like everywhere else).
    """
    user_id, _, _ = auth_data
    set_permission_used(request, "sinas.users.read:own")
    return await _userinfo_claims(user_id, db)
