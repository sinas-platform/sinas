"""Message service for chat processing with tool calling."""
import json
import logging
from typing import List, Dict, Any, Optional, AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chat, Message, Assistant
from app.models.execution import Execution, ExecutionStatus
from app.providers import create_provider
from app.services.webhook_tools import WebhookToolConverter
from app.services.context_tools import ContextTools
from app.services.ontology_tools import OntologyTools
from app.services.mcp import mcp_client
from app.services.execution_engine import executor
from app.core.config import settings

logger = logging.getLogger(__name__)


class MessageService:
    """Service for processing chat messages with LLM and tool calling."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.webhook_converter = WebhookToolConverter()
        self.context_tools = ContextTools()
        self.ontology_tools = OntologyTools()

    async def send_message(
        self,
        chat_id: str,
        user_id: str,
        user_token: str,
        content: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        enabled_webhooks: Optional[List[str]] = None,
        disabled_webhooks: Optional[List[str]] = None,
        enabled_mcp_tools: Optional[List[str]] = None,
        disabled_mcp_tools: Optional[List[str]] = None,
        inject_context: bool = True,
        context_namespaces: Optional[List[str]] = None,
        context_limit: int = 5,
    ) -> Message:
        """
        Send a message and get LLM response (non-streaming).

        Args:
            chat_id: Chat ID
            user_id: User ID
            user_token: User's JWT or API key
            content: Message content
            provider: LLM provider name
            model: Model name
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
            enabled_webhooks: Override enabled webhooks
            disabled_webhooks: Disabled webhooks
            enabled_mcp_tools: Override enabled MCP tools
            disabled_mcp_tools: Disabled MCP tools

        Returns:
            Assistant's response message
        """
        # Get chat
        result = await self.db.execute(
            select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
        )
        chat = result.scalar_one_or_none()
        if not chat:
            raise ValueError("Chat not found")

        # Save user message
        user_message = Message(
            chat_id=chat_id,
            role="user",
            content=content,
            enabled_webhooks=enabled_webhooks,
            enabled_mcp_tools=enabled_mcp_tools
        )
        self.db.add(user_message)
        await self.db.commit()
        await self.db.refresh(user_message)

        # Build conversation history
        messages = await self._build_conversation_history(
            chat=chat,
            inject_context=inject_context,
            user_id=user_id,
            context_namespaces=context_namespaces,
            context_limit=context_limit
        )

        # Get available tools
        tools = await self._get_available_tools(
            user_id=user_id,
            chat=chat,
            message_enabled_webhooks=enabled_webhooks,
            message_disabled_webhooks=disabled_webhooks,
            message_enabled_mcp=enabled_mcp_tools,
            message_disabled_mcp=disabled_mcp_tools
        )

        # Create LLM provider
        llm_provider = create_provider(provider, model)

        # Get response from LLM
        start_time = datetime.now(timezone.utc)
        response = await llm_provider.complete(
            messages=messages,
            model=model or settings.default_model,
            tools=tools if tools else None,
            temperature=temperature,
            max_tokens=max_tokens
        )
        end_time = datetime.now(timezone.utc)

        # Log request
        await self._log_request(
            user_id=user_id,
            chat_id=str(chat_id),
            message_id=str(user_message.id),
            provider=provider or "openai",
            model=model or settings.default_model,
            messages=messages,
            response=response,
            latency_ms=int((end_time - start_time).total_seconds() * 1000)
        )

        # Handle tool calls if present
        if response.get("tool_calls"):
            return await self._handle_tool_calls(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                messages=messages,
                tool_calls=response["tool_calls"],
                provider=provider,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools
            )

        # Save assistant message
        assistant_message = Message(
            chat_id=chat_id,
            role="assistant",
            content=response.get("content", ""),
            enabled_webhooks=enabled_webhooks,
            enabled_mcp_tools=enabled_mcp_tools
        )
        self.db.add(assistant_message)
        await self.db.commit()
        await self.db.refresh(assistant_message)

        return assistant_message

    async def stream_message(
        self,
        chat_id: str,
        user_id: str,
        user_token: str,
        content: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        enabled_webhooks: Optional[List[str]] = None,
        disabled_webhooks: Optional[List[str]] = None,
        enabled_mcp_tools: Optional[List[str]] = None,
        disabled_mcp_tools: Optional[List[str]] = None,
        inject_context: bool = True,
        context_namespaces: Optional[List[str]] = None,
        context_limit: int = 5,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message and stream LLM response.

        Yields:
            Dict chunks with response data
        """
        # Get chat
        result = await self.db.execute(
            select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
        )
        chat = result.scalar_one_or_none()
        if not chat:
            raise ValueError("Chat not found")

        # Save user message
        user_message = Message(
            chat_id=chat_id,
            role="user",
            content=content,
            enabled_webhooks=enabled_webhooks,
            enabled_mcp_tools=enabled_mcp_tools
        )
        self.db.add(user_message)
        await self.db.commit()
        await self.db.refresh(user_message)

        # Build conversation history
        messages = await self._build_conversation_history(
            chat=chat,
            inject_context=inject_context,
            user_id=user_id,
            context_namespaces=context_namespaces,
            context_limit=context_limit
        )

        # Get available tools
        tools = await self._get_available_tools(
            user_id=user_id,
            chat=chat,
            message_enabled_webhooks=enabled_webhooks,
            message_disabled_webhooks=disabled_webhooks,
            message_enabled_mcp=enabled_mcp_tools,
            message_disabled_mcp=disabled_mcp_tools
        )

        # Create LLM provider
        llm_provider = create_provider(provider, model)

        # Stream response
        full_content = ""
        tool_calls = []

        async for chunk in llm_provider.stream(
            messages=messages,
            model=model or settings.default_model,
            tools=tools if tools else None,
            temperature=temperature,
            max_tokens=max_tokens
        ):
            if chunk.get("content"):
                full_content += chunk["content"]

            if chunk.get("tool_calls"):
                tool_calls.extend(chunk["tool_calls"])

            yield chunk

        # Save assistant message after streaming completes
        assistant_message = Message(
            chat_id=chat_id,
            role="assistant",
            content=full_content if full_content else None,
            tool_calls=tool_calls if tool_calls else None,
            enabled_webhooks=enabled_webhooks,
            enabled_mcp_tools=enabled_mcp_tools
        )
        self.db.add(assistant_message)
        await self.db.commit()

        # Handle tool calls if present
        if tool_calls:
            # Execute tools and get final response (but don't yield it since streaming is done)
            await self._handle_tool_calls(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                messages=messages,
                tool_calls=tool_calls,
                provider=provider,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools
            )

    async def _build_conversation_history(
        self,
        chat: Chat,
        inject_context: bool = False,
        user_id: Optional[str] = None,
        context_namespaces: Optional[List[str]] = None,
        context_limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Build conversation history for LLM with optional context injection."""
        messages = []

        # Add system prompt from assistant if exists
        system_content = ""
        if chat.assistant_id:
            result = await self.db.execute(
                select(Assistant).where(Assistant.id == chat.assistant_id)
            )
            assistant = result.scalar_one_or_none()
            if assistant and assistant.system_prompt:
                system_content = assistant.system_prompt

        # Inject relevant context if enabled
        if inject_context and user_id:
            # Determine which namespaces to use:
            # 1. Message-level context_namespaces (most specific)
            # 2. Assistant-level context_namespaces (if assistant exists)
            # 3. None (all namespaces)
            final_namespaces = context_namespaces
            if final_namespaces is None and chat.assistant_id:
                result = await self.db.execute(
                    select(Assistant).where(Assistant.id == chat.assistant_id)
                )
                assistant = result.scalar_one_or_none()
                if assistant and assistant.context_namespaces is not None:
                    final_namespaces = assistant.context_namespaces

            relevant_contexts = await ContextTools.get_relevant_contexts(
                db=self.db,
                user_id=user_id,
                assistant_id=str(chat.assistant_id) if chat.assistant_id else None,
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

            # Always include content, even if None (required for assistant messages with tool_calls)
            message_dict["content"] = msg.content

            if msg.tool_calls:
                message_dict["tool_calls"] = msg.tool_calls

            if msg.tool_call_id:
                message_dict["tool_call_id"] = msg.tool_call_id

            if msg.name:
                message_dict["name"] = msg.name

            messages.append(message_dict)

        return messages

    async def _get_available_tools(
        self,
        user_id: str,
        chat: Chat,
        message_enabled_webhooks: Optional[List[str]],
        message_disabled_webhooks: Optional[List[str]],
        message_enabled_mcp: Optional[List[str]],
        message_disabled_mcp: Optional[List[str]]
    ) -> List[Dict[str, Any]]:
        """Get all available tools (webhooks + MCP + context + ontology + execution continuation)."""
        tools = []

        # Add context tools (always available)
        context_tool_defs = ContextTools.get_tool_definitions()
        tools.extend(context_tool_defs)

        # Add ontology tools (always available)
        ontology_tool_defs = OntologyTools.get_tool_definitions()
        tools.extend(ontology_tool_defs)

        # Determine webhook configuration
        webhook_enabled = message_enabled_webhooks or chat.enabled_webhooks or None
        webhook_disabled = message_disabled_webhooks or []

        # Get webhook tools
        webhook_tools = await self.webhook_converter.get_available_webhooks(
            db=self.db,
            user_id=user_id,
            enabled_webhooks=webhook_enabled,
            disabled_webhooks=webhook_disabled
        )
        tools.extend(webhook_tools)

        # Determine MCP configuration
        mcp_enabled = message_enabled_mcp or chat.enabled_mcp_tools or None

        # Get MCP tools
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
        tools: List[Dict[str, Any]]
    ) -> Message:
        """Execute tool calls and get final response."""
        # Save assistant message with tool calls
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
                    result = await ContextTools.execute_tool(
                        db=self.db,
                        tool_name=tool_name,
                        arguments=arguments,
                        user_id=user_id,
                        chat_id=str(chat_id),
                        group_id=str(chat.group_id) if chat and chat.group_id else None,
                        assistant_id=str(chat.assistant_id) if chat and chat.assistant_id else None
                    )
                elif tool_name in ["explore_ontology", "query_business_data", "create_ontology_data_record", "update_ontology_data_record"]:
                    # Handle ontology tools
                    result = await OntologyTools.execute_tool(
                        db=self.db,
                        tool_name=tool_name,
                        arguments=arguments,
                        user_id=user_id,
                        group_id=str(chat.group_id) if chat and chat.group_id else None,
                        assistant_id=str(chat.assistant_id) if chat and chat.assistant_id else None
                    )
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
                elif tool_name in mcp_client.tools:
                    result = await mcp_client.execute_tool(tool_name, arguments)
                else:
                    result = await self.webhook_converter.execute_webhook_tool(
                        db=self.db,
                        tool_name=tool_name,
                        arguments=arguments,
                        user_token=user_token,
                        chat_id=str(chat_id)
                    )

                result_content = json.dumps(result) if not isinstance(result, str) else result

            except Exception as e:
                logger.error(f"Tool execution failed: {e}")
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
        # Rebuild messages with tool results
        result = await self.db.execute(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
        )
        updated_messages = []
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

        llm_provider = create_provider(provider, model)
        final_response = await llm_provider.complete(
            messages=updated_messages,
            model=model or settings.default_model,
            tools=tools,
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
