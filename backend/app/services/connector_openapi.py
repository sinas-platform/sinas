"""OpenAPI spec parser for connector operations."""
import json
import logging
import re
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


def parse_openapi_spec(raw: str) -> dict[str, Any]:
    """Parse JSON or YAML OpenAPI spec string."""
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError:
        try:
            spec = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse spec as JSON or YAML: {e}")

    if not isinstance(spec, dict):
        raise ValueError("Spec must be a JSON/YAML object")
    if not spec.get("openapi", "").startswith("3"):
        raise ValueError("Only OpenAPI 3.x specs are supported")

    return spec


def resolve_refs(spec: dict, root: Optional[dict] = None, seen: Optional[set] = None) -> Any:
    """Recursively resolve local $ref references."""
    if root is None:
        root = spec
    if seen is None:
        seen = set()

    if isinstance(spec, dict):
        if "$ref" in spec:
            ref = spec["$ref"]
            if not ref.startswith("#/"):
                return spec  # External ref, leave as-is
            if ref in seen:
                return {"type": "object", "description": "(circular reference)"}
            seen = seen | {ref}
            parts = ref.lstrip("#/").split("/")
            node = root
            for part in parts:
                node = node.get(part, {})
            return resolve_refs(node, root, seen)
        return {k: resolve_refs(v, root, seen) for k, v in spec.items()}
    elif isinstance(spec, list):
        return [resolve_refs(item, root, seen) for item in spec]
    return spec


def extract_operations(spec: dict) -> list[dict[str, Any]]:
    """Extract operations from OpenAPI spec and convert to OperationConfig format."""
    spec = resolve_refs(spec)
    paths = spec.get("paths", {})
    operations = []

    for path, path_item in paths.items():
        path_params = path_item.get("parameters", [])

        for method in ("get", "post", "put", "patch", "delete"):
            if method not in path_item:
                continue
            op = path_item[method]
            op_id = op.get("operationId")
            name = _operation_name(op_id, method, path)
            description = op.get("summary") or op.get("description") or f"{method.upper()} {path}"

            # Merge path-level and operation-level parameters
            all_params = path_params + op.get("parameters", [])

            # Build parameters JSON Schema
            parameters, mapping = _build_parameters(all_params, op.get("requestBody"), method)

            operations.append({
                "name": name,
                "method": method.upper(),
                "path": _convert_path_params(path),
                "description": description[:500] if description else None,
                "parameters": parameters,
                "request_body_mapping": mapping,
                "response_mapping": "json",
            })

    return operations


def _operation_name(op_id: Optional[str], method: str, path: str) -> str:
    """Generate a snake_case operation name."""
    if op_id:
        # Clean operationId to snake_case
        name = re.sub(r"[^a-zA-Z0-9]", "_", op_id)
        name = re.sub(r"_+", "_", name).strip("_").lower()
        return name

    # Generate from method + path
    clean = path.replace("{", "by_").replace("}", "")
    clean = re.sub(r"[^a-zA-Z0-9]", "_", clean)
    clean = re.sub(r"_+", "_", clean).strip("_").lower()
    return f"{method}_{clean}"


def _convert_path_params(path: str) -> str:
    """Convert OpenAPI path params {param} to Jinja2 {{ param }}."""
    return re.sub(r"\{(\w+)\}", r"{{ \1 }}", path)


def _build_parameters(
    params: list[dict], request_body: Optional[dict], method: str
) -> tuple[dict[str, Any], str]:
    """Build a flat JSON Schema from OpenAPI parameters + request body."""
    properties = {}
    required = []

    has_path_params = False
    has_query_params = False
    has_body = False

    for param in params:
        p_name = param.get("name")
        p_in = param.get("in")
        p_schema = _simplify_schema(param.get("schema", {"type": "string"}))
        p_desc = param.get("description")

        if p_name:
            prop = {**p_schema}
            if p_desc:
                prop["description"] = p_desc[:200]
            properties[p_name] = prop

            if param.get("required"):
                required.append(p_name)

            if p_in == "path":
                has_path_params = True
            elif p_in == "query":
                has_query_params = True

    # Request body
    if request_body:
        content = request_body.get("content", {})
        json_schema = content.get("application/json", {}).get("schema", {})
        if json_schema:
            json_schema = _simplify_schema(json_schema)
            body_props = json_schema.get("properties", {})
            body_required = json_schema.get("required", [])

            for prop_name, prop_schema in body_props.items():
                if prop_name not in properties:
                    properties[prop_name] = prop_schema
                    if prop_name in body_required:
                        required.append(prop_name)

            has_body = True

    # Determine mapping
    if has_body or method in ("post", "put", "patch"):
        mapping = "path_and_json" if has_path_params else "json"
    elif has_query_params:
        mapping = "path_and_query" if has_path_params else "query"
    else:
        mapping = "json"

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required

    return schema, mapping


def _simplify_schema(schema: dict) -> dict:
    """Strip OpenAPI extensions, keep core JSON Schema fields."""
    if not isinstance(schema, dict):
        return schema

    keep_keys = {"type", "properties", "items", "required", "enum", "description",
                 "default", "minimum", "maximum", "minLength", "maxLength",
                 "pattern", "format", "oneOf", "anyOf", "allOf"}

    result = {}
    for k, v in schema.items():
        if k in keep_keys:
            if k == "properties" and isinstance(v, dict):
                result[k] = {pk: _simplify_schema(pv) for pk, pv in v.items()}
            elif k == "items" and isinstance(v, dict):
                result[k] = _simplify_schema(v)
            elif k in ("oneOf", "anyOf", "allOf") and isinstance(v, list):
                result[k] = [_simplify_schema(item) for item in v]
            else:
                result[k] = v

    return result
