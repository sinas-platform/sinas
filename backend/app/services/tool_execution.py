"""Tool execution: run individual tool calls and manage approval flow."""
import json
import logging
import time
import traceback
import uuid
from typing import Any, Optional

from opentelemetry import trace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_user_permissions
from app.core.permissions import check_permission
from app.core.database import AsyncSessionLocal
from app.models import Agent, Chat, Message
from app.models.function import Function
from app.models.pending_approval import PendingToolApproval
from app.core.config import settings
from app.models.user import User
from app.services.code_execution import execute as execute_code
from app.services.collection_tools import CollectionToolConverter
from app.services.component_tools import ComponentToolConverter
from app.services.connector_tools import ConnectorToolConverter
from app.services.execution_engine import executor as fn_executor
from app.services.function_tools import FunctionToolConverter
from app.services.config_tools import execute_config_tool, is_config_tool
from app.services.db_introspection_tools import execute_db_introspection_tool, is_db_introspection_tool
from app.services.package_tools import execute_package_tool, is_package_tool
from app.services.query_tools import QueryToolConverter
from app.services.skill_tools import SkillToolConverter
from app.services.state_tools import StateTools
from app.services.queue_service import queue_service
from app.services.stream_relay import stream_relay
from app.services.template_renderer import render_template
from app.services.tool_result_store import get_tool_result, save_tool_result

logger = logging.getLogger(__name__)

# Per-tool overrides for the truncation budget, keyed by tool name. Empty by
# default; deployments or callers can register exceptions for tools whose
# results are known-large and structurally safe.
TOOL_RESULT_SIZE_OVERRIDES: dict[str, int] = {}

_TRUNCATION_SUFFIX = "\n\n[... result truncated]"


def _clean_text_cut(content: str, max_size: int) -> str:
    """Fallback: cut plain text at a whitespace boundary and mark it."""
    budget = max_size - len(_TRUNCATION_SUFFIX)
    cut = content[:budget]
    boundary = max(cut.rfind("\n"), cut.rfind(" "))
    if boundary > budget // 2:
        cut = cut[:boundary]
    return cut + _TRUNCATION_SUFFIX


def _largest_string_slot(container):
    """Find the (parent, key) of the largest string value, looking one level
    into nested dicts (a connector response's `body`). Returns (None, None)
    when there is no string worth clipping."""
    best_parent, best_key, best_len = None, None, 0
    stack = [container]
    seen_depth = {id(container): 0}
    while stack:
        node = stack.pop()
        depth = seen_depth[id(node)]
        for key, value in node.items():
            if isinstance(value, str) and len(value) > best_len:
                best_parent, best_key, best_len = node, key, len(value)
            elif isinstance(value, dict) and depth < 1:
                seen_depth[id(value)] = depth + 1
                stack.append(value)
    return best_parent, best_key


def truncate_tool_result(result_content: str, max_size: int) -> str:
    """Cap an oversized tool result without breaking its structure.

    A blind byte-slice leaves the model with unparseable JSON and no way to
    reach the remainder, so it retries the same call forever. Instead:
      - JSON arrays (bare, or the largest list inside a dict, e.g. a
        connector response's `body`) are trimmed to whole elements and a
        marker {"_truncated": true, "returned": n, "total": N} tells the
        model there is more to page through.
      - JSON objects dominated by one string (a document body) get that
        string clipped, with the same marker shape describing the clip.
      - Anything unparseable is cut at a whitespace boundary with a visible
        text suffix, as before.
    Always returns content of at most max_size characters.
    """
    if len(result_content) <= max_size:
        return result_content
    try:
        parsed = json.loads(result_content)
    except (ValueError, TypeError):
        return _clean_text_cut(result_content, max_size)

    def _fit_list(items: list, overhead_probe) -> str | None:
        """Keep a prefix of whole elements such that the rebuilt payload
        fits. Returns the serialized payload, or None if impossible."""
        total = len(items)
        kept = list(items)
        while True:
            marker = {"_truncated": True, "returned": len(kept), "total": total}
            candidate = overhead_probe(kept, marker)
            if len(candidate) <= max_size:
                return candidate
            if not kept:
                return None
            kept.pop()

    if isinstance(parsed, list):
        fitted = _fit_list(
            parsed, lambda kept, marker: json.dumps(kept + [marker])
        )
        if fitted is not None:
            return fitted
        return _clean_text_cut(result_content, max_size)

    if isinstance(parsed, dict):
        # Prefer trimming the largest list value (one level deep covers the
        # connector envelope's `body`).
        list_slots = [
            (parent, key)
            for parent in [parsed]
            + [v for v in parsed.values() if isinstance(v, dict)]
            for key, value in parent.items()
            if isinstance(value, list) and value
        ]
        if list_slots:
            parent, key = max(
                list_slots, key=lambda slot: len(json.dumps(slot[0][slot[1]]))
            )
            original = parent[key]

            def probe(kept, marker):
                parent[key] = kept
                parsed["_truncated"] = {**marker, "field": key}
                return json.dumps(parsed)

            fitted = _fit_list(original, probe)
            if fitted is not None:
                return fitted
            parent[key] = original
            parsed.pop("_truncated", None)

        s_parent, s_key = _largest_string_slot(parsed)
        if s_parent is not None:
            original_str = s_parent[s_key]
            total_len = len(original_str)
            # JSON escaping inflates the string ("\n" -> "\\n"), so converge
            # on the clip length instead of computing it once.
            clip = min(total_len, max_size)
            while clip > 0:
                s_parent[s_key] = original_str[:clip]
                parsed["_truncated"] = {
                    "_truncated": True,
                    "field": s_key,
                    "returned": clip,
                    "total": total_len,
                }
                candidate = json.dumps(parsed)
                if len(candidate) <= max_size:
                    return candidate
                clip -= max(len(candidate) - max_size, 32)
            s_parent[s_key] = original_str
            parsed.pop("_truncated", None)

    return _clean_text_cut(result_content, max_size)


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
    if tool_name in ("continue_execution", "execute_code"):
        return True
    # Package management tools mutate global state — always sequential
    if is_package_tool(tool_name):
        return True
    return False


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
    if tool_name.startswith("pipeline_"):
        return "pipeline:" + tool_name[len("pipeline_"):].replace("__", "/", 1)
    if tool_name.startswith("search_collection_"):
        return "collection:" + tool_name[len("search_collection_"):].replace("__", "/", 1)
    if tool_name.startswith("get_file_"):
        return "collection:" + tool_name[len("get_file_"):].replace("__", "/", 1)
    if tool_name.startswith("write_file_"):
        return "collection:" + tool_name[len("write_file_"):].replace("__", "/", 1)
    if tool_name.startswith("edit_file_"):
        return "collection:" + tool_name[len("edit_file_"):].replace("__", "/", 1)
    if tool_name.startswith("delete_file_"):
        return "collection:" + tool_name[len("delete_file_"):].replace("__", "/", 1)
    if tool_name.startswith("show_component_"):
        return "component:" + tool_name[len("show_component_"):].replace("__", "/", 1)
    if tool_name in ("save_state", "retrieve_state", "update_state", "delete_state", "list_state_keys"):
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
    if key.startswith("pipeline:"):
        ref = key[9:]
        return f"Running pipeline {ref.split('/')[-1].replace('_', ' ')}"
    if key.startswith("collection:"):
        if tool_name.startswith("write_file_"):
            return "Writing file"
        if tool_name.startswith("edit_file_"):
            return "Editing file"
        if tool_name.startswith("delete_file_"):
            return "Deleting file"
        if tool_name.startswith("get_file_"):
            return "Reading file"
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

    # Build tool name → metadata lookup from tools list
    tool_meta_map = {}
    if tools:
        for t in tools:
            fn = t.get("function", {})
            if fn.get("name") and fn.get("_metadata"):
                tool_meta_map[fn["name"]] = fn["_metadata"]

    for tool_call in tool_calls:
        tool_name = tool_call["function"]["name"]
        arguments_str = tool_call["function"]["arguments"]

        meta = tool_meta_map.get(tool_name, {})
        namespace: Optional[str] = None
        name: Optional[str] = None

        if meta.get("system_tool"):
            # Sinas built-in system tool (e.g. package management).
            # Approval requirement comes directly from the tool definition's
            # _metadata.requires_approval flag.
            if not meta.get("requires_approval"):
                continue
            namespace = "sinas"
            name = tool_name
        else:
            # Regular user-defined function tool
            namespace = meta.get("namespace")
            name = meta.get("name")
            if not namespace or not name:
                # Not a function tool (agents, collections, etc.), skip
                continue

            # Load function to check requires_approval flag
            function = await Function.get_by_name(db, namespace, name)
            if not function or not function.requires_approval:
                continue

        # This tool requires approval
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
                "tools": tools,
            },
        )
        db.add(pending_approval)

    if requires_approval:
        await db.commit()

    return requires_approval


async def prepare_agent_delegation(
    db: AsyncSession,
    user_id: str,
    agent_id_str: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Resolve a `call_agent_*` target and create (or load) its sub-chat.

    Everything `execute_agent_tool` does before enqueueing — shared with the
    suspend-on-delegate path in message_service (issue #90), which enqueues
    the child itself and ends the parent job instead of blocking.

    Returns {"error": ...} on failure, else
    {"agent": Agent, "sub_chat": Chat, "content": str, "depth": int}.
    """
    if not agent_id_str:
        return {"error": "Agent ID not provided"}

    result = await db.execute(select(Agent).where(Agent.id == agent_id_str))
    agent = result.scalar_one_or_none()

    if not agent:
        return {"error": f"Agent not found: {agent_id_str}"}

    # Bound delegation chains (issue #90) — reject before spending a sub-chat.
    from app.services.delegation import child_depth_or_error

    child_depth, depth_error = child_depth_or_error()
    if depth_error:
        return {"error": depth_error}

    # Check user has permission to use this sub-agent
    user_permissions = await get_user_permissions(db, user_id)
    agent_perm = f"sinas.agents/{agent.namespace}/{agent.name}.chat:all"
    if not check_permission(user_permissions, agent_perm):
        return {
            "error": "Permission denied",
            "message": f"You don't have permission to use agent '{agent.namespace}/{agent.name}'.",
        }

    # Prepare input data for the agent
    # Filter out internal _* parameters and extract prompt
    user_arguments = {k: v for k, v in arguments.items() if not k.startswith("_")}
    content = user_arguments.pop("prompt", "")
    input_data = user_arguments  # Remaining args become input variables

    # Resume existing chat or create a new one
    resume_chat_id = arguments.get("_chat_id")
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
        # Create sub-chat using our own db session (not the parent's)
        # to avoid cross-session issues with the parent MessageService.
        sub_chat = Chat(
            user_id=user_id,
            agent_id=str(agent.id),
            agent_namespace=agent.namespace,
            agent_name=agent.name,
            title=f"Sub-chat: {agent.name}",
            chat_metadata={"agent_input": input_data} if input_data else None,
        )
        db.add(sub_chat)
        await db.commit()
        await db.refresh(sub_chat)

    return {"agent": agent, "sub_chat": sub_chat, "content": content, "depth": child_depth}


async def execute_agent_tool(
    db: AsyncSession,
    chat: Chat,
    user_id: str,
    user_token: str,
    agent_id_str: str,
    arguments: dict[str, Any],
    create_chat_with_agent_fn,
) -> dict[str, Any]:
    """Execute an agent tool call by creating or resuming a chat."""
    from app.core.telemetry import get_tracer, otel_attr
    _agent_tracer = get_tracer()

    try:
        prep = await prepare_agent_delegation(db, user_id, agent_id_str, arguments)
        if "error" in prep:
            return prep
        agent = prep["agent"]
        sub_chat = prep["sub_chat"]
        content = prep["content"]
        child_depth = prep["depth"]

        # Route agent-to-agent calls through the queue so each sub-agent
        # runs in its own worker — enables agent swarms without recursive blocking.
        channel_id = str(uuid.uuid4())
        _delegate_span = _agent_tracer.start_span("agent.delegate", attributes={
            "agent.target": f"{agent.namespace}/{agent.name}",
            "agent.chat_id": str(sub_chat.id),
            otel_attr("span_type"): "tool",
            otel_attr("input"): content if isinstance(content, str) else json.dumps(content, default=str),
            otel_attr("thread_id"): str(chat.id),
            otel_attr("user_id"): user_id,
            otel_attr("labels"): json.dumps([f"agent:{agent.namespace}/{agent.name}"]),
        })

        await queue_service.enqueue_agent_message(
            chat_id=str(sub_chat.id),
            user_id=user_id,
            user_token=user_token,
            content=content,
            channel_id=channel_id,
            agent=f"{agent.namespace}/{agent.name}",
            depth=child_depth,
        )

        # Wait for the sub-agent to finish by reading the Redis stream.
        # Use a longer timeout than the default SUBSCRIBE_WAIT_TIMEOUT since
        # agent-to-agent calls can take minutes (the sub-agent may itself be
        # making tool calls, calling other agents, etc.). NOTE: this "block"
        # mode holds the parent's worker slot for the whole wait; see
        # settings.agent_delegate_mode and issue #90.
        final_content = ""
        got_terminal = False
        async for event in stream_relay.subscribe(
            channel_id, timeout=settings.agent_delegate_timeout
        ):
            if event.get("content"):
                final_content += event["content"]
            if event.get("type") in ("done", "error"):
                got_terminal = True
                if event.get("type") == "error":
                    return {
                        "agent_name": agent.name,
                        "error": event.get("error", "Sub-agent failed"),
                        "chat_id": str(sub_chat.id),
                    }
                break

        if not got_terminal:
            logger.error(
                f"Agent-to-agent stream timed out: {agent.namespace}/{agent.name} "
                f"(channel={channel_id}, chat={sub_chat.id})"
            )
            _delegate_span.set_status(trace.StatusCode.ERROR, "timeout")
            _delegate_span.end()
            return {
                "agent_name": agent.name,
                "error": "Sub-agent did not respond in time",
                "chat_id": str(sub_chat.id),
            }

        _delegate_span.set_attribute(otel_attr("output"), final_content or "")
        _delegate_span.end()
        return {
            "agent_name": agent.name,
            "response": final_content,
            "chat_id": str(sub_chat.id),
        }

    except Exception as e:
        # agent_id_str, not tool_name: tool_name is not in scope here, and the
        # NameError it raised replaced the real exception in the returned
        # error, hiding every delegation failure cause from the caller.
        logger.error(f"Failed to execute agent tool {agent_id_str}: {e}")
        logger.debug("Delegation failure traceback:\n%s", traceback.format_exc())
        try:
            _delegate_span.set_status(trace.StatusCode.ERROR, str(e)[:200])
            _delegate_span.end()
        except (NameError, Exception):
            pass
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
    print(f"🔧 execute_single_tool called: {tool_name}")
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

            # Look up tool metadata from the tools list (set during tool discovery)
            tool_metadata = {}
            tool_found_in_list = False
            for tool in tools:
                if tool.get("function", {}).get("name") == tool_name:
                    tool_metadata = tool.get("function", {}).get("_metadata", {})
                    tool_found_in_list = True
                    break

            # Metering (§2a leaf-only rule): branches that recurse into a leaf
            # chokepoint — code-exec, sub-agent, pipeline, query, function —
            # are counted there; every other branch counts as one TOOL op
            # after dispatch. This keeps "agent turn calling 3 functions" =
            # 1 agent + 3 function, never double-counted.
            counted_at_leaf = False

            # Built-in tools (no metadata needed)
            if tool_name in ("save_state", "retrieve_state", "update_state", "delete_state", "list_state_keys"):
                result = await StateTools.execute_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    user_id=user_id,
                    chat_id=str(chat_id),
                    agent_id=str(chat.agent_id) if chat and chat.agent_id else None,
                )
            elif tool_name == "retrieve_tool_result":
                from app.services.tool_result_store import list_chat_tool_call_ids

                requested_id = arguments.get("tool_call_id", "")
                stored = await get_tool_result(
                    db=db,
                    tool_call_id=requested_id,
                    user_id=user_id,
                    chat_id=chat_id,
                )
                if stored:
                    result = stored
                else:
                    # Help the agent self-correct instead of giving up. Most
                    # "not found" hits are hallucinated ids — surface the
                    # actual ids available so it picks a real one (or stops).
                    available = (
                        await list_chat_tool_call_ids(db, str(chat_id))
                        if chat_id else []
                    )
                    result = {
                        "error": (
                            f"No tool result found for tool_call_id "
                            f"'{requested_id}' in this chat. Do not invent "
                            "tool_call_ids — only use values you saw in a "
                            "prior compacted reference like "
                            "'[Result from X (tool_call_id: Y). Use "
                            "retrieve_tool_result(\"Y\")…]'. If you don't "
                            "see such a reference, the result is still inline "
                            "in the conversation; just re-read it from there."
                        ),
                        "requested_tool_call_id": requested_id,
                        "available_tool_call_ids": available,
                    }

            elif tool_name == "continue_execution":
                result = await fn_executor.resume_execution(
                    execution_id=arguments["execution_id"],
                    resume_value=arguments["input"],
                )
            elif tool_name == "execute_code":
                counted_at_leaf = True  # metered in code_execution.execute()
                start_time = time.time()
                result = await execute_code(
                    code=arguments.get("code", ""),
                    user_id=user_id,
                    chat_id=str(chat_id),
                )
                logger.debug(f"Code execution completed in {time.time() - start_time:.3f}s")

            elif is_package_tool(tool_name):
                start_time = time.time()
                # Load the calling agent's system_tools and the user's permissions
                agent_system_tools: list[str] = []
                if chat and chat.agent_id:
                    result_agent = await db.execute(
                        select(Agent).where(Agent.id == chat.agent_id)
                    )
                    chat_agent = result_agent.scalar_one_or_none()
                    if chat_agent:
                        agent_system_tools = chat_agent.system_tools or []

                user_permissions = await get_user_permissions(db, user_id)

                result = await execute_package_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    user_id=user_id,
                    permissions=user_permissions,
                    agent_system_tools=agent_system_tools,
                )
                logger.debug(f"Package tool completed in {time.time() - start_time:.3f}s: {tool_name}")

            elif is_config_tool(tool_name):
                start_time = time.time()
                agent_system_tools: list[str] = []
                if chat and chat.agent_id:
                    result_agent = await db.execute(
                        select(Agent).where(Agent.id == chat.agent_id)
                    )
                    chat_agent = result_agent.scalar_one_or_none()
                    if chat_agent:
                        agent_system_tools = chat_agent.system_tools or []

                result = await execute_config_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    agent_system_tools=agent_system_tools,
                )
                logger.debug(f"Config tool completed in {time.time() - start_time:.3f}s: {tool_name}")

            elif is_db_introspection_tool(tool_name):
                start_time = time.time()
                agent_system_tools: list = []
                if chat and chat.agent_id:
                    result_agent = await db.execute(
                        select(Agent).where(Agent.id == chat.agent_id)
                    )
                    chat_agent = result_agent.scalar_one_or_none()
                    if chat_agent:
                        agent_system_tools = chat_agent.system_tools or []

                result = await execute_db_introspection_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    agent_system_tools=agent_system_tools,
                )
                logger.debug(f"DB introspection tool completed in {time.time() - start_time:.3f}s: {tool_name}")

            # Metadata-driven tools — identity comes from _metadata, not tool name parsing
            elif tool_metadata.get("agent_id"):
                counted_at_leaf = True  # sub-agent metered in message_service
                result = await execute_agent_tool(
                    db=db,
                    chat=chat,
                    user_id=user_id,
                    user_token=user_token,
                    agent_id_str=tool_metadata["agent_id"],
                    arguments=arguments,
                    create_chat_with_agent_fn=create_chat_with_agent_fn,
                )
            elif tool_metadata.get("tool_type", "").startswith("collection_"):
                start_time = time.time()
                result = await collection_converter.execute_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    user_id=user_id,
                    metadata=tool_metadata,
                )
                logger.debug(f"Collection tool completed in {time.time() - start_time:.3f}s: {tool_name}")
            elif tool_metadata.get("type") == "pipeline":
                counted_at_leaf = True  # pipeline steps meter at their leaves
                start_time = time.time()
                from app.services.pipeline_tools import PipelineToolConverter

                enabled_pipeline_list = []
                if chat and chat.agent_id:
                    result_agent = await db.execute(
                        select(Agent).where(Agent.id == chat.agent_id)
                    )
                    chat_agent = result_agent.scalar_one_or_none()
                    if chat_agent:
                        enabled_pipeline_list = chat_agent.enabled_pipelines or []

                result = await PipelineToolConverter().execute_pipeline_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    user_id=user_id,
                    user_token=user_token,
                    enabled_pipelines=enabled_pipeline_list,
                )
                logger.debug(f"Pipeline tool completed in {time.time() - start_time:.3f}s: {tool_name}")
            elif tool_metadata.get("type") == "connector":
                start_time = time.time()
                connector_tool_converter = ConnectorToolConverter()
                result = await connector_tool_converter.execute_connector_tool(
                    db=db,
                    tool_name=tool_name,
                    arguments=arguments,
                    user_token=user_token,
                    locked_params=tool_metadata.get("locked_params", {}),
                    user_id=user_id,
                )
                logger.debug(f"Connector tool completed in {time.time() - start_time:.3f}s: {tool_name}")
            elif tool_name.startswith("show_component_"):
                start_time = time.time()
                result = await component_converter.handle_component_tool_call(
                    db=db, tool_name=tool_name, arguments=arguments, user_id=user_id
                )
                if result is None:
                    result = {"error": f"Component not found for tool: {tool_name}"}
                logger.debug(f"Component tool completed in {time.time() - start_time:.3f}s: {tool_name}")
            elif tool_name.startswith("get_skill_"):
                start_time = time.time()
                result = await skill_converter.handle_skill_tool_call(
                    db=db, tool_name=tool_name, arguments=arguments
                )
                if result is None:
                    result = {"error": f"Skill not found for tool: {tool_name}"}
                logger.debug(f"Skill retrieval completed in {time.time() - start_time:.3f}s: {tool_name}")
            elif tool_name.startswith("query_"):
                counted_at_leaf = True  # metered in DatabasePoolManager.execute_query
                start_time = time.time()
                # Get enabled queries list from agent
                enabled_query_list = []
                if chat and chat.agent_id:
                    result_agent = await db.execute(
                        select(Agent).where(Agent.id == chat.agent_id)
                    )
                    chat_agent = result_agent.scalar_one_or_none()
                    if chat_agent:
                        enabled_query_list = chat_agent.enabled_queries or []

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
                logger.debug(f"Query execution completed in {time.time() - start_time:.3f}s: {tool_name}")
            elif tool_found_in_list:
                # Function tool — metadata has namespace/name
                counted_at_leaf = True  # metered in execute_function
                start_time = time.time()
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
                    locked_params=tool_metadata.get("locked_params", {}),
                    overridable_params=tool_metadata.get("overridable_params", {}),
                    enabled_functions=enabled_function_list,
                    tool_call_id=tool_call.get("id"),
                )
                logger.debug(f"Function execution completed in {time.time() - start_time:.3f}s: {tool_name}")
            else:
                logger.warning(
                    f"Security: Tool '{tool_name}' was not in approved tools list. "
                    f"Available tools: {[t.get('function', {}).get('name') for t in tools]}"
                )
                result = {
                    "error": "Unauthorized tool call",
                    "message": f"Tool '{tool_name}' was not in the approved tools list for this agent.",
                }
                counted_at_leaf = True  # rejected — no work done, not billable

            if not counted_at_leaf:
                from app.services import metering

                await metering.record(metering.OperationKind.TOOL)

            result_content = json.dumps(result) if not isinstance(result, str) else result

            # Truncate oversized results (structure-aware) so a single tool
            # call can't crowd the LLM context, without emitting broken JSON.
            max_result_size = TOOL_RESULT_SIZE_OVERRIDES.get(
                tool_name, settings.tool_result_context_max_size
            )
            if len(result_content) > max_result_size:
                print(f"⚠️ Truncating tool result for {tool_name}: {len(result_content)} -> {max_result_size} bytes", flush=True)
                result_content = truncate_tool_result(result_content, max_result_size)

    except Exception as e:
        print(f"❌ Tool execution failed: {tool_name}: {e}", flush=True)
        logger.error(f"Tool execution failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        result_content = json.dumps({"error": str(e)})

    return (tool_call["id"], tool_name, result_content)
