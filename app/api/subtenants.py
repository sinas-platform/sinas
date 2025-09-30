from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.core.database import get_db
from app.core.auth import verify_admin_access
from app.core.config import settings
from app.models.subtenant import Subtenant
from app.api.schemas import SubtenantCreate, SubtenantResponse
import httpx

router = APIRouter(prefix="/subtenants", tags=["subtenants"])


@router.post("", response_model=SubtenantResponse)
async def create_subtenant(
    subtenant_data: SubtenantCreate,
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(verify_admin_access())
):
    """Create a new subtenant (admin only). ID is auto-generated."""
    # Create subtenant with auto-generated UUID
    subtenant = Subtenant(
        description=subtenant_data.description
    )
    
    db.add(subtenant)
    await db.commit()
    await db.refresh(subtenant)
    
    return subtenant


@router.get("", response_model=List[SubtenantResponse])
async def list_subtenants(
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(verify_admin_access())
):
    """List all subtenants (admin only)."""
    result = await db.execute(
        select(Subtenant).order_by(Subtenant.created_at.desc())
    )
    subtenants = result.scalars().all()
    
    return subtenants


@router.get("/{subtenant_id}", response_model=SubtenantResponse)
async def get_subtenant(
    subtenant_id: str,
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(verify_admin_access())
):
    """Get subtenant details (admin only)."""
    result = await db.execute(
        select(Subtenant).where(Subtenant.id == subtenant_id)
    )
    subtenant = result.scalar_one_or_none()
    
    if not subtenant:
        raise HTTPException(status_code=404, detail="Subtenant not found")
    
    return subtenant


@router.delete("/{subtenant_id}")
async def delete_subtenant(
    subtenant_id: str,
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(verify_admin_access())
):
    """Delete a subtenant and all associated data (admin only)."""
    result = await db.execute(
        select(Subtenant).where(Subtenant.id == subtenant_id)
    )
    subtenant = result.scalar_one_or_none()
    
    if not subtenant:
        raise HTTPException(status_code=404, detail="Subtenant not found")
    
    # TODO: Add cascade deletion of all related data
    # This should delete all functions, webhooks, schedules, executions, packages
    # for this subtenant
    
    await db.delete(subtenant)
    await db.commit()
    
    return {"message": f"Subtenant '{subtenant_id}' and all associated data deleted successfully"}


@router.post("/{subtenant_id}/grant-access")
async def grant_user_subtenant_access(
    subtenant_id: str,
    user_email: str,
    db: AsyncSession = Depends(get_db),
    admin: dict = Depends(verify_admin_access())
):
    """Grant a user access to this subtenant via Census service (admin only)."""
    # Verify subtenant exists
    result = await db.execute(
        select(Subtenant).where(Subtenant.id == subtenant_id)
    )
    subtenant = result.scalar_one_or_none()
    
    if not subtenant:
        raise HTTPException(status_code=404, detail="Subtenant not found")
    
    try:
        # First, get the user ID from Census by email
        async with httpx.AsyncClient() as client:
            # Search for user by email
            users_response = await client.get(
                f"{settings.census_api_url}/api/v1/users",
                timeout=10.0
            )
            
            if users_response.status_code != 200:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to query Census users"
                )
            
            users = users_response.json()
            user = None
            
            # Find user by email
            for u in users:
                if u.get("email", "").lower() == user_email.lower():
                    user = u
                    break
            
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail=f"User with email {user_email} not found in Census"
                )
            
            user_id = user["id"]
            
            # Grant access via Census service
            grant_response = await client.post(
                f"{settings.census_api_url}/api/v1/users/{user_id}/service-access",
                json={
                    "service": "maestro",
                    "subtenant_id": subtenant_id,
                    "granted_by": admin["id"]
                },
                timeout=10.0
            )
            
            if grant_response.status_code != 200:
                error_detail = grant_response.text
                raise HTTPException(
                    status_code=grant_response.status_code,
                    detail=f"Failed to grant access in Census: {error_detail}"
                )
            
            return {
                "message": f"Access to subtenant '{subtenant_id}' granted to {user_email}",
                "user_id": user_id,
                "subtenant_id": subtenant_id
            }
            
    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Census service unavailable"
        )


@router.delete("/{subtenant_id}/revoke-access")
async def revoke_user_subtenant_access(
    subtenant_id: str,
    user_email: str,
    db: AsyncSession = Depends(get_db),
    admin: dict = Depends(verify_admin_access())
):
    """Revoke a user's access to this subtenant via Census service (admin only)."""
    # Verify subtenant exists
    result = await db.execute(
        select(Subtenant).where(Subtenant.id == subtenant_id)
    )
    subtenant = result.scalar_one_or_none()
    
    if not subtenant:
        raise HTTPException(status_code=404, detail="Subtenant not found")
    
    try:
        # First, get the user ID from Census by email
        async with httpx.AsyncClient() as client:
            users_response = await client.get(
                f"{settings.census_api_url}/api/v1/users",
                timeout=10.0
            )
            
            if users_response.status_code != 200:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to query Census users"
                )
            
            users = users_response.json()
            user = None
            
            # Find user by email
            for u in users:
                if u.get("email", "").lower() == user_email.lower():
                    user = u
                    break
            
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail=f"User with email {user_email} not found in Census"
                )
            
            user_id = user["id"]
            
            # Revoke access via Census service
            revoke_response = await client.delete(
                f"{settings.census_api_url}/api/v1/users/{user_id}/service-access/maestro/{subtenant_id}",
                timeout=10.0
            )
            
            if revoke_response.status_code != 200:
                error_detail = revoke_response.text
                raise HTTPException(
                    status_code=revoke_response.status_code,
                    detail=f"Failed to revoke access in Census: {error_detail}"
                )
            
            return {
                "message": f"Access to subtenant '{subtenant_id}' revoked from {user_email}",
                "user_id": user_id,
                "subtenant_id": subtenant_id
            }
            
    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Census service unavailable"
        )