"""Skills API endpoints.

Writes go through SkillApplier, the path config apply and package install
use too: the same validation, ownership and change history on every channel.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.skill import Skill
from app.schemas import SkillCreate, SkillResponse, SkillUpdate
from app.services.resources import rest
from app.services.resources.skills import SkillApplier

router = APIRouter(prefix="/skills", tags=["skills"])

_applier = SkillApplier()


@router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(
    request: Request,
    skill_data: SkillCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new skill."""
    user_id, permissions = current_user_data

    # Check permission to create skills
    permission = "sinas.skills.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create skills")
    set_permission_used(request, permission)

    ctx = rest.api_context(db, user_id)
    # A clash is a 400 "Skill 'ns/name' already exists", as before.
    result = await rest.write(
        _applier, ctx, rest.parse_spec(_applier, skill_data.model_dump()), must_create=True
    )
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return SkillResponse.model_validate(result.obj)


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    request: Request,
    namespace: str = None,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all skills accessible to the user."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware filtering
    additional_filters = Skill.is_active == True
    if namespace:
        additional_filters = and_(additional_filters, Skill.namespace == namespace)

    skills = await Skill.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=additional_filters,
    )

    set_permission_used(request, "sinas.skills.read")

    return [SkillResponse.model_validate(skill) for skill in skills]


@router.get("/{namespace}/{name}", response_model=SkillResponse)
async def get_skill(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific skill by namespace and name."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    skill = await Skill.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.skills/{namespace}/{name}.read")

    return SkillResponse.model_validate(skill)


@router.put("/{namespace}/{name}", response_model=SkillResponse)
async def update_skill(
    namespace: str,
    name: str,
    skill_data: SkillUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a skill."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    skill = await Skill.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.skills/{namespace}/{name}.update")

    ctx = rest.api_context(db, user_id)
    skill = await rest.locked(_applier, ctx, skill)
    # As before: fields left out (or null) are unchanged.
    patch = {key: value for key, value in skill_data.model_dump().items() if value is not None}
    await rest.write(_applier, ctx, rest.patch_spec(_applier, skill, patch), existing=skill)
    await rest.commit(db, ctx)
    await db.refresh(skill)
    return SkillResponse.model_validate(skill)


@router.delete("/{namespace}/{name}", status_code=204)
async def delete_skill(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a skill."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    skill = await Skill.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="delete",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.skills/{namespace}/{name}.delete")

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, skill), ctx)
    await rest.commit(db, ctx)
    return None
