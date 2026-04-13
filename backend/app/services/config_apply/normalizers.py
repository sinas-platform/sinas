"""
Normalization helpers for config references
"""
from typing import Any


def normalize_function_references(function_names: list[str]) -> list[str]:
    """
    Normalize function names to namespace/name format.
    If a function name doesn't contain '/', prepend 'default/' to it.

    Args:
        function_names: List of function names (may or may not include namespace)

    Returns:
        List of normalized function names in namespace/name format
    """
    normalized = []
    for func_name in function_names:
        if "/" not in func_name:
            # No namespace specified, use default
            normalized.append(f"default/{func_name}")
        else:
            # Already has namespace
            normalized.append(func_name)
    return normalized


def normalize_skill_references(skills: list[Any]) -> list[dict[str, Any]]:
    """
    Normalize skill references to dict format with skill and preload keys.
    Supports backward compatibility with string format.

    Args:
        skills: List of skill configs (strings or dicts)

    Returns:
        List of normalized skill configs as dicts
    """
    normalized = []
    for skill_item in skills:
        if isinstance(skill_item, str):
            # Old format: "namespace/name"
            skill_ref = skill_item
            if "/" not in skill_ref:
                skill_ref = f"default/{skill_ref}"
            normalized.append({"skill": skill_ref, "preload": False})
        elif isinstance(skill_item, dict):
            # New format: {"skill": "namespace/name", "preload": bool}
            skill_ref = skill_item.get("skill", "")
            if "/" not in skill_ref:
                skill_ref = f"default/{skill_ref}"
            normalized.append({"skill": skill_ref, "preload": skill_item.get("preload", False)})
        else:
            # Pydantic model (EnabledSkillConfigYaml)
            skill_ref = skill_item.skill
            if "/" not in skill_ref:
                skill_ref = f"default/{skill_ref}"
            normalized.append({"skill": skill_ref, "preload": skill_item.preload})
    return normalized


def normalize_store_references(store_refs: list) -> list[dict]:
    """Normalize store references to dict format."""
    normalized = []
    for ref in store_refs:
        if isinstance(ref, str):
            normalized.append({"store": ref, "access": "readwrite"})
        elif hasattr(ref, 'model_dump'):
            normalized.append(ref.model_dump())
        elif isinstance(ref, dict):
            normalized.append(ref)
    return normalized


def should_skip_existing(
    existing,
    managed_by: str,
    config_name: str,
    config_hash: str,
    resource_type: str,
    resource_name: str,
    track_change,
    warnings: list[str],
) -> bool:
    """Check if an existing resource should be skipped during config apply.

    Handles:
    - Inactive (soft-deleted) resources: reclaim ownership and proceed with update
    - Mismatched managed_by: warn and skip (resource owned by another config/package)
    - Unchanged checksum: skip (already up to date)

    Returns True if the resource should be skipped (no update needed).
    Returns False if the resource should be updated.
    """
    # Inactive resources can be reclaimed by any manager
    if hasattr(existing, "is_active") and not existing.is_active:
        existing.managed_by = managed_by
        existing.config_name = config_name
        # Fall through to checksum check — will be updated below
    elif existing.managed_by and existing.managed_by != managed_by:
        warnings.append(
            f"{resource_type.title()} '{resource_name}' exists but is managed by '{existing.managed_by}'. Skipping."
        )
        track_change("unchanged", resource_type, resource_name)
        return True

    # Check if content has changed
    is_active = getattr(existing, "is_active", True)
    if existing.config_checksum == config_hash and is_active:
        track_change("unchanged", resource_type, resource_name)
        return True

    return False


def normalize_collection_references(coll_refs: list) -> list[dict]:
    """Normalize collection references to dict format with access mode.

    Plain strings → {"collection": "ns/name", "access": "readonly"} (backward compat).
    """
    normalized = []
    for ref in coll_refs:
        if isinstance(ref, str):
            coll = ref if "/" in ref else f"default/{ref}"
            normalized.append({"collection": coll, "access": "readonly"})
        elif hasattr(ref, "model_dump"):
            normalized.append(ref.model_dump())
        elif isinstance(ref, dict):
            normalized.append(ref)
    return normalized
