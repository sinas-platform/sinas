"""Schema errors of a SinasConfig / SinasPackage document, one per problem.

Pydantic reports a document's problems as one multi-line dump, located by
raw paths (`spec.agents.0.EnabledSkillConfigYaml.preloadd`). Here each one
becomes its own (path, message), the message naming the resource it is in
(`agent 'support/triage'`) and, for an unknown key, the field that was
probably meant (`output_schema` → `outputSchema`).
"""

from __future__ import annotations

import difflib
import types
from typing import Any, Optional, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.schemas.config import ConfigSpec, SinasConfig

# Kinds without a resource applier (or before the registry is importable).
_EXTRA_NOUNS = {"users": "user", "variables": "variable"}


def schema_errors(error: PydanticValidationError, document: Any) -> list[tuple[str, str]]:
    """[(path, message)] for a failed SinasConfig validation of `document`."""
    entries = []
    for err in error.errors(include_url=False):
        raw_loc = tuple(err["loc"])
        loc, from_union = _data_path(raw_loc, document, err["type"])
        entries.append((err, raw_loc, loc, from_union))

    def is_union_mismatch(entry) -> bool:
        err, _raw, _loc, from_union = entry
        return from_union and err["type"].endswith("_type")

    out: list[tuple[str, str]] = []
    for entry in entries:
        err, raw_loc, loc, _ = entry
        # A value checked against every member of a union (`- skill: x` vs a
        # plain string) fails each member; the "should be a valid string" of
        # the members it clearly wasn't meant as only buries the real problem.
        if is_union_mismatch(entry) and any(
            other is not entry
            and not is_union_mismatch(other)
            and other[2][: len(loc)] == loc
            for other in entries
        ):
            continue
        item = (_format_path(loc), _message(err, raw_loc, loc, document))
        if item not in out:
            out.append(item)
    return out


# --------------------------------------------------------------- locations


def _data_path(raw_loc: tuple, document: Any, err_type: str) -> tuple[tuple, bool]:
    """The error's location in the document: pydantic's path without the
    union-member tags it inserts (a member's class name, `str`). Returns the
    path and whether any tag was dropped."""
    path: list = []
    current = document
    dropped = False
    for i, segment in enumerate(raw_loc):
        last = i == len(raw_loc) - 1
        if isinstance(current, dict) and segment in current:
            current = current[segment]
            path.append(segment)
        elif (
            isinstance(current, list)
            and isinstance(segment, int)
            and 0 <= segment < len(current)
        ):
            current = current[segment]
            path.append(segment)
        elif last and err_type == "missing":
            path.append(segment)  # a required key that isn't there
        else:
            dropped = True  # a union member's tag
    return tuple(path), dropped


def _format_path(loc: tuple) -> str:
    out = ""
    for segment in loc:
        if isinstance(segment, int):
            out += f"[{segment}]"
        else:
            out += f".{segment}" if out else str(segment)
    return out or "root"


# --------------------------------------------------------------- messages


def _message(err: dict, raw_loc: tuple, loc: tuple, document: Any) -> str:
    resource, within = _resource_of(loc, document)
    if err["type"] == "extra_forbidden":
        key = loc[-1] if loc else ""
        message = f"unknown field '{_format_path(within) if within else key}'"
        hint = _did_you_mean(key, _known_keys(raw_loc[:-1]))
        if hint:
            message += f" (did you mean '{hint}'?)"
    else:
        message = err["msg"]
        if resource and within:  # outside a resource, the error's path says it
            message = f"{_format_path(within)}: {message}"
    return f"{resource}: {message}" if resource else message


def _resource_of(loc: tuple, document: Any) -> tuple[Optional[str], tuple]:
    """(`agent 'ns/name'`, the path inside it) for a location within one
    resource of the spec; (None, loc) for anything else."""
    if len(loc) < 3 or loc[0] != "spec" or not isinstance(loc[2], int):
        return None, loc
    section, index = loc[1], loc[2]
    try:
        item = document["spec"][section][index]
    except (KeyError, IndexError, TypeError):
        item = None
    noun = _noun(section)
    key = _resource_key(section, item) if isinstance(item, dict) else None
    label = f"{noun} '{key}'" if key else f"{noun} #{index + 1}"
    return label, loc[3:]


def _noun(section: str) -> str:
    if section in _EXTRA_NOUNS:
        return _EXTRA_NOUNS[section]
    try:
        from app.services.resources.registry import all_appliers

        for applier in all_appliers():
            if applier.config_section == section:
                return applier.noun
    except Exception:  # pragma: no cover - only ever a nicer label
        pass
    return section


def _resource_key(section: str, item: dict) -> Optional[str]:
    """How config apply names the resource: namespace/name, else its name,
    email (users), path (webhooks) or package name (dependencies)."""
    name = item.get("name")
    if isinstance(name, str) and name:
        model = _section_model(section)
        if model is not None and "namespace" in model.model_fields:
            namespace = item.get("namespace")
            return f"{namespace if isinstance(namespace, str) and namespace else 'default'}/{name}"
        return name
    for field in ("email", "path", "packageName"):
        value = item.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _section_model(section: str) -> Optional[type[BaseModel]]:
    field = ConfigSpec.model_fields.get(section)
    if field is None:
        return None
    args = get_args(field.annotation)
    return args[0] if args and _is_model(args[0]) else None


# --------------------------------------------------------------- did you mean


def _did_you_mean(key: Any, known: set[str]) -> Optional[str]:
    """The known field an unknown key was probably meant as: the same name in
    another casing (`output_schema`, `OutputSchema` → `outputSchema`), else a
    close spelling (`toolDney` → `toolDeny`)."""
    if not isinstance(key, str) or not known:
        return None

    def norm(value: str) -> str:
        return value.replace("_", "").replace("-", "").lower()

    for candidate in sorted(known):
        if norm(candidate) == norm(key):
            return candidate
    close = difflib.get_close_matches(key, sorted(known), n=1, cutoff=0.8)
    return close[0] if close else None


def _known_keys(raw_loc: tuple) -> set[str]:
    """The keys the model at `raw_loc` (pydantic's own path, union tags
    included) accepts: aliases, and field names where those populate too."""
    model = _model_at(raw_loc)
    if model is None:
        return set()
    by_name = bool(model.model_config.get("populate_by_name"))
    keys: set[str] = set()
    for name, field in model.model_fields.items():
        if field.alias:
            keys.add(field.alias)
            if by_name:
                keys.add(name)
        else:
            keys.add(name)
    return keys


def _model_at(raw_loc: tuple) -> Optional[type[BaseModel]]:
    annotation: Any = SinasConfig
    for segment in raw_loc:
        annotation = _step(annotation, segment)
        if annotation is None:
            return None
    annotation = _without_none(annotation)
    return annotation if _is_model(annotation) else None


def _step(annotation: Any, segment: Any) -> Any:
    annotation = _without_none(annotation)
    if _is_model(annotation):
        for name, field in annotation.model_fields.items():
            if segment in (name, field.alias):
                return field.annotation
        return None
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is list and isinstance(segment, int) and args:
        return args[0]
    if origin is dict and len(args) == 2:
        return args[1]
    if origin in (Union, types.UnionType):
        for member in args:
            if getattr(member, "__name__", None) == segment:
                return member
    return None


def _without_none(annotation: Any) -> Any:
    """Optional[X] → X (pydantic doesn't tag a union with None)."""
    if get_origin(annotation) in (Union, types.UnionType):
        members = [a for a in get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)
