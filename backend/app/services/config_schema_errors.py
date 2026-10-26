"""Schema errors of a SinasConfig / SinasPackage document, one per problem,
and the fields older Sinas versions wrote that a document may still carry.

Pydantic reports a document's problems as one multi-line dump, located by
raw paths (`spec.agents.0.EnabledSkillConfigYaml.preloadd`). Here each one
becomes its own (path, message), the message naming the resource it is in
(`agent 'support/triage'`) and, for an unknown key, the field that was
probably meant (`output_schema` → `outputSchema`).
"""

from __future__ import annotations

import difflib
import types
from dataclasses import dataclass
from typing import Any, Optional, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.schemas.config import ConfigSpec, SinasConfig

# Kinds without a resource applier (or before the registry is importable).
_EXTRA_NOUNS = {"users": "user", "variables": "variable"}


# --------------------------------------------------------------- legacy fields

# Fields an export or package from an older Sinas wrote that this version no
# longer has, by spec section: dropped before validation, with a warning,
# instead of failing a file that applied fine before. Found by diffing the
# 0.3.0 and 0.4.0 config schemas and exports against this one.
LEGACY_FIELDS: dict[str, dict[str, str]] = {
    "users": {
        # Written on every user by the 0.3/0.4 export, null included.
        "lastLoginAt": "is runtime state, not configuration, and is ignored",
    },
    "components": {
        "cssOverrides": (
            "was removed in 0.5.0 and is ignored; move the CSS into `sourceCode` "
            "as a <style> block"
        ),
    },
}


def strip_legacy_fields(document: Any) -> list[str]:
    """Drop LEGACY_FIELDS from the document's resources, in place. Returns
    one warning per field, naming the resources that had it."""
    spec = document.get("spec") if isinstance(document, dict) else None
    if not isinstance(spec, dict):
        return []
    warnings = []
    for section, fields in LEGACY_FIELDS.items():
        items = spec.get(section)
        if not isinstance(items, list):
            continue
        for field, why in fields.items():
            labels = []
            for index, item in enumerate(items):
                if isinstance(item, dict) and field in item:
                    del item[field]
                    labels.append(_resource_label(section, index, item))
            if labels:
                shown = ", ".join(labels[:5]) + (f" and {len(labels) - 5} more" if len(labels) > 5 else "")
                warnings.append(f"`{field}` ({shown}) {why}")
    return warnings


# --------------------------------------------------------------- errors


@dataclass
class _Located:
    err: dict
    raw_loc: tuple
    # Where in the document: ((segment, is_list_index), ...).
    path: tuple
    # The document paths at which the error went through a union member.
    unions: tuple


def schema_errors(
    error: PydanticValidationError, document: Any, prefix: tuple = ()
) -> list[tuple[str, str]]:
    """[(path, message)] for a failed SinasConfig validation of `document`.
    `prefix`: where the validated value sits in a SinasConfig document, for
    a part of one validated on its own (("package",) for package metadata);
    `document` is then the whole document, or {"package": that value}."""
    located = [
        _locate(err, prefix + tuple(err["loc"]), document)
        for err in error.errors(include_url=False)
    ]

    def shallow_mismatch(e: _Located) -> bool:
        # The value isn't even of this union member's type (a mapping where
        # a string could go): only a guess at what was meant.
        return bool(e.unions) and e.path == e.unions[-1] and e.err["type"].endswith("_type")

    out: list[tuple[str, str]] = []
    merged: set = set()
    for e in located:
        if shallow_mismatch(e):
            point = e.unions[-1]
            # A member the value got into (`- {skill: 123}` is a skill entry)
            # says what is wrong; the members it isn't only bury that.
            if any(o.path != point and point in o.unions for o in located):
                continue
            # Fits no member at all (`- 123`): one error for the union.
            if point in merged:
                continue
            merged.add(point)
            options = [
                o.err["msg"].removeprefix("Input should be ")
                for o in located
                if shallow_mismatch(o) and o.unions[-1] == point
            ]
            message = "Input should be " + " or ".join(dict.fromkeys(options))
        else:
            message = None
        item = (_format_path(e.path), _message(e, document, message))
        if item not in out:
            out.append(item)
    return out


def _locate(err: dict, raw_loc: tuple, document: Any) -> _Located:
    """Follow pydantic's path through the schema and the document at once:
    a segment where the schema has a union is the member's tag pydantic
    inserts (`str`, a class name), not a key of the document."""
    annotation: Any = SinasConfig
    current = document
    path: list = []
    unions: list = []
    for segment in raw_loc:
        annotation = _without_none(annotation)
        if _is_union(annotation):
            unions.append(tuple(path))
            annotation = next(
                (m for m in get_args(annotation) if _member_tag(m) == segment), None
            )
            continue
        is_index = isinstance(current, list) and isinstance(segment, int)
        if isinstance(current, dict) and not isinstance(segment, str):
            # pydantic reports a non-text key as an int (`yes:` loads as True)
            segment = next((k for k in current if k == segment and not isinstance(k, str)), segment)
        path.append((segment, is_index))
        if is_index:
            current = current[segment] if 0 <= segment < len(current) else None
        elif isinstance(current, dict):
            current = current.get(segment)
        else:
            current = None
        annotation = _step(annotation, segment) if annotation is not None else None
    return _Located(err, raw_loc, tuple(path), tuple(unions))


def _format_path(path: tuple) -> str:
    out = ""
    for segment, is_index in path:
        if is_index:
            out += f"[{segment}]"
        else:
            key = _key_text(segment)
            out += f".{key}" if out else key
    return out or "root"


def _key_text(key: Any) -> str:
    """A mapping key as YAML would show it (`yes:` loads as True)."""
    if isinstance(key, bool):
        return "true" if key else "false"
    return str(key)


# --------------------------------------------------------------- messages


def _message(e: _Located, document: Any, message: Optional[str]) -> str:
    resource, within = _resource_of(e.path, document)
    err = e.err
    if err["type"] == "extra_forbidden":
        key = e.path[-1][0] if e.path else ""
        message = f"unknown field '{_format_path(within) if within else _key_text(key)}'"
        hint = _did_you_mean(key, _known_keys(e.raw_loc[:-1]))
        if hint:
            message += f" (did you mean '{hint}'?)"
    else:
        if err["type"] == "invalid_key" and e.path:
            message = f"the key {_key_text(e.path[-1][0])} isn't text; quote it"
            within = within[:-1]
        message = message or err["msg"].removeprefix("Value error, ")
        if resource and within:  # outside a resource, the error's path says it
            message = f"{_format_path(within)}: {message}"
    return f"{resource}: {message}" if resource else message


def _resource_of(path: tuple, document: Any) -> tuple[Optional[str], tuple]:
    """(`agent 'ns/name'`, the path inside it) for a location within one
    resource of the spec; (None, path) for anything else."""
    if len(path) < 3 or path[0][0] != "spec" or not path[2][1]:
        return None, path
    section, index = path[1][0], path[2][0]
    try:
        item = document["spec"][section][index]
    except (KeyError, IndexError, TypeError):
        item = None
    return _resource_label(section, index, item), path[3:]


def _resource_label(section: str, index: int, item: Any) -> str:
    noun = _noun(section)
    key = _resource_key(section, item) if isinstance(item, dict) else None
    return f"{noun} '{key}'" if key else f"{noun} #{index + 1}"


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
    return str(section)


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
    if _is_union(annotation):
        for member in args:
            if _member_tag(member) == segment:
                return member
    return None


def _member_tag(member: Any) -> Optional[str]:
    """The tag pydantic puts in an error's path for a union member: a
    model's class name, a plain type's name (`str`)."""
    return getattr(member, "__name__", None)


def _is_union(annotation: Any) -> bool:
    return get_origin(annotation) in (Union, types.UnionType)


def _without_none(annotation: Any) -> Any:
    """Optional[X] → X (pydantic doesn't tag a union with None)."""
    if _is_union(annotation):
        members = [a for a in get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)
