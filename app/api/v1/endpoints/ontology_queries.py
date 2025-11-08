"""API endpoints for ConceptQuery management and execution."""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query as QueryParam
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models import ConceptQuery, Concept, DataSource
from app.schemas.ontology import (
    ConceptQueryCreate,
    ConceptQueryUpdate,
    ConceptQueryResponse,
    SyncStatusResponse,
    SyncTriggerResponse,
)
from app.services.ontology.sync_service import SyncService, schedule_sync_job, remove_sync_job
from app.services.ontology.query_validator import sql_validator

router = APIRouter(prefix="/ontology/queries", tags=["Ontology - Queries"])


@router.post("", response_model=ConceptQueryResponse, status_code=status.HTTP_201_CREATED)
async def create_concept_query(
    request: Request,
    query_data: ConceptQueryCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Create a query definition for a concept."""
    user_id, permissions = current_user_data

    # Verify concept exists
    result = await db.execute(
        select(Concept).where(Concept.id == query_data.concept_id)
    )
    concept = result.scalar_one_or_none()
    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {query_data.concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.queries.{concept.namespace}.{concept.name}.create:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to create query for concept {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # Check if concept already has a query
    result = await db.execute(
        select(ConceptQuery).where(ConceptQuery.concept_id == query_data.concept_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Concept {query_data.concept_id} already has a query defined"
        )

    # Verify data source if provided
    if query_data.data_source_id:
        result = await db.execute(
            select(DataSource).where(DataSource.id == query_data.data_source_id)
        )
        datasource = result.scalar_one_or_none()
        if not datasource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"DataSource {query_data.data_source_id} not found"
            )

    # Validate: self-managed concepts shouldn't have sql_text or data_source
    if concept.is_self_managed:
        if query_data.sql_text or query_data.data_source_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Self-managed concepts should not have SQL queries or data sources"
            )

    # Validate: non-self-managed concepts must have sql_text and data_source
    if not concept.is_self_managed:
        if not query_data.sql_text or not query_data.data_source_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="External concepts must have SQL query and data source"
            )

        # Validate SQL query
        try:
            sql_validator.validate_and_raise(query_data.sql_text)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )

    db_query = ConceptQuery(
        concept_id=query_data.concept_id,
        data_source_id=query_data.data_source_id,
        sql_text=query_data.sql_text,
        sync_enabled=query_data.sync_enabled,
        sync_schedule=query_data.sync_schedule,
    )

    db.add(db_query)
    await db.commit()
    await db.refresh(db_query)

    # If sync_enabled, schedule the sync job
    if db_query.sync_enabled:
        await schedule_sync_job(db, db_query)

    return db_query


@router.get("", response_model=List[ConceptQueryResponse])
async def list_concept_queries(
    request: Request,
    concept_id: Optional[UUID] = QueryParam(None),
    sync_enabled: Optional[bool] = QueryParam(None),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List all concept queries with optional filters."""
    user_id, permissions = current_user_data

    query = select(ConceptQuery)

    if concept_id:
        query = query.where(ConceptQuery.concept_id == concept_id)
    if sync_enabled is not None:
        query = query.where(ConceptQuery.sync_enabled == sync_enabled)

    result = await db.execute(query)
    queries = result.scalars().all()

    # Filter queries based on permissions
    filtered_queries = []
    for query_obj in queries:
        # Load the concept to check permissions
        concept_result = await db.execute(
            select(Concept).where(Concept.id == query_obj.concept_id)
        )
        concept = concept_result.scalar_one_or_none()
        if concept:
            required_perm = f"sinas.ontology.queries.{concept.namespace}.{concept.name}.read:group"
            if check_permission(permissions, required_perm):
                filtered_queries.append(query_obj)

    # Set permission used for tracking (use wildcard pattern)
    set_permission_used(request, "sinas.ontology.queries.*.*.read:group", has_perm=True)

    return filtered_queries


@router.get("/{query_id}", response_model=ConceptQueryResponse)
async def get_concept_query(
    request: Request,
    query_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get a specific concept query by ID."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(ConceptQuery).where(ConceptQuery.id == query_id)
    )
    query_obj = result.scalar_one_or_none()

    if not query_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ConceptQuery {query_id} not found"
        )

    # Load concept to check permissions
    concept_result = await db.execute(
        select(Concept).where(Concept.id == query_obj.concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {query_obj.concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.queries.{concept.namespace}.{concept.name}.read:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to read query for concept {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    return query_obj


@router.put("/{query_id}", response_model=ConceptQueryResponse)
async def update_concept_query(
    request: Request,
    query_id: UUID,
    query_update: ConceptQueryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Update a concept query."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(ConceptQuery).where(ConceptQuery.id == query_id)
    )
    query_obj = result.scalar_one_or_none()

    if not query_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ConceptQuery {query_id} not found"
        )

    # Load concept to check permissions
    concept_result = await db.execute(
        select(Concept).where(Concept.id == query_obj.concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {query_obj.concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.queries.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update query for concept {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # Verify data source if being updated
    update_data = query_update.model_dump(exclude_unset=True)

    if "data_source_id" in update_data and update_data["data_source_id"]:
        result = await db.execute(
            select(DataSource).where(DataSource.id == update_data["data_source_id"])
        )
        datasource = result.scalar_one_or_none()
        if not datasource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"DataSource {update_data['data_source_id']} not found"
            )

    # Validate SQL query if being updated
    if "sql_text" in update_data and update_data["sql_text"]:
        try:
            sql_validator.validate_and_raise(update_data["sql_text"])
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )

    for field, value in update_data.items():
        setattr(query_obj, field, value)

    await db.commit()
    await db.refresh(query_obj)

    # If sync_enabled changed, update scheduled job
    if 'sync_enabled' in update_data or 'sync_schedule' in update_data:
        if query_obj.sync_enabled:
            await schedule_sync_job(db, query_obj)
        else:
            await remove_sync_job(query_obj.concept_id)

    return query_obj


@router.delete("/{query_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_concept_query(
    request: Request,
    query_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Delete a concept query."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(ConceptQuery).where(ConceptQuery.id == query_id)
    )
    query_obj = result.scalar_one_or_none()

    if not query_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ConceptQuery {query_id} not found"
        )

    # Load concept to check permissions
    concept_result = await db.execute(
        select(Concept).where(Concept.id == query_obj.concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {query_obj.concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.queries.{concept.namespace}.{concept.name}.delete:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to delete query for concept {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # Remove scheduled sync job if exists
    await remove_sync_job(query_obj.concept_id)

    # Drop synced table if exists
    if query_obj.sync_enabled:
        result = await db.execute(
            select(Concept).where(Concept.id == query_obj.concept_id)
        )
        concept = result.scalar_one_or_none()
        if concept:
            sync_service = SyncService(db)
            await sync_service.drop_sync_table(concept)

    await db.delete(query_obj)
    await db.commit()


# ============================================================================
# Sync Management Endpoints
# ============================================================================

@router.get("/sync/{concept_id}/status", response_model=SyncStatusResponse)
async def get_sync_status(
    request: Request,
    concept_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get sync status for a concept."""
    user_id, permissions = current_user_data

    # Load concept to check permissions
    concept_result = await db.execute(
        select(Concept).where(Concept.id == concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.queries.{concept.namespace}.{concept.name}.read:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to read query for concept {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    result = await db.execute(
        select(ConceptQuery).where(ConceptQuery.concept_id == concept_id)
    )
    query_obj = result.scalar_one_or_none()

    if not query_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No query found for concept {concept_id}"
        )

    # TODO: Calculate next_sync_at from schedule
    # TODO: Check actual sync job status

    return SyncStatusResponse(
        concept_id=concept_id,
        sync_enabled=query_obj.sync_enabled,
        last_synced_at=query_obj.last_synced_at,
        next_sync_at=None,  # TODO: Calculate from schedule
        sync_schedule=query_obj.sync_schedule,
        status="pending" if query_obj.sync_enabled else "disabled",
    )


@router.post("/sync/{concept_id}/trigger", response_model=SyncTriggerResponse)
async def trigger_sync(
    request: Request,
    concept_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Manually trigger a sync for a concept."""
    user_id, permissions = current_user_data

    # Load concept to check permissions
    concept_result = await db.execute(
        select(Concept).where(Concept.id == concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.queries.{concept.namespace}.{concept.name}.execute:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to execute query for concept {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    result = await db.execute(
        select(ConceptQuery).where(ConceptQuery.concept_id == concept_id)
    )
    query_obj = result.scalar_one_or_none()

    if not query_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No query found for concept {concept_id}"
        )

    if not query_obj.sync_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sync is not enabled for this concept"
        )

    # Trigger actual sync job
    sync_service = SyncService(db)
    try:
        result = await sync_service.sync_concept(concept_id)

        return SyncTriggerResponse(
            concept_id=concept_id,
            triggered=True,
            message=f"Sync completed: {result['row_count']} rows synced",
        )
    except Exception as e:
        return SyncTriggerResponse(
            concept_id=concept_id,
            triggered=False,
            message=f"Sync failed: {str(e)}",
        )
