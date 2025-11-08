"""Authentication endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from datetime import timedelta

from app.core.database import get_db
from app.core.auth import (
    create_otp_session,
    verify_otp_code,
    get_or_create_user,
    get_user_permissions,
    create_access_token,
    create_api_key,
    get_current_user,
    set_permission_used,
)
from app.core.config import settings
from app.models import User, APIKey
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    OTPVerifyRequest,
    OTPVerifyResponse,
    UserResponse,
    APIKeyCreate,
    APIKeyResponse,
    APIKeyCreatedResponse,
)

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Initiate login by sending OTP to email.

    Creates user if doesn't exist and assigns to "Users" group.
    """
    # Create OTP session and send email
    otp_session = await create_otp_session(db, request.email)

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
    Verify OTP and return access token.
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

    # Get user's permissions
    permissions = await get_user_permissions(db, str(user.id))

    # Create access token
    access_token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        permissions=permissions,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes)
    )

    return OTPVerifyResponse(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse.model_validate(user)
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    request: Request,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current authenticated user info."""
    set_permission_used(request, "sinas.users.read:own")

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


@router.post("/api-keys", response_model=APIKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_user_api_key(
    api_key_request: APIKeyCreate,
    request: Request,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new API key for the current user.

    API key permissions must be a subset of user's group permissions.
    """
    set_permission_used(request, "sinas.api_keys.create:own")

    # Get user
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Create API key
    api_key, plain_key = await create_api_key(
        db=db,
        user=user,
        name=api_key_request.name,
        permissions=api_key_request.permissions,
        expires_at=api_key_request.expires_at,
        created_by=user
    )

    return APIKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        permissions=api_key.permissions,
        is_active=api_key.is_active,
        last_used_at=api_key.last_used_at,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
        api_key=plain_key
    )


@router.get("/api-keys", response_model=List[APIKeyResponse])
async def list_api_keys(
    request: Request,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all API keys for the current user."""
    set_permission_used(request, "sinas.api_keys.read:own")

    result = await db.execute(
        select(APIKey).where(APIKey.user_id == user_id).order_by(APIKey.created_at.desc())
    )
    api_keys = result.scalars().all()

    return [APIKeyResponse.model_validate(key) for key in api_keys]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Revoke (deactivate) an API key."""
    set_permission_used(request, "sinas.api_keys.delete:own")

    result = await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user_id)
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )

    api_key.is_active = False
    await db.commit()

    return None
