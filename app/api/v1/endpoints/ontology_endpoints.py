"""API endpoints for Endpoint configuration management."""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query as QueryParam
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models import (
    Endpoint,
    EndpointProperty,
    EndpointFilter,
    EndpointOrder,
    EndpointJoin,
    Concept,
    Property,
    Relationship,
)
from app.schemas.ontology import (
    EndpointCreate,
    EndpointUpdate,
    EndpointResponse,
    EndpointPropertyCreate,
    EndpointPropertyResponse,
    EndpointFilterCreate,
    EndpointFilterResponse,
    EndpointOrderCreate,
    EndpointOrderResponse,
    EndpointJoinCreate,
    EndpointJoinResponse,
)

router = APIRouter(prefix="/ontology/endpoints", tags=["Ontology - Endpoints"])


# ============================================================================
# Endpoint CRUD
# ============================================================================

@router.post("", response_model=EndpointResponse, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    request: Request,
    endpoint: EndpointCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Create a new API endpoint configuration."""
    user_id, permissions = current_user_data

    # Verify concept exists
    result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = result.scalar_one_or_none()
    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept {endpoint.subject_concept_id} not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.create:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to create endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # Check route uniqueness
    result = await db.execute(
        select(Endpoint).where(Endpoint.route == endpoint.route)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Route {endpoint.route} already exists"
        )

    db_endpoint = Endpoint(
        name=endpoint.name,
        route=endpoint.route,
        subject_concept_id=endpoint.subject_concept_id,
        response_format=endpoint.response_format,
        enabled=endpoint.enabled,
        description=endpoint.description,
        limit_default=endpoint.limit_default,
    )

    db.add(db_endpoint)
    await db.commit()
    await db.refresh(db_endpoint)

    return db_endpoint


@router.get("", response_model=List[EndpointResponse])
async def list_endpoints(
    request: Request,
    enabled: Optional[bool] = QueryParam(None),
    concept_id: Optional[UUID] = QueryParam(None),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List all endpoints with optional filters."""
    user_id, permissions = current_user_data

    query = select(Endpoint).join(Concept, Endpoint.subject_concept_id == Concept.id)

    if enabled is not None:
        query = query.where(Endpoint.enabled == enabled)
    if concept_id:
        query = query.where(Endpoint.subject_concept_id == concept_id)

    result = await db.execute(query.order_by(Endpoint.name))
    endpoints = result.scalars().all()

    # Filter endpoints based on permissions
    filtered_endpoints = []
    for endpoint in endpoints:
        # Fetch the concept to get namespace and name
        concept_result = await db.execute(
            select(Concept).where(Concept.id == endpoint.subject_concept_id)
        )
        concept = concept_result.scalar_one_or_none()
        if concept:
            required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.read:group"
            if check_permission(permissions, required_perm):
                filtered_endpoints.append(endpoint)

    # Set permission used for tracking (use wildcard pattern)
    set_permission_used(request, "sinas.ontology.endpoints.*.*.read:group", has_perm=True)

    return filtered_endpoints


@router.get("/{endpoint_id}", response_model=EndpointResponse)
async def get_endpoint(
    request: Request,
    endpoint_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get a specific endpoint by ID."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Endpoint).where(Endpoint.id == endpoint_id)
    )
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint {endpoint_id} not found"
        )

    # Fetch the concept to get namespace and name
    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.read:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to read endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    return endpoint


@router.put("/{endpoint_id}", response_model=EndpointResponse)
async def update_endpoint(
    request: Request,
    endpoint_id: UUID,
    endpoint_update: EndpointUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Update an endpoint."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Endpoint).where(Endpoint.id == endpoint_id)
    )
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint {endpoint_id} not found"
        )

    # Fetch the concept to get namespace and name
    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    # Check route uniqueness if being updated
    update_data = endpoint_update.model_dump(exclude_unset=True)

    if "route" in update_data:
        result = await db.execute(
            select(Endpoint).where(
                Endpoint.route == update_data["route"],
                Endpoint.id != endpoint_id
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Route {update_data['route']} already exists"
            )

    for field, value in update_data.items():
        setattr(endpoint, field, value)

    await db.commit()
    await db.refresh(endpoint)

    return endpoint


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    request: Request,
    endpoint_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Delete an endpoint."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Endpoint).where(Endpoint.id == endpoint_id)
    )
    endpoint = result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint {endpoint_id} not found"
        )

    # Fetch the concept to get namespace and name
    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.delete:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to delete endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    await db.delete(endpoint)
    await db.commit()


# ============================================================================
# Endpoint Properties
# ============================================================================

@router.post("/properties", response_model=EndpointPropertyResponse, status_code=status.HTTP_201_CREATED)
async def add_endpoint_property(
    request: Request,
    prop: EndpointPropertyCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Add a property to an endpoint."""
    user_id, permissions = current_user_data

    # Verify endpoint, concept, and property exist
    result = await db.execute(
        select(Endpoint).where(Endpoint.id == prop.endpoint_id)
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")

    # Fetch the concept to get namespace and name
    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific (update because we're modifying endpoint configuration)
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    result = await db.execute(
        select(Property).where(Property.id == prop.property_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")

    db_prop = EndpointProperty(
        endpoint_id=prop.endpoint_id,
        concept_id=prop.concept_id,
        property_id=prop.property_id,
        alias=prop.alias,
        aggregation=prop.aggregation,
        include=prop.include,
    )

    db.add(db_prop)
    await db.commit()
    await db.refresh(db_prop)

    return db_prop


@router.get("/properties", response_model=List[EndpointPropertyResponse])
async def list_endpoint_properties(
    request: Request,
    endpoint_id: Optional[UUID] = QueryParam(None),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List endpoint properties."""
    user_id, permissions = current_user_data

    query = select(EndpointProperty)

    if endpoint_id:
        query = query.where(EndpointProperty.endpoint_id == endpoint_id)

    result = await db.execute(query)
    properties = result.scalars().all()

    # Filter properties based on permissions
    filtered_properties = []
    for prop in properties:
        # Fetch the endpoint and concept to get namespace and name
        endpoint_result = await db.execute(
            select(Endpoint).where(Endpoint.id == prop.endpoint_id)
        )
        endpoint = endpoint_result.scalar_one_or_none()
        if endpoint:
            concept_result = await db.execute(
                select(Concept).where(Concept.id == endpoint.subject_concept_id)
            )
            concept = concept_result.scalar_one_or_none()
            if concept:
                required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.read:group"
                if check_permission(permissions, required_perm):
                    filtered_properties.append(prop)

    # Set permission used for tracking (use wildcard pattern)
    set_permission_used(request, "sinas.ontology.endpoints.*.*.read:group", has_perm=True)

    return filtered_properties


@router.delete("/properties/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint_property(
    request: Request,
    property_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Remove a property from an endpoint."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(EndpointProperty).where(EndpointProperty.id == property_id)
    )
    prop = result.scalar_one_or_none()

    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint property not found")

    # Fetch the endpoint and concept to get namespace and name
    endpoint_result = await db.execute(
        select(Endpoint).where(Endpoint.id == prop.endpoint_id)
    )
    endpoint = endpoint_result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated endpoint not found"
        )

    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific (update because we're modifying endpoint configuration)
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    await db.delete(prop)
    await db.commit()


# ============================================================================
# Endpoint Filters
# ============================================================================

@router.post("/filters", response_model=EndpointFilterResponse, status_code=status.HTTP_201_CREATED)
async def add_endpoint_filter(
    request: Request,
    filter_data: EndpointFilterCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Add a filter to an endpoint."""
    user_id, permissions = current_user_data

    # Verify endpoint and property exist
    result = await db.execute(
        select(Endpoint).where(Endpoint.id == filter_data.endpoint_id)
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")

    # Fetch the concept to get namespace and name
    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific (update because we're modifying endpoint configuration)
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    result = await db.execute(
        select(Property).where(Property.id == filter_data.property_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")

    db_filter = EndpointFilter(
        endpoint_id=filter_data.endpoint_id,
        property_id=filter_data.property_id,
        op=filter_data.op,
        param_name=filter_data.param_name,
        required=filter_data.required,
        default_value=filter_data.default_value,
    )

    db.add(db_filter)
    await db.commit()
    await db.refresh(db_filter)

    return db_filter


@router.get("/filters", response_model=List[EndpointFilterResponse])
async def list_endpoint_filters(
    request: Request,
    endpoint_id: Optional[UUID] = QueryParam(None),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List endpoint filters."""
    user_id, permissions = current_user_data

    query = select(EndpointFilter)

    if endpoint_id:
        query = query.where(EndpointFilter.endpoint_id == endpoint_id)

    result = await db.execute(query)
    filters = result.scalars().all()

    # Filter based on permissions
    filtered_filters = []
    for filter_obj in filters:
        # Fetch the endpoint and concept to get namespace and name
        endpoint_result = await db.execute(
            select(Endpoint).where(Endpoint.id == filter_obj.endpoint_id)
        )
        endpoint = endpoint_result.scalar_one_or_none()
        if endpoint:
            concept_result = await db.execute(
                select(Concept).where(Concept.id == endpoint.subject_concept_id)
            )
            concept = concept_result.scalar_one_or_none()
            if concept:
                required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.read:group"
                if check_permission(permissions, required_perm):
                    filtered_filters.append(filter_obj)

    # Set permission used for tracking (use wildcard pattern)
    set_permission_used(request, "sinas.ontology.endpoints.*.*.read:group", has_perm=True)

    return filtered_filters


@router.delete("/filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint_filter(
    request: Request,
    filter_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Remove a filter from an endpoint."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(EndpointFilter).where(EndpointFilter.id == filter_id)
    )
    filter_obj = result.scalar_one_or_none()

    if not filter_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint filter not found")

    # Fetch the endpoint and concept to get namespace and name
    endpoint_result = await db.execute(
        select(Endpoint).where(Endpoint.id == filter_obj.endpoint_id)
    )
    endpoint = endpoint_result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated endpoint not found"
        )

    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific (update because we're modifying endpoint configuration)
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    await db.delete(filter_obj)
    await db.commit()


# ============================================================================
# Endpoint Orders
# ============================================================================

@router.post("/orders", response_model=EndpointOrderResponse, status_code=status.HTTP_201_CREATED)
async def add_endpoint_order(
    request: Request,
    order_data: EndpointOrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Add an order/sort to an endpoint."""
    user_id, permissions = current_user_data

    # Verify endpoint and property exist
    result = await db.execute(
        select(Endpoint).where(Endpoint.id == order_data.endpoint_id)
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")

    # Fetch the concept to get namespace and name
    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific (update because we're modifying endpoint configuration)
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    result = await db.execute(
        select(Property).where(Property.id == order_data.property_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")

    db_order = EndpointOrder(
        endpoint_id=order_data.endpoint_id,
        property_id=order_data.property_id,
        direction=order_data.direction,
        priority=order_data.priority,
    )

    db.add(db_order)
    await db.commit()
    await db.refresh(db_order)

    return db_order


@router.get("/orders", response_model=List[EndpointOrderResponse])
async def list_endpoint_orders(
    request: Request,
    endpoint_id: Optional[UUID] = QueryParam(None),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List endpoint orders."""
    user_id, permissions = current_user_data

    query = select(EndpointOrder)

    if endpoint_id:
        query = query.where(EndpointOrder.endpoint_id == endpoint_id)

    result = await db.execute(query.order_by(EndpointOrder.priority))
    orders = result.scalars().all()

    # Filter based on permissions
    filtered_orders = []
    for order in orders:
        # Fetch the endpoint and concept to get namespace and name
        endpoint_result = await db.execute(
            select(Endpoint).where(Endpoint.id == order.endpoint_id)
        )
        endpoint = endpoint_result.scalar_one_or_none()
        if endpoint:
            concept_result = await db.execute(
                select(Concept).where(Concept.id == endpoint.subject_concept_id)
            )
            concept = concept_result.scalar_one_or_none()
            if concept:
                required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.read:group"
                if check_permission(permissions, required_perm):
                    filtered_orders.append(order)

    # Set permission used for tracking (use wildcard pattern)
    set_permission_used(request, "sinas.ontology.endpoints.*.*.read:group", has_perm=True)

    return filtered_orders


@router.delete("/orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint_order(
    request: Request,
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Remove an order from an endpoint."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(EndpointOrder).where(EndpointOrder.id == order_id)
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint order not found")

    # Fetch the endpoint and concept to get namespace and name
    endpoint_result = await db.execute(
        select(Endpoint).where(Endpoint.id == order.endpoint_id)
    )
    endpoint = endpoint_result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated endpoint not found"
        )

    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific (update because we're modifying endpoint configuration)
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    await db.delete(order)
    await db.commit()


# ============================================================================
# Endpoint Joins
# ============================================================================

@router.post("/joins", response_model=EndpointJoinResponse, status_code=status.HTTP_201_CREATED)
async def add_endpoint_join(
    request: Request,
    join_data: EndpointJoinCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Add a join to an endpoint."""
    user_id, permissions = current_user_data

    # Verify endpoint and relationship exist
    result = await db.execute(
        select(Endpoint).where(Endpoint.id == join_data.endpoint_id)
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")

    # Fetch the concept to get namespace and name
    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific (update because we're modifying endpoint configuration)
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    result = await db.execute(
        select(Relationship).where(Relationship.id == join_data.relationship_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found")

    db_join = EndpointJoin(
        endpoint_id=join_data.endpoint_id,
        relationship_id=join_data.relationship_id,
        join_type=join_data.join_type,
    )

    db.add(db_join)
    await db.commit()
    await db.refresh(db_join)

    return db_join


@router.get("/joins", response_model=List[EndpointJoinResponse])
async def list_endpoint_joins(
    request: Request,
    endpoint_id: Optional[UUID] = QueryParam(None),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List endpoint joins."""
    user_id, permissions = current_user_data

    query = select(EndpointJoin)

    if endpoint_id:
        query = query.where(EndpointJoin.endpoint_id == endpoint_id)

    result = await db.execute(query)
    joins = result.scalars().all()

    # Filter based on permissions
    filtered_joins = []
    for join in joins:
        # Fetch the endpoint and concept to get namespace and name
        endpoint_result = await db.execute(
            select(Endpoint).where(Endpoint.id == join.endpoint_id)
        )
        endpoint = endpoint_result.scalar_one_or_none()
        if endpoint:
            concept_result = await db.execute(
                select(Concept).where(Concept.id == endpoint.subject_concept_id)
            )
            concept = concept_result.scalar_one_or_none()
            if concept:
                required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.read:group"
                if check_permission(permissions, required_perm):
                    filtered_joins.append(join)

    # Set permission used for tracking (use wildcard pattern)
    set_permission_used(request, "sinas.ontology.endpoints.*.*.read:group", has_perm=True)

    return filtered_joins


@router.delete("/joins/{join_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint_join(
    request: Request,
    join_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Remove a join from an endpoint."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(EndpointJoin).where(EndpointJoin.id == join_id)
    )
    join = result.scalar_one_or_none()

    if not join:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint join not found")

    # Fetch the endpoint and concept to get namespace and name
    endpoint_result = await db.execute(
        select(Endpoint).where(Endpoint.id == join.endpoint_id)
    )
    endpoint = endpoint_result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated endpoint not found"
        )

    concept_result = await db.execute(
        select(Concept).where(Concept.id == endpoint.subject_concept_id)
    )
    concept = concept_result.scalar_one_or_none()

    if not concept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated concept not found"
        )

    # Check permissions - namespace.concept specific (update because we're modifying endpoint configuration)
    required_perm = f"sinas.ontology.endpoints.{concept.namespace}.{concept.name}.update:group"
    if not check_permission(permissions, required_perm):
        set_permission_used(request, required_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to update endpoints for {concept.namespace}.{concept.name}")
    set_permission_used(request, required_perm, has_perm=True)

    await db.delete(join)
    await db.commit()
