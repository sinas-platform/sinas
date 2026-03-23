"""Tool execution: run individual tool calls and manage approval flow."""
import json
import logging
import time
import traceback
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_user_permissions
from app.core.database import AsyncSessionLocal
from app.models import Agent, Chat, Message
from app.models.function import Function
from app.models.pending_approval import PendingToolApproval
from app.models.user import User
from app.services.code_execution import execute as execute_code
from app.services.collection_tools import CollectionToolConverter
from app.services.component_tools import ComponentToolConverter
from app.services.execution_engine import executor as fn_executor
from app.services.function_tools import FunctionToolConverter
from app.services.query_tools import QueryToolConverter
from app.services.skill_tools import SkillToolConverter
from app.services.state_tools import StateTools
from app.services.queue_service import queue_service
from app.services.stream_relay import stream_relay
from app.services.template_renderer import render_template
from app.services.tool_discovery import parse_function_name, resolve_agent_patterns

logger = logging.getLogger(__name__)


def validate_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate tool calls and filter out corrupted ones.

    Returns only valid tool calls.
    """
    if not tool_calls:
        return []

    valid_tool_calls = []
    for tc in tool_calls:
        try:
            # Check required fields
            if not tc.get("id") or not tc.get("function", {}).get("name"):
                print(f"\u26a0\ufe0f Skipping tool call without id or name: {tc}")
                continue

            # Validate arguments is valid JSON
            args_str = tc.get("function", {}).get("arguments", "")
            if args_str:
                json.loads(args_str)  # This will raise if invalid

            valid_tool_calls.append(tc)
        except json.JSONDecodeError as e:
            print(f"\u26a0\ufe0f Invalid tool call arguments JSON: {e}")
            print(f"   Tool call: {tc.get('function', {}).get('name')}")
            print(f"   Arguments: {repr(args_str[:200])}")
            # Skip this tool call - don't add to valid list
            continue
        except Exception as e:
            print(f"\u26a0\ufe0f Error validating tool call: {e}")
            continue

    return valid_tool_calls


def is_sequential_tool(tool_name: str) -> bool:
    """Check if a tool must be executed sequentially (not parallelizable)."""
    return tool_name.startswith("call_agent_") or tool_name == "continue_execution" or tool_name == "execute_code"


def safe_parse_arguments(arguments: Any) -> dict:
    """Safely parse tool call arguments to a dict."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str) and arguments.strip():
        try:
            return json.loads(arguments)
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def tool_name_to_status_key(tool_name: str) -> str:
    """Convert LLM tool name to status_templates lookup key.

    LLM tool names -> prefixed keys:
      "default__search_web"                -> "function:default/search_web"
      "call_agent_support__helper"         -> "agent:support/helper"
      "query_analytics__daily_report"      -> "query:analytics/daily_report"
      "search_collection_docs__manuals"    -> "collection:docs/manuals"
      "get_file_docs__manuals"             -> "collection:docs/manuals"
      "get_skill_default__tone"            -> "skill:default/tone"
      "show_component_ui__chart"           -> "component:ui/chart"
      "save_state"                         -> "state:save_state"
    """
    if tool_name.startswith("call_agent_"):
        return "agent:" + tool_name[len("call_agent_"):].replace("__", "/", 1)
    if tool_name.startswith("get_skill_"):
        return "skill:" + tool_name[len("get_skill_"):].replace("__", "/", 1)
    if tool_name.startswith("query_"):
        return "query:" + tool_name[len("query_"):].replace("__", "/", 1)
    if tool_name.startswith("search_collection_"):
        return "collection:" + tool_name[len("search_collection_"):].replace("__", "/", 1)
    if tool_name.startswith("get_file_"):
        return "collection:" + tool_name[len("get_file_"):].replace("__", "/", 1)
    if tool_name.startswith("show_component_"):
        return "component:" + tool_name[len("show_component_"):].replace("__", "/", 1)
    if tool_name in ("save_state", "retrieve_state", "update_state", "delete_state"):
        return f"state:{tool_name}"
    # Default: function
    return "function:" + tool_name.replace("__", "/", 1)


def build_tool_status(tool_name: str, arguments: dict, status_templates: dict[str, str]) -> str:
    """Build human-readable status for a tool call.

    Looks up status_templates by type-prefixed key (e.g. "function:web/search").
    Falls back to humanized tool name.
    """
    key = tool_name_to_status_key(tool_name)
    template = status_templates.get(key)
    if template:
        try:
            return render_template(template, arguments)
        except Exception:
            pass

    # Fallback: humanize by tool type
    if key.startswith("agent:"):
        return f"Calling {key[6:]}"
    if key.startswith("skill:"):
        return f"Loading skill {key[6:]}"
    if key.startswith("query:"):
        ref = key[6:]
        return f"Running query {ref.split('/')[-1].replace('_', ' ')}"
    if key.startswith("collection:"):
        return "Searching files"
    if key.startswith("component:"):
        return f"Rendering {key[10:]}"
    if key.startswith("state:"):
        verb = tool_name.split("_")[0].capitalize()
        return f"{verb} state"
    # function:ns/name
    ref = key[9:]  # strip "function:"
    return f"Running {ref.split('/')[-1].replace('_', ' ')}"


async def check_approval_requirements(
    db: AsyncSession,
    tool_calls: list[dict[str, Any]],
    chat_id: str,
    user_id: str,
    message_id: str,
    messages: list[dict[str, Any]],
    provider: Optional[str],
    model: Optional[str],
    temperature: float,
    max_tokens: Optional[int],
    tools: Optional[list[dict[str, Any]]] = None,
) -> bool:
    """Check if any tool calls require user approval before execution.

    If approval is needed, creates PendingToolApproval records.

    Returns:
        True if any tool calls require approval, False otherwise
    """
    requires_approval = False

    for tool_call in tool_calls:
        tool_name = tool_call["function"]["name"]
        arguments_str = tool_call["function"]["arguments"]

        # Parse namespace/name from tool_name
        namespace, name = parse_function_name(tool_name)
        if not namespace or not name:
            # Not a function tool, skip
            continue

        # Load function to check requires_approval flag
        function = await Function.get_by_name(db, namespace, name)
        if not function or not function.requires_approval:
            continue

        # This function requires approval
        requires_approval = True

        # Parse arguments safely - handle empty strings
        parsed_args = arguments_str
        if isinstance(arguments_str, str):
            parsed_args = json.loads(arguments_str) if arguments_str.strip() else {}

        # Create PendingToolApproval record
        pending_approval = PendingToolApproval(
            chat_id=chat_id,
            message_id=message_id,
            user_id=user_id,
            tool_call_id=tool_call["id"],
            function_namespace=namespace,
            function_name=name,
            arguments=parsed_args,
            all_tool_calls=tool_calls,
            conversation_context={
                "provider": provider,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": messages,
                "tools": tools,  # Store tools list with metadata for resuming execution
            },
        )
        db.add(pending_approval)

    if requires_approval:
        await db.commit()

    return requires_approval


async def execute_agent_tool(
    db: AsyncSession,
    chat: Chat,
    user_id: str,
    user_token: str,
    tool_name: str,
    arguments: dict[str, Any],
    enabled_agent_ids: list[str],
    create_chat_with_agent_fn,
) -> dict[str, Any]:
    """Execute an agent tool call by creating or resuming a chat."""
    # Extract agent ID from arguments (passed as _agent_id parameter)
    agent_id_str = arguments.get("_agent_id")
    if not agent_id_str:
        return {"error": "Missing _agent_id in agent tool call"}

    # Verify this agent ID is in enabled list
    if agent_id_str not in enabled_agent_ids:
        return {"error": f"Agent {agent_id_str} not enabled for this agent"}

    # Load agent
    result = await db.execute(select(Agent).where(Agent.id == agent_id_str))
    agent = result.scalar_one_or_none()

    if not agent:
        return {"error": f"Agent not found: {agent_id_str}"}

    # Prepare input data for the agent
    # Filter out internal _* parameters and extract prompt
    user_arguments = {k: v for k, v in arguments.items() if not k.startswith("_")}
    content = user_arguments.pop("prompt", "")
    input_data = user_arguments  # Remaining args become input variables

    # Resume existing chat or create a new one
    resume_chat_id = arguments.get("_chat_id")
    try:
        if resume_chat_id:
            # Verify the chat exists, belongs to this user and agent
            result = await db.execute(
                select(Chat).where(
                    Chat.id == resume_chat_id,
                    Chat.user_id == user_id,
                    Chat.agent_id == agent_id_str,
                )
            )
            sub_chat = result.scalar_one_or_none()
            if not sub_chat:
                return {"error": f"Chat {resume_chat_id} not found or does not belong to this agent"}
            logger.info(f"Resuming sub-agent chat {sub_chat.id} with {agent.namespace}/{agent.name}")
        else:
            sub_chat = await create_chat_with_agent_fn(
                agent_id=str(agent.id),
                user_id=user_id,
                input_data=input_data,
                name=f"Sub-chat: {agent.name}",
            )

        # Route agent-to-agent calls through the queue so each sub-agent
        # runs in its own worker — enables agent swarms without recursive blocking.
        channel_id = str(uuid.uuid4())

        await queue_service.enqueue_agent_message(
            chat_id=str(sub_chat.id),
            user_id=user_id,
            user_token=user_token,
            content=content,
            channel_id=channel_id,
            agent=f"{agent.namespace}/{agent.name}",
        )

        # Wait for the sub-agent to finish by reading the Redis stream
        final_content = ""
        async for event in stream_relay.subscribe(channel_id):
            if event.get("content"):
                final_content += event["content"]
            if event.get("type") in ("done", "error"):
                if event.get("type") == "error":
                    return {
                        "agent_name": agent.name,
                        "error": event.get("error", "Sub-agent failed"),
                        "chat_id": str(sub_chat.id),
                    }
                break

        return {
            "agent_name": agent.name,
            "response": final_content,
            "chat_id": str(sub_chat.id),
        }

    except Exception as e:
        logger.error(f"Failed to execute agent tool {tool_name}: {e}")
        return {"error": str(e)}


async def execute_single_tool(
    tool_call: dict[str, Any],
    chat_id: str,
    user_id: str,
    user_token: str,
    tools: list[dict[str, Any]],
    function_converter: FunctionToolConverter,
    query_converter: QueryToolConverter,
    skill_converter: SkillToolConverter,
    component_converter: ComponentToolConverter,
    collection_converter: CollectionToolConverter,
    create_chat_with_agent_fn,
) -> tuple[str, str, str]:
    """Execute a single tool call. Uses its own DB session for parallel safety.

    Returns:
        Tuple of (tool_call_id, tool_name, result_content)
    """
    tool_name = tool_call["function"]["name"]
    arguments_str = tool_call["function"]["arguments"]

    # Handle arguments parsing
    try:
        if isinstance(arguments_str, str):
            arguments = json.loads(arguments_str) if arguments_str.strip() else {}
        else:
            arguments = arguments_str
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse tool arguments for {tool_name}: {e}")
        result_content = json.dumps({
            "error": f"Invalid JSON arguments: {str(e)}",
            "raw_arguments": arguments_str[:200] if isinstance(arguments_str, str) else str(arguments_str)[:200]
        })
        return (tool_call["id"], tool_name, result_content)

    # Execute tool with its own session
    try:
        async with AsyncSessionLocal() as db:
            # Get chat for context
            result_chat = await db.execute(select(Chat).where(Chat.id == chat_id))
            chat = result_chat.scalar_one_or_none()

            if tool_name in [
                "save_state",
                "retrieve_state",
                "update_state",
                "delete_state",
            ]:
                result = await StateTools.execute_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    user_id=user_id,
                    chat_id=str(chat_id),
                    agent_id=str(chat.agent_id) if chat and chat.agent_id else None,
                )
            elif tool_name == "continue_execution":
                # Resume directly in the container (bypasses queue)
                result = await fn_executor.resume_execution(
                    execution_id=arguments["execution_id"],
                    resume_value=arguments["input"],
                )
            elif tool_name.startswith("call_agent_"):
                # Resolve enabled agent patterns to actual agent IDs
                enabled_agent_ids = []
                if chat and chat.agent_id:
                    result_agent = await db.execute(
                        select(Agent).where(Agent.id == chat.agent_id)
                    )
                    chat_agent = result_agent.scalar_one_or_none()
                    if chat_agent and chat_agent.enabled_agents:
                        user_perms = await get_user_permissions(db, user_id)
                        resolved = await resolve_agent_patterns(
                            db, chat_agent.enabled_agents, user_id, user_perms
                        )
                        enabled_agent_ids = [str(a.id) for a in resolved]

                result = await execute_agent_tool(
                    db=db,
                    chat=chat,
                    user_id=user_id,
                    user_token=user_token,
                    tool_name=tool_name,
                    arguments=arguments,
                    enabled_agent_ids=enabled_agent_ids,
                    create_chat_with_agent_fn=create_chat_with_agent_fn,
                )
            elif tool_name == "execute_code":
                start_time = time.time()
                result = await execute_code(
                    code=arguments.get("code", ""),
                    user_id=user_id,
                    chat_id=str(chat_id),
                )
                elapsed = time.time() - start_time
                logger.debug(f"Code execution completed in {elapsed:.3f}s")
            elif tool_name.startswith("show_component_"):
                start_time = time.time()
                result = await component_converter.handle_component_tool_call(
                    db=db, tool_name=tool_name, arguments=arguments, user_id=user_id
                )
                elapsed = time.time() - start_time
                logger.debug(f"Component tool completed in {elapsed:.3f}s: {tool_name}")
                if result is None:
                    result = {"error": f"Component not found for tool: {tool_name}"}
            elif tool_name.startswith("get_skill_"):
                start_time = time.time()
                result = await skill_converter.handle_skill_tool_call(
                    db=db, tool_name=tool_name, arguments=arguments
                )
                elapsed = time.time() - start_time
                logger.debug(f"Skill retrieval completed in {elapsed:.3f}s: {tool_name}")
                if result is None:
                    result = {"error": f"Skill not found for tool: {tool_name}"}
            elif tool_name.startswith("query_"):
                start_time = time.time()
                # Extract metadata for locked/overridable params
                tool_metadata = {}
                for tool in tools:
                    if tool.get("function", {}).get("name") == tool_name:
                        tool_metadata = tool.get("function", {}).get("_metadata", {})
                        break

                # Get enabled queries list from agent
                enabled_query_list = []
                if chat and chat.agent_id:
                    result_agent = await db.execute(
                        select(Agent).where(Agent.id == chat.agent_id)
                    )
                    chat_agent = result_agent.scalar_one_or_none()
                    if chat_agent:
                        enabled_query_list = chat_agent.enabled_queries or []

                # Get user email for context injection
                user_email = None
                user_result = await db.execute(select(User).where(User.id == user_id))
                user_obj = user_result.scalar_one_or_none()
                if user_obj:
                    user_email = user_obj.email

                result = await query_converter.execute_query_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    user_id=user_id,
                    user_email=user_email,
                    locked_params=tool_metadata.get("locked_params", {}),
                    overridable_params=tool_metadata.get("overridable_params", {}),
                    enabled_queries=enabled_query_list,
                )
                elapsed = time.time() - start_time
                logger.debug(f"Query execution completed in {elapsed:.3f}s: {tool_name}")
            elif tool_name.startswith("search_collection_") or tool_name.startswith("get_file_"):
                start_time = time.time()
                tool_metadata = {}
                for tool in tools:
                    if tool.get("function", {}).get("name") == tool_name:
                        tool_metadata = tool.get("function", {}).get("_metadata", {})
                        break
                result = await collection_converter.execute_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    user_id=user_id,
                    metadata=tool_metadata,
                )
                elapsed = time.time() - start_time
                logger.debug(f"Collection tool completed in {elapsed:.3f}s: {tool_name}")
            elif tool_name.startswith("connector__"):
                # Connector tool — execute in-process HTTP call
                start_time = time.time()
                tool_metadata = {}
                for tool in tools:
                    if tool.get("function", {}).get("name") == tool_name:
                        tool_metadata = tool.get("function", {}).get("_metadata", {})
                        break
                locked_params = tool_metadata.get("locked_params", {})

                from app.services.connector_tools import ConnectorToolConverter
                connector_tool_converter = ConnectorToolConverter()
                result = await connector_tool_converter.execute_connector_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    user_token=user_token,
                    locked_params=locked_params,
                )
                elapsed = time.time() - start_time
                logger.debug(f"Connector tool completed in {elapsed:.3f}s: {tool_name}")
            else:
                # Default: execute as function tool
                start_time = time.time()

                tool_found = False
                locked_params = {}
                overridable_params = {}

                for tool in tools:
                    if tool.get("function", {}).get("name") == tool_name:
                        tool_found = True
                        metadata = tool.get("function", {}).get("_metadata", {})
                        locked_params = metadata.get("locked_params", {})
                        overridable_params = metadata.get("overridable_params", {})
                        break

                if not tool_found:
                    logger.warning(
                        f"Security: Tool '{tool_name}' was not in approved tools list. "
                        f"Available tools: {[t.get('function', {}).get('name') for t in tools]}"
                    )
                    result = {
                        "error": "Unauthorized tool call",
                        "message": f"Tool '{tool_name}' was not in the approved tools list for this agent.",
                    }
                else:
                    enabled_function_list = []
                    if chat and chat.agent_id:
                        result_agent = await db.execute(
                            select(Agent).where(Agent.id == chat.agent_id)
                        )
                        chat_agent = result_agent.scalar_one_or_none()
                        if chat_agent:
                            enabled_function_list = chat_agent.enabled_functions or []

                    result = await function_converter.execute_function_tool(
                        db=db,
                        tool_name=tool_name,
                        arguments=arguments,
                        user_id=user_id,
                        user_token=user_token,
                        chat_id=str(chat_id),
                        locked_params=locked_params,
                        overridable_params=overridable_params,
                        enabled_functions=enabled_function_list,
                        tool_call_id=tool_call.get("id"),
                    )

                elapsed = time.time() - start_time
                logger.debug(f"Function execution completed in {elapsed:.3f}s: {tool_name}")

            result_content = json.dumps(result) if not isinstance(result, str) else result

    except Exception as e:
        logger.error(f"Tool execution failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        result_content = json.dumps({"error": str(e)})

    return (tool_call["id"], tool_name, result_content)
