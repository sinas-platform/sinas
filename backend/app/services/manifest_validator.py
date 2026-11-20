"""Manifest validation service — checks resource existence and permission satisfaction."""
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_permission
from app.models.agent import Agent
from app.models.component import Component
from app.models.connector import Connector
from app.models.manifest import Manifest
from app.models.file import Collection
from app.models.function import Function
from app.models.mcp_server import McpServer
from app.models.pipeline import Pipeline
from app.models.query import Query
from app.models.skill import Skill
from app.models.state import State
from app.models.store import Store
from app.schemas.manifest import ManifestStatusResponse, PermissionStatus, ResourceStatus, StoreDependencyStatus

# The resource types a manifest's requiredResources may name -> (model, the
# config kind it is declared under). One table for both checks of them: the
# config parser's (a config or package declaring a manifest whose required
# resource exists nowhere is refused) and /status's (ready or missing now).
MANIFEST_RESOURCE_TYPES = {
    "agent": (Agent, "agents"),
    "function": (Function, "functions"),
    "skill": (Skill, "skills"),
    "collection": (Collection, "collections"),
    "connector": (Connector, "connectors"),
    "query": (Query, "queries"),
    "store": (Store, "stores"),
    "component": (Component, "components"),
    "pipeline": (Pipeline, "pipelines"),
    "mcp_server": (McpServer, "mcpServers"),
}
# /status also accepts plurals (manifests registered through the API).
_PLURALS = {"queries": "query", "mcp_servers": "mcp_server"}
for _singular in MANIFEST_RESOURCE_TYPES:
    _PLURALS.setdefault(_singular + "s", _singular)
RESOURCE_TYPE_MAP = {
    **{t: model for t, (model, _kind) in MANIFEST_RESOURCE_TYPES.items()},
    **{p: MANIFEST_RESOURCE_TYPES[s][0] for p, s in _PLURALS.items()},
}


async def check_required_resources(
    db: AsyncSession, required_resources: list[dict[str, Any]]
) -> tuple[list[ResourceStatus], list[ResourceStatus]]:
    """(satisfied, missing) for a manifest's requiredResources, as they are
    now. A type this check doesn't know is reported missing."""
    satisfied: list[ResourceStatus] = []
    missing: list[ResourceStatus] = []
    for res_ref in required_resources or []:
        res_type = res_ref.get("type", "")
        res_ns = res_ref.get("namespace", "default")
        res_name = res_ref.get("name", "")
        status = ResourceStatus(type=res_type, namespace=res_ns, name=res_name, exists=False)

        model = RESOURCE_TYPE_MAP.get(res_type)
        if model is not None:
            resource = await model.get_by_name(db, namespace=res_ns, name=res_name)
            status.exists = resource is not None

        (satisfied if status.exists else missing).append(status)
    return satisfied, missing


async def validate_manifest_status(
    db: AsyncSession,
    manifest: Manifest,
    user_id: str,
    permissions: dict[str, bool],
) -> ManifestStatusResponse:
    """
    Validate a manifest's resource dependencies and permission requirements.

    Returns a structured status showing which resources exist and which
    permissions the user has.
    """
    # An exposed tool's resource has to exist like a required one.
    exposed = [
        {"type": t.get("type"), "namespace": t.get("namespace", "default"), "name": t.get("name")}
        for t in (manifest.exposed_tools or [])
    ]
    declared = list(manifest.required_resources or [])
    declared += [e for e in exposed if e not in declared]
    satisfied, missing = await check_required_resources(db, declared)

    # Check required permissions
    req_granted: list[str] = []
    req_missing: list[str] = []
    for perm in manifest.required_permissions or []:
        if check_permission(permissions, perm):
            req_granted.append(perm)
        else:
            req_missing.append(perm)

    # Check optional permissions
    opt_granted: list[str] = []
    opt_missing: list[str] = []
    for perm in manifest.optional_permissions or []:
        if check_permission(permissions, perm):
            opt_granted.append(perm)
        else:
            opt_missing.append(perm)

    # Check store dependencies
    stores_satisfied: list[StoreDependencyStatus] = []
    stores_missing: list[StoreDependencyStatus] = []
    for dep in manifest.store_dependencies or []:
        store_ref = dep.get("store", "")
        key = dep.get("key")
        status = StoreDependencyStatus(store=store_ref, key=key, exists=False)

        # Parse store ref
        parts = store_ref.split("/", 1)
        if len(parts) == 2:
            from app.models.store import Store

            store = await Store.get_by_name(db, parts[0], parts[1])
            if store:
                if key:
                    q = select(State).where(and_(State.store_id == store.id, State.key == key)).limit(1)
                    result = await db.execute(q)
                    status.exists = result.scalar_one_or_none() is not None
                else:
                    status.exists = True  # Store exists, no specific key required

        if status.exists:
            stores_satisfied.append(status)
        else:
            stores_missing.append(status)

    ready = len(missing) == 0 and len(req_missing) == 0 and len(stores_missing) == 0

    return ManifestStatusResponse(
        ready=ready,
        resources={"satisfied": satisfied, "missing": missing},
        permissions={
            "required": PermissionStatus(granted=req_granted, missing=req_missing),
            "optional": PermissionStatus(granted=opt_granted, missing=opt_missing),
        },
        stores={"satisfied": stores_satisfied, "missing": stores_missing},
    )
