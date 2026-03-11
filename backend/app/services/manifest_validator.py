"""Manifest validation service — checks resource existence and permission satisfaction."""
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_permission
from app.models.agent import Agent
from app.models.manifest import Manifest
from app.models.file import Collection
from app.models.function import Function
from app.models.skill import Skill
from app.models.state import State
from app.schemas.manifest import ManifestStatusResponse, PermissionStatus, ResourceStatus, StoreDependencyStatus

# Map resource type strings to SQLAlchemy models (accept both singular and plural)
RESOURCE_TYPE_MAP = {
    "agent": Agent,
    "agents": Agent,
    "function": Function,
    "functions": Function,
    "skill": Skill,
    "skills": Skill,
    "collection": Collection,
    "collections": Collection,
}


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
    satisfied: list[ResourceStatus] = []
    missing: list[ResourceStatus] = []

    # Check each required resource
    for res_ref in manifest.required_resources or []:
        res_type = res_ref.get("type", "")
        res_ns = res_ref.get("namespace", "default")
        res_name = res_ref.get("name", "")

        model = RESOURCE_TYPE_MAP.get(res_type)
        status = ResourceStatus(type=res_type, namespace=res_ns, name=res_name, exists=False)

        if model is not None:
            resource = await model.get_by_name(db, namespace=res_ns, name=res_name)
            if resource is not None:
                status.exists = True

        if status.exists:
            satisfied.append(status)
        else:
            missing.append(status)

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
