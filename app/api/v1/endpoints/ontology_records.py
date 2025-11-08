"""Ontology data endpoints for CRUD operations on self-managed concepts."""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Optional
import uuid as uuid_lib
import json
from datetime import datetime

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.permissions import check_permission
from app.models.ontology import Concept, Property, DataType
from app.models.user import GroupMember
from pydantic import BaseModel

router = APIRouter(prefix="/ontology/records", tags=["ontology-records"])


class OntologyRecordCreate(BaseModel):
    """Schema for creating ontology data."""
    data: Dict[str, Any]


class OntologyRecordUpdate(BaseModel):
    """Schema for updating ontology data."""
    data: Dict[str, Any]


async def get_user_group_ids(db: AsyncSession, user_id: uuid_lib.UUID) -> List[uuid_lib.UUID]:
    """Get all group IDs that the user is a member of."""
    result = await db.execute(
        select(GroupMember.group_id).where(
            and_(
                GroupMember.user_id == user_id,
                GroupMember.active == True
            )
        )
    )
    return [row[0] for row in result.all()]


async def get_concept_with_permissions(
    db: AsyncSession,
    namespace: str,
    concept_name: str,
    user_groups: List[uuid_lib.UUID]
) -> Concept:
    """Get a concept and verify user has access via groups."""
    result = await db.execute(
        select(Concept).where(
            and_(
                Concept.namespace == namespace,
                Concept.name == concept_name,
                Concept.is_self_managed == True,
                Concept.group_id.in_(user_groups) if user_groups else False
            )
        )
    )
    concept = result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=404,
            detail=f"Self-managed concept '{namespace}.{concept_name}' not found or not accessible"
        )

    return concept


def get_dynamic_table_name(concept: Concept) -> str:
    """Get the dynamic table name for a concept."""
    # Sanitize namespace and name for table naming
    safe_namespace = concept.namespace.lower().replace('-', '_').replace('.', '_')
    safe_name = concept.name.lower().replace('-', '_').replace('.', '_')
    return f"ontology_{safe_namespace}_{safe_name}"


def validate_data_against_properties(
    data: Dict[str, Any],
    properties: List[Property],
    is_create: bool = False
) -> Dict[str, Any]:
    """
    Validate and cast data against concept properties.

    Args:
        data: Input data dictionary
        properties: List of Property models
        is_create: If True, require all required properties

    Returns:
        Validated and cast data dictionary

    Raises:
        HTTPException: If validation fails
    """
    validated = {}
    property_map = {prop.name: prop for prop in properties}

    # Check required properties for create operations
    if is_create:
        for prop in properties:
            if prop.is_required and prop.name not in data:
                if prop.default_value is not None:
                    data[prop.name] = prop.default_value
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Required property '{prop.name}' is missing"
                    )

    # Validate and cast each provided property
    for key, value in data.items():
        if key not in property_map:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown property '{key}' for this concept"
            )

        prop = property_map[key]

        # Type casting and validation
        try:
            if value is None:
                if prop.is_required:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Property '{key}' is required and cannot be null"
                    )
                validated[key] = None
            elif prop.data_type == DataType.STRING:
                validated[key] = str(value)
            elif prop.data_type == DataType.INT:
                validated[key] = int(value)
            elif prop.data_type == DataType.DECIMAL:
                validated[key] = float(value)
            elif prop.data_type == DataType.BOOL:
                validated[key] = bool(value)
            elif prop.data_type == DataType.DATETIME:
                # Accept ISO format strings or datetime objects
                if isinstance(value, str):
                    validated[key] = datetime.fromisoformat(value.replace('Z', '+00:00'))
                else:
                    validated[key] = value
            elif prop.data_type == DataType.JSON:
                # Ensure it's valid JSON-serializable
                validated[key] = value if isinstance(value, (dict, list)) else json.loads(value)
            else:
                validated[key] = value
        except (ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid value for property '{key}': {str(e)}"
            )

    return validated


@router.post("/{namespace}/{concept_name}", response_model=Dict[str, Any])
async def create_record(
    request: Request,
    namespace: str,
    concept_name: str,
    data_create: OntologyRecordCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Create a new record for a self-managed concept."""
    user_id, permissions = current_user_data
    user_uuid = uuid_lib.UUID(user_id)

    # Check permissions - namespace/concept specific
    required_perm = f"sinas.ontology.records.{namespace}.{concept_name}.create:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create records in this concept")

    # Set permission used (most specific that matches)
    if permissions.get(f"sinas.ontology.records.{namespace}.{concept_name}.create:all"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.create:all")
    elif permissions.get(f"sinas.ontology.records.{namespace}.{concept_name}.create:group"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.create:group")
    elif permissions.get(f"sinas.ontology.records.{namespace}.*.create:all"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.*.create:all")
    elif permissions.get(f"sinas.ontology.records.{namespace}.*.create:group"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.*.create:group")
    elif permissions.get("sinas.ontology.records.*.*.create:all"):
        set_permission_used(request, "sinas.ontology.records.*.*.create:all")
    else:
        set_permission_used(request, "sinas.ontology.records.*.*.create:group")

    # Get user's groups
    user_groups = await get_user_group_ids(db, user_uuid)

    # Get concept and verify access
    concept = await get_concept_with_permissions(db, namespace, concept_name, user_groups)

    # Get concept properties
    result = await db.execute(
        select(Property).where(Property.concept_id == concept.id)
    )
    properties = result.scalars().all()

    # Validate data
    validated_data = validate_data_against_properties(data_create.data, properties, is_create=True)

    # Generate ID
    record_id = str(uuid_lib.uuid4())
    validated_data['id'] = record_id
    validated_data['created_at'] = datetime.utcnow()
    validated_data['updated_at'] = datetime.utcnow()

    # Get table name
    table_name = get_dynamic_table_name(concept)

    # Build INSERT statement
    columns = list(validated_data.keys())
    placeholders = [f":{col}" for col in columns]

    insert_sql = text(f"""
        INSERT INTO {table_name} ({', '.join(columns)})
        VALUES ({', '.join(placeholders)})
    """)

    try:
        await db.execute(insert_sql, validated_data)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create record: {str(e)}"
        )

    return {
        "id": record_id,
        "concept": concept_name,
        "namespace": namespace,
        "data": validated_data
    }


@router.get("/{namespace}/{concept_name}/{record_id}", response_model=Dict[str, Any])
async def get_record(
    request: Request,
    namespace: str,
    concept_name: str,
    record_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Get a specific record from a self-managed concept."""
    user_id, permissions = current_user_data
    user_uuid = uuid_lib.UUID(user_id)

    # Check permissions - namespace/concept specific
    if not check_ontology_record_permission(permissions, "read", namespace, concept_name, "group"):
        if not check_ontology_record_permission(permissions, "read", namespace, concept_name, "all"):
            set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.read:group", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to read records from this concept")

    # Set permission used (most specific that matches)
    if permissions.get(f"sinas.ontology.records.{namespace}.{concept_name}.read:all"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.read:all")
    elif permissions.get(f"sinas.ontology.records.{namespace}.{concept_name}.read:group"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.read:group")
    elif permissions.get(f"sinas.ontology.records.{namespace}.*.read:all"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.*.read:all")
    elif permissions.get(f"sinas.ontology.records.{namespace}.*.read:group"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.*.read:group")
    elif permissions.get("sinas.ontology.records.*.*.read:all"):
        set_permission_used(request, "sinas.ontology.records.*.*.read:all")
    else:
        set_permission_used(request, "sinas.ontology.records.*.*.read:group")

    # Get user's groups
    user_groups = await get_user_group_ids(db, user_uuid)

    # Get concept and verify access
    concept = await get_concept_with_permissions(db, namespace, concept_name, user_groups)

    # Get table name
    table_name = get_dynamic_table_name(concept)

    # Query the record
    select_sql = text(f"SELECT * FROM {table_name} WHERE id = :record_id")

    try:
        result = await db.execute(select_sql, {"record_id": record_id})
        row = result.mappings().one_or_none()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch record: {str(e)}"
        )

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Record not found"
        )

    return {
        "id": record_id,
        "concept": concept_name,
        "namespace": namespace,
        "data": dict(row)
    }


@router.put("/{namespace}/{concept_name}/{record_id}", response_model=Dict[str, Any])
async def update_record(
    request: Request,
    namespace: str,
    concept_name: str,
    record_id: str,
    data_update: OntologyRecordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Update a record in a self-managed concept."""
    user_id, permissions = current_user_data
    user_uuid = uuid_lib.UUID(user_id)

    # Check permissions - namespace/concept specific
    required_perm = f"sinas.ontology.records.{namespace}.{concept_name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update records in this concept")

    # Set permission used (most specific that matches)
    if permissions.get(f"sinas.ontology.records.{namespace}.{concept_name}.update:all"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.update:all")
    elif permissions.get(f"sinas.ontology.records.{namespace}.{concept_name}.update:group"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.update:group")
    elif permissions.get(f"sinas.ontology.records.{namespace}.*.update:all"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.*.update:all")
    elif permissions.get(f"sinas.ontology.records.{namespace}.*.update:group"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.*.update:group")
    elif permissions.get("sinas.ontology.records.*.*.update:all"):
        set_permission_used(request, "sinas.ontology.records.*.*.update:all")
    else:
        set_permission_used(request, "sinas.ontology.records.*.*.update:group")

    # Get user's groups
    user_groups = await get_user_group_ids(db, user_uuid)

    # Get concept and verify access
    concept = await get_concept_with_permissions(db, namespace, concept_name, user_groups)

    # Get concept properties
    result = await db.execute(
        select(Property).where(Property.concept_id == concept.id)
    )
    properties = result.scalars().all()

    # Validate data (not a create, so required fields are optional)
    validated_data = validate_data_against_properties(data_update.data, properties, is_create=False)

    # Add updated_at timestamp
    validated_data['updated_at'] = datetime.utcnow()

    # Get table name
    table_name = get_dynamic_table_name(concept)

    # Build UPDATE statement
    set_clauses = [f"{col} = :{col}" for col in validated_data.keys()]
    validated_data['record_id'] = record_id

    update_sql = text(f"""
        UPDATE {table_name}
        SET {', '.join(set_clauses)}
        WHERE id = :record_id
    """)

    try:
        result = await db.execute(update_sql, validated_data)
        await db.commit()

        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Record not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update record: {str(e)}"
        )

    # Fetch updated record
    select_sql = text(f"SELECT * FROM {table_name} WHERE id = :record_id")
    result = await db.execute(select_sql, {"record_id": record_id})
    row = result.mappings().one()

    return {
        "id": record_id,
        "concept": concept_name,
        "namespace": namespace,
        "data": dict(row)
    }


@router.delete("/{namespace}/{concept_name}/{record_id}")
async def delete_record(
    request: Request,
    namespace: str,
    concept_name: str,
    record_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a record from a self-managed concept."""
    user_id, permissions = current_user_data
    user_uuid = uuid_lib.UUID(user_id)

    # Check permissions - namespace/concept specific
    required_perm = f"sinas.ontology.records.{namespace}.{concept_name}.delete:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete records from this concept")

    # Set permission used (most specific that matches)
    if permissions.get(f"sinas.ontology.records.{namespace}.{concept_name}.delete:all"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.delete:all")
    elif permissions.get(f"sinas.ontology.records.{namespace}.{concept_name}.delete:group"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.delete:group")
    elif permissions.get(f"sinas.ontology.records.{namespace}.*.delete:all"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.*.delete:all")
    elif permissions.get(f"sinas.ontology.records.{namespace}.*.delete:group"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.*.delete:group")
    elif permissions.get("sinas.ontology.records.*.*.delete:all"):
        set_permission_used(request, "sinas.ontology.records.*.*.delete:all")
    else:
        set_permission_used(request, "sinas.ontology.records.*.*.delete:group")

    # Get user's groups
    user_groups = await get_user_group_ids(db, user_uuid)

    # Get concept and verify access
    concept = await get_concept_with_permissions(db, namespace, concept_name, user_groups)

    # Get table name
    table_name = get_dynamic_table_name(concept)

    # Build DELETE statement
    delete_sql = text(f"DELETE FROM {table_name} WHERE id = :record_id")

    try:
        result = await db.execute(delete_sql, {"record_id": record_id})
        await db.commit()

        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Record not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete record: {str(e)}"
        )

    return {
        "message": f"Record '{record_id}' deleted successfully",
        "concept": f"{namespace}.{concept_name}"
    }


@router.get("/{namespace}/{concept_name}", response_model=Dict[str, Any])
async def list_records(
    request: Request,
    namespace: str,
    concept_name: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List records from a self-managed concept with pagination."""
    user_id, permissions = current_user_data
    user_uuid = uuid_lib.UUID(user_id)

    # Check permissions - namespace/concept specific
    required_perm = f"sinas.ontology.records.{namespace}.{concept_name}.read:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read records from this concept")

    # Set permission used (most specific that matches)
    if permissions.get(f"sinas.ontology.records.{namespace}.{concept_name}.read:all"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.read:all")
    elif permissions.get(f"sinas.ontology.records.{namespace}.{concept_name}.read:group"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.{concept_name}.read:group")
    elif permissions.get(f"sinas.ontology.records.{namespace}.*.read:all"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.*.read:all")
    elif permissions.get(f"sinas.ontology.records.{namespace}.*.read:group"):
        set_permission_used(request, f"sinas.ontology.records.{namespace}.*.read:group")
    elif permissions.get("sinas.ontology.records.*.*.read:all"):
        set_permission_used(request, "sinas.ontology.records.*.*.read:all")
    else:
        set_permission_used(request, "sinas.ontology.records.*.*.read:group")

    # Get user's groups
    user_groups = await get_user_group_ids(db, user_uuid)

    # Get concept and verify access
    concept = await get_concept_with_permissions(db, namespace, concept_name, user_groups)

    # Get table name
    table_name = get_dynamic_table_name(concept)

    # Query records with pagination
    select_sql = text(f"""
        SELECT * FROM {table_name}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :skip
    """)

    count_sql = text(f"SELECT COUNT(*) as total FROM {table_name}")

    try:
        result = await db.execute(select_sql, {"skip": skip, "limit": limit})
        rows = result.mappings().all()

        count_result = await db.execute(count_sql)
        total = count_result.scalar()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch records: {str(e)}"
        )

    return {
        "concept": concept_name,
        "namespace": namespace,
        "total": total,
        "skip": skip,
        "limit": limit,
        "records": [dict(row) for row in rows]
    }
