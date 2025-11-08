"""Auto-generated CRUD API for self-managed concept data."""
from typing import List, Dict, Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query as QueryParam
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission, get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models import Concept, Property
from app.services.ontology.schema_manager import SchemaManager

router = APIRouter(prefix="/ontology/data", tags=["Ontology - Data"])


async def get_concept_by_path(
    namespace: str,
    concept_name: str,
    db: AsyncSession
) -> Concept:
    """Get a concept by namespace and name."""
    result = await db.execute(
        select(Concept).where(
            Concept.namespace == namespace,
            Concept.name == concept_name
        )
    )
    concept = result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {namespace}.{concept_name} not found"
        )

    if not concept.is_self_managed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Concept {namespace}.{concept_name} is not self-managed"
        )

    return concept


@router.post("/{namespace}/{concept_name}", status_code=status.HTTP_201_CREATED)
async def create_concept_data(
    request: Request,
    namespace: str,
    concept_name: str,
    data: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    auth_data: tuple = Depends(get_current_user_with_permissions),
):
    """Create a new record for a self-managed concept."""
    user_id, permissions = auth_data

    # Check permission for this specific concept
    perm_key = f"sinas.ontology.data.{namespace}.{concept_name}.create"
    wildcard_perm = "sinas.ontology.data.*.create"

    has_specific = check_permission(permissions, perm_key)
    has_wildcard = check_permission(permissions, wildcard_perm)

    if has_specific:
        set_permission_used(request, perm_key, True)
    elif has_wildcard:
        set_permission_used(request, wildcard_perm, True)
    else:
        set_permission_used(request, perm_key, False)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {perm_key}"
        )

    # Get concept
    concept = await get_concept_by_path(namespace, concept_name, db)

    # Get table name
    schema_manager = SchemaManager(db)
    table_name = schema_manager.get_table_name(concept)

    # Build insert query
    columns = ["id"] + list(data.keys())
    placeholders = [":id"] + [f":{key}" for key in data.keys()]

    insert_sql = f"""
        INSERT INTO {table_name} ({', '.join(columns)})
        VALUES ({', '.join(placeholders)})
        RETURNING *
    """

    # Generate ID
    record_id = uuid4()
    params = {"id": str(record_id), **data}

    try:
        result = await db.execute(text(insert_sql), params)
        await db.commit()

        row = result.fetchone()
        return dict(row._mapping)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create record: {str(e)}"
        )


@router.get("/{namespace}/{concept_name}")
async def list_concept_data(
    request: Request,
    namespace: str,
    concept_name: str,
    limit: int = QueryParam(100, ge=1, le=1000),
    offset: int = QueryParam(0, ge=0),
    db: AsyncSession = Depends(get_db),
    auth_data: tuple = Depends(get_current_user_with_permissions),
):
    """List all records for a self-managed concept."""
    user_id, permissions = auth_data

    # Check permission
    perm_key = f"sinas.ontology.data.{namespace}.{concept_name}.read"
    wildcard_perm = "sinas.ontology.data.*.read"

    has_specific = check_permission(permissions, perm_key)
    has_wildcard = check_permission(permissions, wildcard_perm)

    if has_specific:
        set_permission_used(request, perm_key, True)
    elif has_wildcard:
        set_permission_used(request, wildcard_perm, True)
    else:
        set_permission_used(request, perm_key, False)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {perm_key}"
        )

    # Get concept
    concept = await get_concept_by_path(namespace, concept_name, db)

    # Get table name
    schema_manager = SchemaManager(db)
    table_name = schema_manager.get_table_name(concept)

    # Build select query
    select_sql = f"""
        SELECT * FROM {table_name}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """

    try:
        result = await db.execute(text(select_sql), {"limit": limit, "offset": offset})
        rows = result.fetchall()

        return {
            "data": [dict(row._mapping) for row in rows],
            "count": len(rows),
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch records: {str(e)}"
        )


@router.get("/{namespace}/{concept_name}/{record_id}")
async def get_concept_data(
    request: Request,
    namespace: str,
    concept_name: str,
    record_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get a specific record by ID."""
    user_id, permissions = auth_data

    # Check permission
    perm_key = f"sinas.ontology.data.{namespace}.{concept_name}.read"
    wildcard_perm = "sinas.ontology.data.*.read"

    has_specific = check_permission(permissions, perm_key)
    has_wildcard = check_permission(permissions, wildcard_perm)

    if has_specific:
        set_permission_used(request, perm_key, True)
    elif has_wildcard:
        set_permission_used(request, wildcard_perm, True)
    else:
        set_permission_used(request, perm_key, False)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {perm_key}"
        )

    # Get concept
    concept = await get_concept_by_path(namespace, concept_name, db)

    # Get table name
    schema_manager = SchemaManager(db)
    table_name = schema_manager.get_table_name(concept)

    # Build select query
    select_sql = f"SELECT * FROM {table_name} WHERE id = :id"

    try:
        result = await db.execute(text(select_sql), {"id": str(record_id)})
        row = result.fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Record {record_id} not found"
            )

        return dict(row._mapping)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch record: {str(e)}"
        )


@router.put("/{namespace}/{concept_name}/{record_id}")
async def update_concept_data(
    request: Request,
    namespace: str,
    concept_name: str,
    record_id: UUID,
    data: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    auth_data: tuple = Depends(get_current_user_with_permissions),
):
    """Update a specific record."""
    user_id, permissions = auth_data

    # Check permission
    perm_key = f"sinas.ontology.data.{namespace}.{concept_name}.update"
    wildcard_perm = "sinas.ontology.data.*.update"

    has_specific = check_permission(permissions, perm_key)
    has_wildcard = check_permission(permissions, wildcard_perm)

    if has_specific:
        set_permission_used(request, perm_key, True)
    elif has_wildcard:
        set_permission_used(request, wildcard_perm, True)
    else:
        set_permission_used(request, perm_key, False)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {perm_key}"
        )

    # Get concept
    concept = await get_concept_by_path(namespace, concept_name, db)

    # Get table name
    schema_manager = SchemaManager(db)
    table_name = schema_manager.get_table_name(concept)

    # Build update query
    set_clauses = [f"{key} = :{key}" for key in data.keys()]
    update_sql = f"""
        UPDATE {table_name}
        SET {', '.join(set_clauses)}, updated_at = NOW()
        WHERE id = :id
        RETURNING *
    """

    params = {"id": str(record_id), **data}

    try:
        result = await db.execute(text(update_sql), params)
        await db.commit()

        row = result.fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Record {record_id} not found"
            )

        return dict(row._mapping)

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update record: {str(e)}"
        )


@router.delete("/{namespace}/{concept_name}/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_concept_data(
    request: Request,
    namespace: str,
    concept_name: str,
    record_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth_data: tuple = Depends(get_current_user_with_permissions),
):
    """Delete a specific record."""
    user_id, permissions = auth_data

    # Check permission
    perm_key = f"sinas.ontology.data.{namespace}.{concept_name}.delete"
    wildcard_perm = "sinas.ontology.data.*.delete"

    has_specific = check_permission(permissions, perm_key)
    has_wildcard = check_permission(permissions, wildcard_perm)

    if has_specific:
        set_permission_used(request, perm_key, True)
    elif has_wildcard:
        set_permission_used(request, wildcard_perm, True)
    else:
        set_permission_used(request, perm_key, False)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {perm_key}"
        )

    # Get concept
    concept = await get_concept_by_path(namespace, concept_name, db)

    # Get table name
    schema_manager = SchemaManager(db)
    table_name = schema_manager.get_table_name(concept)

    # Build delete query
    delete_sql = f"DELETE FROM {table_name} WHERE id = :id"

    try:
        result = await db.execute(text(delete_sql), {"id": str(record_id)})
        await db.commit()

        if result.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Record {record_id} not found"
            )

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete record: {str(e)}"
        )
