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
