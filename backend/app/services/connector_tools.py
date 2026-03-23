"""Connector tool converter — exposes connector operations as agent tools."""
import logging
import re
from typing import Any, Optional

from jinja2 import Template
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connector import Connector
from app.services.connector_service import connector_service

logger = logging.getLogger(__name__)

# Prefix for all connector tool names
CONNECTOR_TOOL_PREFIX = "connector__"


class ConnectorToolConverter:
    """Converts connector operations to OpenAI-format tools and executes them."""

    async def get_available_connectors(
        self,
        db: AsyncSession,
        enabled_connectors: Optional[list[dict[str, Any]]] = None,
        agent_input_context: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Build tool definitions for enabled connector operations.

        enabled_connectors format:
        [
            {
                "connector": "namespace/name",
                "operations": ["post_message", "get_channel"],
                "parameters": {
                    "post_message": {"channel": "{{ slack_channel }}"}
                }
            }
        ]
        """
        if not enabled_connectors:
            return []

        tools = []
        for entry in enabled_connectors:
            connector_ref = entry.get("connector", "")
            parts = connector_ref.split("/", 1)
            if len(parts) != 2:
                logger.warning(f"Invalid connector reference: {connector_ref}")
                continue

            namespace, name = parts
            connector = await Connector.get_by_name(db, namespace, name)
            if not connector or not connector.is_active:
                logger.warning(f"Connector '{connector_ref}' not found or inactive")
                continue

            enabled_ops = entry.get("operations", [])
            param_overrides = entry.get("parameters", {})

            for op in connector.operations:
                op_name = op.get("name")
                if not op_name:
                    continue
                # Filter to enabled operations
                if enabled_ops and op_name not in enabled_ops:
                    continue

                # Resolve parameter overrides
                locked_params = {}
                op_params = param_overrides.get(op_name, {})
                if op_params and agent_input_context:
                    for param_key, param_value in op_params.items():
                        if isinstance(param_value, str) and "{{" in param_value:
                            try:
                                locked_params[param_key] = Template(param_value).render(**agent_input_context)
                            except Exception:
                                locked_params[param_key] = param_value
                        else:
                            locked_params[param_key] = param_value
                elif op_params:
                    locked_params = dict(op_params)

                tool = self._operation_to_tool(connector, op, locked_params)
                tools.append(tool)

        return tools

    def _operation_to_tool(
        self,
        connector: Connector,
        operation: dict[str, Any],
        locked_params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Convert a connector operation to an OpenAI-format tool."""
        op_name = operation["name"]
        tool_name = f"{CONNECTOR_TOOL_PREFIX}{connector.namespace}__{connector.name}__{op_name}"

        # Build description
        desc = operation.get("description") or f"{operation['method']} {operation['path']}"
        desc = f"[{connector.namespace}/{connector.name}] {desc}"

        # Build parameters schema, excluding locked params
        parameters = dict(operation.get("parameters", {"type": "object", "properties": {}}))
        if locked_params and "properties" in parameters:
            filtered_props = {
                k: v for k, v in parameters["properties"].items()
                if k not in locked_params
            }
            parameters = {**parameters, "properties": filtered_props}
            # Remove locked params from required
            if "required" in parameters:
                parameters["required"] = [
                    r for r in parameters["required"]
                    if r not in locked_params
                ]
                if not parameters["required"]:
                    del parameters["required"]

        return {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": desc,
                "parameters": parameters,
                "_metadata": {
                    "type": "connector",
                    "connector_namespace": connector.namespace,
                    "connector_name": connector.name,
                    "operation_name": op_name,
                    "locked_params": locked_params or {},
                },
            },
        }

    async def execute_connector_tool(
        self,
        db: AsyncSession,
        tool_name: str,
        arguments: dict[str, Any],
        user_token: Optional[str] = None,
        locked_params: Optional[dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute a connector tool call."""
        # Parse tool name: connector__namespace__name__operation
        parts = tool_name[len(CONNECTOR_TOOL_PREFIX):].split("__", 2)
        if len(parts) != 3:
            return {"error": f"Invalid connector tool name: {tool_name}"}

        namespace, name, operation_name = parts

        connector = await Connector.get_by_name(db, namespace, name)
        if not connector or not connector.is_active:
            return {"error": f"Connector '{namespace}/{name}' not found or inactive"}

        # Merge locked params with LLM-provided arguments
        merged_params = {**(locked_params or {}), **arguments}

        try:
            result = await connector_service.execute_operation(
                db=db,
                connector=connector,
                operation_name=operation_name,
                parameters=merged_params,
                user_token=user_token,
                user_id=user_id,
            )
            return result
        except Exception as e:
            logger.exception(f"Connector operation failed: {namespace}/{name}/{operation_name}")
            return {"error": str(e)}


def parse_connector_tool_name(tool_name: str) -> Optional[tuple[str, str, str]]:
    """Parse connector tool name into (namespace, name, operation).
    Returns None if not a connector tool.
    """
    if not tool_name.startswith(CONNECTOR_TOOL_PREFIX):
        return None
    parts = tool_name[len(CONNECTOR_TOOL_PREFIX):].split("__", 2)
    if len(parts) != 3:
        return None
    return tuple(parts)
