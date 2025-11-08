"""API endpoints for Property and Relationship management."""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models import Property, Concept, Relationship
from app.schemas.ontology import (
    PropertyCreate,
    PropertyUpdate,
    PropertyResponse,
    RelationshipCreate,
    RelationshipUpdate,
    RelationshipResponse,
)
from app.services.ontology.schema_manager import SchemaManager

property_router = APIRouter(prefix="/ontology/properties", tags=["Ontology - Properties"])
relationship_router = APIRouter(prefix="/ontology/relationships", tags=["Ontology - Relationships"])


# ============================================================================
# Property Endpoints
# ============================================================================

@property_router.post("", response_model=PropertyResponse, status_code=status.HTTP_201_CREATED)
async def create_property(
    request: Request,
    property_data: PropertyCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Create a new property for a concept."""
    user_id, permissions = current_user_data

    # Verify concept exists
    result = await db.execute(
        select(Concept).where(Concept.id == property_data.concept_id)
    )
    concept = result.scalar_one_or_none()
    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {property_data.concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.properties.{concept.namespace}.{concept.name}.create:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to create properties for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # Check if property already exists with same name in this concept
    result = await db.execute(
        select(Property).where(
            Property.concept_id == property_data.concept_id,
            Property.name == property_data.name,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Property {property_data.name} already exists for this concept"
        )

    db_property = Property(
        concept_id=property_data.concept_id,
        name=property_data.name,
        display_name=property_data.display_name,
        description=property_data.description,
        data_type=property_data.data_type,
        is_identifier=property_data.is_identifier,
        is_required=property_data.is_required,
        default_value=property_data.default_value,
    )

    db.add(db_property)
    await db.commit()
    await db.refresh(db_property)

    # If concept is self-managed, alter table to add column
    if concept.is_self_managed:
        schema_manager = SchemaManager(db)
        await schema_manager.add_column(concept, db_property)

    return db_property


@property_router.get("", response_model=List[PropertyResponse])
async def list_properties(
    request: Request,
    concept_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List all properties, optionally filtered by concept."""
    user_id, permissions = current_user_data

    # If filtering by concept_id, check namespace.concept specific permission
    if concept_id:
        result = await db.execute(
            select(Concept).where(Concept.id == concept_id)
        )
        concept = result.scalar_one_or_none()
        if not concept:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Concept {concept_id} not found"
            )

        required_perm = f"sinas.ontology.properties.{concept.namespace}.{concept.name}.read:group"
        if not check_permission(permissions, required_perm):
            set_permission_used(request, required_perm, has_perm=False)
            raise HTTPException(status_code=403, detail=f"Not authorized to read properties for {concept.namespace}.{concept.name}")
        set_permission_used(request, required_perm, has_perm=True)

    query = select(Property)

    if concept_id:
        query = query.where(Property.concept_id == concept_id)

    result = await db.execute(query.order_by(Property.name))
    properties = result.scalars().all()

    return properties


@property_router.get("/{property_id}", response_model=PropertyResponse)
async def get_property(
    request: Request,
    property_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get a specific property by ID."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Property).where(Property.id == property_id)
    )
    property_obj = result.scalar_one_or_none()

    if not property_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property {property_id} not found"
        )

    # Get concept to check permissions
    result = await db.execute(
        select(Concept).where(Concept.id == property_obj.concept_id)
    )
    concept = result.scalar_one_or_none()
    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {property_obj.concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.properties.{concept.namespace}.{concept.name}.read:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to read properties for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    return property_obj


@property_router.put("/{property_id}", response_model=PropertyResponse)
async def update_property(
    request: Request,
    property_id: UUID,
    property_update: PropertyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Update a property."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Property).where(Property.id == property_id)
    )
    property_obj = result.scalar_one_or_none()

    if not property_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property {property_id} not found"
        )

    # Get concept to check permissions and if it's self-managed
    result = await db.execute(
        select(Concept).where(Concept.id == property_obj.concept_id)
    )
    concept = result.scalar_one_or_none()
    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {property_obj.concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.properties.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update properties for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # Update fields
    update_data = property_update.model_dump(exclude_unset=True)
    old_property = Property(
        id=property_obj.id,
        concept_id=property_obj.concept_id,
        name=property_obj.name,
        data_type=property_obj.data_type,
        is_identifier=property_obj.is_identifier,
        is_required=property_obj.is_required,
        default_value=property_obj.default_value,
    )

    for field, value in update_data.items():
        setattr(property_obj, field, value)

    await db.commit()
    await db.refresh(property_obj)

    # If concept is self-managed and data_type changed, migrate column
    if concept and concept.is_self_managed and 'data_type' in update_data:
        schema_manager = SchemaManager(db)
        await schema_manager.migrate_column(concept, old_property, property_obj)

    return property_obj


@property_router.delete("/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_property(
    request: Request,
    property_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Delete a property."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Property).where(Property.id == property_id)
    )
    property_obj = result.scalar_one_or_none()

    if not property_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property {property_id} not found"
        )

    # Get concept to check permissions and if it's self-managed
    result = await db.execute(
        select(Concept).where(Concept.id == property_obj.concept_id)
    )
    concept = result.scalar_one_or_none()
    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {property_obj.concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.properties.{concept.namespace}.{concept.name}.delete:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to delete properties for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # If concept is self-managed, rename column to deleted_columnname_timestamp
    if concept and concept.is_self_managed:
        schema_manager = SchemaManager(db)
        await schema_manager.mark_column_deleted(concept, property_obj)

    await db.delete(property_obj)
    await db.commit()


# ============================================================================
# Relationship Endpoints
# ============================================================================

@relationship_router.post("", response_model=RelationshipResponse, status_code=status.HTTP_201_CREATED)
async def create_relationship(
    request: Request,
    relationship: RelationshipCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Create a new relationship between concepts."""
    user_id, permissions = current_user_data

    # Verify concepts exist and get from_concept for namespace
    result = await db.execute(
        select(Concept).where(
            Concept.id.in_([relationship.from_concept_id, relationship.to_concept_id])
        )
    )
    concepts = result.scalars().all()
    if len(concepts) != 2:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both concepts not found"
        )

    # Get from_concept for permission check
    from_concept = next((c for c in concepts if c.id == relationship.from_concept_id), None)
    if not from_concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="From concept not found"
        )

    # Check permissions - namespace specific
    required_perm = f"sinas.ontology.relationships.{from_concept.namespace}.create:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to create relationships for {from_concept.namespace}")
    set_permission_used(request, required_perm, has_perm=True)

    # Verify properties exist
    result = await db.execute(
        select(Property).where(
            Property.id.in_([relationship.from_property_id, relationship.to_property_id])
        )
    )
    properties = result.scalars().all()
    if len(properties) != 2:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both properties not found"
        )

    db_relationship = Relationship(
        from_concept_id=relationship.from_concept_id,
        to_concept_id=relationship.to_concept_id,
        name=relationship.name,
        cardinality=relationship.cardinality,
        from_property_id=relationship.from_property_id,
        to_property_id=relationship.to_property_id,
        description=relationship.description,
    )

    db.add(db_relationship)
    await db.commit()
    await db.refresh(db_relationship)

    return db_relationship


@relationship_router.get("", response_model=List[RelationshipResponse])
async def list_relationships(
    request: Request,
    concept_id: Optional[UUID] = Query(None, description="Filter by concept (from or to)"),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List all relationships, optionally filtered by concept."""
    user_id, permissions = current_user_data

    # If filtering by concept_id, check namespace specific permission
    if concept_id:
        result = await db.execute(
            select(Concept).where(Concept.id == concept_id)
        )
        concept = result.scalar_one_or_none()
        if not concept:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Concept {concept_id} not found"
            )

        required_perm = f"sinas.ontology.relationships.{concept.namespace}.read:group"
        if not check_permission(permissions, required_perm):
            set_permission_used(request, required_perm, has_perm=False)
            raise HTTPException(status_code=403, detail=f"Not authorized to read relationships for {concept.namespace}")
        set_permission_used(request, required_perm, has_perm=True)
    else:
        # For wildcard list, check wildcard permission
        required_perm = "sinas.ontology.relationships.*.read:group"
        if not check_permission(permissions, required_perm):
            set_permission_used(request, required_perm, has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to read relationships")
        set_permission_used(request, required_perm, has_perm=True)

    query = select(Relationship)

    if concept_id:
        query = query.where(
            (Relationship.from_concept_id == concept_id) |
            (Relationship.to_concept_id == concept_id)
        )

    result = await db.execute(query.order_by(Relationship.name))
    relationships = result.scalars().all()

    return relationships


@relationship_router.get("/{relationship_id}", response_model=RelationshipResponse)
async def get_relationship(
    request: Request,
    relationship_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get a specific relationship by ID."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Relationship).where(Relationship.id == relationship_id)
    )
    relationship = result.scalar_one_or_none()

    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found"
        )

    # Get from_concept to check permissions
    result = await db.execute(
        select(Concept).where(Concept.id == relationship.from_concept_id)
    )
    from_concept = result.scalar_one_or_none()
    if not from_concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"From concept {relationship.from_concept_id} not found"
        )

    # Check permissions - namespace specific
    required_perm = f"sinas.ontology.relationships.{from_concept.namespace}.read:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to read relationships for {from_concept.namespace}")
    set_permission_used(request, required_perm, has_perm=True)

    return relationship


@relationship_router.put("/{relationship_id}", response_model=RelationshipResponse)
async def update_relationship(
    request: Request,
    relationship_id: UUID,
    relationship_update: RelationshipUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Update a relationship."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Relationship).where(Relationship.id == relationship_id)
    )
    relationship = result.scalar_one_or_none()

    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found"
        )

    # Get from_concept to check permissions
    result = await db.execute(
        select(Concept).where(Concept.id == relationship.from_concept_id)
    )
    from_concept = result.scalar_one_or_none()
    if not from_concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"From concept {relationship.from_concept_id} not found"
        )

    # Check permissions - namespace specific
    required_perm = f"sinas.ontology.relationships.{from_concept.namespace}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update relationships for {from_concept.namespace}")
    set_permission_used(request, required_perm, has_perm=True)

    # Update fields
    update_data = relationship_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(relationship, field, value)

    await db.commit()
    await db.refresh(relationship)

    return relationship


@relationship_router.delete("/{relationship_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_relationship(
    request: Request,
    relationship_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Delete a relationship."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Relationship).where(Relationship.id == relationship_id)
    )
    relationship = result.scalar_one_or_none()

    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Relationship {relationship_id} not found"
        )

    # Get from_concept to check permissions
    result = await db.execute(
        select(Concept).where(Concept.id == relationship.from_concept_id)
    )
    from_concept = result.scalar_one_or_none()
    if not from_concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"From concept {relationship.from_concept_id} not found"
        )

    # Check permissions - namespace specific
    required_perm = f"sinas.ontology.relationships.{from_concept.namespace}.delete:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to delete relationships for {from_concept.namespace}")
    set_permission_used(request, required_perm, has_perm=True)

    await db.delete(relationship)
    await db.commit()
