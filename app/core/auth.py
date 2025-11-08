"""Authentication and authorization system with OTP, JWT, and API keys."""
import hashlib
import random
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Tuple

from fastapi import HTTPException, Depends, Header, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.email import send_otp_email_async
from app.core.permissions import (
    check_permission,
    expand_permission_dict,
    validate_permission_subset,
    DEFAULT_GROUP_PERMISSIONS,
)
from app.models import (
    User,
    Group,
    GroupMember,
    GroupPermission,
    OTPSession,
    APIKey,
)

security = HTTPBearer()


def normalize_email(email: str) -> str:
    """Normalize email address to lowercase."""
    return email.strip().lower()


def generate_otp_code(length: int = 6) -> str:
    """Generate a random numeric OTP code."""
    return ''.join(random.choices(string.digits, k=length))


async def create_otp_session(db: AsyncSession, email: str) -> OTPSession:
    """
    Create a new OTP session and send the code via email.

    Args:
        db: Database session
        email: User's email address

    Returns:
        Created OTPSession
    """
    otp_code = generate_otp_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.otp_expire_minutes)

    # Delete any existing OTP sessions for this email
    result = await db.execute(
        select(OTPSession).where(OTPSession.email == normalize_email(email))
    )
    existing_sessions = result.scalars().all()
    for session in existing_sessions:
        await db.delete(session)

    # Create new OTP session
    otp_session = OTPSession(
        email=normalize_email(email),
        otp_code=otp_code,
        expires_at=expires_at
    )
    db.add(otp_session)
    await db.commit()
    await db.refresh(otp_session)

    # Send OTP email
    await send_otp_email_async(db, email, otp_code)

    return otp_session


async def verify_otp_code(db: AsyncSession, session_id: str, otp_code: str) -> Optional[OTPSession]:
    """
    Verify an OTP code against a session.

    Args:
        db: Database session
        session_id: OTP session ID
        otp_code: OTP code to verify

    Returns:
        OTPSession if valid, None otherwise
    """
    result = await db.execute(
        select(OTPSession).where(OTPSession.id == session_id)
    )
    otp_session = result.scalar_one_or_none()

    if not otp_session:
        return None

    # Check if expired
    if otp_session.expires_at < datetime.now(timezone.utc):
        return None

    # Check if already verified
    if otp_session.verified:
        return None

    # Check if code matches
    if otp_session.otp_code != otp_code:
        return None

    # Mark as verified
    otp_session.verified = True
    await db.commit()

    return otp_session


async def get_or_create_user(
    db: AsyncSession,
    email: str,
    assign_to_users_group: bool = True
) -> User:
    """
    Get existing user or create new one.

    Args:
        db: Database session
        email: User's email address
        assign_to_users_group: Whether to assign new users to "Users" group

    Returns:
        User object
    """
    normalized_email = normalize_email(email)

    # Try to get existing user
    result = await db.execute(
        select(User).where(User.email == normalized_email)
    )
    user = result.scalar_one_or_none()

    if user:
        return user

    # Create new user
    user = User(email=normalized_email, is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Assign to default group if requested
    if assign_to_users_group:
        result = await db.execute(
            select(Group).where(Group.name == "Users")
        )
        users_group = result.scalar_one_or_none()

        if users_group:
            membership = GroupMember(
                group_id=users_group.id,
                user_id=user.id,
                active=True
            )
            db.add(membership)
            await db.commit()

    return user


async def get_user_permissions(db: AsyncSession, user_id: str) -> Dict[str, bool]:
    """
    Get all permissions for a user by aggregating from their active groups.

    Args:
        db: Database session
        user_id: User's UUID

    Returns:
        Dictionary of permission_key: bool
    """
    # Get all active group memberships
    result = await db.execute(
        select(GroupMember).where(
            GroupMember.user_id == user_id,
            GroupMember.active == True
        )
    )
    memberships = result.scalars().all()

    if not memberships:
        return {}

    # Collect all permissions from all groups
    all_permissions = {}

    for membership in memberships:
        result = await db.execute(
            select(GroupPermission).where(
                GroupPermission.group_id == membership.group_id
            )
        )
        group_permissions = result.scalars().all()

        for perm in group_permissions:
            # Later groups can override earlier ones
            all_permissions[perm.permission_key] = perm.permission_value

    # Expand wildcards to concrete permissions
    expanded = expand_permission_dict(all_permissions)

    return expanded


def create_access_token(
    user_id: str,
    email: str,
    permissions: Dict[str, bool],
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create JWT access token with user info and permissions.

    Args:
        user_id: User UUID
        email: User email
        permissions: Dict of all user permissions
        expires_delta: Optional custom expiration

    Returns:
        Encoded JWT token
    """
    to_encode = {
        "sub": str(user_id),
        "email": email,
        "permissions": permissions
    }

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


# API Key Management

def generate_api_key() -> Tuple[str, str, str]:
    """
    Generate a new API key.

    Returns:
        Tuple of (full_key, key_hash, key_prefix)
    """
    key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    key_prefix = key[:8]
    return key, key_hash, key_prefix


def hash_api_key(key: str) -> str:
    """Hash an API key for comparison."""
    return hashlib.sha256(key.encode()).hexdigest()


async def create_api_key(
    db: AsyncSession,
    user: User,
    name: str,
    permissions: Dict[str, bool],
    expires_at: Optional[datetime] = None,
    created_by: Optional[User] = None
) -> Tuple[APIKey, str]:
    """
    Create a new API key for a user.

    Args:
        db: Database session
        user: User to create key for
        name: Friendly name for the key
        permissions: Permission dict (must be subset of user's permissions)
        expires_at: Optional expiration date
        created_by: User creating the key

    Returns:
        Tuple of (api_key_model, plain_key)

    Raises:
        HTTPException if permissions are not a subset of user's permissions
    """
    # Get user's maximum permissions from groups
    user_permissions = await get_user_permissions(db, str(user.id))

    # Validate that requested permissions are a subset of user permissions
    is_valid, violations = validate_permission_subset(permissions, user_permissions)

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"API key permissions exceed user's group permissions. Violations: {', '.join(violations)}"
        )

    # Generate key
    plain_key, key_hash, key_prefix = generate_api_key()

    # Create API key record
    api_key = APIKey(
        user_id=user.id,
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        permissions=permissions,
        is_active=True,
        expires_at=expires_at,
        created_by=created_by.id if created_by else user.id
    )

    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return api_key, plain_key


async def validate_api_key(db: AsyncSession, key: str) -> Optional[Tuple[User, Dict[str, bool]]]:
    """
    Validate an API key and return the user and permissions.

    Args:
        db: Database session
        key: Plain API key from request

    Returns:
        Tuple of (user, permissions) if valid, None otherwise
    """
    key_hash = hash_api_key(key)

    # Find active API key
    result = await db.execute(
        select(APIKey).where(
            APIKey.key_hash == key_hash,
            APIKey.is_active == True
        )
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        return None

    # Check if expired
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        return None

    # Update last used timestamp
    api_key.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    # Get user
    result = await db.execute(
        select(User).where(User.id == api_key.user_id)
    )
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        return None

    return user, api_key.permissions


# Authentication Dependencies

async def verify_jwt_or_api_key(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> Tuple[str, str, Dict[str, bool]]:
    """
    Verify either JWT token or API key from Authorization header.

    Supports:
    - Bearer <jwt_token>
    - Bearer <api_key>

    Returns:
        Tuple of (user_id, email, permissions)

    Raises:
        HTTPException 401 if authentication fails
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header"
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format"
        )

    token = authorization[7:]  # Remove "Bearer " prefix

    # Try JWT first
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id = payload.get("sub")
        email = payload.get("email")
        permissions = payload.get("permissions", {})

        if not user_id or not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload"
            )

        return str(user_id), email, permissions

    except JWTError:
        # If JWT fails, try API key
        result = await validate_api_key(db, token)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired credentials"
            )

        user, permissions = result
        return str(user.id), user.email, permissions


def require_permission(required_permission: str):
    """
    Dependency factory to require a specific permission.

    Usage:
        @router.post("/chats")
        async def create_chat(
            user_id: str = Depends(require_permission("sinas.chats.create:own"))
        ):
            ...

    Args:
        required_permission: Permission string like "sinas.chats.create:own"

    Returns:
        Dependency function that returns user_id if authorized
    """
    async def permission_checker(
        request: Request,
        auth_data: Tuple[str, str, Dict[str, bool]] = Depends(verify_jwt_or_api_key)
    ) -> str:
        user_id, email, permissions = auth_data
        has_perm = check_permission(permissions, required_permission)

        # Store permission info in request state for logging
        request.state.user_id = user_id
        request.state.user_email = email
        request.state.permission_used = required_permission
        request.state.has_permission = has_perm

        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {required_permission}"
            )

        return user_id

    return permission_checker


async def get_current_user(
    request: Request,
    auth_data: Tuple[str, str, Dict[str, bool]] = Depends(verify_jwt_or_api_key)
) -> str:
    """
    Get current authenticated user ID without requiring specific permission.

    Returns:
        user_id
    """
    user_id, email, _ = auth_data

    # Store user info in request state for logging
    request.state.user_id = user_id
    request.state.user_email = email

    return user_id


async def get_current_user_with_permissions(
    request: Request,
    auth_data: Tuple[str, str, Dict[str, bool]] = Depends(verify_jwt_or_api_key)
) -> Tuple[str, Dict[str, bool]]:
    """
    Get current authenticated user ID and their permissions.

    IMPORTANT: Endpoints using this dependency MUST call
    set_permission_used(request, "permission.key") to log
    the permission for compliance tracking.

    Returns:
        Tuple of (user_id, permissions)
    """
    user_id, email, permissions = auth_data

    # Store user info in request state for logging
    request.state.user_id = user_id
    request.state.user_email = email

    return user_id, permissions


def set_permission_used(request: Request, permission: str, has_perm: bool = True):
    """
    Store permission decision in request state for compliance logging.

    Call this after manual permission checks in endpoint code.

    Example:
        if permissions.get("sinas.functions.read:all"):
            set_permission_used(request, "sinas.functions.read:all")
        elif permissions.get("sinas.functions.read:own"):
            set_permission_used(request, "sinas.functions.read:own")

    Args:
        request: FastAPI Request object
        permission: Permission key that was checked (e.g. "sinas.functions.read:all")
        has_perm: Whether user has the permission (default True)
    """
    request.state.permission_used = permission
    request.state.has_permission = has_perm


# Group initialization helper

async def initialize_default_groups(db: AsyncSession):
    """
    Initialize default groups (GuestUsers, Users, Admins) with permissions.

    Should be called during application startup or setup.
    """
    for group_name, permissions in DEFAULT_GROUP_PERMISSIONS.items():
        # Check if group exists
        result = await db.execute(
            select(Group).where(Group.name == group_name)
        )
        group = result.scalar_one_or_none()

        if not group:
            # Create group
            group = Group(name=group_name, description=f"Default {group_name} group")
            db.add(group)
            await db.commit()
            await db.refresh(group)

        # Update permissions
        for perm_key, perm_value in permissions.items():
            result = await db.execute(
                select(GroupPermission).where(
                    GroupPermission.group_id == group.id,
                    GroupPermission.permission_key == perm_key
                )
            )
            existing_perm = result.scalar_one_or_none()

            if existing_perm:
                existing_perm.permission_value = perm_value
            else:
                new_perm = GroupPermission(
                    group_id=group.id,
                    permission_key=perm_key,
                    permission_value=perm_value
                )
                db.add(new_perm)

        await db.commit()


async def initialize_superadmin(db: AsyncSession):
    """
    Initialize superadmin user if SUPERADMIN_EMAIL is set.

    Creates:
    - Adds user to Admins group with full system access
    - Only creates if Admins group is empty (prevents auto-creation after manual setup)
    """
    from app.core.config import settings

    if not settings.superadmin_email:
        return  # No superadmin email configured

    email = normalize_email(settings.superadmin_email)

    # Get Admins group (should already exist from initialize_default_groups)
    result = await db.execute(
        select(Group).where(Group.name == "Admins")
    )
    admins_group = result.scalar_one_or_none()

    if not admins_group:
        import logging
        logger = logging.getLogger(__name__)
        logger.error("Admins group not found. Run initialize_default_groups first.")
        return

    # Check if any user is already in Admins group
    result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == admins_group.id,
            GroupMember.active == True
        )
    )
    existing_members = result.scalars().all()

    if existing_members:
        # Admins group already has members, don't auto-create
        return

    # Get or create admin user
    user = await get_or_create_user(db, email, assign_to_users_group=False)

    # Check if user is already in Admins group
    result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == admins_group.id,
            GroupMember.user_id == user.id
        )
    )
    existing_membership = result.scalar_one_or_none()

    if not existing_membership:
        # Add user to Admins group
        membership = GroupMember(
            group_id=admins_group.id,
            user_id=user.id,
            role="admin",
            active=True
        )
        db.add(membership)
        await db.commit()

        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Admin user created: {email}")
