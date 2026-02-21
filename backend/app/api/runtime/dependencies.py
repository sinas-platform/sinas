"""Shared dependencies for runtime API endpoints."""
from typing import Optional

from fastapi import Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app import App


async def get_app_context(
    db: AsyncSession,
    x_application: Optional[str] = Header(None),
    app_query: Optional[str] = Query(None, alias="app"),
) -> Optional[App]:
    """Resolve app from X-Application header or ?app= query param.

    Returns None if neither provided; raises 404 if provided but not found.
    """
    app_ref = x_application or app_query
    if not app_ref:
        return None

    if "/" not in app_ref:
        raise HTTPException(status_code=400, detail="App reference must be in 'namespace/name' format")

    namespace, name = app_ref.split("/", 1)
    app = await App.get_by_name(db, namespace=namespace, name=name)
    if not app:
        raise HTTPException(status_code=404, detail=f"App '{app_ref}' not found")

    return app


def get_namespace_filter(app: Optional[App], resource_type: str) -> Optional[list[str]]:
    """Return namespace list from app.exposed_namespaces for a resource type.

    Returns None when no app context (no filtering).
    Returns [] when app doesn't expose this resource type (empty result).
    Returns list of namespaces to filter by otherwise.
    """
    if app is None:
        return None

    exposed = app.exposed_namespaces or {}
    if resource_type not in exposed:
        return []

    return exposed[resource_type]
