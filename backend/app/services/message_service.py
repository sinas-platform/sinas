"""Message service for chat processing with tool calling."""
import json
import logging
from typing import List, Dict, Any, Optional, AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import jsonschema

from app.models import Chat, Message, Agent
from app.models.llm_provider import LLMProvider
from app.models.execution import Execution, ExecutionStatus
from app.models.function import Function
from app.models.pending_approval import PendingToolApproval
from app.providers import create_provider
from app.services.function_tools import FunctionToolConverter
from app.services.state_tools import StateTools
from app.services.mcp import mcp_client
from app.services.execution_engine import executor
from app.services.content_converter import ContentConverter
from app.services.template_renderer import render_template
from app.core.config import settings

logger = logging.getLogger(__name__)


class MessageService:
    """Service for processing chat messages with LLM and tool calling."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.function_converter = FunctionToolConverter()
        self.context_tools = StateTools()

    async def create_chat_with_agent(
        self,
        agent_id: str,
        user_id: str,
        input_data: Dict[str, Any],
        group_id: Optional[str] = None,
        name: Optional[str] = None
    ) -> Chat:
        """
        Create a chat with an agent using input validation and template rendering.

        Args:
            agent_id: Agent to use
            user_id: User ID
            input_data: Input data to validate and use for template rendering
            group_id: Optional group ID
            name: Optional chat name

        Returns:
            Created chat

        Raises:
            ValueError: If input validation fails
        """
        # Get agent
        result = await self.db.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise ValueError("Agent not found")

        # Validate input against agent's input_schema
        if agent.input_schema:
            try:
                jsonschema.validate(instance=input_data, schema=agent.input_schema)
            except jsonschema.ValidationError as e:
                raise ValueError(f"Input validation failed: {e.message}")

        # Create chat
        # Note: Chat no longer stores tool config - this is managed at agent level
        # Store agent input context in chat_metadata for function parameter templating
        chat = Chat(
            user_id=user_id,
            group_id=group_id,
            agent_id=agent_id,
            title=name or f"Chat with {agent.name}",
            chat_metadata={"agent_input": input_data} if input_data else None
        )
        self.db.add(chat)
        await self.db.commit()
        await self.db.refresh(chat)

        # Pre-populate with initial_messages if present
        if agent.initial_messages:
            for msg_data in agent.initial_messages:
                # Render message content with input_data if it's a string
                content = msg_data["content"]
                if isinstance(content, str) and input_data:
                    try:
                        content = render_template(content, input_data)
                    except Exception as e:
                        logger.error(f"Failed to render initial message template: {e}")

                message = Message(
                    chat_id=chat.id,
                    role=msg_data["role"],
                    content=content
                )
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
        enabled_functions: Optional[List[str]],
        disabled_functions: Optional[List[str]],
        enabled_mcp_tools: Optional[List[str]],
        disabled_mcp_tools: Optional[List[str]],
        inject_context: bool,
        state_namespaces: Optional[List[str]],
        context_limit: int,
        template_variables: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Prepare message context (shared logic for streaming and non-streaming).

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
            from sqlalchemy.orm import joinedload
            result = await self.db.execute(
                select(Agent)
                .options(joinedload(Agent.llm_provider))
                .where(Agent.id == chat.agent_id)
            )
            agent = result.scalar_one_or_none()

        # Determine final provider/model/temperature
        # Priority: message params > agent settings > database default

        # Get provider name (for create_provider call)
        provider_name = None
        if provider:
            provider_name = provider
        elif agent and agent.llm_provider_id:
            # Load provider relationship if needed
            if not agent.llm_provider:
                result = await self.db.execute(
                    select(LLMProvider).where(LLMProvider.id == agent.llm_provider_id)
                )
                agent.llm_provider = result.scalar_one_or_none()
            if agent.llm_provider:
                provider_name = agent.llm_provider.name

        # Get model: message param > agent model > provider default
        final_model = model or (agent.model if agent else None)
        if not final_model and agent and agent.llm_provider:
            final_model = agent.llm_provider.default_model

        final_temperature = temperature if temperature != 0.7 else (agent.temperature if agent else 0.7)
        final_max_tokens = agent.max_tokens if agent else None

        # Get provider type for content conversion (needed before building conversation history)
        provider_type = None
        provider_config = None
        if provider_name:
            result = await self.db.execute(
                select(LLMProvider).where(LLMProvider.name == provider_name)
            )
            provider_config = result.scalar_one_or_none()
            if provider_config:
                provider_type = provider_config.provider_type

        # If still no provider type, try to detect from model
        if not provider_type and final_model:
            if final_model.startswith("gpt-") or final_model.startswith("o1-"):
                provider_type = "openai"
            elif final_model.startswith("mistral-") or final_model.startswith("pixtral-"):
                provider_type = "mistral"
            else:
                provider_type = "ollama"  # Default fallback

        # If still no provider type, get default provider to determine type
        if not provider_type:
            result = await self.db.execute(
                select(LLMProvider).where(
                    LLMProvider.is_default == True,
                    LLMProvider.is_active == True
                )
            )
            provider_config = result.scalar_one_or_none()
            if provider_config:
                provider_type = provider_config.provider_type
                # Also set final_model if not set
                if not final_model:
                    final_model = provider_config.default_model

        # Save user message
        user_message = Message(
            chat_id=chat_id,
            role="user",
            content=content
        )
        self.db.add(user_message)
        await self.db.commit()
        await self.db.refresh(user_message)

        # Extract template variables from chat metadata if not provided
        final_template_variables = template_variables
        if final_template_variables is None and chat.chat_metadata:
            final_template_variables = chat.chat_metadata.get("agent_input")

        # Build conversation history (with content conversion)
        messages = await self._build_conversation_history(
            chat=chat,
            inject_context=inject_context,
            user_id=user_id,
            state_namespaces=state_namespaces,
            context_limit=context_limit,
            template_variables=final_template_variables,
            provider_type=provider_type
        )

        # Get available tools
        tools = await self._get_available_tools(
            user_id=user_id,
            chat=chat,
            message_enabled_functions=enabled_functions,
            message_disabled_functions=disabled_functions,
            message_enabled_mcp=enabled_mcp_tools,
            message_disabled_mcp=disabled_mcp_tools
        )

        # Create LLM provider
        llm_provider = await create_provider(provider_name, final_model, self.db)

        # If no model specified, use the provider's default model
        if not final_model:
            # Get the provider config that was used
            if provider_name:
                result = await self.db.execute(
                    select(LLMProvider).where(
                        LLMProvider.name == provider_name,
                        LLMProvider.is_active == True
                    )
                )
            else:
                result = await self.db.execute(
                    select(LLMProvider).where(
                        LLMProvider.is_default == True,
                        LLMProvider.is_active == True
                    )
                )
            provider_config = result.scalar_one_or_none()
            if provider_config:
                final_model = provider_config.default_model

        # Build response_format from agent's output_schema if present
        response_format = None
        if agent and agent.output_schema and agent.output_schema.get("properties"):
            # Ensure schema has additionalProperties: false for strict mode
            schema = dict(agent.output_schema)
            if "additionalProperties" not in schema:
                schema["additionalProperties"] = False

            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{agent.name.lower().replace(' ', '_')}_response",
                    "strict": True,
                    "schema": schema
                }
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
            "response_format": response_format
        }

    async def send_message(
        self,
        chat_id: str,
        user_id: str,
        user_token: str,
        content: str
    ) -> Message:
        """
        Send a message and get LLM response (non-streaming).

        All agent behavior (LLM, tools, context) is defined by the agent.
        """
        # Prepare message context
        prep = await self._prepare_message_context(
            chat_id=chat_id,
            user_id=user_id,
            content=content,
            provider=None,
            model=None,
            temperature=None,
            enabled_functions=None,
            disabled_functions=None,
            enabled_mcp_tools=None,
            disabled_mcp_tools=None,
            inject_context=True,
            state_namespaces=None,
            context_limit=5,
            template_variables=None
        )

        # Get response from LLM (non-streaming)
        start_time = datetime.now(timezone.utc)

        # Build kwargs for LLM provider
        llm_kwargs = {}
        if prep["response_format"]:
            llm_kwargs["response_format"] = prep["response_format"]

        # Strip _metadata from tools before sending to LLM
        clean_tools = self._strip_tool_metadata(prep["tools"])

        response = await prep["llm_provider"].complete(
            messages=prep["messages"],
            model=prep["final_model"],
            tools=clean_tools,
            temperature=prep["final_temperature"],
            max_tokens=prep["final_max_tokens"],
            **llm_kwargs
        )
        end_time = datetime.now(timezone.utc)

        # Log request
        await self._log_request(
            user_id=user_id,
            chat_id=str(chat_id),
            message_id=str(prep["user_message"].id),
            provider=prep["provider_name"],
            model=prep["final_model"],
            messages=prep["messages"],
            response=response,
            latency_ms=int((end_time - start_time).total_seconds() * 1000)
        )

        # Handle tool calls if present
        if response.get("tool_calls"):
            # Check if any functions require approval
            for tool_call in response["tool_calls"]:
                tool_name = tool_call["function"]["name"]
                namespace, name = self._parse_function_name(tool_name)
                if namespace and name:
                    function = await Function.get_by_name(self.db, namespace, name)
                    if function and function.requires_approval:
                        raise ValueError(
                            f"Function {namespace}/{name} requires user approval. "
                            "Please use streaming mode to handle approval flow."
                        )

            return await self._handle_tool_calls(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                messages=prep["messages"],
                tool_calls=response["tool_calls"],
                provider=prep["provider_name"],
                model=prep["final_model"],
                temperature=prep["final_temperature"],
                max_tokens=prep["final_max_tokens"],
                tools=prep["tools"]
            )

        # Save assistant message
        assistant_message = Message(
            chat_id=chat_id,
            role="assistant",
            content=response.get("content", "")
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
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message and stream LLM response.

        All agent behavior (LLM, tools, context) is defined by the agent.

        Yields:
            Dict chunks with response data
        """
        # Prepare message (reuse common logic)
        prep = await self._prepare_message_context(
            chat_id=chat_id,
            user_id=user_id,
            content=content,
            provider=None,
            model=None,
            temperature=None,
            enabled_functions=None,
            disabled_functions=None,
            enabled_mcp_tools=None,
            disabled_mcp_tools=None,
            inject_context=True,
            state_namespaces=None,
            context_limit=5,
            template_variables=None
        )

        # Stream response
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
        ):
            yield chunk

    def _strip_tool_metadata(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        """
        Remove _metadata from tools before sending to LLM provider.
        LLM providers don't accept extra fields in tool definitions.

        Args:
            tools: Tools list with optional _metadata fields

        Returns:
            Clean tools list without _metadata
        """
        if not tools:
            return tools

        clean_tools = []
        for tool in tools:
            clean_tool = tool.copy()
            if "function" in clean_tool and "_metadata" in clean_tool["function"]:
                clean_tool = {
                    **clean_tool,
                    "function": {k: v for k, v in clean_tool["function"].items() if k != "_metadata"}
                }
            clean_tools.append(clean_tool)
        return clean_tools

    async def _stream_response(
        self,
        llm_provider,
        messages: List[Dict[str, Any]],
        final_model: Optional[str],
        tools: Optional[List[Dict[str, Any]]],
        final_temperature: float,
        max_tokens: Optional[int],
        chat_id: str,
        user_id: str,
        user_token: str,
        provider_name: Optional[str],
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Stream LLM response.

        Yields:
            Dict chunks with response data
        """
        # Stream response
        full_content = ""
        tool_calls_list = []  # Accumulate tool calls by index (OpenAI sends by index)

        # Strip _metadata from tools before sending to LLM (keep original tools for later lookup)
        clean_tools = self._strip_tool_metadata(tools)

        async for chunk in llm_provider.stream(
            messages=messages,
            model=final_model,
            tools=clean_tools,
            temperature=final_temperature,
            max_tokens=max_tokens
        ):
            if chunk.get("content"):
                full_content += chunk["content"]

            # Accumulate tool calls (streaming sends deltas with index)
            if chunk.get("tool_calls"):
                for tc in chunk["tool_calls"]:
                    # OpenAI sends tool calls with an index property in streaming
                    # First chunk has id/type/name, subsequent chunks have only arguments
                    tc_index = tc.get("index", 0)

                    # Extend list if needed
                    while len(tool_calls_list) <= tc_index:
                        tool_calls_list.append({
                            "id": None,
                            "type": "function",
                            "function": {
                                "name": "",
                                "arguments": ""
                            }
                        })

                    # Update ID, type, name if provided (first chunk)
                    if tc.get("id"):
                        tool_calls_list[tc_index]["id"] = tc["id"]
                    if tc.get("type"):
                        tool_calls_list[tc_index]["type"] = tc["type"]
                    if tc.get("function", {}).get("name"):
                        tool_calls_list[tc_index]["function"]["name"] = tc["function"]["name"]

                    # Accumulate arguments (all chunks)
                    if tc.get("function", {}).get("arguments"):
                        tool_calls_list[tc_index]["function"]["arguments"] += tc["function"]["arguments"]

            yield chunk

        # Use accumulated tool calls
        tool_calls = tool_calls_list if tool_calls_list else []

        # Save assistant message after streaming completes
        assistant_message = Message(
            chat_id=chat_id,
            role="assistant",
            content=full_content if full_content else None,
            tool_calls=tool_calls if tool_calls else None
        )
        self.db.add(assistant_message)
        await self.db.commit()
        await self.db.refresh(assistant_message)

        # Handle tool calls if present
        if tool_calls:
            # Check if any functions require approval
            approval_needed = await self._check_approval_requirements(
                tool_calls=tool_calls,
                chat_id=chat_id,
                user_id=user_id,
                message_id=str(assistant_message.id),
                messages=messages,
                provider=provider_name,
                model=final_model,
                temperature=final_temperature,
                max_tokens=max_tokens,
                tools=tools  # Pass tools with metadata for later execution
            )

            if approval_needed:
                # Yield approval_required events
                for tool_call in tool_calls:
                    tool_name = tool_call["function"]["name"]
                    arguments_str = tool_call["function"]["arguments"]

                    # Parse namespace/name from tool_name
                    namespace, name = self._parse_function_name(tool_name)
                    if not namespace or not name:
                        continue

                    # Check if this specific function requires approval
                    function = await Function.get_by_name(self.db, namespace, name)
                    if function and function.requires_approval:
                        yield {
                            "type": "approval_required",
                            "tool_call_id": tool_call["id"],
                            "function_namespace": namespace,
                            "function_name": name,
                            "arguments": json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
                        }

                # PAUSE - don't execute tools yet, wait for approval
                return

            # No approval needed - execute tools immediately
            await self._handle_tool_calls(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                messages=messages,
                tool_calls=tool_calls,
                provider=provider_name,
                model=final_model,
                temperature=final_temperature,
                max_tokens=max_tokens,
                tools=tools
            )

    def _parse_function_name(self, tool_name: str) -> tuple[Optional[str], Optional[str]]:
        """
        Parse namespace and name from tool_name.

        Handles both namespace__name (LLM format) and namespace/name formats.

        Returns:
            Tuple of (namespace, name) or (None, None) if not a function
        """
        # Skip non-function tools
        if tool_name in ["save_context", "retrieve_context", "update_context", "delete_context",
                         "continue_execution"] or tool_name.startswith("call_agent_"):
            return None, None

        # Convert namespace__name to namespace/name if needed
        if "__" in tool_name and "/" not in tool_name:
            tool_name = tool_name.replace("__", "/", 1)

        # Parse namespace/name
        if "/" not in tool_name:
            return None, None

        namespace, name = tool_name.split("/", 1)
        return namespace, name

    async def _check_approval_requirements(
        self,
        tool_calls: List[Dict[str, Any]],
        chat_id: str,
        user_id: str,
        message_id: str,
        messages: List[Dict[str, Any]],
        provider: Optional[str],
        model: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """
        Check if any tool calls require user approval before execution.

        If approval is needed, creates PendingToolApproval records.

        Returns:
            True if any tool calls require approval, False otherwise
        """
        requires_approval = False

        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            arguments_str = tool_call["function"]["arguments"]

            # Parse namespace/name from tool_name
            namespace, name = self._parse_function_name(tool_name)
            if not namespace or not name:
                # Not a function tool, skip
                continue

            # Load function to check requires_approval flag
            function = await Function.get_by_name(self.db, namespace, name)
            if not function or not function.requires_approval:
                continue

            # This function requires approval
            requires_approval = True

            # Create PendingToolApproval record
            pending_approval = PendingToolApproval(
                chat_id=chat_id,
                message_id=message_id,
                user_id=user_id,
                tool_call_id=tool_call["id"],
                function_namespace=namespace,
                function_name=name,
                arguments=json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str,
                all_tool_calls=tool_calls,
                conversation_context={
                    "provider": provider,
                    "model": model,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": messages,
                    "tools": tools  # Store tools list with metadata for resuming execution
                }
            )
            self.db.add(pending_approval)

        if requires_approval:
            await self.db.commit()

        return requires_approval

    async def _build_conversation_history(
        self,
        chat: Chat,
        inject_context: bool = False,
        user_id: Optional[str] = None,
        state_namespaces: Optional[List[str]] = None,
        context_limit: int = 5,
        template_variables: Optional[Dict[str, Any]] = None,
        provider_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Build conversation history for LLM with optional context injection.

        Args:
            chat: Chat object
            inject_context: Whether to inject stored context
            user_id: User ID for context retrieval
            state_namespaces: Namespaces to filter context
            context_limit: Max context items to inject
            template_variables: Variables for Jinja2 template rendering in system_prompt

        Returns:
            List of message dicts for LLM
        """
        messages = []

        # Add system prompt from agent if exists
        system_content = ""
        if chat.agent_id:
            result = await self.db.execute(
                select(Agent).where(Agent.id == chat.agent_id)
            )
            agent = result.scalar_one_or_none()
            if agent and agent.system_prompt:
                # Render system prompt with Jinja2 if template_variables provided
                if template_variables:
                    try:
                        system_content = render_template(agent.system_prompt, template_variables)
                    except Exception as e:
                        logger.error(f"Failed to render system prompt template: {e}")
                        system_content = agent.system_prompt
                else:
                    system_content = agent.system_prompt

            # Add output schema instruction if agent has one
            if agent and agent.output_schema and agent.output_schema.get("properties"):
                schema_instruction = f"\n\nIMPORTANT: You must respond with valid JSON matching this exact schema:\n```json\n{json.dumps(agent.output_schema, indent=2)}\n```\nDo not include any text outside the JSON object."
                system_content += schema_instruction

        # Inject relevant context if enabled
        # No agent = no context injection
        if inject_context and user_id and chat.agent_id:
            # Determine which namespaces to use:
            # 1. Message-level state_namespaces (most specific)
            # 2. Agent-level state_namespaces
            final_namespaces = state_namespaces
            if final_namespaces is None:
                result = await self.db.execute(
                    select(Agent).where(Agent.id == chat.agent_id)
                )
                agent = result.scalar_one_or_none()
                if agent:
                    # Combine readonly and readwrite namespaces for context injection
                    final_namespaces = (agent.state_namespaces_readonly or []) + (agent.state_namespaces_readwrite or [])

            # Context access is opt-in: None or [] means no access
            if final_namespaces is None or len(final_namespaces) == 0:
                # No namespaces = no context injection
                pass
            else:
                relevant_contexts = await StateTools.get_relevant_contexts(
                    db=self.db,
                    user_id=user_id,
                    agent_id=str(chat.agent_id) if chat.agent_id else None,
                    group_id=str(chat.group_id) if chat.group_id else None,
                    namespaces=final_namespaces,
                    limit=context_limit
                )

                if relevant_contexts:
                    context_section = "\n\n## Stored Context\n"
                    context_section += "The following information has been saved about the user and should inform your responses:\n\n"

                    for ctx in relevant_contexts:
                        context_section += f"**{ctx.namespace}/{ctx.key}**"
                        if ctx.description:
                            context_section += f" - {ctx.description}"
                        context_section += "\n"
                        context_section += f"```json\n{json.dumps(ctx.value, indent=2)}\n```\n\n"

                    if system_content:
                        system_content += context_section
                    else:
                        system_content = context_section.strip()

        if system_content:
            messages.append({
                "role": "system",
                "content": system_content
            })

        # Add chat message history
        result = await self.db.execute(
            select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at)
        )
        chat_messages = result.scalars().all()

        for msg in chat_messages:
            message_dict = {"role": msg.role}

            # Convert content to provider-specific format if needed
            content = msg.content
            if content and provider_type:
                # Try to parse JSON content (might be multimodal)
                try:
                    parsed_content = json.loads(content)
                    # If it's a list, it might be multimodal content
                    if isinstance(parsed_content, list):
                        content = ContentConverter.convert_message_content(parsed_content, provider_type)
                except (json.JSONDecodeError, TypeError):
                    # Not JSON, treat as plain string (no conversion needed)
                    pass

            # Always include content, even if None (required for assistant messages with tool_calls)
            message_dict["content"] = content

            if msg.tool_calls:
                message_dict["tool_calls"] = msg.tool_calls

            if msg.tool_call_id:
                message_dict["tool_call_id"] = msg.tool_call_id

            if msg.name:
                message_dict["name"] = msg.name

            messages.append(message_dict)

        return messages

    async def _get_agent_tools(self, agent_ids: List[str]) -> List[Dict[str, Any]]:
        """Get tool definitions for enabled agents."""
        tools = []

        for agent_id in agent_ids:
            result = await self.db.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent = result.scalar_one_or_none()

            if not agent or not agent.is_active:
                continue

            # Build tool definition for this agent
            # Use clean name, store ID as hidden parameter
            tool_def = {
                "type": "function",
                "function": {
                    "name": f"call_agent_{agent.name.lower().replace(' ', '_').replace('-', '_')}",
                    "description": f"{agent.name}: {agent.description}" if agent.description else f"Call the {agent.name} agent"
                }
            }

            # Build parameters - always include agent_id as a hidden constant
            if agent.input_schema and agent.input_schema.get("properties"):
                # Merge input_schema with agent_id
                params = dict(agent.input_schema)
                if "properties" not in params:
                    params["properties"] = {}
                params["properties"]["_agent_id"] = {
                    "type": "string",
                    "description": "Internal agent identifier",
                    "const": str(agent.id)  # Force this specific value
                }
                tool_def["function"]["parameters"] = params
            else:
                # Default: simple prompt + hidden agent_id
                tool_def["function"]["parameters"] = {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The prompt or query to send to the agent"
                        },
                        "_agent_id": {
                            "type": "string",
                            "description": "Internal agent identifier",
                            "const": str(agent.id)
                        }
                    },
                    "required": ["prompt"]
                }

            tools.append(tool_def)

        return tools

    async def _get_available_tools(
        self,
        user_id: str,
        chat: Chat,
        message_enabled_functions: Optional[List[str]],
        message_disabled_functions: Optional[List[str]],
        message_enabled_mcp: Optional[List[str]],
        message_disabled_mcp: Optional[List[str]]
    ) -> List[Dict[str, Any]]:
        """Get all available tools (functions + MCP + context + agents + execution continuation)."""
        tools = []

        # No agent = no tools
        if not chat.agent_id:
            return tools

        # Get agent configuration
        agent = None
        result = await self.db.execute(
            select(Agent).where(Agent.id == chat.agent_id)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            return tools

        # Add state tools (based on agent's state namespace access)
        context_tool_defs = await StateTools.get_tool_definitions(
            db=self.db,
            user_id=user_id,
            agent_state_namespaces_readonly=agent.state_namespaces_readonly,
            agent_state_namespaces_readwrite=agent.state_namespaces_readwrite
        )
        tools.extend(context_tool_defs)

        # Ontology tools removed - extracted to sinas-ontology project

        # Add agent tools (other agents this agent can call)
        agent_enabled = agent.enabled_agents or []
        if agent_enabled:
            agent_tools = await self._get_agent_tools(agent_enabled)
            tools.extend(agent_tools)

        # Determine function configuration
        # Priority: message override > agent config
        # Note: Empty list [] means no functions, None means all functions from agent
        if message_enabled_functions is not None:
            function_enabled = message_enabled_functions
        else:
            function_enabled = agent.enabled_functions
        function_disabled = message_disabled_functions or []

        # Get agent input context for function parameter templating
        agent_input_context = {}
        if chat.chat_metadata and "agent_input" in chat.chat_metadata:
            agent_input_context = chat.chat_metadata["agent_input"]

        # Get function tools (only if list has items - opt-in)
        if function_enabled and len(function_enabled) > 0:
            function_tools = await self.function_converter.get_available_functions(
                db=self.db,
                user_id=user_id,
                enabled_functions=function_enabled,
                disabled_functions=function_disabled,
                function_parameters=agent.function_parameters,
                agent_input_context=agent_input_context
            )
            tools.extend(function_tools)

        # Determine MCP configuration
        # Priority: message override > agent config
        if message_enabled_mcp is not None:
            mcp_enabled = message_enabled_mcp
        else:
            mcp_enabled = agent.enabled_mcp_tools

        # Get MCP tools (only if list has items - opt-in)
        if mcp_enabled and len(mcp_enabled) > 0:
            mcp_tools = await mcp_client.get_available_tools(
                enabled_tools=mcp_enabled
            )
            tools.extend(mcp_tools)

        # Check for paused executions belonging to this chat
        result = await self.db.execute(
            select(Execution).where(
                Execution.chat_id == chat.id,
                Execution.status == ExecutionStatus.AWAITING_INPUT
            ).limit(10)
        )
        paused_executions = result.scalars().all()

        if paused_executions:
            # Add continue_execution tool with details about paused executions
            execution_list = "\n".join([
                f"- {ex.execution_id}: {ex.function_name} - {ex.input_prompt}"
                for ex in paused_executions
            ])

            tools.append({
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
                                "enum": [ex.execution_id for ex in paused_executions]
                            },
                            "input": {
                                "type": "object",
                                "description": "Input data to provide to the paused execution"
                            }
                        },
                        "required": ["execution_id", "input"]
                    }
                }
            })

        return tools

    async def _execute_agent_tool(
        self,
        chat: Chat,
        user_id: str,
        user_token: str,
        tool_name: str,
        arguments: Dict[str, Any],
        enabled_agent_ids: List[str]
    ) -> Dict[str, Any]:
        """Execute an agent tool call by creating a new chat and getting a response."""
        # Extract agent ID from arguments (passed as _agent_id parameter)
        agent_id_str = arguments.get("_agent_id")
        if not agent_id_str:
            return {"error": f"Missing _agent_id in agent tool call"}

        # Verify this agent ID is in enabled list
        if agent_id_str not in enabled_agent_ids:
            return {"error": f"Agent {agent_id_str} not enabled for this agent"}

        # Load agent
        result = await self.db.execute(
            select(Agent).where(Agent.id == agent_id_str)
        )
        agent = result.scalar_one_or_none()

        if not agent:
            return {"error": f"Agent not found: {agent_id_str}"}

        # Prepare input data for the agent
        # Filter out internal _agent_id parameter
        user_arguments = {k: v for k, v in arguments.items() if not k.startswith("_")}

        # If arguments contain just "prompt", send as message content
        # Otherwise, use as input_data for validation
        if "prompt" in user_arguments and len(user_arguments) == 1:
            # Simple prompt mode
            input_data = {}
            content = user_arguments["prompt"]
        else:
            # Structured input mode
            input_data = user_arguments
            content = json.dumps(user_arguments)

        # Create a new chat for this agent call
        try:
            sub_chat = await self.create_chat_with_agent(
                agent_id=str(agent.id),
                user_id=user_id,
                input_data=input_data,
                group_id=str(chat.group_id) if chat.group_id else None,
                name=f"Sub-chat: {agent.name}"
            )

            # Send message to the agent
            # Output schema enforcement happens automatically in send_message
            response_message = await self.send_message(
                chat_id=str(sub_chat.id),
                user_id=user_id,
                user_token=user_token,
                content=content,
                template_variables=input_data
            )

            # Return the agent's response
            return {
                "agent_name": agent.name,
                "response": response_message.content,
                "chat_id": str(sub_chat.id)
            }

        except Exception as e:
            logger.error(f"Failed to execute agent tool {tool_name}: {e}")
            return {"error": str(e)}

    async def _handle_tool_calls(
        self,
        chat_id: str,
        user_id: str,
        user_token: str,
        messages: List[Dict[str, Any]],
        tool_calls: List[Dict[str, Any]],
        provider: Optional[str],
        model: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
        tools: List[Dict[str, Any]],
        permissions: Optional[Dict[str, bool]] = None
    ) -> Message:
        """Execute tool calls and get final response."""
        # Get permissions if not provided
        if permissions is None:
            from app.core.auth import get_user_permissions
            permissions = await get_user_permissions(self.db, user_id)

        # Check if assistant message with these tool calls already exists (e.g., from approval flow)
        # Get the first tool call ID to check
        first_tool_call_id = tool_calls[0]["id"] if tool_calls else None
        existing_message = None

        if first_tool_call_id:
            result = await self.db.execute(
                select(Message).where(
                    Message.chat_id == chat_id,
                    Message.role == "assistant",
                    Message.tool_calls.isnot(None)
                ).order_by(Message.created_at.desc()).limit(10)
            )
            for msg in result.scalars().all():
                if msg.tool_calls and any(tc.get("id") == first_tool_call_id for tc in msg.tool_calls):
                    existing_message = msg
                    break

        # Only create assistant message if it doesn't already exist
        if not existing_message:
            assistant_message = Message(
                chat_id=chat_id,
                role="assistant",
                content=None,
                tool_calls=tool_calls
            )
            self.db.add(assistant_message)
            await self.db.commit()

        # Execute each tool call
        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            arguments_str = tool_call["function"]["arguments"]
            arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str

            # Execute tool (context, webhook, MCP, or execution continuation)
            try:
                # Get chat for context
                result_chat = await self.db.execute(
                    select(Chat).where(Chat.id == chat_id)
                )
                chat = result_chat.scalar_one_or_none()

                if tool_name in ["save_context", "retrieve_context", "update_context", "delete_context"]:
                    # Handle context tools
                    result = await StateTools.execute_tool(
                        db=self.db,
                        tool_name=tool_name,
                        arguments=arguments,
                        user_id=user_id,
                        chat_id=str(chat_id),
                        group_id=str(chat.group_id) if chat and chat.group_id else None,
                        agent_id=str(chat.agent_id) if chat and chat.agent_id else None
                    )
                # Ontology tools removed - extracted to sinas-ontology project
                elif tool_name == "continue_execution":
                    # Handle execution continuation
                    result = await executor.execute_function(
                        function_name="",  # Not needed for resume
                        input_data=arguments["input"],
                        execution_id=arguments["execution_id"],
                        trigger_type="",  # Not needed for resume
                        trigger_id="",  # Not needed for resume
                        user_id=user_id,
                        resume_data=arguments["input"]
                    )
                elif tool_name.startswith("call_agent_"):
                    # Handle agent tool calls - get enabled agents from chat's agent
                    enabled_agent_ids = []
                    if chat and chat.agent_id:
                        result_agent = await self.db.execute(
                            select(Agent).where(Agent.id == chat.agent_id)
                        )
                        chat_agent = result_agent.scalar_one_or_none()
                        if chat_agent:
                            enabled_agent_ids = chat_agent.enabled_agents or []

                    result = await self._execute_agent_tool(
                        chat=chat,
                        user_id=user_id,
                        user_token=user_token,
                        tool_name=tool_name,
                        arguments=arguments,
                        enabled_agent_ids=enabled_agent_ids
                    )
                elif tool_name in mcp_client.tools:
                    result = await mcp_client.execute_tool(tool_name, arguments)
                else:
                    # Default: execute as function tool
                    # Extract prefilled params from tool metadata (if available)
                    prefilled_params = {}
                    for tool in tools:
                        if tool.get("function", {}).get("name") == tool_name:
                            prefilled_params = tool.get("function", {}).get("_metadata", {}).get("prefilled_params", {})
                            break

                    result = await self.function_converter.execute_function_tool(
                        db=self.db,
                        tool_name=tool_name,
                        arguments=arguments,
                        user_id=user_id,
                        user_token=user_token,
                        chat_id=str(chat_id),
                        prefilled_params=prefilled_params
                    )

                result_content = json.dumps(result) if not isinstance(result, str) else result

            except Exception as e:
                import traceback
                logger.error(f"Tool execution failed: {e}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                result_content = json.dumps({"error": str(e)})

            # Save tool result message
            tool_message = Message(
                chat_id=chat_id,
                role="tool",
                content=result_content,
                tool_call_id=tool_call["id"],
                name=tool_name
            )
            self.db.add(tool_message)

        await self.db.commit()

        # Get final response from LLM with tool results
        # First, rebuild system prompt with template variables
        result_chat = await self.db.execute(
            select(Chat).where(Chat.id == chat_id)
        )
        chat = result_chat.scalar_one_or_none()

        updated_messages = []

        # Add system prompt from agent if exists
        if chat and chat.agent_id:
            result_agent = await self.db.execute(
                select(Agent).where(Agent.id == chat.agent_id)
            )
            agent = result_agent.scalar_one_or_none()
            if agent and agent.system_prompt:
                # Render system prompt with template variables from chat metadata
                system_content = agent.system_prompt
                if chat.chat_metadata and "agent_input" in chat.chat_metadata:
                    try:
                        system_content = render_template(
                            agent.system_prompt,
                            chat.chat_metadata["agent_input"]
                        )
                    except Exception as e:
                        logger.error(f"Failed to render system prompt template: {e}")

                # Add output schema instruction if agent has one
                if agent.output_schema and agent.output_schema.get("properties"):
                    schema_instruction = f"\n\nIMPORTANT: You must respond with valid JSON matching this exact schema:\n```json\n{json.dumps(agent.output_schema, indent=2)}\n```\nDo not include any text outside the JSON object."
                    system_content += schema_instruction

                updated_messages.append({
                    "role": "system",
                    "content": system_content
                })

        # Rebuild messages with tool results
        result = await self.db.execute(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
        )
        for msg in result.scalars().all():
            message_dict = {"role": msg.role}
            if msg.content:
                message_dict["content"] = msg.content
            if msg.tool_calls:
                message_dict["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                message_dict["tool_call_id"] = msg.tool_call_id
            if msg.name:
                message_dict["name"] = msg.name
            updated_messages.append(message_dict)

        llm_provider = await create_provider(provider, model, self.db)

        # Strip _metadata from tools before sending to LLM
        clean_tools = self._strip_tool_metadata(tools)

        final_response = await llm_provider.complete(
            messages=updated_messages,
            model=model,
            tools=clean_tools,
            temperature=temperature,
            max_tokens=max_tokens
        )

        # Check if the response has more tool calls (for multi-step tool usage)
        if final_response.get("tool_calls"):
            # Recursively handle the next round of tool calls
            return await self._handle_tool_calls(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                messages=updated_messages,
                tool_calls=final_response["tool_calls"],
                provider=provider,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools
            )

        # Save final assistant message
        final_message = Message(
            chat_id=chat_id,
            role="assistant",
            content=final_response.get("content", "")
        )
        self.db.add(final_message)
        await self.db.commit()
        await self.db.refresh(final_message)

        return final_message

    async def _log_request(
        self,
        user_id: str,
        chat_id: str,
        message_id: str,
        provider: str,
        model: str,
        messages: List[Dict[str, Any]],
        response: Dict[str, Any],
        latency_ms: int
    ):
        """
        Log LLM request for analytics.

        Note: This is now handled by the global RequestLoggerMiddleware which logs
        all requests to ClickHouse. LLM-specific metadata could be added to request.state
        if needed for more detailed LLM analytics.
        """
        # LLM request logging now handled by middleware
        # Keeping method for backward compatibility but it's a no-op
        pass
