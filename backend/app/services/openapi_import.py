"""OpenAPI spec import service.

Parses OpenAPI v3 specs and generates SINAS functions that call the API endpoints.
"""
import json
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ParameterInfo:
    """Extracted parameter from an OpenAPI operation."""

    name: str
    location: str  # "path", "query", "header"
    required: bool
    schema: dict[str, Any]
    description: str = ""


@dataclass
class OperationInfo:
    """Extracted operation from an OpenAPI spec."""

    method: str  # GET, POST, etc.
    path: str  # /pets/{petId}
    operation_id: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    parameters: list[ParameterInfo] = field(default_factory=list)
    request_body_schema: Optional[dict[str, Any]] = None
    request_body_required: bool = False
    response_schema: Optional[dict[str, Any]] = None
    tags: list[str] = field(default_factory=list)


def parse_openapi_spec(raw: str) -> dict:
    """Parse a JSON or YAML string into an OpenAPI spec dict.

    Validates that it looks like an OpenAPI v3 spec.
    """
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError:
        # Try YAML
        try:
            import yaml

            spec = yaml.safe_load(raw)
        except Exception:
            raise ValueError("Spec must be valid JSON or YAML")

    if not isinstance(spec, dict):
        raise ValueError("Spec must be a JSON/YAML object")

    # Check for OpenAPI v3
    openapi_version = spec.get("openapi", "")
    if not openapi_version.startswith("3."):
        swagger = spec.get("swagger", "")
        if swagger:
            raise ValueError(
                f"Swagger {swagger} specs are not supported. Please use OpenAPI v3.x."
            )
        raise ValueError(
            "Missing or unsupported OpenAPI version. Only OpenAPI v3.x is supported."
        )

    if "paths" not in spec:
        raise ValueError("Spec has no 'paths' defined")

    return spec


def resolve_refs(spec: dict) -> dict:
    """Resolve local $ref references recursively.

    Only handles local refs (#/components/...). External refs are left as-is.
    """

    def _resolve(obj: Any, root: dict, seen: set | None = None) -> Any:
        if seen is None:
            seen = set()

        if isinstance(obj, dict):
            if "$ref" in obj:
                ref = obj["$ref"]
                if not ref.startswith("#/"):
                    return obj  # External ref, leave as-is

                if ref in seen:
                    return {"type": "object", "description": f"Circular ref: {ref}"}
                seen = seen | {ref}

                # Navigate to the referenced object
                parts = ref[2:].split("/")
                target = root
                for part in parts:
                    # Handle URL-encoded characters in $ref paths
                    part = part.replace("~1", "/").replace("~0", "~")
                    if isinstance(target, dict) and part in target:
                        target = target[part]
                    else:
                        return obj  # Can't resolve, leave as-is
                return _resolve(target, root, seen)
            else:
                return {k: _resolve(v, root, seen) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_resolve(item, root, seen) for item in obj]
        return obj

    return _resolve(spec, spec)


def extract_operations(spec: dict) -> list[OperationInfo]:
    """Walk spec paths and extract each operation."""
    resolved = resolve_refs(spec)
    operations = []
    paths = resolved.get("paths", {})

    http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        # Path-level parameters apply to all operations on this path
        path_params = _extract_parameters(path_item.get("parameters", []))

        for method in http_methods:
            if method not in path_item:
                continue

            op = path_item[method]
            if not isinstance(op, dict):
                continue

            # Merge path-level and operation-level parameters
            op_params = _extract_parameters(op.get("parameters", []))
            # Operation params override path params with same name+location
            merged_params = {(p.name, p.location): p for p in path_params}
            merged_params.update({(p.name, p.location): p for p in op_params})

            # Request body
            request_body = op.get("requestBody", {})
            body_schema = None
            body_required = False
            if isinstance(request_body, dict):
                body_required = request_body.get("required", False)
                content = request_body.get("content", {})
                # Prefer application/json
                json_content = content.get("application/json", {})
                if json_content:
                    body_schema = json_content.get("schema")

            # Response schema (200 or 201)
            response_schema = None
            responses = op.get("responses", {})
            for code in ["200", "201", "default"]:
                if code in responses:
                    resp = responses[code]
                    if isinstance(resp, dict):
                        resp_content = resp.get("content", {})
                        json_resp = resp_content.get("application/json", {})
                        if json_resp:
                            response_schema = json_resp.get("schema")
                            break

            operations.append(
                OperationInfo(
                    method=method.upper(),
                    path=path,
                    operation_id=op.get("operationId"),
                    summary=op.get("summary"),
                    description=op.get("description"),
                    parameters=list(merged_params.values()),
                    request_body_schema=body_schema,
                    request_body_required=body_required,
                    response_schema=response_schema,
                    tags=op.get("tags", []),
                )
            )

    return operations


def _extract_parameters(params: list) -> list[ParameterInfo]:
    """Extract ParameterInfo from a list of OpenAPI parameter objects."""
    result = []
    for p in params:
        if not isinstance(p, dict):
            continue
        name = p.get("name", "")
        if not name:
            continue
        result.append(
            ParameterInfo(
                name=name,
                location=p.get("in", "query"),
                required=p.get("required", False),
                schema=p.get("schema", {"type": "string"}),
                description=p.get("description", ""),
            )
        )
    return result


def operation_to_function_name(op: OperationInfo) -> str:
    """Convert operation to a snake_case function name.

    Uses operationId if available, otherwise generates from method+path.
    """
    if op.operation_id:
        name = op.operation_id
    else:
        # Generate from method + path: GET /pets/{petId} -> get_pets_by_pet_id
        path_parts = []
        for segment in op.path.strip("/").split("/"):
            if segment.startswith("{") and segment.endswith("}"):
                path_parts.append("by_" + segment[1:-1])
            else:
                path_parts.append(segment)
        name = op.method.lower() + "_" + "_".join(path_parts)

    # Convert camelCase/PascalCase to snake_case
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", name)
    name = name.lower()

    # Clean up: replace non-alphanumeric with underscore
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip("_")

    # Ensure starts with letter
    if name and name[0].isdigit():
        name = "op_" + name

    return name or "unnamed_operation"


def build_input_schema(
    op: OperationInfo,
    auth_type: str = "none",
) -> dict[str, Any]:
    """Build a flat JSON Schema combining path/query params + body properties.

    Adds api_key property when auth is configured.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    # Path and query parameters
    for param in op.parameters:
        if param.location in ("path", "query"):
            prop = dict(param.schema)
            if param.description:
                prop["description"] = param.description
            properties[param.name] = prop
            if param.required or param.location == "path":
                required.append(param.name)

    # Request body properties (flattened)
    if op.request_body_schema:
        body_props = op.request_body_schema.get("properties", {})
        body_required = op.request_body_schema.get("required", [])
        for prop_name, prop_schema in body_props.items():
            if prop_name not in properties:  # Don't override params
                properties[prop_name] = _simplify_schema(prop_schema)
                if op.request_body_required and prop_name in body_required:
                    required.append(prop_name)

    # Add api_key override when auth is configured
    if auth_type != "none":
        properties["api_key"] = {
            "type": "string",
            "description": "API key override (optional, reads from state if not provided)",
        }

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required

    return schema


def build_output_schema(op: OperationInfo) -> dict[str, Any]:
    """Extract output schema from 200/201 response."""
    if op.response_schema:
        return _simplify_schema(op.response_schema)

    # Default: generic object
    return {"type": "object"}


def _simplify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Simplify a JSON Schema for SINAS function use.

    Removes OpenAPI-specific extensions, keeps core JSON Schema.
    """
    if not isinstance(schema, dict):
        return {"type": "object"}

    result: dict[str, Any] = {}
    # Copy core JSON Schema fields
    for key in [
        "type",
        "description",
        "enum",
        "format",
        "default",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "items",
        "properties",
        "required",
        "additionalProperties",
        "oneOf",
        "anyOf",
        "allOf",
    ]:
        if key in schema:
            if key in ("items", "additionalProperties") and isinstance(schema[key], dict):
                result[key] = _simplify_schema(schema[key])
            elif key == "properties" and isinstance(schema[key], dict):
                result[key] = {k: _simplify_schema(v) for k, v in schema[key].items()}
            elif key in ("oneOf", "anyOf", "allOf") and isinstance(schema[key], list):
                result[key] = [_simplify_schema(s) for s in schema[key] if isinstance(s, dict)]
            else:
                result[key] = schema[key]

    if "type" not in result:
        result["type"] = "object"

    return result


def generate_function_code(
    op: OperationInfo,
    func_name: str,
    base_url: str,
    auth_type: str = "none",
    auth_header: str = "Authorization",
    auth_state_namespace: Optional[str] = None,
    auth_state_key: Optional[str] = None,
) -> str:
    """Generate Python code for a function that calls the API endpoint."""
    # Build path with f-string substitution for path params
    path_params = [p for p in op.parameters if p.location == "path"]
    query_params = [p for p in op.parameters if p.location == "query"]

    # Build URL
    api_path = op.path
    for p in path_params:
        api_path = api_path.replace("{" + p.name + "}", "' + str(input['" + p.name + "']) + '")

    lines = []
    lines.append(f"def {func_name}(input, context):")

    # Docstring
    desc = op.summary or op.description or f"{op.method} {op.path}"
    lines.append(f'    """{desc}"""')
    lines.append("    import requests")
    lines.append("")
    lines.append(f'    base_url = "{base_url}"')

    # URL construction
    if path_params:
        lines.append(f"    url = base_url + '{api_path}'")
    else:
        lines.append(f'    url = base_url + "{op.path}"')

    # Headers
    has_body = op.method in ("POST", "PUT", "PATCH") and op.request_body_schema
    if has_body:
        lines.append('    headers = {"Content-Type": "application/json"}')
    else:
        lines.append("    headers = {}")

    # Auth block
    if auth_type != "none":
        lines.append("")
        lines.append("    # Auth: read API key from state, allow input override")
        lines.append('    api_key = input.get("api_key", "")')
        if auth_state_namespace and auth_state_key:
            lines.append("    if not api_key:")
            lines.append("        try:")
            lines.append("            state_resp = requests.get(")
            lines.append(
                f'                "http://host.docker.internal:8000/api/runtime/states/{auth_state_namespace}/{auth_state_key}",'
            )
            lines.append(
                """                headers={"Authorization": f"Bearer {context['access_token']}"}"""
            )
            lines.append("            )")
            lines.append("            if state_resp.status_code == 200:")
            lines.append(
                '                api_key = state_resp.json().get("value", {}).get("key", "")'
            )
            lines.append("        except Exception:")
            lines.append("            pass")

        # Set auth header
        if auth_type == "bearer":
            lines.append("    if api_key:")
            lines.append(f'        headers["{auth_header}"] = f"Bearer {{api_key}}"')
        elif auth_type == "api_key":
            lines.append("    if api_key:")
            lines.append(f'        headers["{auth_header}"] = api_key')

    # Query parameters
    if query_params:
        lines.append("")
        lines.append("    # Query parameters")
        lines.append("    params = {}")
        for p in query_params:
            lines.append(f'    if "{p.name}" in input:')
            lines.append(f'        params["{p.name}"] = input["{p.name}"]')

    # Request body
    if has_body:
        lines.append("")
        lines.append("    # Request body")
        body_props = op.request_body_schema.get("properties", {}) if op.request_body_schema else {}
        if body_props:
            lines.append("    body = {}")
            for prop_name in body_props:
                lines.append(f'    if "{prop_name}" in input:')
                lines.append(f'        body["{prop_name}"] = input["{prop_name}"]')
        else:
            # No known properties — pass any non-param input as body
            lines.append("    # Pass all non-parameter input as request body")
            param_names = {p.name for p in op.parameters}
            lines.append(f"    _param_names = {param_names!r}")
            lines.append(
                "    body = {k: v for k, v in input.items() if k not in _param_names and k != 'api_key'}"
            )

    # Make the HTTP call
    lines.append("")
    method_lower = op.method.lower()
    call_args = ["url", "headers=headers"]
    if query_params:
        call_args.append("params=params")
    if has_body:
        call_args.append("json=body")
    lines.append(f"    response = requests.{method_lower}({', '.join(call_args)})")
    lines.append("    response.raise_for_status()")

    # Return
    lines.append("")
    lines.append(
        '    if response.headers.get("content-type", "").startswith("application/json"):'
    )
    lines.append("        return response.json()")
    lines.append('    return {"body": response.text, "status_code": response.status_code}')

    return "\n".join(lines)


def _slugify(text: str) -> str:
    """Convert text to a valid namespace slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9_]", "_", text)
    text = re.sub(r"_+", "_", text)
    text = text.strip("_")
    if text and text[0].isdigit():
        text = "api_" + text
    return text or "imported"


async def import_openapi(
    spec_str: str,
    namespace: Optional[str] = None,
    base_url_override: Optional[str] = None,
    auth_type: str = "none",
    auth_header: str = "Authorization",
    auth_state_namespace: Optional[str] = None,
    auth_state_key: Optional[str] = None,
    selected_operations: Optional[list[str]] = None,
    dry_run: bool = True,
    db=None,
    user_id=None,
) -> dict[str, Any]:
    """Orchestrate the full OpenAPI import flow.

    Returns a dict with: functions, warnings, created, skipped.
    """
    from sqlalchemy import select

    from app.models.function import Function, FunctionVersion

    spec = parse_openapi_spec(spec_str)
    operations = extract_operations(spec)

    # Determine namespace
    if not namespace:
        title = spec.get("info", {}).get("title", "imported")
        namespace = _slugify(title)
    # Validate namespace format
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", namespace):
        namespace = _slugify(namespace)

    # Determine base URL
    base_url = base_url_override
    if not base_url:
        servers = spec.get("servers", [])
        if servers and isinstance(servers[0], dict):
            base_url = servers[0].get("url", "")
    if not base_url:
        base_url = "https://api.example.com"
    # Remove trailing slash
    base_url = base_url.rstrip("/")

    warnings: list[str] = []
    previews: list[dict[str, Any]] = []
    created = 0
    skipped = 0

    for op in operations:
        func_name = operation_to_function_name(op)

        # Filter by selected operations
        if selected_operations is not None:
            op_identifier = op.operation_id or func_name
            if op_identifier not in selected_operations:
                continue

        input_schema = build_input_schema(op, auth_type)
        output_schema = build_output_schema(op)
        code = generate_function_code(
            op=op,
            func_name=func_name,
            base_url=base_url,
            auth_type=auth_type,
            auth_header=auth_header,
            auth_state_namespace=auth_state_namespace,
            auth_state_key=auth_state_key,
        )

        description = op.summary or op.description or f"{op.method} {op.path}"

        # Check if function already exists
        status = "will_create"
        if db:
            result = await db.execute(
                select(Function).where(
                    Function.namespace == namespace,
                    Function.name == func_name,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                status = "exists_skip"
                skipped += 1

        preview = {
            "name": func_name,
            "description": description,
            "method": op.method,
            "path": op.path,
            "operation_id": op.operation_id,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "code": code,
            "status": status,
            "requirements": ["requests"],
        }
        previews.append(preview)

        # Actually create if not dry run
        if not dry_run and db and user_id and status == "will_create":
            function = Function(
                user_id=user_id,
                namespace=namespace,
                name=func_name,
                description=description,
                code=code,
                input_schema=input_schema,
                output_schema=output_schema,
                requirements=["requests"],
            )
            db.add(function)
            await db.flush()

            version = FunctionVersion(
                function_id=function.id,
                version=1,
                code=code,
                input_schema=input_schema,
                output_schema=output_schema,
                created_by=str(user_id),
            )
            db.add(version)
            created += 1

    if not dry_run and db:
        await db.commit()

    if not operations:
        warnings.append("No operations found in the spec")

    return {
        "namespace": namespace,
        "functions": previews,
        "warnings": warnings,
        "created": created,
        "skipped": skipped,
    }
