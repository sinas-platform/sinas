"""Save-time validation for pipeline definitions.

Shape-level rules only: step types/fields, mapping-expression compilation,
cursor/perUser/asTool constraints. Cross-resource existence (does the connector
operation / function / agent exist?) is deliberately NOT enforced here — config
apply installs resources in order and references may not exist yet; missing
targets fail at run time with a clear error, matching the rest of config apply.
"""
import re
from typing import Any, Optional

from app.services.pipeline_mapping import validate_expression, validate_template

STEP_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
REF_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*/[a-zA-Z_][a-zA-Z0-9_-]*$")
TABLE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?$")

MAX_STEPS = 32

STEP_TYPES = ("connector", "function", "agent", "query", "load")

# Allowed keys per step type. `input` / `message` / `items` / `primaryKey` also
# accept their `.$` expression variant.
_COMMON_KEYS = {"name", "type", "input", "input.$", "retry"}
_ALLOWED_KEYS: dict[str, set[str]] = {
    "connector": _COMMON_KEYS | {"connector", "operation", "cursor", "allowStatuses", "paginate"},
    "function": _COMMON_KEYS | {"function", "cursor"},
    "agent": _COMMON_KEYS | {"agent", "message", "message.$"},
    "query": _COMMON_KEYS | {"query"},
    "load": {"name", "type", "retry", "connection", "table", "primaryKey.$", "items.$"},
}

_BACKOFFS = ("none", "linear", "exponential")


def _validate_retry(retry: Any, where: str) -> list[str]:
    if not isinstance(retry, dict):
        return [f"{where}: retry must be an object"]
    errors = []
    unknown = set(retry) - {"maxAttempts", "backoff"}
    if unknown:
        errors.append(f"{where}: unknown retry keys: {sorted(unknown)}")
    attempts = retry.get("maxAttempts", 1)
    if not isinstance(attempts, int) or not (1 <= attempts <= 10):
        errors.append(f"{where}: retry.maxAttempts must be an integer 1–10")
    if retry.get("backoff", "none") not in _BACKOFFS:
        errors.append(f"{where}: retry.backoff must be one of {_BACKOFFS}")
    return errors


def _validate_cursor(cursor: Any, where: str) -> list[str]:
    if not isinstance(cursor, dict):
        return [f"{where}: cursor must be an object"]
    errors = []
    unknown = set(cursor) - {"param", "path", "initial", "initial.$"}
    if unknown:
        errors.append(f"{where}: unknown cursor keys: {sorted(unknown)}")
    if not isinstance(cursor.get("param"), str) or not cursor.get("param"):
        errors.append(f"{where}: cursor.param is required (string)")
    if not isinstance(cursor.get("path"), str) or not cursor.get("path"):
        errors.append(f"{where}: cursor.path is required (JMESPath expression)")
    else:
        errors.extend(validate_expression(cursor["path"], f"{where}.cursor.path"))
    if "initial" in cursor and "initial.$" in cursor:
        errors.append(f"{where}: cursor has both 'initial' and 'initial.$'")
    if "initial.$" in cursor:
        errors.extend(validate_expression(cursor["initial.$"], f"{where}.cursor.initial.$"))
    return errors


def _validate_step(step: Any, index: int) -> list[str]:
    where = f"steps[{index}]"
    if not isinstance(step, dict):
        return [f"{where}: step must be an object"]
    errors: list[str] = []

    name = step.get("name")
    if not isinstance(name, str) or not STEP_NAME_RE.match(name or ""):
        errors.append(f"{where}: invalid or missing step name")
    else:
        where = f"steps[{index}] ({name})"

    step_type = step.get("type")
    if step_type not in STEP_TYPES:
        errors.append(f"{where}: type must be one of {STEP_TYPES}")
        return errors

    unknown = set(step) - _ALLOWED_KEYS[step_type]
    if unknown:
        errors.append(f"{where}: unknown keys for type '{step_type}': {sorted(unknown)}")

    # Resource reference per type
    ref_field = {"connector": "connector", "function": "function", "agent": "agent", "query": "query"}.get(step_type)
    if ref_field:
        ref = step.get(ref_field)
        if not isinstance(ref, str) or not REF_RE.match(ref or ""):
            errors.append(f"{where}: '{ref_field}' must be a 'namespace/name' reference")

    if step_type == "connector" and (not isinstance(step.get("operation"), str) or not step.get("operation")):
        errors.append(f"{where}: 'operation' is required")

    if step_type == "connector" and "allowStatuses" in step:
        statuses = step["allowStatuses"]
        if not isinstance(statuses, list) or not all(isinstance(s, int) for s in statuses):
            errors.append(f"{where}: allowStatuses must be a list of integers")

    if step_type == "connector" and "paginate" in step:
        # Reserved for v1.1; reject now rather than silently ignoring.
        errors.append(f"{where}: 'paginate' is not supported yet")

    if step_type == "load":
        if not isinstance(step.get("connection"), str) or not step.get("connection"):
            errors.append(f"{where}: 'connection' is required")
        table = step.get("table")
        if not isinstance(table, str) or not TABLE_RE.match(table or ""):
            errors.append(f"{where}: 'table' must be a (schema-qualified) identifier")
        if not isinstance(step.get("primaryKey.$"), str):
            errors.append(f"{where}: 'primaryKey.$' is required (JMESPath over each item)")
        else:
            errors.extend(validate_expression(step["primaryKey.$"], f"{where}.primaryKey.$"))
        if not isinstance(step.get("items.$"), str):
            errors.append(f"{where}: 'items.$' is required (JMESPath expression)")
        else:
            errors.extend(validate_expression(step["items.$"], f"{where}.items.$"))

    # Mapping fields
    if "input" in step and "input.$" in step:
        errors.append(f"{where}: step has both 'input' and 'input.$'")
    if "input.$" in step:
        errors.extend(validate_expression(step["input.$"], f"{where}.input.$"))
    if "input" in step:
        if not isinstance(step["input"], dict):
            errors.append(f"{where}: 'input' must be an object (use 'input.$' for an expression)")
        else:
            errors.extend(validate_template(step["input"], f"{where}.input"))

    if step_type == "agent":
        if "message" in step and "message.$" in step:
            errors.append(f"{where}: step has both 'message' and 'message.$'")
        if "message.$" in step:
            errors.extend(validate_expression(step["message.$"], f"{where}.message.$"))
        if "message" in step and not isinstance(step["message"], str):
            errors.append(f"{where}: 'message' must be a string")

    if "cursor" in step:
        errors.extend(_validate_cursor(step["cursor"], where))

    if "retry" in step:
        errors.extend(_validate_retry(step["retry"], where))

    return errors


def validate_pipeline_definition(
    steps: Any,
    *,
    per_user: Optional[dict[str, Any]] = None,
    as_tool: bool = False,
    input_schema: Optional[dict[str, Any]] = None,
    description: Optional[str] = None,
    tool_description: Optional[str] = None,
    concurrency: Optional[str] = None,
    output_mapping: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Validate a full pipeline definition. Returns a list of error strings."""
    errors: list[str] = []

    if not isinstance(steps, list) or not steps:
        return ["steps must be a non-empty list"]
    if len(steps) > MAX_STEPS:
        errors.append(f"too many steps ({len(steps)} > {MAX_STEPS})")

    names = [s.get("name") for s in steps if isinstance(s, dict)]
    dupes = {n for n in names if n and names.count(n) > 1}
    if dupes:
        errors.append(f"duplicate step names: {sorted(dupes)}")

    cursor_steps = [
        s.get("name") for s in steps
        if isinstance(s, dict) and s.get("cursor")
    ]
    if len(cursor_steps) > 1:
        errors.append(f"at most one step may declare cursor config (found: {cursor_steps})")

    for i, step in enumerate(steps):
        errors.extend(_validate_step(step, i))

    if per_user is not None:
        if not isinstance(per_user, dict):
            errors.append("perUser must be an object")
        else:
            unknown = set(per_user) - {"connector", "disableAfterFailures"}
            if unknown:
                errors.append(f"unknown perUser keys: {sorted(unknown)}")
            ref = per_user.get("connector")
            if not isinstance(ref, str) or not REF_RE.match(ref or ""):
                errors.append("perUser.connector must be a 'namespace/name' reference")
            daf = per_user.get("disableAfterFailures")
            if daf is not None and (not isinstance(daf, int) or daf < 1):
                errors.append("perUser.disableAfterFailures must be a positive integer")

    if as_tool:
        if not isinstance(input_schema, dict) or not input_schema.get("properties"):
            errors.append("asTool pipelines require an inputSchema with properties")
        if not (tool_description or description):
            errors.append("asTool pipelines require a description (or toolDescription)")

    if concurrency is not None and concurrency not in ("single", "parallel"):
        errors.append("concurrency must be 'single' or 'parallel'")

    if output_mapping is not None:
        if not isinstance(output_mapping, dict) or set(output_mapping) - {"output", "output.$"}:
            errors.append("output mapping must contain only 'output' or 'output.$'")
        elif "output" in output_mapping and "output.$" in output_mapping:
            errors.append("output mapping has both 'output' and 'output.$'")
        elif "output.$" in output_mapping:
            errors.extend(validate_expression(output_mapping["output.$"], "output.$"))
        elif "output" in output_mapping:
            errors.extend(validate_template(output_mapping["output"], "output"))

    return errors
