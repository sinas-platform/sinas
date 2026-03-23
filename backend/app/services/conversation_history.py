"""Build conversation history for LLM with windowing and content conversion."""
import json
import logging
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Agent, Chat, Message
from app.services.content_converter import ContentConverter
from app.services.content_tokens import refresh_sinas_file_urls
from app.services.skill_tools import SkillToolConverter
from app.services.state_tools import StateTools
from app.services.template_renderer import render_template

logger = logging.getLogger(__name__)


async def build_conversation_history(
    db: AsyncSession,
    chat: Chat,
    skill_converter: SkillToolConverter,
    inject_context: bool = False,
    user_id: Optional[str] = None,
    context_limit: int = 5,
    template_variables: Optional[dict[str, Any]] = None,
    provider_type: Optional[str] = None,
    current_user_content: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Build conversation history for LLM with optional context injection.

    Args:
        db: Database session
        chat: Chat object
        skill_converter: SkillToolConverter for preloaded skills
        inject_context: Whether to inject stored context
        user_id: User ID for context retrieval
        context_limit: Max context items to inject
        template_variables: Variables for Jinja2 template rendering in system_prompt
        provider_type: LLM provider type for content conversion
        current_user_content: Original unstripped content for current turn

    Returns:
        List of message dicts for LLM
    """
    messages: list[dict[str, Any]] = []

    # Add system prompt from agent if exists
    system_content = ""
    if chat.agent_id:
        result = await db.execute(select(Agent).where(Agent.id == chat.agent_id))
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

        # Inject preloaded skills content into system prompt
        if agent and agent.enabled_skills:
            preloaded_content = await skill_converter.get_preloaded_skills_content(
                db=db, enabled_skills=agent.enabled_skills
            )
            if preloaded_content:
                system_content += f"\n\n# Preloaded Skills\n\n{preloaded_content}"

        # Add output schema instruction if agent has one
        if agent and agent.output_schema and agent.output_schema.get("properties"):
            schema_instruction = f"\n\nIMPORTANT: You must respond with valid JSON matching this exact schema:\n```json\n{json.dumps(agent.output_schema, indent=2)}\n```\nDo not include any text outside the JSON object."
            system_content += schema_instruction

    # Inject relevant context if enabled
    # No agent = no context injection
    if inject_context and user_id and chat.agent_id:
        # Determine which stores to use for context injection
        final_stores = None
        result = await db.execute(select(Agent).where(Agent.id == chat.agent_id))
        agent = result.scalar_one_or_none()
        if agent:
            final_stores = agent.enabled_stores or []

        # Context access is opt-in: None or [] means no access
        if not final_stores:
            # No stores = no context injection
            pass
        else:
            relevant_contexts = await StateTools.get_relevant_contexts(
                db=db,
                user_id=user_id,
                agent_id=str(chat.agent_id) if chat.agent_id else None,
                enabled_stores=final_stores,
                limit=context_limit,
            )

            if relevant_contexts:
                context_section = "\n\n## Stored Context\n"
                context_section += "The following information has been saved about the user and should inform your responses:\n\n"

                for ctx in relevant_contexts:
                    store_label = f"{ctx.store.namespace}/{ctx.store.name}" if ctx.store else "unknown"
                    context_section += f"**{store_label}/{ctx.key}**"
                    if ctx.description:
                        context_section += f" - {ctx.description}"
                    context_section += "\n"
                    context_section += f"```json\n{json.dumps(ctx.value, indent=2)}\n```\n\n"

                if system_content:
                    system_content += context_section
                else:
                    system_content = context_section.strip()

    if system_content:
        messages.append({"role": "system", "content": system_content})

    # Add chat message history (with windowing for long conversations)
    result = await db.execute(
        select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at)
    )
    all_messages = result.scalars().all()

    # Apply windowing if conversation exceeds max_history_messages
    if len(all_messages) > settings.max_history_messages:
        chat_messages = all_messages[-settings.max_history_messages:]

        # Ensure tool call/result pairs aren't split by the window boundary.
        # Walk backwards from the start of the window to include any orphaned
        # assistant messages that contain tool_calls referenced by tool results
        # at the start of the window, and vice versa.
        window_start_idx = len(all_messages) - settings.max_history_messages
        prepend = []

        # Collect tool_call_ids referenced by tool messages at the start of the window
        referenced_tc_ids = set()
        for msg in chat_messages:
            if msg.role == "tool" and msg.tool_call_id:
                referenced_tc_ids.add(msg.tool_call_id)
            elif msg.role != "tool":
                break  # Stop once we hit non-tool messages

        if referenced_tc_ids:
            # Scan messages before the window for assistant messages with matching tool_calls
            for msg in reversed(all_messages[:window_start_idx]):
                if msg.tool_calls:
                    msg_tc_ids = {tc.get("id") for tc in msg.tool_calls if tc.get("id")}
                    if msg_tc_ids & referenced_tc_ids:
                        prepend.insert(0, msg)
                        referenced_tc_ids -= msg_tc_ids
                if not referenced_tc_ids:
                    break

        chat_messages = prepend + list(chat_messages)
    else:
        chat_messages = all_messages

    # Repair orphaned tool calls: if an assistant message has tool_calls
    # but some don't have matching tool result messages, inject synthetic
    # error results so the LLM provider doesn't reject the history.
    chat_messages = list(chat_messages)
    existing_tool_result_ids = {
        msg.tool_call_id for msg in chat_messages
        if msg.role == "tool" and msg.tool_call_id
    }
    repairs: list[tuple[int, list[dict]]] = []  # (insert_after_idx, messages)
    for idx, msg in enumerate(chat_messages):
        if msg.tool_calls:
            missing = []
            for tc in msg.tool_calls:
                tc_id = tc.get("id")
                if tc_id and tc_id not in existing_tool_result_ids:
                    missing.append(SimpleNamespace(
                        role="tool",
                        content="[Error: function call was interrupted or timed out]",
                        tool_call_id=tc_id,
                        tool_calls=None,
                        name=tc.get("function", {}).get("name", "unknown"),
                    ))
            if missing:
                repairs.append((idx, missing))

    # Insert repairs in reverse order to preserve indices
    for insert_idx, repair_msgs in reversed(repairs):
        for i, rm in enumerate(repair_msgs):
            chat_messages.insert(insert_idx + 1 + i, rm)

    # Remove orphaned tool results: tool result messages whose tool_call_id
    # doesn't match any tool_call in the conversation. This prevents LLM
    # providers from rejecting the history with "unexpected tool call id".
    all_tool_call_ids = set()
    for msg in chat_messages:
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    all_tool_call_ids.add(tc_id)

    chat_messages = [
        msg for msg in chat_messages
        if not (msg.role == "tool" and msg.tool_call_id and msg.tool_call_id not in all_tool_call_ids)
    ]

    for idx, msg in enumerate(chat_messages):
        message_dict: dict[str, Any] = {"role": msg.role}

        # For the last user message, use original unstripped content so the
        # LLM sees full base64 data for the current turn (DB has the stripped version).
        is_last_user = (
            current_user_content is not None
            and idx == len(chat_messages) - 1
            and msg.role == "user"
        )
        content = current_user_content if is_last_user else msg.content
        if content and provider_type:
            # Try to parse JSON content (might be multimodal)
            try:
                parsed_content = json.loads(content)
                # If it's a list, it might be multimodal content
                if isinstance(parsed_content, list):
                    parsed_content = refresh_sinas_file_urls(parsed_content)
                    content = ContentConverter.convert_message_content(
                        parsed_content, provider_type
                    )
            except (json.JSONDecodeError, TypeError):
                # Not JSON, treat as plain string (no conversion needed)
                pass

        # Always include content, even if None (required for assistant messages with tool_calls)
        message_dict["content"] = content

        if msg.tool_calls:
            # Strip UI-only fields (e.g. description) before sending to LLM
            message_dict["tool_calls"] = [
                {k: v for k, v in tc.items() if k != "description"}
                for tc in msg.tool_calls
            ]

        if msg.tool_call_id:
            message_dict["tool_call_id"] = msg.tool_call_id

        if msg.name:
            message_dict["name"] = msg.name

        messages.append(message_dict)

    return messages
