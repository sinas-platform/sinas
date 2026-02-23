"""Runtime discovery endpoints — list resources visible to the current user, optionally filtered by app context."""
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.runtime.dependencies import get_app_context, get_namespace_filter
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.models.agent import Agent
from app.models.app import App
from app.models.file import Collection
from app.models.function import Function
from app.models.skill import Skill
from app.models.template import Template
from app.schemas.agent import AgentResponse
from app.schemas.file import CollectionResponse
from app.schemas.function import FunctionResponse
from app.schemas.skill import SkillResponse
from app.schemas.template import TemplateResponse

router = APIRouter()


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(
    request: Request,
    x_application: Optional[str] = Header(None),
    app_query: Optional[str] = Query(None, alias="app"),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List agents visible to the current user, optionally filtered by app context."""
    user_id, permissions = current_user_data

    app = await get_app_context(db, x_application, app_query)
    ns_filter = get_namespace_filter(app, "agents")
    if ns_filter is not None and len(ns_filter) == 0:
        set_permission_used(request, "sinas.agents.read")
        return []

    filters = Agent.is_active == True  # noqa: E712
    if ns_filter is not None:
        filters = and_(filters, Agent.namespace.in_(ns_filter))

    agents = await Agent.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=filters,
    )

    set_permission_used(request, "sinas.agents.read")
    return [AgentResponse.model_validate(agent) for agent in agents]


@router.get("/functions", response_model=list[FunctionResponse])
async def list_functions(
    request: Request,
    x_application: Optional[str] = Header(None),
    app_query: Optional[str] = Query(None, alias="app"),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List functions visible to the current user, optionally filtered by app context."""
    user_id, permissions = current_user_data

    app = await get_app_context(db, x_application, app_query)
    ns_filter = get_namespace_filter(app, "functions")
    if ns_filter is not None and len(ns_filter) == 0:
        set_permission_used(request, "sinas.functions.read")
        return []

    filters = Function.is_active == True  # noqa: E712
    if ns_filter is not None:
        filters = and_(filters, Function.namespace.in_(ns_filter))

    functions = await Function.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=filters,
    )

    set_permission_used(request, "sinas.functions.read")
    return [FunctionResponse.model_validate(f) for f in functions]


@router.get("/skills", response_model=list[SkillResponse])
async def list_skills(
    request: Request,
    x_application: Optional[str] = Header(None),
    app_query: Optional[str] = Query(None, alias="app"),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List skills visible to the current user, optionally filtered by app context."""
    user_id, permissions = current_user_data

    app = await get_app_context(db, x_application, app_query)
    ns_filter = get_namespace_filter(app, "skills")
    if ns_filter is not None and len(ns_filter) == 0:
        set_permission_used(request, "sinas.skills.read")
        return []

    filters = Skill.is_active == True  # noqa: E712
    if ns_filter is not None:
        filters = and_(filters, Skill.namespace.in_(ns_filter))

    skills = await Skill.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=filters,
    )

    set_permission_used(request, "sinas.skills.read")
    return [SkillResponse.model_validate(skill) for skill in skills]


@router.get("/collections", response_model=list[CollectionResponse])
async def list_collections(
    request: Request,
    x_application: Optional[str] = Header(None),
    app_query: Optional[str] = Query(None, alias="app"),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List collections visible to the current user, optionally filtered by app context."""
    user_id, permissions = current_user_data

    app = await get_app_context(db, x_application, app_query)
    ns_filter = get_namespace_filter(app, "collections")
    if ns_filter is not None and len(ns_filter) == 0:
        set_permission_used(request, "sinas.collections.read")
        return []

    filters = None
    if ns_filter is not None:
        filters = Collection.namespace.in_(ns_filter)

    collections = await Collection.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=filters,
    )

    set_permission_used(request, "sinas.collections.read")
    return [CollectionResponse.model_validate(col) for col in collections]


@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates(
    request: Request,
    x_application: Optional[str] = Header(None),
    app_query: Optional[str] = Query(None, alias="app"),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List templates visible to the current user, optionally filtered by app context."""
    user_id, permissions = current_user_data

    app = await get_app_context(db, x_application, app_query)
    ns_filter = get_namespace_filter(app, "templates")
    if ns_filter is not None and len(ns_filter) == 0:
        set_permission_used(request, "sinas.templates.read")
        return []

    filters = Template.is_active == True  # noqa: E712
    if ns_filter is not None:
        filters = and_(filters, Template.namespace.in_(ns_filter))

    templates = await Template.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=filters,
    )

    set_permission_used(request, "sinas.templates.read")
    return [TemplateResponse.model_validate(t) for t in templates]
