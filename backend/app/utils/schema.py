"""JSON Schema utilities for validation and type coercion."""
import json
from typing import Any

import jsonschema


def coerce_types(data: Any, schema: dict[str, Any]) -> Any:
    """
    Coerce data types to match JSON schema.

    Handles common cases where data comes in as strings but needs to be
    converted to proper types (e.g., "1" -> 1, "true" -> True).

    Args:
        data: Input data (usually a dict)
        schema: JSON Schema definition

    Returns:
        Data with coerced types
    """
    if not isinstance(data, dict) or not isinstance(schema, dict):
        return data

    properties = schema.get("properties", {})

    # If data is dict and schema has properties, coerce each property
    if properties and isinstance(data, dict):
        coerced = {}
        for key, value in data.items():
            if key in properties:
                prop_schema = properties[key]
                prop_type = prop_schema.get("type")

                # Coerce based on type
                if prop_type == "number" or prop_type == "integer":
                    if isinstance(value, str):
                        try:
                            coerced[key] = int(value) if prop_type == "integer" else float(value)
                        except (ValueError, TypeError):
                            coerced[key] = value
                    else:
                        coerced[key] = value

                elif prop_type == "boolean":
                    if isinstance(value, str):
                        coerced[key] = value.lower() in ("true", "1", "yes")
                    else:
                        coerced[key] = bool(value)

                elif prop_type == "array":
                    if isinstance(value, str):
                        try:
                            coerced[key] = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            coerced[key] = value
                    else:
                        coerced[key] = value

                elif prop_type == "object":
                    if isinstance(value, str):
                        try:
                            coerced[key] = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            coerced[key] = value
                    else:
                        coerced[key] = value
                else:
                    coerced[key] = value
            else:
                # Key not in schema, pass through
                coerced[key] = value
        return coerced

    return data


def validate_with_coercion(data: Any, schema: dict[str, Any]) -> Any:
    """
    Coerce types and then validate against JSON schema.

    Args:
        data: Input data
        schema: JSON Schema definition

    Returns:
        Coerced data (if validation passes)

    Raises:
        jsonschema.ValidationError: If validation fails
    """
    coerced_data = coerce_types(data, schema)
    jsonschema.validate(coerced_data, schema)
    return coerced_data
