"""Shared dependencies for runtime API endpoints."""
from typing import Optional

from fastapi import Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manifest import Manifest


async def get_manifest_context(
    db: AsyncSession,
    x_application: Optional[str] = Header(None),
    app_query: Optional[str] = Query(None, alias="app"),
) -> Optional[Manifest]:
    """Resolve manifest from X-Application header or ?app= query param.

    Returns None if neither provided; raises 404 if provided but not found.
    """
    app_ref = x_application or app_query
    if not app_ref:
        return None

    if "/" not in app_ref:
        raise HTTPException(status_code=400, detail="Manifest reference must be in 'namespace/name' format")

    namespace, name = app_ref.split("/", 1)
    manifest = await Manifest.get_by_name(db, namespace=namespace, name=name)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Manifest '{app_ref}' not found")

    return manifest


def get_namespace_filter(manifest: Optional[Manifest], resource_type: str) -> Optional[list[str]]:
    """Return namespace list from manifest.exposed_namespaces for a resource type.

    Returns None when no manifest context (no filtering).
    Returns None when manifest exposes ["*"] for this type (all namespaces).
    Returns [] when manifest doesn't expose this resource type (empty result).
    Returns list of namespaces to filter by otherwise.
    """
    if manifest is None:
        return None

    exposed = manifest.exposed_namespaces or {}
    if resource_type not in exposed:
        return []

    namespaces = exposed[resource_type]
    if namespaces == ["*"]:
        return None

    return namespaces
