from fastapi import HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, Annotated
import httpx
import asyncio
from jose import JWTError, jwt
from datetime import datetime

from app.core.config import settings

security = HTTPBearer()


async def verify_census_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Verify JWT token with Census service."""
    try:
        # For development, we can decode locally if we have the same secret key
        # In production, you might want to verify with Census service directly
        
        if not settings.census_jwt_secret:
            # If no local secret, verify with Census service
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.census_api_url}/api/v1/users/me",
                    headers={"Authorization": f"Bearer {credentials.credentials}"},
                    timeout=10.0
                )
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid or expired token"
                    )
                return response.json()
        else:
            # Decode JWT locally
            payload = jwt.decode(
                credentials.credentials,
                settings.census_jwt_secret,
                algorithms=["HS256"]
            )
            user_id = payload.get("sub")
            email = payload.get("email")
            exp = payload.get("exp")
            
            if not user_id or not email:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid token payload"
                )
            
            if exp and datetime.utcnow().timestamp() > exp:
                raise HTTPException(
                    status_code=401,
                    detail="Token has expired"
                )
            
            return {
                "id": user_id,
                "email": email,
                "is_active": True  # Assume active if token is valid
            }
            
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials"
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Census service unavailable"
        )


async def get_user_subtenant_access(user_id: str, token: str, service: str = "maestro") -> Optional[str]:
    """Get user's active subtenant for the service from Census."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.census_api_url}/api/v1/users/{user_id}/service-access",
                headers={"Authorization": f"Bearer {token}"},
                params={"active_only": True},
                timeout=10.0
            )
            
            if response.status_code != 200:
                return None
            
            service_access_list = response.json()
            
            # Find active access for this service
            for access in service_access_list:
                if access.get("service") == service and access.get("active"):
                    return access.get("subtenant_id")
            
            return None
            
    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Census service unavailable"
        )


async def get_current_user_with_subtenant(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> tuple[dict, str]:
    """Get current user and their active subtenant."""
    # First verify the token and get user data
    user_data = await verify_census_token(credentials)
    user_id = user_data["id"]
    
    # Get user's subtenant access using the same token
    subtenant_id = await get_user_subtenant_access(user_id, credentials.credentials)
    
    if not subtenant_id:
        raise HTTPException(
            status_code=403,
            detail="No active subtenant access for Maestro service. Contact admin to grant access."
        )
    
    return user_data, subtenant_id


async def get_current_subtenant(
    user_subtenant: tuple[dict, str] = Depends(get_current_user_with_subtenant)
) -> str:
    """Extract subtenant ID for the current authenticated user."""
    _, subtenant_id = user_subtenant
    return subtenant_id


async def is_user_admin(user_id: str, token: str) -> bool:
    """Check if user is admin via Census service."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.census_api_url}/api/v1/users/{user_id}/groups",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0
            )
            
            if response.status_code != 200:
                return False
            
            groups = response.json()
            
            # Check if user is in "Admins" group
            for group in groups:
                if group.get("group_name") == "Admins" and group.get("active"):
                    return True
            
            return False
            
    except httpx.RequestError:
        return False


async def get_admin_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Verify user is admin via Census service."""
    # First verify the token and get user data
    user_data = await verify_census_token(credentials)
    user_id = user_data["id"]
    
    # Check admin status using the same token
    if not await is_user_admin(user_id, credentials.credentials):
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )
    
    return user_data


def verify_admin_access():
    """Dependency to verify admin access."""
    return get_admin_user


def get_subtenant_context():
    """Dependency to get subtenant context for authenticated requests."""
    return get_current_subtenant