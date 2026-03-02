"""App validation service — checks resource existence and permission satisfaction."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_permission
from app.models.agent import Agent
from app.models.app import App
from app.models.file import Collection
from app.models.function import Function
from app.models.skill import Skill
from app.models.state import State
from app.schemas.app import AppStatusResponse, PermissionStatus, ResourceStatus, StateDependencyStatus

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


async def validate_app_status(
    db: AsyncSession,
    app: App,
    user_id: str,
    permissions: dict[str, bool],
) -> AppStatusResponse:
    """
    Validate an app's resource dependencies and permission requirements.

    Returns a structured status showing which resources exist and which
    permissions the user has.
    """
    satisfied: list[ResourceStatus] = []
    missing: list[ResourceStatus] = []

    # Check each required resource
    for res_ref in app.required_resources or []:
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
    for perm in app.required_permissions or []:
        if check_permission(permissions, perm):
            req_granted.append(perm)
        else:
            req_missing.append(perm)

    # Check optional permissions
    opt_granted: list[str] = []
    opt_missing: list[str] = []
    for perm in app.optional_permissions or []:
        if check_permission(permissions, perm):
            opt_granted.append(perm)
        else:
            opt_missing.append(perm)

    # Check state dependencies
    states_satisfied: list[StateDependencyStatus] = []
    states_missing: list[StateDependencyStatus] = []
    for dep in app.state_dependencies or []:
        ns = dep.get("namespace", "")
        key = dep.get("key")
        status = StateDependencyStatus(namespace=ns, key=key, exists=False)

        q = select(State).where(State.namespace == ns)
        if key:
            q = q.where(State.key == key)
        q = q.limit(1)
        result = await db.execute(q)
        if result.scalar_one_or_none() is not None:
            status.exists = True

        if status.exists:
            states_satisfied.append(status)
        else:
            states_missing.append(status)

    ready = len(missing) == 0 and len(req_missing) == 0 and len(states_missing) == 0

    return AppStatusResponse(
        ready=ready,
        resources={"satisfied": satisfied, "missing": missing},
        permissions={
            "required": PermissionStatus(granted=req_granted, missing=req_missing),
            "optional": PermissionStatus(granted=opt_granted, missing=opt_missing),
        },
        states={"satisfied": states_satisfied, "missing": states_missing},
    )
