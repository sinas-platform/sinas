"""Component-to-tool converter for LLM tool calling.

When an agent has `enabled_components`, each component is exposed as a tool
the LLM can call. Calling the tool returns a component reference block that
the frontend renders as an embedded iframe.
"""
import json
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.component import Component

logger = logging.getLogger(__name__)


class ComponentToolConverter:
    """Converts components to OpenAI tool format for agent tool calling."""

    async def get_available_components(
        self,
        db: AsyncSession,
        enabled_components: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """
        Get components and convert to OpenAI tools format.

        Args:
            db: Database session
            enabled_components: List of "namespace/name" component references

        Returns:
            List of components in OpenAI tool format
        """
        tools = []

        if not enabled_components:
            return tools

        for comp_ref in enabled_components:
            if "/" not in comp_ref:
                logger.warning(f"Invalid component reference format: {comp_ref}")
                continue

            namespace, name = comp_ref.split("/", 1)

            component = await Component.get_by_name(db, namespace, name)
            if not component or not component.is_active:
                logger.warning(f"Component {comp_ref} not found or inactive")
                continue

            tool = self._component_to_tool(component)
            tools.append(tool)

        return tools

    def _component_to_tool(self, component: Component) -> dict[str, Any]:
        """
        Convert a component to OpenAI tool format.

        The component's input_schema defines the tool parameters.
        When called, returns a component reference block for frontend rendering.
        """
        safe_name = f"show_component_{component.namespace}_{component.name}".replace("-", "_")

        description = (
            component.description
            or f"Show the '{component.title or component.name}' interactive component"
        )

        # Use component's input_schema as tool parameters, or empty object
        parameters = component.input_schema or {
            "type": "object",
            "properties": {},
            "required": [],
        }

        return {
            "type": "function",
            "function": {
                "name": safe_name,
                "description": description,
                "parameters": parameters,
            },
        }

    async def handle_component_tool_call(
        self,
        db: AsyncSession,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """
        Handle a component tool call by returning a component reference block.

        The frontend detects these blocks in assistant messages and renders
        the component as an embedded iframe.

        Args:
            db: Database session
            tool_name: Name of the tool (e.g., "show_component_default_dashboard")
            arguments: Tool arguments (component input vars)
            user_id: User ID for generating render token

        Returns:
            Component reference dict or None if component not found
        """
        if not tool_name.startswith("show_component_"):
            logger.warning(f"Invalid component tool name: {tool_name}")
            return None

        # Extract namespace/name from tool name
        comp_id = tool_name[len("show_component_"):]
        parts = comp_id.split("_", 1)

        if len(parts) != 2:
            logger.warning(f"Could not parse component from tool name: {tool_name}")
            return None

        namespace, name = parts
        # Convert back from safe name
        namespace = namespace.replace("_", "-")
        name = name.replace("_", "-")

        component = await Component.get_by_name(db, namespace, name)
        if not component or not component.is_active:
            logger.warning(f"Component {namespace}/{name} not found or inactive")
            return None

        # Generate render token for iframe embedding
        from app.api.runtime.endpoints.components import generate_component_render_token

        render_token = generate_component_render_token(
            component.namespace, component.name, user_id
        )

        # Return component reference block
        return {
            "type": "component",
            "namespace": component.namespace,
            "name": component.name,
            "title": component.title or component.name,
            "input": arguments,
            "compile_status": component.compile_status,
            "render_token": render_token,
        }
