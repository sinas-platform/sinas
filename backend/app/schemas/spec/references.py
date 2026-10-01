"""Target references: one config field per target type, holding "ns/name"."""

from typing import Any

# Config YAML names the target with one field per target type, holding
# "namespace/name" (namespace defaults to "default"). REST splits it into
# separate namespace and name fields; the canonical spec holds
# target_namespace / target_name.
CONFIG_REF_FIELD: dict[str, str] = {
    "function": "functionName",
    "agent": "agentName",
    "pipeline": "pipelineName",
}


def accept_config_reference(data: Any, kind: str) -> Any:
    """Turn the config shape's `targetType` + `functionName` / `agentName` /
    `pipelineName` into target_namespace / target_name. Only the target
    type's own reference is kept: the others never mean anything, and
    storing them let a later type switch silently pick up a stale one."""
    if not isinstance(data, dict):
        return data
    refs = {field: data.get(field) for field in CONFIG_REF_FIELD.values()}
    if not any(field in data for field in refs):
        return data
    data = {key: value for key, value in data.items() if key not in refs}
    target_type = data.get("targetType", data.get("target_type", "function"))
    field = CONFIG_REF_FIELD.get(target_type)
    if field is None:
        return data  # let the Literal report the bad target type
    ref = refs.get(field)
    if not ref:
        raise ValueError(f"{field} is required for {target_type}-target {kind}")
    namespace, name = ref.split("/", 1) if "/" in ref else ("default", ref)
    return {**data, "target_namespace": namespace, "target_name": name}


def config_reference(target_type: str, namespace: str, name: Any) -> dict[str, str]:
    field = CONFIG_REF_FIELD.get(target_type)
    return {field: f"{namespace}/{name}"} if field and name else {}
