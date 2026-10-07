"""Component-to-tool converter for LLM tool calling.

When an agent has `enabled_components`, each component is exposed as a tool
the LLM can call. Calling the tool returns a component reference block that
the frontend renders as an embedded iframe.
"""
import json
import logging
from typing import Any, Optional

from sqlalchemy import select
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
        # "{ns}__{name}", as functions and queries name their tools (and as
        # tool_name_to_status_key reads them back). Replacing "-" with "_"
        # and splitting on the first "_" lost any name containing either.
        safe_name = f"show_component_{component.namespace}__{component.name}"

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

    async def _exact_lookup(self, db: AsyncSession, comp_id: str) -> Optional[Component]:
        """The component whose tool name is exactly this. A namespace or
        name may itself contain "__", so every split is tried; two different
        components producing the same name is refused, not guessed."""
        matches = []
        start = 0
        while (index := comp_id.find("__", start)) != -1:
            namespace, name = comp_id[:index], comp_id[index + 2:]
            if namespace and name:
                component = await Component.get_by_name(db, namespace, name)
                if component is not None and component.is_active:
                    matches.append(component)
            start = index + 1
        if len(matches) > 1:
            logger.warning(f"Ambiguous component tool name: show_component_{comp_id}")
            return None
        return matches[0] if matches else None

    async def _legacy_lookup(self, db: AsyncSession, comp_id: str) -> Optional[Component]:
        """Tool names recorded before the "__" form. First what the old
        handler looked up (split on the first "_", every "_" in the name
        read as "-"), so an old chat opens what it always opened; then the
        other spellings that produce this name, if exactly one exists."""
        namespace, _, name = comp_id.partition("_")
        if namespace and name:
            component = await Component.get_by_name(db, namespace, name.replace("_", "-"))
            if component is not None and component.is_active:
                return component
        candidates = (
            await db.execute(select(Component).where(Component.is_active == True))  # noqa: E712
        ).scalars().all()
        matches = [
            c for c in candidates
            if f"{c.namespace}_{c.name}".replace("-", "_") == comp_id
        ]
        if len(matches) > 1:
            logger.warning(f"Ambiguous legacy component tool name: show_component_{comp_id}")
            return None
        return matches[0] if matches else None

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

        comp_id = tool_name[len("show_component_"):]
        if "__" in comp_id:
            component = await self._exact_lookup(db, comp_id)
        else:
            # Tool calls recorded before the "__" form (in existing chats):
            # match the old spelling, where "-" had become "_".
            component = await self._legacy_lookup(db, comp_id)
        if component is None or not component.is_active:
            logger.warning(f"Could not resolve component from tool name: {tool_name}")
            return None
        namespace, name = component.namespace, component.name
        if not component or not component.is_active:
            logger.warning(f"Component {namespace}/{name} not found or inactive")
            return None

        # Generate render token for iframe embedding
        from app.services.content_tokens import generate_component_render_token

        render_token = generate_component_render_token(
            component.namespace, component.name, user_id
        )

        # Return component reference block (rendered client-side in iframe)
        display_name = component.title or f"{component.namespace}/{component.name}"
        return {
            "type": "component",
            "namespace": component.namespace,
            "name": component.name,
            "title": display_name,
            "input": arguments,
            "compile_status": component.compile_status,
            "render_token": render_token,
            "display": f"[USER WILL SEE COMPONENT '{display_name}' HERE]",
        }
