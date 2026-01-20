"""Authentication endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import timedelta

from app.core.database import get_db
from app.core.auth import (
    create_otp_session,
    verify_otp_code,
    get_or_create_user,
    get_user_permissions,
    create_access_token,
    create_refresh_token,
    validate_refresh_token,
    revoke_refresh_token,
    get_current_user,
    get_current_user_with_permissions,
    set_permission_used,
)
from app.core.config import settings
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    OTPVerifyRequest,
    OTPVerifyResponse,
    ExternalAuthRequest,
    RefreshRequest,
    RefreshResponse,
    LogoutRequest,
    UserResponse,
    PermissionCheckRequest,
    PermissionCheckResponse,
    PermissionCheckResult,
)

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Initiate login by sending OTP to email.

    Creates user if doesn't exist (when auto_provision_users=true).
    Fails fast if user doesn't exist and auto-provisioning is disabled.
    """
    # Check if user exists when auto-provisioning is disabled
    if not settings.auto_provision_users:
        from app.core.auth import normalize_email
        result = await db.execute(
            select(User).where(User.email == normalize_email(request.email))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User not found and auto-provisioning is disabled"
            )

    # Create OTP session and send email
    try:
        otp_session = await create_otp_session(db, request.email)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to send OTP email: {str(e)}"
        )

    return LoginResponse(
        message="OTP sent to your email",
        session_id=otp_session.id
    )


@router.post("/verify-otp", response_model=OTPVerifyResponse)
async def verify_otp(
    request: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Verify OTP and return access + refresh tokens.

    Returns short-lived access token (15 min) and long-lived refresh token (30 days).
    """
    # Verify OTP
    otp_session = await verify_otp_code(db, str(request.session_id), request.otp_code)

    if not otp_session:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP"
        )

    # Get or create user
    user = await get_or_create_user(db, otp_session.email, assign_to_users_group=True)

    # Create access token (short-lived, no permissions in payload)
    access_token = create_access_token(
        user_id=str(user.id),
        email=user.email
    )

    # Create refresh token (long-lived, stored in DB)
    refresh_token_plain, _ = await create_refresh_token(db, str(user.id))

    return OTPVerifyResponse(
        access_token=access_token,
        refresh_token=refresh_token_plain,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,  # Convert to seconds
        user=UserResponse.model_validate(user)
    )


@router.post("/external-auth", response_model=OTPVerifyResponse)
async def exchange_external_token(
    request: ExternalAuthRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Exchange external OIDC token for SINAS JWT.

    Only works if EXTERNAL_AUTH_ENABLED=true and OIDC_ISSUER configured.
    """
    from app.services.auth import get_oidc_provider
    from app.services.auth.group_sync import sync_user_groups

    # Get OIDC provider
    provider = get_oidc_provider()
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="External authentication not configured"
        )

    # Validate external token
    user_info = await provider.validate_token(request.token)
    if not user_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid external token"
        )

    # Get or create user
    result = await db.execute(
        select(User).where(User.external_user_id == user_info["external_user_id"])
    )
    user = result.scalar_one_or_none()

    if user:
        # Update existing user metadata
        user.external_metadata = user_info.get("metadata")
        user.email = user_info["email"]
    else:
        # Try linking by email
        result = await db.execute(
            select(User).where(User.email == user_info["email"])
        )
        user = result.scalar_one_or_none()

        if user:
            # Link existing OTP user to external auth
            user.external_user_id = user_info["external_user_id"]
            user.external_metadata = user_info.get("metadata")
        else:
            # Create new user
            if not settings.auto_provision_users:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User auto-provisioning disabled"
                )

            user = User(
                email=user_info["email"],
                external_user_id=user_info["external_user_id"],
                external_metadata=user_info.get("metadata")
            )
            db.add(user)
            await db.flush()

    # Sync groups based on external groups
    await sync_user_groups(db, user, user_info.get("external_groups", []))

    await db.commit()

    # Create access token (short-lived, no permissions in payload)
    access_token = create_access_token(
        user_id=str(user.id),
        email=user.email
    )

    # Create refresh token (long-lived, stored in DB)
    refresh_token_plain, _ = await create_refresh_token(db, str(user.id))

    return OTPVerifyResponse(
        access_token=access_token,
        refresh_token=refresh_token_plain,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserResponse.model_validate(user)
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_access_token(
    request: RefreshRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Refresh access token using refresh token.

    Exchange a valid refresh token for a new short-lived access token.
    Refresh token remains valid and can be reused until expiry or logout.
    """
    # Validate refresh token
    result = await validate_refresh_token(db, request.refresh_token)

    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )

    user_id, email = result

    # Create new access token
    access_token = create_access_token(
        user_id=user_id,
        email=email
    )

    return RefreshResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: LogoutRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Logout by revoking refresh token.

    After logout, the refresh token can no longer be used to get new access tokens.
    Existing access tokens remain valid until they expire (max 15 minutes).
    """
    success = await revoke_refresh_token(db, request.refresh_token)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Refresh token not found"
        )

    return None


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    request: Request,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current authenticated user info."""
    set_permission_used(request, "sinas.users.get:own")

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    return UserResponse.model_validate(user)


@router.post("/check-permissions", response_model=PermissionCheckResponse)
async def check_permissions(
    request: Request,
    check_request: PermissionCheckRequest,
    current_user_data = Depends(get_current_user_with_permissions)
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
    from app.core.permissions import check_permission

    user_id, permissions = current_user_data

    # Check each permission
    checks = []
    for perm in check_request.permissions:
        has_perm = check_permission(permissions, perm)
        checks.append(PermissionCheckResult(
            permission=perm,
            has_permission=has_perm
        ))

    # Calculate overall result based on logic
    if check_request.logic == "AND":
        result = all(check.has_permission for check in checks)
    else:  # OR
        result = any(check.has_permission for check in checks)

    return PermissionCheckResponse(
        result=result,
        logic=check_request.logic,
        checks=checks
    )
