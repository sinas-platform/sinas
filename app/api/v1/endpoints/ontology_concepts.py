"""API endpoints for Concept management."""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models import Concept, Group, Property
from app.schemas.ontology import (
    ConceptCreate,
    ConceptUpdate,
    ConceptResponse,
    PropertyResponse,
)
from app.services.ontology.schema_manager import SchemaManager

router = APIRouter(prefix="/ontology/concepts", tags=["Ontology - Concepts"])


@router.post("", response_model=ConceptResponse, status_code=status.HTTP_201_CREATED)
async def create_concept(
    request: Request,
    concept: ConceptCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Create a new concept."""
    user_id, permissions = current_user_data

    # Check permissions - namespace specific
    required_perm = f"sinas.ontology.concepts.{concept.namespace}.create:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to create concepts in namespace {concept.namespace}")
    set_permission_used(request, required_perm, has_perm=True)

    # Verify group exists
    result = await db.execute(select(Group).where(Group.id == concept.group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group {concept.group_id} not found"
        )

    # Check if concept already exists with same namespace/name in this group
    result = await db.execute(
        select(Concept).where(
            Concept.group_id == concept.group_id,
            Concept.namespace == concept.namespace,
            Concept.name == concept.name,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Concept {concept.namespace}.{concept.name} already exists in this group"
        )

    db_concept = Concept(
        group_id=concept.group_id,
        namespace=concept.namespace,
        name=concept.name,
        display_name=concept.display_name,
        description=concept.description,
        is_self_managed=concept.is_self_managed,
    )

    db.add(db_concept)
    await db.commit()
    await db.refresh(db_concept)

    # If self-managed, create the table structure (initially empty, properties added later)
    if db_concept.is_self_managed:
        schema_manager = SchemaManager(db)
        # Table will be created when first property is added
        # Or we can create it with just id + audit columns
        await schema_manager.create_table(db_concept, [])

    return db_concept


@router.get("", response_model=List[ConceptResponse])
async def list_concepts(
    request: Request,
    group_id: Optional[UUID] = Query(None),
    namespace: Optional[str] = Query(None),
    is_self_managed: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List all concepts with optional filters."""
    user_id, permissions = current_user_data

    query = select(Concept)

    if group_id:
        query = query.where(Concept.group_id == group_id)
    if namespace:
        query = query.where(Concept.namespace == namespace)
    if is_self_managed is not None:
        query = query.where(Concept.is_self_managed == is_self_managed)

    result = await db.execute(query.order_by(Concept.namespace, Concept.name))
    concepts = result.scalars().all()

    # Filter concepts based on permissions
    filtered_concepts = []
    for concept in concepts:
        # Check if user has permission to read this namespace.concept
        # Users with :all scope automatically get :group access via scope hierarchy
        required_perm = f"sinas.ontology.concepts.{concept.namespace}.{concept.name}.read:group"
        if check_permission(permissions, required_perm):
            filtered_concepts.append(concept)

    # Set permission used for tracking (use wildcard pattern)
    if namespace:
        set_permission_used(request, f"sinas.ontology.concepts.{namespace}.*.read:group", has_perm=True)
    else:
        set_permission_used(request, "sinas.ontology.concepts.*.*.read:group", has_perm=True)

    return filtered_concepts


@router.get("/{concept_id}", response_model=ConceptResponse)
async def get_concept(
    request: Request,
    concept_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get a specific concept by ID."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Concept).where(Concept.id == concept_id)
    )
    concept = result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.concepts.{concept.namespace}.{concept.name}.read:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to read concept {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    return concept


@router.get("/{concept_id}/properties", response_model=List[PropertyResponse])
async def get_concept_properties(
    request: Request,
    concept_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get all properties for a concept."""
    user_id, permissions = current_user_data

    # Verify concept exists
    result = await db.execute(
        select(Concept).where(Concept.id == concept_id)
    )
    concept = result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.concepts.{concept.namespace}.{concept.name}.read:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to read concept {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # Get properties
    result = await db.execute(
        select(Property).where(Property.concept_id == concept_id).order_by(Property.name)
    )
    properties = result.scalars().all()

    return properties


@router.put("/{concept_id}", response_model=ConceptResponse)
async def update_concept(
    request: Request,
    concept_id: UUID,
    concept_update: ConceptUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Update a concept (only display_name and description can be changed)."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Concept).where(Concept.id == concept_id)
    )
    concept = result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.concepts.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update concept {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # Update fields
    update_data = concept_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(concept, field, value)

    await db.commit()
    await db.refresh(concept)

    return concept


@router.delete("/{concept_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_concept(
    request: Request,
    concept_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Delete a concept and all related data."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Concept).where(Concept.id == concept_id)
    )
    concept = result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.concepts.{concept.namespace}.{concept.name}.delete:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to delete concept {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # If self-managed, drop the table
    if concept.is_self_managed:
        schema_manager = SchemaManager(db)
        await schema_manager.drop_table(concept)

    # TODO: If synced, drop the synced table

    await db.delete(concept)
    await db.commit()
