"""Skills API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from typing import List

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.permissions import check_permission
from app.models.skill import Skill
from app.schemas import SkillCreate, SkillUpdate, SkillResponse

router = APIRouter(prefix="/skills", tags=["skills"])


@router.post("", response_model=SkillResponse)
async def create_skill(
    request: Request,
    skill_data: SkillCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Create a new skill."""
    user_id, permissions = current_user_data

    # Check permission to create skills
    permission = "sinas.skills.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create skills")
    set_permission_used(request, permission)

    # Check if skill name already exists in this namespace
    result = await db.execute(
        select(Skill).where(
            and_(
                Skill.namespace == skill_data.namespace,
                Skill.name == skill_data.name
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Skill '{skill_data.namespace}/{skill_data.name}' already exists"
        )

    # Create skill
    skill = Skill(
        user_id=user_id,
        namespace=skill_data.namespace,
        name=skill_data.name,
        description=skill_data.description,
        content=skill_data.content
    )

    db.add(skill)
    await db.commit()
    await db.refresh(skill)

    return SkillResponse.model_validate(skill)


@router.get("", response_model=List[SkillResponse])
async def list_skills(
    request: Request,
    namespace: str = None,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List all skills accessible to the user."""
    user_id, permissions = current_user_data

    # Build query based on permissions
    if check_permission(permissions, "sinas.skills.read:all"):
        # Admin can see all skills
        set_permission_used(request, "sinas.skills.read:all")
        query = select(Skill).where(Skill.is_active == True)
    else:
        # User can see only their own skills
        permission = "sinas.skills.read:own"
        if not check_permission(permissions, permission):
            set_permission_used(request, permission, has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to list skills")
        set_permission_used(request, permission)
        query = select(Skill).where(
            and_(
                Skill.user_id == user_id,
                Skill.is_active == True
            )
        )

    # Filter by namespace if provided
    if namespace:
        query = query.where(Skill.namespace == namespace)

    result = await db.execute(query.order_by(Skill.namespace, Skill.name))
    skills = result.scalars().all()

    return [SkillResponse.model_validate(skill) for skill in skills]


@router.get("/{namespace}/{name}", response_model=SkillResponse)
async def get_skill(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Get a specific skill by namespace and name."""
    user_id, permissions = current_user_data

    # Get skill
    skill = await Skill.get_by_name(db, namespace, name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{namespace}/{name}' not found")

    # Check permissions
    skill_perm = f"sinas.skills/{namespace}/{name}.read:own"
    has_permission = (
        check_permission(permissions, f"sinas.skills/{namespace}/{name}.read:all") or
        (check_permission(permissions, skill_perm) and str(skill.user_id) == user_id)
    )

    if not has_permission:
        set_permission_used(request, skill_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to read skill '{namespace}/{name}'")

    set_permission_used(
        request,
        f"sinas.skills/{namespace}/{name}.read:all"
        if check_permission(permissions, f"sinas.skills/{namespace}/{name}.read:all")
        else skill_perm
    )

    return SkillResponse.model_validate(skill)


@router.put("/{namespace}/{name}", response_model=SkillResponse)
async def update_skill(
    namespace: str,
    name: str,
    skill_data: SkillUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Update a skill."""
    user_id, permissions = current_user_data

    # Get skill
    skill = await Skill.get_by_name(db, namespace, name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{namespace}/{name}' not found")

    # Check permissions
    skill_perm = f"sinas.skills/{namespace}/{name}.update:own"
    has_permission = (
        check_permission(permissions, f"sinas.skills/{namespace}/{name}.update:all") or
        (check_permission(permissions, skill_perm) and str(skill.user_id) == user_id)
    )

    if not has_permission:
        set_permission_used(request, skill_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update skill '{namespace}/{name}'")

    set_permission_used(
        request,
        f"sinas.skills/{namespace}/{name}.update:all"
        if check_permission(permissions, f"sinas.skills/{namespace}/{name}.update:all")
        else skill_perm
    )

    # If namespace or name is being updated, check for conflicts
    new_namespace = skill_data.namespace or skill.namespace
    new_name = skill_data.name or skill.name

    if (new_namespace != skill.namespace or new_name != skill.name):
        result = await db.execute(
            select(Skill).where(
                and_(
                    Skill.namespace == new_namespace,
                    Skill.name == new_name,
                    Skill.id != skill.id
                )
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Skill '{new_namespace}/{new_name}' already exists"
            )

    # Update fields
    if skill_data.namespace is not None:
        skill.namespace = skill_data.namespace
    if skill_data.name is not None:
        skill.name = skill_data.name
    if skill_data.description is not None:
        skill.description = skill_data.description
    if skill_data.content is not None:
        skill.content = skill_data.content
    if skill_data.is_active is not None:
        skill.is_active = skill_data.is_active

    await db.commit()
    await db.refresh(skill)

    return SkillResponse.model_validate(skill)


@router.delete("/{namespace}/{name}", status_code=204)
async def delete_skill(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a skill."""
    user_id, permissions = current_user_data

    # Get skill
    skill = await Skill.get_by_name(db, namespace, name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{namespace}/{name}' not found")

    # Check permissions
    skill_perm = f"sinas.skills/{namespace}/{name}.delete:own"
    has_permission = (
        check_permission(permissions, f"sinas.skills/{namespace}/{name}.delete:all") or
        (check_permission(permissions, skill_perm) and str(skill.user_id) == user_id)
    )

    if not has_permission:
        set_permission_used(request, skill_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to delete skill '{namespace}/{name}'")

    set_permission_used(
        request,
        f"sinas.skills/{namespace}/{name}.delete:all"
        if check_permission(permissions, f"sinas.skills/{namespace}/{name}.delete:all")
        else skill_perm
    )

    await db.delete(skill)
    await db.commit()

    return None
