"""Tool discovery: resolve available tools for an agent/chat context."""
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_user_permissions
from app.core.permissions import check_permission
from app.models import Agent, Chat
from app.models.execution import Execution, ExecutionStatus
from app.services.code_execution import get_tool_definition as get_code_exec_tool_definition
from app.services.collection_tools import CollectionToolConverter
from app.services.component_tools import ComponentToolConverter
from app.services.connector_tools import ConnectorToolConverter
from app.services.function_tools import FunctionToolConverter
from app.services.query_tools import QueryToolConverter
from app.services.skill_tools import SkillToolConverter
from app.services.state_tools import StateTools

logger = logging.getLogger(__name__)


async def resolve_agent_patterns(
    db: AsyncSession,
    patterns: list[str],
    user_id: str,
    permissions: dict[str, bool],
) -> list[Agent]:
    """Resolve agent patterns to Agent objects.

    Supports:
    - "*/*" — all active agents
    - "namespace/*" — all active agents in namespace
    - "namespace/name" — specific agent by namespace/name
    - UUID string — legacy lookup by ID
    """
    seen_ids: set[uuid.UUID] = set()
    resolved: list[Agent] = []

    for pattern in patterns:
        agents_to_check: list[Agent] = []

        if pattern == "*/*":
            result = await db.execute(
                select(Agent).where(Agent.is_active == True)
            )
            agents_to_check = list(result.scalars().all())
        elif pattern.endswith("/*"):
            ns = pattern[:-2]
            result = await db.execute(
                select(Agent).where(Agent.namespace == ns, Agent.is_active == True)
            )
            agents_to_check = list(result.scalars().all())
        elif "/" in pattern:
            ns, name = pattern.split("/", 1)
            agent = await Agent.get_by_name(db, ns, name)
            if agent:
                agents_to_check = [agent]
        else:
            # Try as UUID (legacy)
            try:
                uuid.UUID(pattern)
                result = await db.execute(
                    select(Agent).where(Agent.id == pattern, Agent.is_active == True)
                )
                agent = result.scalar_one_or_none()
                if agent:
                    agents_to_check = [agent]
            except ValueError:
                pass

        for agent in agents_to_check:
            if agent.id in seen_ids:
                continue
            perm = f"sinas.agents/{agent.namespace}/{agent.name}.read:own"
            if check_permission(permissions, perm):
                seen_ids.add(agent.id)
                resolved.append(agent)

    return resolved


async def get_agent_tools(agents: list[Agent]) -> list[dict[str, Any]]:
    """Get tool definitions for resolved agent objects."""
    tools = []

    for agent in agents:
        if not agent.is_active:
            continue

        # Build tool definition for this agent
        # Use clean name, store ID as hidden parameter
        tool_def = {
            "type": "function",
            "function": {
                "name": f"call_agent_{agent.name.lower().replace(' ', '_').replace('-', '_')}",
                "description": f"{agent.name}: {agent.description}"
                if agent.description
                else f"Call the {agent.name} agent",
            },
        }

        # Hidden parameters included in all agent tools
        _hidden = {
            "_agent_id": {
                "type": "string",
                "description": "Internal agent identifier",
                "const": str(agent.id),
                "default": str(agent.id),
            },
            "_chat_id": {
                "type": "string",
                "description": "Optional chat ID to resume a previous conversation with this agent instead of starting a new one. Use a chat_id returned from a previous call to continue that conversation.",
            },
        }

        # Build parameters - always include prompt + agent_id
        _prompt = {
            "prompt": {
                "type": "string",
                "description": "The message or query to send to the agent",
            },
        }

        if agent.input_schema and agent.input_schema.get("properties"):
            # Merge input_schema with prompt and hidden params
            params = dict(agent.input_schema)
            if "properties" not in params:
                params["properties"] = {}
            params["properties"] = {**_prompt, **params["properties"], **_hidden}
            if "required" not in params:
                params["required"] = []
            for req in ["prompt", "_agent_id"]:
                if req not in params["required"]:
                    params["required"].append(req)
            tool_def["function"]["parameters"] = params
        else:
            # Default: simple prompt + hidden params
            tool_def["function"]["parameters"] = {
                "type": "object",
                "properties": {
                    **_prompt,
                    **_hidden,
                },
                "required": ["prompt", "_agent_id"],
            }

        tools.append(tool_def)

    return tools


def parse_function_name(tool_name: str) -> tuple[Optional[str], Optional[str]]:
    """Parse namespace and name from tool_name.

    Handles both namespace__name (LLM format) and namespace/name formats.

    Returns:
        Tuple of (namespace, name) or (None, None) if not a function
    """
    # Skip non-function tools
    if tool_name in [
        "save_state",
        "retrieve_state",
        "update_state",
        "delete_state",
        "continue_execution",
        "execute_code",
    ] or tool_name.startswith("call_agent_") or tool_name.startswith("query_") or tool_name.startswith("search_collection_") or tool_name.startswith("get_file_"):
        return None, None

    # Convert namespace__name to namespace/name if needed
    if "__" in tool_name and "/" not in tool_name:
        tool_name = tool_name.replace("__", "/", 1)

    # Parse namespace/name
    if "/" not in tool_name:
        return None, None

    namespace, name = tool_name.split("/", 1)
    return namespace, name


def strip_tool_metadata(
    tools: Optional[list[dict[str, Any]]]
) -> Optional[list[dict[str, Any]]]:
    """Remove _metadata from tools before sending to LLM provider.

    LLM providers don't accept extra fields in tool definitions.
    """
    if not tools:
        return tools

    clean_tools = []
    for tool in tools:
        clean_tool = tool.copy()
        if "function" in clean_tool and "_metadata" in clean_tool["function"]:
            clean_tool = {
                **clean_tool,
                "function": {
                    k: v for k, v in clean_tool["function"].items() if k != "_metadata"
                },
            }
        clean_tools.append(clean_tool)
    return clean_tools


async def get_available_tools(
    db: AsyncSession,
    user_id: str,
    chat: Chat,
    function_converter: FunctionToolConverter,
    query_converter: QueryToolConverter,
    skill_converter: SkillToolConverter,
    component_converter: ComponentToolConverter,
    collection_converter: CollectionToolConverter,
    connector_converter: Optional[ConnectorToolConverter] = None,
) -> list[dict[str, Any]]:
    """Get all available tools (functions + context + agents + execution continuation)."""
    tools: list[dict[str, Any]] = []

    # No agent = no tools
    if not chat.agent_id:
        return tools

    # Get agent configuration
    result = await db.execute(select(Agent).where(Agent.id == chat.agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        return tools

    # Add state tools (based on agent's enabled stores)
    context_tool_defs = await StateTools.get_tool_definitions(
        db=db,
        user_id=user_id,
        enabled_stores=agent.enabled_stores,
    )
    tools.extend(context_tool_defs)

    # Add agent tools (other agents this agent can call)
    agent_enabled = agent.enabled_agents or []
    if agent_enabled:
        user_permissions = await get_user_permissions(db, user_id)
        resolved_agents = await resolve_agent_patterns(
            db, agent_enabled, user_id, user_permissions
        )
        # Exclude self to prevent recursion
        resolved_agents = [a for a in resolved_agents if a.id != chat.agent_id]
        agent_tools = await get_agent_tools(resolved_agents)
        tools.extend(agent_tools)

    # Get agent input context for function parameter templating
    agent_input_context = {}
    if chat.chat_metadata and "agent_input" in chat.chat_metadata:
        agent_input_context = chat.chat_metadata["agent_input"]

    # Get function tools (only if list has items - opt-in)
    if agent.enabled_functions and len(agent.enabled_functions) > 0:
        function_tools = await function_converter.get_available_functions(
            db=db,
            user_id=user_id,
            enabled_functions=agent.enabled_functions,
            function_parameters=agent.function_parameters,
            agent_input_context=agent_input_context,
        )
        tools.extend(function_tools)

    # Get query tools (only if list has items - opt-in)
    if agent.enabled_queries and len(agent.enabled_queries) > 0:
        query_tools = await query_converter.get_available_queries(
            db=db,
            user_id=user_id,
            enabled_queries=agent.enabled_queries,
            query_parameters=agent.query_parameters,
            agent_input_context=agent_input_context,
        )
        tools.extend(query_tools)

    # Get skill tools (only if list has items - opt-in)
    if agent.enabled_skills and len(agent.enabled_skills) > 0:
        skill_tools = await skill_converter.get_available_skills(
            db=db, enabled_skills=agent.enabled_skills
        )
        tools.extend(skill_tools)

    # Get collection tools (only if list has items - opt-in)
    if agent.enabled_collections and len(agent.enabled_collections) > 0:
        collection_tools = await collection_converter.get_available_collections(
            db=db, user_id=user_id, enabled_collections=agent.enabled_collections
        )
        tools.extend(collection_tools)

    # Get component tools (only if list has items - opt-in)
    if agent.enabled_components and len(agent.enabled_components) > 0:
        component_tools = await component_converter.get_available_components(
            db=db, enabled_components=agent.enabled_components
        )
        tools.extend(component_tools)

    # Get connector tools (only if list has items - opt-in)
    if connector_converter and agent.enabled_connectors and len(agent.enabled_connectors) > 0:
        agent_input = chat.chat_metadata.get("agent_input", {}) if chat.chat_metadata else {}
        connector_tools = await connector_converter.get_available_connectors(
            db=db,
            enabled_connectors=agent.enabled_connectors,
            agent_input_context=agent_input,
        )
        tools.extend(connector_tools)

    # Add built-in retrieve_tool_result tool (always available)
    tools.append({
        "type": "function",
        "function": {
            "name": "retrieve_tool_result",
            "description": "Retrieve the full result of a previous tool call by its ID. Use this when you need to access data from an earlier tool call that is no longer shown inline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_call_id": {
                        "type": "string",
                        "description": "The tool_call_id to look up",
                    }
                },
                "required": ["tool_call_id"],
            },
        },
    })

    # Add code execution tool if enabled on the agent
    if agent.enable_code_execution:
        tools.append(await get_code_exec_tool_definition(db))

    # Check for paused executions belonging to this chat
    result = await db.execute(
        select(Execution)
        .where(Execution.chat_id == chat.id, Execution.status == ExecutionStatus.AWAITING_INPUT)
        .limit(10)
    )
    paused_executions = result.scalars().all()

    if paused_executions:
        # Add continue_execution tool with details about paused executions
        execution_list = "\n".join(
            [
                f"- {ex.execution_id}: {ex.function_name} - {ex.input_prompt}"
                for ex in paused_executions
            ]
        )

        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "continue_execution",
                    "description": f"Continue a paused function execution by providing required input. Currently paused executions:\n{execution_list}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "execution_id": {
                                "type": "string",
                                "description": "The execution ID to continue",
                                "enum": [ex.execution_id for ex in paused_executions],
                            },
                            "input": {
                                "type": "object",
                                "description": "Input data to provide to the paused execution",
                            },
                        },
                        "required": ["execution_id", "input"],
                    },
                },
            }
        )

    return tools
