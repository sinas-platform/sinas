"""Declarative data mapping for pipelines: JMESPath with AWS States-style keys.

One convention used everywhere (step input, cursor.path, primaryKey, items,
pipeline output):

- ``key: value``        — literal, passed through (recursively for dicts/lists).
- ``key.$: <jmespath>`` — evaluated against the run context; result bound to ``key``.
- A whole value may be an expression via the ``.$``-suffixed variant of its
  field (e.g. step ``input.$``).

A missing path yields ``None`` (JMESPath semantics). Syntactically invalid
expressions are rejected at save/config-apply time via `validate_expression`.
"""
from typing import Any

import jmespath
from jmespath.exceptions import ParseError

EXPR_SUFFIX = ".$"


class MappingError(ValueError):
    """Raised for invalid mapping shapes or expressions."""


def validate_expression(expr: Any, where: str) -> list[str]:
    """Validate one JMESPath expression; returns a list of error strings."""
    if not isinstance(expr, str) or not expr.strip():
        return [f"{where}: expression must be a non-empty string"]
    try:
        jmespath.compile(expr)
    except ParseError as e:
        return [f"{where}: invalid JMESPath expression: {e}"]
    return []


def validate_template(template: Any, where: str) -> list[str]:
    """Recursively validate a mapping template (dict/list/scalars with .$ keys)."""
    errors: list[str] = []
    if isinstance(template, dict):
        for key, value in template.items():
            if isinstance(key, str) and key.endswith(EXPR_SUFFIX):
                base = key[: -len(EXPR_SUFFIX)]
                if base + EXPR_SUFFIX != key or not base:
                    errors.append(f"{where}: invalid expression key '{key}'")
                    continue
                if base in template:
                    errors.append(
                        f"{where}: both '{base}' and '{key}' present — pick literal or expression"
                    )
                errors.extend(validate_expression(value, f"{where}.{key}"))
            else:
                errors.extend(validate_template(value, f"{where}.{key}"))
    elif isinstance(template, list):
        for i, item in enumerate(template):
            errors.extend(validate_template(item, f"{where}[{i}]"))
    return errors


def evaluate_expression(expr: str, context: dict[str, Any]) -> Any:
    """Evaluate a single JMESPath expression against the run context."""
    return jmespath.search(expr, context)


def resolve_template(template: Any, context: dict[str, Any]) -> Any:
    """Resolve a mapping template against the run context.

    Dicts: ``key.$`` entries are evaluated and bound to ``key``; other entries
    recurse. Lists recurse per element. Scalars pass through.
    """
    if isinstance(template, dict):
        resolved: dict[str, Any] = {}
        for key, value in template.items():
            if isinstance(key, str) and key.endswith(EXPR_SUFFIX):
                resolved[key[: -len(EXPR_SUFFIX)]] = evaluate_expression(value, context)
            else:
                resolved[key] = resolve_template(value, context)
        return resolved
    if isinstance(template, list):
        return [resolve_template(item, context) for item in template]
    return template


def resolve_field(container: dict[str, Any], field: str, context: dict[str, Any], default: Any = None) -> Any:
    """Resolve a field that may be given as ``field`` (template) or ``field.$`` (expression).

    Used for step ``input`` / pipeline ``output`` style fields where the whole
    value may be one expression.
    """
    expr_key = field + EXPR_SUFFIX
    if expr_key in container:
        return evaluate_expression(container[expr_key], context)
    if field in container:
        return resolve_template(container[field], context)
    return default
