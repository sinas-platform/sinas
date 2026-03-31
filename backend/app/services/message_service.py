"""Message service for chat processing with tool calling."""
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Optional

import jsonschema
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.auth import get_user_permissions
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import Agent, Chat, Message
from app.models.function import Function
from app.models.llm_provider import LLMProvider
from app.providers import create_provider
from app.services.collection_tools import CollectionToolConverter
from app.services.component_tools import ComponentToolConverter
from app.services.connector_tools import ConnectorToolConverter
from app.utils.schema import validate_with_coercion
from app.services.content_tokens import strip_base64_data, refresh_message_tokens  # noqa: F401 — re-exported
from app.services.hook_service import run_hooks, HookResult
from app.services.tool_result_store import save_tool_result
from app.services.conversation_history import build_conversation_history
from app.services.function_tools import FunctionToolConverter
from app.services.query_tools import QueryToolConverter
from app.services.skill_tools import SkillToolConverter
from app.services.state_tools import StateTools
from app.services.template_renderer import render_template
from app.services.tool_discovery import (
    get_available_tools,
    parse_function_name,
    strip_tool_metadata,
)
from app.services.tool_execution import (
    build_tool_status,
    check_approval_requirements,
    execute_single_tool,
    is_sequential_tool,
    safe_parse_arguments,
    validate_tool_calls,
)

logger = logging.getLogger(__name__)


class MessageService:
    """Service for processing chat messages with LLM and tool calling."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.function_converter = FunctionToolConverter()
        self.query_converter = QueryToolConverter()
        self.skill_converter = SkillToolConverter()
        self.component_converter = ComponentToolConverter()
        self.collection_converter = CollectionToolConverter()
        self.connector_converter = ConnectorToolConverter()
        self.context_tools = StateTools()

    async def create_chat_with_agent(
        self, agent_id: str, user_id: str, input_data: dict[str, Any], name: Optional[str] = None
    ) -> Chat:
        """Create a chat with an agent using input validation and template rendering.

        Args:
            agent_id: Agent to use
            user_id: User ID
            input_data: Input data to validate and use for template rendering
            name: Optional chat name

        Returns:
            Created chat

        Raises:
            ValueError: If input validation fails
        """
        # Get agent
        result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            raise ValueError("Agent not found")

        # Validate input against agent's input_schema
        if agent.input_schema:
            try:
                input_data = validate_with_coercion(input_data, agent.input_schema)
            except jsonschema.ValidationError as e:
                raise ValueError(f"Input validation failed: {e.message}")

        # Create chat
        chat = Chat(
            user_id=user_id,
            agent_id=agent_id,
            agent_namespace=agent.namespace,
            agent_name=agent.name,
            title=name or f"Chat with {agent.name}",
            chat_metadata={"agent_input": input_data} if input_data else None,
        )
        self.db.add(chat)
        await self.db.commit()
        await self.db.refresh(chat)

        # Pre-populate with initial_messages if present
        if agent.initial_messages:
            for msg_data in agent.initial_messages:
                content = msg_data["content"]
                if isinstance(content, str) and input_data:
                    try:
                        content = render_template(content, input_data)
                    except Exception as e:
                        logger.error(f"Failed to render initial message template: {e}")

                message = Message(chat_id=chat.id, role=msg_data["role"], content=content)
                self.db.add(message)
            await self.db.commit()

        return chat

    async def _prepare_message_context(
        self,
        chat_id: str,
        user_id: str,
        content: str,
        provider: Optional[str],
        model: Optional[str],
        temperature: float,
        inject_context: bool,
        context_limit: int,
        template_variables: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        """Prepare message context (shared logic for streaming and non-streaming).

        Returns dict with: chat, user_message, messages, tools, llm_provider,
        provider_name, final_model, final_temperature, final_max_tokens, response_format
        """
        # Get chat
        result = await self.db.execute(
            select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
        )
        chat = result.scalar_one_or_none()
        if not chat:
            raise ValueError("Chat not found")

        # Get agent settings if chat has an agent
        agent = None
        if chat.agent_id:
            result = await self.db.execute(
                select(Agent)
                .options(joinedload(Agent.llm_provider))
                .where(Agent.id == chat.agent_id)
            )
            agent = result.scalar_one_or_none()

        # Determine final provider/model/temperature
        # Priority: message params > agent settings > database default
        provider_name = None
        if provider:
            provider_name = provider
        elif agent and agent.llm_provider_id:
            if not agent.llm_provider:
                result = await self.db.execute(
                    select(LLMProvider).where(
                        LLMProvider.id == agent.llm_provider_id,
                        LLMProvider.is_active == True
                    )
                )
                agent.llm_provider = result.scalar_one_or_none()

            if agent.llm_provider:
                provider_name = agent.llm_provider.name
            else:
                result = await self.db.execute(
                    select(LLMProvider).where(LLMProvider.id == agent.llm_provider_id)
                )
                inactive_provider = result.scalar_one_or_none()
                if inactive_provider:
                    raise ValueError(
                        f"Agent '{agent.namespace}/{agent.name}' is configured to use LLM provider "
                        f"'{inactive_provider.name}' which is currently inactive. Please activate the "
                        f"provider or update the agent's LLM provider setting."
                    )

        final_model = model or (agent.model if agent else None)
        if not final_model and agent and agent.llm_provider:
            final_model = agent.llm_provider.default_model

        final_temperature = (
            temperature if temperature != 0.7 else (agent.temperature if agent else 0.7)
        )
        final_max_tokens = agent.max_tokens if agent else None

        # Get provider type for content conversion
        provider_type = None
        provider_config = None
        if provider_name:
            result = await self.db.execute(
                select(LLMProvider).where(LLMProvider.name == provider_name)
            )
            provider_config = result.scalar_one_or_none()
            if provider_config:
                provider_type = provider_config.provider_type

        if not provider_type and final_model:
            if final_model.startswith("gpt-") or final_model.startswith("o1-"):
                provider_type = "openai"
            elif final_model.startswith("mistral-") or final_model.startswith("pixtral-"):
                provider_type = "mistral"
            else:
                provider_type = "ollama"

        if not provider_type:
            result = await self.db.execute(
                select(LLMProvider).where(
                    LLMProvider.is_default == True, LLMProvider.is_active == True
                )
            )
            provider_config = result.scalar_one_or_none()
            if provider_config:
                provider_type = provider_config.provider_type
                if not final_model:
                    final_model = provider_config.default_model

        # Save user message (strip inline base64 before persisting)
        user_message = Message(chat_id=chat_id, role="user", content=strip_base64_data(content))
        self.db.add(user_message)
        await self.db.commit()
        await self.db.refresh(user_message)

        # Run user message hooks (before LLM call)
        hook_result = None
        if agent and agent.hooks and agent.hooks.get("on_user_message"):
            hook_result = await run_hooks(
                hooks=agent.hooks["on_user_message"],
                message_content=content,
                message_role="user",
                chat_id=chat_id,
                agent_namespace=agent.namespace,
                agent_name=agent.name,
                user_id=user_id,
                session_key=getattr(chat, "session_key", None),
            )
            if hook_result.blocked:
                # Save the block reply as assistant message
                block_message = Message(
                    chat_id=chat_id, role="assistant",
                    content=hook_result.reply or "Message blocked by hook",
                )
                self.db.add(block_message)
                await self.db.commit()
                await self.db.refresh(block_message)
                return {"blocked": True, "block_message": block_message}

            if hook_result.mutated_content:
                content = hook_result.mutated_content
                # Update the saved user message with mutated content
                user_message.content = strip_base64_data(content)
                await self.db.commit()

        # Extract template variables from chat metadata if not provided
        final_template_variables = template_variables
        if final_template_variables is None and chat.chat_metadata:
            final_template_variables = chat.chat_metadata.get("agent_input")

        # Build conversation history
        messages = await build_conversation_history(
            db=self.db,
            chat=chat,
            skill_converter=self.skill_converter,
            inject_context=inject_context,
            user_id=user_id,
            context_limit=context_limit,
            template_variables=final_template_variables,
            provider_type=provider_type,
            current_user_content=content,
        )

        # Get available tools
        tools = await get_available_tools(
            db=self.db,
            user_id=user_id,
            chat=chat,
            function_converter=self.function_converter,
            query_converter=self.query_converter,
            skill_converter=self.skill_converter,
            component_converter=self.component_converter,
            collection_converter=self.collection_converter,
            connector_converter=self.connector_converter,
        )

        # Create LLM provider
        llm_provider = await create_provider(provider_name, final_model, self.db)

        # If no model specified, use the provider's default model
        if not final_model:
            if provider_name:
                result = await self.db.execute(
                    select(LLMProvider).where(
                        LLMProvider.name == provider_name, LLMProvider.is_active == True
                    )
                )
            else:
                result = await self.db.execute(
                    select(LLMProvider).where(
                        LLMProvider.is_default == True, LLMProvider.is_active == True
                    )
                )
            provider_config = result.scalar_one_or_none()
            if provider_config:
                final_model = provider_config.default_model

        # Build response_format from agent's output_schema if present
        response_format = None
        if agent and agent.output_schema and agent.output_schema.get("properties"):
            schema = dict(agent.output_schema)
            if "additionalProperties" not in schema:
                schema["additionalProperties"] = False

            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{agent.name.lower().replace(' ', '_')}_response",
                    "strict": True,
                    "schema": schema,
                },
            }

        return {
            "chat": chat,
            "user_message": user_message,
            "messages": messages,
            "tools": tools,
            "llm_provider": llm_provider,
            "provider_name": provider_name,
            "final_model": final_model,
            "final_temperature": final_temperature,
            "final_max_tokens": final_max_tokens,
            "response_format": response_format,
            "status_templates": agent.status_templates if agent else {},
        }

    async def send_message(
        self, chat_id: str, user_id: str, user_token: str, content: str
    ) -> Message:
        """Send a message and get LLM response (non-streaming)."""
        prep = await self._prepare_message_context(
            chat_id=chat_id,
            user_id=user_id,
            content=content,
            provider=None,
            model=None,
            temperature=None,
            inject_context=True,
            context_limit=5,
            template_variables=None,
        )

        # User hook blocked the pipeline
        if prep.get("blocked"):
            return prep["block_message"]

        start_time = datetime.now(UTC)

        llm_kwargs = {}
        if prep["response_format"]:
            llm_kwargs["response_format"] = prep["response_format"]

        clean_tools = strip_tool_metadata(prep["tools"])

        response = await prep["llm_provider"].complete(
            messages=prep["messages"],
            model=prep["final_model"],
            tools=clean_tools,
            temperature=prep["final_temperature"],
            max_tokens=prep["final_max_tokens"],
            **llm_kwargs,
        )
        end_time = datetime.now(UTC)

        await self._log_request(
            user_id=user_id,
            chat_id=str(chat_id),
            message_id=str(prep["user_message"].id),
            provider=prep["provider_name"],
            model=prep["final_model"],
            messages=prep["messages"],
            response=response,
            latency_ms=int((end_time - start_time).total_seconds() * 1000),
        )

        if response.get("tool_calls"):
            for tool_call in response["tool_calls"]:
                tool_name = tool_call["function"]["name"]
                namespace, name = parse_function_name(tool_name)
                if namespace and name:
                    function = await Function.get_by_name(self.db, namespace, name)
                    if function and function.requires_approval:
                        raise ValueError(
                            f"Function {namespace}/{name} requires user approval. "
                            "Please use streaming mode to handle approval flow."
                        )

            async for chunk in self._handle_tool_calls(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                messages=prep["messages"],
                tool_calls=response["tool_calls"],
                provider=prep["provider_name"],
                model=prep["final_model"],
                temperature=prep["final_temperature"],
                max_tokens=prep["final_max_tokens"],
                tools=prep["tools"],
            ):
                pass

            result = await self.db.execute(
                select(Message)
                .where(Message.chat_id == chat_id)
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            return result.scalar_one()

        assistant_content = response.get("content", "")

        # Run assistant message hooks
        agent = None
        if prep["chat"].agent_id:
            agent_result = await self.db.execute(
                select(Agent).where(Agent.id == prep["chat"].agent_id)
            )
            agent = agent_result.scalar_one_or_none()

        if agent and agent.hooks and agent.hooks.get("on_assistant_message"):
            hook_result = await run_hooks(
                hooks=agent.hooks["on_assistant_message"],
                message_content=assistant_content,
                message_role="assistant",
                chat_id=chat_id,
                agent_namespace=agent.namespace,
                agent_name=agent.name,
                user_id=user_id,
                session_key=getattr(prep["chat"], "session_key", None),
            )
            if hook_result.blocked:
                assistant_content = hook_result.reply or "Response blocked by hook"
            elif hook_result.mutated_content:
                assistant_content = hook_result.mutated_content

        assistant_message = Message(
            chat_id=chat_id, role="assistant", content=assistant_content
        )
        self.db.add(assistant_message)
        await self.db.commit()
        await self.db.refresh(assistant_message)

        return assistant_message

    async def send_message_stream(
        self,
        chat_id: str,
        user_id: str,
        user_token: str,
        content: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a message and stream LLM response."""
        prep = await self._prepare_message_context(
            chat_id=chat_id,
            user_id=user_id,
            content=content,
            provider=None,
            model=None,
            temperature=None,
            inject_context=True,
            context_limit=5,
            template_variables=None,
        )

        # User hook blocked the pipeline
        if prep.get("blocked"):
            yield {"type": "message", "content": prep["block_message"].content}
            yield {"type": "done", "status": "blocked"}
            return

        try:
            async for chunk in self._stream_response(
                llm_provider=prep["llm_provider"],
                messages=prep["messages"],
                final_model=prep["final_model"],
                tools=prep["tools"],
                final_temperature=prep["final_temperature"],
                max_tokens=prep["final_max_tokens"],
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                provider_name=prep["provider_name"],
                status_templates=prep["status_templates"],
            ):
                yield chunk
        except Exception as e:
            print(f"❌ Error during message streaming: {e}", flush=True)
            error_content = f"An error occurred while processing your message. Please try again.\n\nError: {str(e)[:300]}"
            # Save error as assistant message so chat history stays valid
            error_message = Message(chat_id=chat_id, role="assistant", content=error_content)
            self.db.add(error_message)
            await self.db.commit()
            yield {"content": error_content, "type": "error", "error": str(e)[:300]}

    async def _stream_response(
        self,
        llm_provider,
        messages: list[dict[str, Any]],
        final_model: Optional[str],
        tools: Optional[list[dict[str, Any]]],
        final_temperature: float,
        max_tokens: Optional[int],
        chat_id: str,
        user_id: str,
        user_token: str,
        provider_name: Optional[str],
        status_templates: dict[str, str] = {},
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream LLM response."""
        full_content = ""
        tool_calls_list = []

        clean_tools = strip_tool_metadata(tools)

        async for chunk in llm_provider.stream(
            messages=messages,
            model=final_model,
            tools=clean_tools,
            temperature=final_temperature,
            max_tokens=max_tokens,
        ):
            if chunk.get("content"):
                full_content += chunk["content"]

            if chunk.get("tool_calls"):
                for tc in chunk["tool_calls"]:
                    tc_index = tc.get("index")

                    if tc_index is None and tc.get("id"):
                        for idx, existing_tc in enumerate(tool_calls_list):
                            if existing_tc.get("id") == tc["id"]:
                                tc_index = idx
                                break
                        if tc_index is None:
                            tc_index = len(tool_calls_list)

                    if tc_index is None:
                        tc_index = 0

                    while len(tool_calls_list) <= tc_index:
                        tool_calls_list.append(
                            {
                                "id": None,
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        )

                    if tc.get("id"):
                        tool_calls_list[tc_index]["id"] = tc["id"]
                    if tc.get("type"):
                        tool_calls_list[tc_index]["type"] = tc["type"]
                    if tc.get("function", {}).get("name"):
                        tool_calls_list[tc_index]["function"]["name"] = tc["function"]["name"]
                    if tc.get("function", {}).get("arguments"):
                        tool_calls_list[tc_index]["function"]["arguments"] += tc["function"][
                            "arguments"
                        ]

            yield chunk

        tool_calls = tool_calls_list if tool_calls_list else []

        if tool_calls:
            tool_calls = validate_tool_calls(tool_calls)
            for tc in tool_calls:
                args = safe_parse_arguments(tc["function"].get("arguments", ""))
                tc["description"] = build_tool_status(tc["function"]["name"], args, status_templates)

        # Run assistant message hooks (post-stream, retroactive update)
        final_content = full_content
        if final_content and not tool_calls:
            chat = await self.db.get(Chat, chat_id)
            agent = None
            if chat and chat.agent_id:
                agent_result = await self.db.execute(
                    select(Agent).where(Agent.id == chat.agent_id)
                )
                agent = agent_result.scalar_one_or_none()

            if agent and agent.hooks and agent.hooks.get("on_assistant_message"):
                hook_result = await run_hooks(
                    hooks=agent.hooks["on_assistant_message"],
                    message_content=final_content,
                    message_role="assistant",
                    chat_id=chat_id,
                    agent_namespace=agent.namespace,
                    agent_name=agent.name,
                    user_id=user_id,
                    session_key=getattr(chat, "session_key", None),
                )
                if hook_result.blocked:
                    final_content = hook_result.reply or "Response blocked by hook"
                elif hook_result.mutated_content:
                    final_content = hook_result.mutated_content

        assistant_message = Message(
            chat_id=chat_id,
            role="assistant",
            content=final_content if final_content else None,
            tool_calls=tool_calls if tool_calls else None,
        )
        self.db.add(assistant_message)
        await self.db.commit()
        await self.db.refresh(assistant_message)

        if tool_calls:
            approval_needed = await check_approval_requirements(
                db=self.db,
                tool_calls=tool_calls,
                chat_id=chat_id,
                user_id=user_id,
                message_id=str(assistant_message.id),
                messages=messages,
                provider=provider_name,
                model=final_model,
                temperature=final_temperature,
                max_tokens=max_tokens,
                tools=tools,
            )

            if approval_needed:
                for tool_call in tool_calls:
                    tool_name = tool_call["function"]["name"]
                    arguments_str = tool_call["function"]["arguments"]

                    namespace, name = parse_function_name(tool_name)
                    if not namespace or not name:
                        continue

                    function = await Function.get_by_name(self.db, namespace, name)
                    if function and function.requires_approval:
                        parsed_args = arguments_str
                        if isinstance(arguments_str, str):
                            parsed_args = json.loads(arguments_str) if arguments_str.strip() else {}

                        yield {
                            "type": "approval_required",
                            "tool_call_id": tool_call["id"],
                            "function_namespace": namespace,
                            "function_name": name,
                            "arguments": parsed_args,
                        }

                return

            async for chunk in self._handle_tool_calls(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                messages=messages,
                tool_calls=tool_calls,
                provider=provider_name,
                model=final_model,
                temperature=final_temperature,
                max_tokens=max_tokens,
                tools=tools,
                status_templates=status_templates,
            ):
                yield chunk

    async def _handle_tool_calls(
        self,
        chat_id: str,
        user_id: str,
        user_token: str,
        messages: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        provider: Optional[str],
        model: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
        tools: list[dict[str, Any]],
        permissions: Optional[dict[str, bool]] = None,
        status_templates: dict[str, str] = {},
        depth: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute tool calls, stream LLM follow-up response, and save final message."""
        import sys
        print(f"🔧 _handle_tool_calls: {len(tool_calls)} calls: {[tc['function']['name'] for tc in tool_calls]}", flush=True, file=sys.stderr)
        if permissions is None:
            permissions = await get_user_permissions(self.db, user_id)

        # Check if assistant message with these tool calls already exists
        first_tool_call_id = tool_calls[0]["id"] if tool_calls else None
        existing_message = None

        if first_tool_call_id:
            result = await self.db.execute(
                select(Message)
                .where(
                    Message.chat_id == chat_id,
                    Message.role == "assistant",
                    Message.tool_calls.isnot(None),
                )
                .order_by(Message.created_at.desc())
                .limit(10)
            )
            for msg in result.scalars().all():
                if msg.tool_calls and any(
                    tc.get("id") == first_tool_call_id for tc in msg.tool_calls
                ):
                    existing_message = msg
                    break

        if not existing_message:
            for tc in tool_calls:
                if "description" not in tc:
                    args = safe_parse_arguments(tc["function"].get("arguments", ""))
                    tc["description"] = build_tool_status(tc["function"]["name"], args, status_templates)
            assistant_message = Message(
                chat_id=chat_id, role="assistant", content=None, tool_calls=tool_calls
            )
            self.db.add(assistant_message)
            await self.db.commit()

        # Separate tool calls into parallel and sequential groups
        valid_tool_calls = [tc for tc in tool_calls if tc.get("id")]
        parallel_calls = []
        sequential_calls = []

        for tc in valid_tool_calls:
            tool_name = tc["function"]["name"]
            if is_sequential_tool(tool_name):
                sequential_calls.append(tc)
            else:
                parallel_calls.append(tc)

        tool_results: dict[str, tuple[str, str, str]] = {}

        # Execute parallel tools concurrently
        if parallel_calls:
            for tc in parallel_calls:
                args = safe_parse_arguments(tc["function"].get("arguments", ""))
                yield {
                    "type": "tool_start",
                    "tool_call_id": tc["id"],
                    "name": tc["function"]["name"],
                    "arguments": tc["function"].get("arguments", "{}"),
                    "description": build_tool_status(tc["function"]["name"], args, status_templates),
                }

            # Execute parallel tools and stream results as they complete
            tool_timeout = settings.function_timeout
            pending_tasks = {}
            for tc in parallel_calls:
                task = asyncio.create_task(execute_single_tool(
                    tc, chat_id, user_id, user_token, tools,
                    self.function_converter, self.query_converter,
                    self.skill_converter, self.component_converter,
                    self.collection_converter, self.create_chat_with_agent,
                ))
                pending_tasks[task] = tc

            deadline = asyncio.get_event_loop().time() + tool_timeout
            while pending_tasks:
                remaining = max(0.1, deadline - asyncio.get_event_loop().time())
                done, _ = await asyncio.wait(
                    pending_tasks.keys(),
                    timeout=min(remaining, 5.0),  # Check every 5s for partial results
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in done:
                    tc = pending_tasks.pop(task)
                    try:
                        res = task.result()
                        tool_results[tc["id"]] = res
                        # Save immediately before yielding
                        self.db.add(Message(chat_id=chat_id, role="tool", content=res[2], tool_call_id=res[0], name=res[1]))
                        await self.db.commit()
                        yield {"type": "tool_end", "tool_call_id": tc["id"], "name": tc["function"]["name"], "result": res[2]}
                    except Exception as e:
                        print(f"❌ Tool {tc['function']['name']} failed: {e}", flush=True)
                        error_content = json.dumps({"error": str(e)})
                        tool_results[tc["id"]] = (tc["id"], tc["function"]["name"], error_content)
                        self.db.add(Message(chat_id=chat_id, role="tool", content=error_content, tool_call_id=tc["id"], name=tc["function"]["name"]))
                        await self.db.commit()
                        yield {"type": "tool_end", "tool_call_id": tc["id"], "name": tc["function"]["name"], "result": error_content}

                # Check if we've exceeded the deadline
                if asyncio.get_event_loop().time() >= deadline and pending_tasks:
                    for task, tc in pending_tasks.items():
                        task.cancel()
                        print(f"❌ Tool {tc['function']['name']} timed out", flush=True)
                        error_content = json.dumps({"error": f"Tool timed out after {tool_timeout}s"})
                        tool_results[tc["id"]] = (tc["id"], tc["function"]["name"], error_content)
                        self.db.add(Message(chat_id=chat_id, role="tool", content=error_content, tool_call_id=tc["id"], name=tc["function"]["name"]))
                    await self.db.commit()
                    for task, tc in list(pending_tasks.items()):
                        yield {"type": "tool_end", "tool_call_id": tc["id"], "name": tc["function"]["name"], "result": json.dumps({"error": f"Tool timed out after {tool_timeout}s"})}
                    break

        # Execute sequential tools one by one
        for tc in sequential_calls:
            args = safe_parse_arguments(tc["function"].get("arguments", ""))
            yield {
                "type": "tool_start",
                "tool_call_id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": tc["function"].get("arguments", "{}"),
                "description": build_tool_status(tc["function"]["name"], args, status_templates),
            }
            res = await execute_single_tool(
                tc, chat_id, user_id, user_token, tools,
                self.function_converter, self.query_converter,
                self.skill_converter, self.component_converter,
                self.collection_converter, self.create_chat_with_agent,
            )
            # Save immediately before yielding
            self.db.add(Message(chat_id=chat_id, role="tool", content=res[2], tool_call_id=res[0], name=res[1]))
            await self.db.commit()
            yield {"type": "tool_end", "tool_call_id": tc["id"], "name": tc["function"]["name"], "result": res[2]}
            tool_results[tc["id"]] = res

        # Rebuild messages with tool results for LLM follow-up
        # Persist tool results to tool_call_results store (background)
        async def _persist_results():
            try:
                async with AsyncSessionLocal() as store_db:
                    for tc_id, (res_id, res_name, res_content) in tool_results.items():
                        try:
                            result_parsed = json.loads(res_content) if isinstance(res_content, str) else res_content
                        except (json.JSONDecodeError, TypeError):
                            result_parsed = {"raw": res_content}
                        await save_tool_result(
                            db=store_db, tool_call_id=res_id, tool_name=res_name,
                            arguments={}, result=result_parsed, user_id=user_id,
                            chat_id=chat_id, source="agent",
                        )
            except Exception:
                pass
        asyncio.create_task(_persist_results())

        result_chat = await self.db.execute(select(Chat).where(Chat.id == chat_id))
        chat = result_chat.scalar_one_or_none()

        updated_messages = []

        if chat and chat.agent_id:
            result_agent = await self.db.execute(select(Agent).where(Agent.id == chat.agent_id))
            agent = result_agent.scalar_one_or_none()
            if agent and agent.system_prompt:
                system_content = agent.system_prompt
                if chat.chat_metadata and "agent_input" in chat.chat_metadata:
                    try:
                        system_content = render_template(
                            agent.system_prompt, chat.chat_metadata["agent_input"]
                        )
                    except Exception as e:
                        logger.error(f"Failed to render system prompt template: {e}")

                if agent.output_schema and agent.output_schema.get("properties"):
                    schema_instruction = f"\n\nIMPORTANT: You must respond with valid JSON matching this exact schema:\n```json\n{json.dumps(agent.output_schema, indent=2)}\n```\nDo not include any text outside the JSON object."
                    system_content += schema_instruction

                updated_messages.append({"role": "system", "content": system_content})

        result = await self.db.execute(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
        )
        for msg in result.scalars().all():
            message_dict = {"role": msg.role}
            if msg.content:
                message_dict["content"] = msg.content
            if msg.tool_calls:
                validated_tc = validate_tool_calls(msg.tool_calls)
                if validated_tc:
                    message_dict["tool_calls"] = [
                        {k: v for k, v in tc.items() if k != "description"}
                        for tc in validated_tc
                    ]
                elif msg.tool_calls:
                    print(f"\u26a0\ufe0f Skipping message {msg.id} with corrupted tool calls")
                    continue
            if msg.tool_call_id:
                message_dict["tool_call_id"] = msg.tool_call_id
            if msg.name:
                message_dict["name"] = msg.name
            updated_messages.append(message_dict)

        llm_provider = await create_provider(provider, model, self.db)

        clean_tools = strip_tool_metadata(tools)

        # Stream the response after tool execution
        full_content = ""
        tool_calls_list = []

        async for chunk in llm_provider.stream(
            messages=updated_messages,
            model=model,
            tools=clean_tools,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            if chunk.get("content"):
                full_content += chunk["content"]

            if chunk.get("tool_calls"):
                for tc in chunk["tool_calls"]:
                    tc_index = tc.get("index")

                    if tc_index is None and tc.get("id"):
                        for idx, existing_tc in enumerate(tool_calls_list):
                            if existing_tc.get("id") == tc["id"]:
                                tc_index = idx
                                break
                        if tc_index is None:
                            tc_index = len(tool_calls_list)

                    if tc_index is None:
                        tc_index = 0

                    while len(tool_calls_list) <= tc_index:
                        tool_calls_list.append(
                            {
                                "id": None,
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        )

                    if tc.get("id"):
                        tool_calls_list[tc_index]["id"] = tc["id"]
                    if tc.get("type"):
                        tool_calls_list[tc_index]["type"] = tc["type"]
                    if tc.get("function", {}).get("name"):
                        tool_calls_list[tc_index]["function"]["name"] = tc["function"]["name"]
                    if tc.get("function", {}).get("arguments"):
                        tool_calls_list[tc_index]["function"]["arguments"] += tc["function"][
                            "arguments"
                        ]

            yield chunk

        final_tool_calls = tool_calls_list if tool_calls_list else None

        if final_tool_calls:
            final_tool_calls = validate_tool_calls(final_tool_calls)
            for tc in final_tool_calls:
                args = safe_parse_arguments(tc["function"].get("arguments", ""))
                tc["description"] = build_tool_status(tc["function"]["name"], args, status_templates)

            # Persist intermediate assistant message (content + tool_calls) so
            # reasoning tokens visible during streaming survive the server refresh.
            intermediate_msg = Message(
                chat_id=chat_id,
                role="assistant",
                content=full_content if full_content else None,
                tool_calls=final_tool_calls,
            )
            self.db.add(intermediate_msg)
            await self.db.commit()

            if depth >= settings.max_tool_iterations:
                logger.warning(
                    "Tool iteration limit (%d) reached for chat %s — stopping",
                    settings.max_tool_iterations,
                    chat_id,
                )
                yield {
                    "type": "error",
                    "error": f"Tool iteration limit ({settings.max_tool_iterations}) reached. Stopping to prevent runaway loops.",
                }
                return

            async for result_chunk in self._handle_tool_calls(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                messages=updated_messages,
                tool_calls=final_tool_calls,
                provider=provider,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                status_templates=status_templates,
                depth=depth + 1,
            ):
                yield result_chunk
            return

        # Run assistant hooks on the final response (after all tool calls complete)
        final_content = full_content
        if final_content:
            chat = await self.db.get(Chat, chat_id)
            agent = None
            if chat and chat.agent_id:
                agent_result = await self.db.execute(
                    select(Agent).where(Agent.id == chat.agent_id)
                )
                agent = agent_result.scalar_one_or_none()

            if agent and agent.hooks and agent.hooks.get("on_assistant_message"):
                hook_result = await run_hooks(
                    hooks=agent.hooks["on_assistant_message"],
                    message_content=final_content,
                    message_role="assistant",
                    chat_id=chat_id,
                    agent_namespace=agent.namespace,
                    agent_name=agent.name,
                    user_id=user_id,
                    session_key=getattr(chat, "session_key", None),
                )
                if hook_result.blocked:
                    final_content = hook_result.reply or "Response blocked by hook"
                elif hook_result.mutated_content:
                    final_content = hook_result.mutated_content

        final_message = Message(
            chat_id=chat_id, role="assistant", content=final_content if final_content else None
        )
        self.db.add(final_message)
        await self.db.commit()
        await self.db.refresh(final_message)

    async def _log_request(
        self,
        user_id: str,
        chat_id: str,
        message_id: str,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        response: dict[str, Any],
        latency_ms: int,
    ):
        """Log LLM request for analytics (no-op, handled by middleware)."""
        pass
