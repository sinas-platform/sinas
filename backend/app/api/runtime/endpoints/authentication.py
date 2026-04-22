"""Authentication endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    create_access_token,
    create_otp_session,
    create_refresh_token,
    get_current_user,
    get_current_user_with_permissions,
    get_user_by_email,
    normalize_email,
    revoke_refresh_token,
    set_permission_used,
    validate_refresh_token,
    verify_otp_code,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import check_permission
from app.core.rate_limit import rate_limit_by_ip, rate_limit_by_value
from app.models import User
from app.models.user import Role, UserRole
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    OTPVerifyRequest,
    OTPVerifyResponse,
    PermissionCheckRequest,
    PermissionCheckResponse,
    PermissionCheckResult,
    RefreshRequest,
    RefreshResponse,
    AuthUserResponse,
)

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, http_request: Request, db: AsyncSession = Depends(get_db)):
    """
    Initiate login by sending OTP to email.

    User must exist - users are created by admins only.
    """
    await rate_limit_by_ip(http_request, "login", settings.rate_limit_login_ip_max, settings.rate_limit_window_seconds)
    await rate_limit_by_value(request.email, "login:email", settings.rate_limit_login_email_max, settings.rate_limit_window_seconds)

    # Check if user exists - no auto-provisioning
    result = await db.execute(select(User).where(User.email == normalize_email(request.email)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not found. Contact your administrator.",
        )

    # Create OTP session and send email
    try:
        otp_session = await create_otp_session(db, request.email)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to send OTP email: {str(e)}",
        )

    return LoginResponse(message="OTP sent to your email", session_id=otp_session.id)


@router.post("/verify-otp", response_model=OTPVerifyResponse)
async def verify_otp(request: OTPVerifyRequest, http_request: Request, db: AsyncSession = Depends(get_db)):
    """
    Verify OTP and return access + refresh tokens.

    Returns short-lived access token (15 min) and long-lived refresh token (30 days).
    """
    await rate_limit_by_ip(http_request, "verify_otp", settings.rate_limit_otp_ip_max, settings.rate_limit_window_seconds)

    # Verify OTP
    otp_session = await verify_otp_code(db, str(request.session_id), request.otp_code)

    if not otp_session:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP"
        )

    # Get user (must exist - checked during login)
    user = await get_user_by_email(db, otp_session.email)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not found. Contact your administrator.",
        )

    # Create access token (short-lived, no permissions in payload)
    access_token = create_access_token(user_id=str(user.id), email=user.email)

    # Create refresh token (long-lived, stored in DB)
    refresh_token_plain, _ = await create_refresh_token(db, str(user.id))

    # Get user's roles
    roles_result = await db.execute(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id, UserRole.active == True)
    )
    role_names = [r[0] for r in roles_result.all()]

    user_resp = AuthUserResponse.model_validate(user)
    user_resp.roles = role_names

    return OTPVerifyResponse(
        access_token=access_token,
        refresh_token=refresh_token_plain,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
        user=user_resp,
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_access_token(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """
    Refresh access token using refresh token.

    Exchange a valid refresh token for a new short-lived access token.
    Refresh token remains valid and can be reused until expiry or logout.
    """
    # Validate refresh token
    result = await validate_refresh_token(db, request.refresh_token)

    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        )

    user_id, email = result

    # Create new access token
    access_token = create_access_token(user_id=user_id, email=email)

    return RefreshResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: LogoutRequest, db: AsyncSession = Depends(get_db)):
    """
    Logout by revoking refresh token.

    After logout, the refresh token can no longer be used to get new access tokens.
    Existing access tokens remain valid until they expire (max 15 minutes).
    """
    success = await revoke_refresh_token(db, request.refresh_token)

    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Refresh token not found")

    return None


@router.get("/me", response_model=AuthUserResponse)
async def get_current_user_info(
    request: Request, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Get current authenticated user info with roles."""
    set_permission_used(request, "sinas.users.read:own")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Get user's roles (single query with join)
    roles_result = await db.execute(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id, UserRole.active == True)
    )
    role_names = [r[0] for r in roles_result.all()]

    resp = AuthUserResponse.model_validate(user)
    resp.roles = role_names
    return resp


@router.post("/check-permissions", response_model=PermissionCheckResponse)
async def check_permissions(
    request: Request,
    check_request: PermissionCheckRequest,
    current_user_data=Depends(get_current_user_with_permissions),
):
    """
    Check if the authenticated user has specific permission(s).

    Supports AND/OR logic:
    - AND: User must have ALL specified permissions
    - OR: User must have AT LEAST ONE of the specified permissions

    Example request:
    ```json
    {
        "permissions": ["sinas.functions.read:all", "sinas.functions.create:own"],
        "logic": "OR"
    }
    ```
    """
    user_id, permissions = current_user_data

    # Check each permission and log the check
    checks = []
    for perm in check_request.permissions:
        has_perm = check_permission(permissions, perm)
        set_permission_used(request, perm, has_perm=has_perm)
        checks.append(PermissionCheckResult(permission=perm, has_permission=has_perm))

    # Calculate overall result based on logic
    if check_request.logic == "AND":
        result = all(check.has_permission for check in checks)
    else:  # OR
        result = any(check.has_permission for check in checks)

    return PermissionCheckResponse(result=result, logic=check_request.logic, checks=checks)
