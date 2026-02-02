"""Skill-to-tool converter for LLM tool calling."""
import logging
from typing import List, Dict, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill

logger = logging.getLogger(__name__)


class SkillToolConverter:
    """Converts skills to OpenAI tool format and manages retrieval."""

    async def get_available_skills(
        self,
        db: AsyncSession,
        enabled_skills: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get skills and convert to OpenAI tools format.

        Only skills with preload=false are exposed as tools.
        Preloaded skills (preload=true) should be retrieved via get_preloaded_skills_content().

        Args:
            db: Database session
            enabled_skills: List of skill configs with {"skill": "namespace/name", "preload": bool}

        Returns:
            List of skills in OpenAI tool format (only non-preloaded)
        """
        tools = []

        # If no enabled_skills specified, return empty
        if not enabled_skills:
            return tools

        for skill_config in enabled_skills:
            # Skip if preloaded (will be injected into system prompt instead)
            if skill_config.get("preload", False):
                continue

            skill_ref = skill_config.get("skill")
            if not skill_ref:
                logger.warning(f"Invalid skill config: {skill_config}")
                continue

            # Parse namespace/name
            if "/" not in skill_ref:
                logger.warning(f"Invalid skill reference format: {skill_ref}")
                continue

            namespace, name = skill_ref.split("/", 1)

            # Load skill by namespace/name
            skill = await Skill.get_by_name(db, namespace, name)

            if not skill or not skill.is_active:
                logger.warning(f"Skill {skill_ref} not found or inactive")
                continue

            # Convert skill to tool format
            tool = self.skill_to_tool(skill)
            tools.append(tool)

        return tools

    async def get_preloaded_skills_content(
        self,
        db: AsyncSession,
        enabled_skills: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Get content for preloaded skills to inject into system prompt.

        Only returns content for skills with preload=true.

        Args:
            db: Database session
            enabled_skills: List of skill configs with {"skill": "namespace/name", "preload": bool}

        Returns:
            Combined markdown content for all preloaded skills
        """
        contents = []

        # If no enabled_skills specified, return empty
        if not enabled_skills:
            return ""

        for skill_config in enabled_skills:
            # Only include preloaded skills
            if not skill_config.get("preload", False):
                continue

            skill_ref = skill_config.get("skill")
            if not skill_ref:
                logger.warning(f"Invalid skill config: {skill_config}")
                continue

            # Parse namespace/name
            if "/" not in skill_ref:
                logger.warning(f"Invalid skill reference format: {skill_ref}")
                continue

            namespace, name = skill_ref.split("/", 1)

            # Load skill by namespace/name
            skill = await Skill.get_by_name(db, namespace, name)

            if not skill or not skill.is_active:
                logger.warning(f"Preloaded skill {skill_ref} not found or inactive")
                continue

            # Add skill content with header
            contents.append(f"# Skill: {skill.namespace}/{skill.name}\n\n{skill.content}")

        return "\n\n---\n\n".join(contents)

    def skill_to_tool(self, skill: Skill) -> Dict[str, Any]:
        """
        Convert a skill to OpenAI tool format.

        The skill's description becomes the tool description (shown to LLM),
        and calling the tool returns the skill's content (markdown instructions).

        Args:
            skill: Skill model instance

        Returns:
            Tool definition in OpenAI format
        """
        # Create a safe function name from namespace/name
        # Replace non-alphanumeric chars with underscore
        safe_name = f"get_skill_{skill.namespace}_{skill.name}".replace("-", "_")

        return {
            "type": "function",
            "function": {
                "name": safe_name,
                "description": skill.description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }

    async def handle_skill_tool_call(
        self,
        db: AsyncSession,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Optional[str]:
        """
        Handle a skill tool call by returning the skill's content.

        Args:
            db: Database session
            tool_name: Name of the tool (e.g., "get_skill_default_web_research")
            arguments: Tool arguments (empty for skills)

        Returns:
            Skill content (markdown) or None if skill not found
        """
        # Extract namespace/name from tool name
        # Format: get_skill_{namespace}_{name}
        if not tool_name.startswith("get_skill_"):
            logger.warning(f"Invalid skill tool name: {tool_name}")
            return None

        # Remove prefix and split
        skill_id = tool_name[len("get_skill_"):]
        parts = skill_id.split("_", 1)

        if len(parts) != 2:
            logger.warning(f"Could not parse skill from tool name: {tool_name}")
            return None

        namespace, name = parts
        # Convert back from safe name (underscores to hyphens)
        namespace = namespace.replace("_", "-")
        name = name.replace("_", "-")

        # Load skill
        skill = await Skill.get_by_name(db, namespace, name)

        if not skill or not skill.is_active:
            logger.warning(f"Skill {namespace}/{name} not found or inactive")
            return None

        # Return the skill content (markdown instructions)
        return skill.content
