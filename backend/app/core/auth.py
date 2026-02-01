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
    validate_permission_subset,
    DEFAULT_ROLE_PERMISSIONS,
)
from app.models import (
    User,
    Role,
    UserRole,
    RolePermission,
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


async def get_user_by_email(
    db: AsyncSession,
    email: str
) -> User | None:
    """
    Get user by email address.

    Args:
        db: Database session
        email: User's email address

    Returns:
        User object or None if not found
    """
    normalized_email = normalize_email(email)

    result = await db.execute(
        select(User).where(User.email == normalized_email)
    )
    return result.scalar_one_or_none()


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
        select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.active == True
        )
    )
    memberships = result.scalars().all()

    if not memberships:
        return {}

    # Collect all permissions from all groups
    all_permissions = {}

    for membership in memberships:
        result = await db.execute(
            select(RolePermission).where(
                RolePermission.role_id == membership.role_id
            )
        )
        group_permissions = result.scalars().all()

        for perm in group_permissions:
            # OR logic: if ANY group grants permission (true), user has it
            # Don't let a false permission override an existing true permission
            if perm.permission_value or perm.permission_key not in all_permissions:
                all_permissions[perm.permission_key] = perm.permission_value

    # Return permissions as-is (with wildcards) - they will be matched at runtime
    return all_permissions


def create_access_token(
    user_id: str,
    email: str,
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create JWT access token (short-lived, no permissions in payload).

    Best Practice: Permissions are fetched from DB on each request,
    not embedded in token. This ensures immediate permission updates.

    Args:
        user_id: User UUID
        email: User email
        expires_delta: Optional custom expiration

    Returns:
        Encoded JWT token
    """
    now = datetime.now(timezone.utc)

    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.access_token_expire_minutes)

    to_encode = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),  # Issued at (best practice)
        "exp": int(expire.timestamp())
    }

    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


async def create_refresh_token(
    db: AsyncSession,
    user_id: str
) -> Tuple[str, "RefreshToken"]:
    """
    Create a refresh token and store it in the database.

    Refresh tokens are long-lived and stored in DB for revocation control.

    Args:
        db: Database session
        user_id: User UUID

    Returns:
        Tuple of (plain_token, refresh_token_model)
    """
    from app.models import RefreshToken
    import uuid as uuid_lib

    # Generate random token
    plain_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plain_token.encode()).hexdigest()
    token_prefix = plain_token[:8]

    # Calculate expiration
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)

    # Create refresh token record
    refresh_token = RefreshToken(
        user_id=uuid_lib.UUID(user_id),
        token_hash=token_hash,
        token_prefix=token_prefix,
        is_revoked=False,
        expires_at=expires_at
    )

    db.add(refresh_token)
    await db.commit()
    await db.refresh(refresh_token)

    return plain_token, refresh_token


async def validate_refresh_token(
    db: AsyncSession,
    plain_token: str
) -> Optional[Tuple[str, str]]:
    """
    Validate a refresh token and return user info.

    Args:
        db: Database session
        plain_token: Plain refresh token from request

    Returns:
        Tuple of (user_id, email) if valid, None otherwise
    """
    from app.models import RefreshToken

    token_hash = hashlib.sha256(plain_token.encode()).hexdigest()

    # Find active refresh token
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.is_revoked == False
        )
    )
    refresh_token = result.scalar_one_or_none()

    if not refresh_token:
        return None

    # Check if expired
    if refresh_token.expires_at < datetime.now(timezone.utc):
        return None

    # Update last used timestamp
    refresh_token.last_used_at = datetime.now(timezone.utc)

    # Get user and update last_login
    result = await db.execute(
        select(User).where(User.id == refresh_token.user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        return None

    # Update last login timestamp
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    return str(user.id), user.email


async def revoke_refresh_token(
    db: AsyncSession,
    plain_token: str
) -> bool:
    """
    Revoke a refresh token (logout).

    Args:
        db: Database session
        plain_token: Plain refresh token to revoke

    Returns:
        True if revoked, False if not found
    """
    from app.models import RefreshToken

    token_hash = hashlib.sha256(plain_token.encode()).hexdigest()

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    refresh_token = result.scalar_one_or_none()

    if not refresh_token:
        return False

    refresh_token.is_revoked = True
    refresh_token.revoked_at = datetime.now(timezone.utc)
    await db.commit()

    return True


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

    # Get user and update last_login
    result = await db.execute(
        select(User).where(User.id == api_key.user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        return None

    # Update last login timestamp
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    return user, api_key.permissions


# Authentication Dependencies

# HTTPBearer security scheme for Swagger UI
http_bearer = HTTPBearer(auto_error=False)


async def verify_jwt_or_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db)
) -> Tuple[str, str, Dict[str, bool]]:
    """
    Verify either JWT access token or API key from Authorization or X-API-Key header.

    BEST PRACTICE: Permissions are loaded from DB, not from JWT payload.
    This ensures immediate permission updates without waiting for token expiry.

    Supports:
    - Bearer <jwt_access_token> (15 min) in Authorization header
    - Bearer <api_key> (long-lived) in Authorization header
    - <api_key> in X-API-Key header

    Returns:
        Tuple of (user_id, email, permissions)

    Raises:
        HTTPException 401 if authentication fails
    """
    # Try X-API-Key header first
    if x_api_key:
        token = x_api_key
    elif credentials:
        token = credentials.credentials  # HTTPBearer already strips "Bearer " prefix
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header"
        )

    # Try JWT first
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id = payload.get("sub")
        email = payload.get("email")

        if not user_id or not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload"
            )

        # BEST PRACTICE: Load permissions from database, not from JWT
        # This ensures permissions are always current (no stale token permissions)
        permissions = await get_user_permissions(db, str(user_id))

        # Verify user exists and update last_login
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )

        # Update last login timestamp
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()

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


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer)
) -> Optional[str]:
    """
    Get current authenticated user ID if auth header provided, otherwise return None.
    Used for optional authentication on runtime endpoints.

    Returns:
        user_id or None
    """
    if not credentials:
        return None

    try:
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            user_id, email, _ = await verify_jwt_or_api_key(credentials, db)
            # Store user info in request state for logging
            request.state.user_id = user_id
            request.state.user_email = email
            return user_id
    except Exception:
        # Return None on auth failure for optional auth
        return None


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

async def initialize_default_roles(db: AsyncSession):
    """
    Initialize default groups (GuestUsers, Users, Admins) with permissions.

    Should be called during application startup or setup.
    """
    for group_name, permissions in DEFAULT_ROLE_PERMISSIONS.items():
        # Check if group exists
        result = await db.execute(
            select(Role).where(Role.name == group_name)
        )
        group = result.scalar_one_or_none()

        if not group:
            # Create group
            group = Role(name=group_name, description=f"Default {group_name} group")
            db.add(group)
            await db.commit()
            await db.refresh(group)

        # Update permissions
        for perm_key, perm_value in permissions.items():
            result = await db.execute(
                select(RolePermission).where(
                    RolePermission.role_id == group.id,
                    RolePermission.permission_key == perm_key
                )
            )
            existing_perm = result.scalar_one_or_none()

            if existing_perm:
                existing_perm.permission_value = perm_value
            else:
                new_perm = RolePermission(
                    role_id=group.id,
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
        select(Role).where(Role.name == "Admins")
    )
    admins_role = result.scalar_one_or_none()

    if not admins_role:
        import logging
        logger = logging.getLogger(__name__)
        logger.error("Admins group not found. Run initialize_default_groups first.")
        return

    # Check if any user is already in Admins group
    result = await db.execute(
        select(UserRole).where(
            UserRole.role_id == admins_role.id,
            UserRole.active == True
        )
    )
    existing_members = result.scalars().all()

    if existing_members:
        # Admins role already has members, don't auto-create
        return

    # Get or create admin user
    user = await get_user_by_email(db, email)

    if not user:
        # Create superadmin user (only happens on first startup)
        normalized_email = normalize_email(email)
        user = User(email=normalized_email)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    # Check if user is already in Admins group
    result = await db.execute(
        select(UserRole).where(
            UserRole.role_id == admins_role.id,
            UserRole.user_id == user.id
        )
    )
    existing_membership = result.scalar_one_or_none()

    if not existing_membership:
        # Add user to Admins group
        membership = UserRole(
            role_id=admins_role.id,
            user_id=user.id,
            role="admin",
            active=True
        )
        db.add(membership)
        await db.commit()

        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Admin user created: {email}")
