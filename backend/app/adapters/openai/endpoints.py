"""OpenAI SDK-compatible adapter endpoints."""
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import AsyncSessionLocal, get_db
from app.core.permissions import check_permission
from app.models.agent import Agent
from app.models.chat import Chat
from app.models.llm_provider import LLMProvider
from app.models.user import User
from app.providers import create_provider
from app.services.message_service import MessageService

from .schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    DeltaContent,
    ModelListResponse,
    ModelObject,
    StreamChoice,
    UsageInfo,
)

logger = logging.getLogger(__name__)
router = APIRouter()


async def _resolve_model(
    model: Optional[str], db: AsyncSession
) -> tuple[Optional[Agent], Optional[LLMProvider], str]:
    """
    Resolve model string to either an agent or an LLM provider.

    Returns (agent, provider, resolved_model_name).
    - Agent mode: agent is set, provider is None
    - Passthrough mode: agent is None, provider is set
    - Default agent: falls back to is_default agent
    """
    if model and "/" in model:
        # Agent mode: namespace/name
        ns, name = model.split("/", 1)
        agent = await Agent.get_by_name(db, ns, name)
        if not agent:
            raise HTTPException(404, f"Agent '{model}' not found")
        return agent, None, model

    if model:
        # Passthrough mode: find provider that has this model
        result = await db.execute(
            select(LLMProvider).where(LLMProvider.is_active == True)
        )
        providers = result.scalars().all()

        # Check default_model first, then config["models"]
        for provider in providers:
            if provider.default_model == model:
                return None, provider, model
            if provider.config and model in (provider.config.get("models") or []):
                return None, provider, model

        raise HTTPException(404, f"No provider found for model '{model}'")

    # No model specified — use default agent
    result = await db.execute(
        select(Agent).where(Agent.is_default == True, Agent.is_active == True)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(400, "No model specified and no default agent configured")
    return agent, None, f"{agent.namespace}/{agent.name}"


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    x_chat_id: Optional[str] = Header(None, alias="X-Chat-ID"),
):
    """OpenAI-compatible chat completions endpoint."""
    user_id, permissions = current_user_data
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")

    agent, provider, model_name = await _resolve_model(request.model, db)

    if agent:
        return await _agent_mode(
            request=request,
            http_request=http_request,
            agent=agent,
            model_name=model_name,
            user_id=user_id,
            permissions=permissions,
            user_token=user_token,
            chat_id=x_chat_id,
            db=db,
        )
    else:
        return await _passthrough_mode(
            request=request,
            provider=provider,
            model_name=model_name,
            db=db,
        )


async def _agent_mode(
    request: ChatCompletionRequest,
    http_request: Request,
    agent: Agent,
    model_name: str,
    user_id: str,
    permissions: dict,
    user_token: str,
    chat_id: Optional[str],
    db: AsyncSession,
):
    """Handle agent mode: route through MessageService."""
    # Permission check
    perm = f"sinas.agents/{agent.namespace}/{agent.name}.chat:all"
    if not check_permission(permissions, perm):
        set_permission_used(http_request, perm, has_perm=False)
        raise HTTPException(403, f"Not authorized to use agent '{agent.namespace}/{agent.name}'")
    set_permission_used(http_request, perm)

    # Extract last user message
    last_user_msg = ""
    for msg in reversed(request.messages):
        if msg.role == "user":
            last_user_msg = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
            break

    if not last_user_msg:
        raise HTTPException(400, "No user message found in messages array")

    # Get or create chat
    if chat_id:
        chat = await db.get(Chat, chat_id)
        if not chat or str(chat.user_id) != user_id or str(chat.agent_id) != str(agent.id):
            raise HTTPException(404, "Chat not found")
    else:
        # Create new chat
        chat = Chat(
            user_id=user_id,
            agent_id=agent.id,
            agent_namespace=agent.namespace,
            agent_name=agent.name,
            title=f"OpenAI adapter: {agent.namespace}/{agent.name}",
        )
        db.add(chat)
        await db.commit()
        await db.refresh(chat)

    chat_id_str = str(chat.id)
    message_service = MessageService(db)

    if request.stream:
        return await _agent_stream(
            message_service, chat_id_str, user_id, user_token,
            last_user_msg, model_name, chat_id_str,
        )
    else:
        response_message = await message_service.send_message(
            chat_id=chat_id_str,
            user_id=user_id,
            user_token=user_token,
            content=last_user_msg,
        )
        content = response_message.content or ""
        resp = ChatCompletionResponse(
            model=model_name,
            choices=[Choice(message=ChoiceMessage(content=content))],
        )
        from fastapi.responses import JSONResponse

        return JSONResponse(
            content=resp.model_dump(),
            headers={"X-Chat-ID": chat_id_str},
        )


async def _agent_stream(
    message_service: MessageService,
    chat_id: str,
    user_id: str,
    user_token: str,
    content: str,
    model_name: str,
    response_chat_id: str,
):
    """Stream agent response in OpenAI SSE format."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    async def generate():
        import time

        created = int(time.time())

        # First chunk with role
        first_chunk = ChatCompletionChunk(
            id=chunk_id,
            created=created,
            model=model_name,
            choices=[StreamChoice(delta=DeltaContent(role="assistant"))],
        )
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        async for chunk in message_service.send_message_stream(
            chat_id=chat_id,
            user_id=user_id,
            user_token=user_token,
            content=content,
        ):
            text = chunk.get("content", "")
            if text:
                data_chunk = ChatCompletionChunk(
                    id=chunk_id,
                    created=created,
                    model=model_name,
                    choices=[StreamChoice(delta=DeltaContent(content=text))],
                )
                yield f"data: {data_chunk.model_dump_json()}\n\n"

        # Final chunk
        final_chunk = ChatCompletionChunk(
            id=chunk_id,
            created=created,
            model=model_name,
            choices=[StreamChoice(delta=DeltaContent(), finish_reason="stop")],
        )
        yield f"data: {final_chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"X-Chat-ID": response_chat_id},
    )


async def _passthrough_mode(
    request: ChatCompletionRequest,
    provider: LLMProvider,
    model_name: str,
    db: AsyncSession,
):
    """Handle passthrough mode: direct LLM provider call."""
    llm_provider = await create_provider(provider.name, model_name, db)

    # Convert messages to dict format
    messages = []
    for msg in request.messages:
        m = {"role": msg.role}
        if msg.content is not None:
            m["content"] = msg.content
        if msg.tool_calls:
            m["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            m["tool_call_id"] = msg.tool_call_id
        if msg.name:
            m["name"] = msg.name
        messages.append(m)

    kwargs = {}
    if request.temperature is not None:
        kwargs["temperature"] = request.temperature
    if request.max_tokens is not None:
        kwargs["max_tokens"] = request.max_tokens

    if request.stream:
        return await _passthrough_stream(
            llm_provider, messages, model_name, kwargs
        )
    else:
        response = await llm_provider.complete(
            messages=messages,
            model=model_name,
            **kwargs,
        )
        content = response.get("content", "")
        return ChatCompletionResponse(
            model=model_name,
            choices=[Choice(message=ChoiceMessage(content=content))],
        )


async def _passthrough_stream(
    llm_provider,
    messages: list,
    model_name: str,
    kwargs: dict,
):
    """Stream passthrough response in OpenAI SSE format."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    async def generate():
        import time

        created = int(time.time())

        first_chunk = ChatCompletionChunk(
            id=chunk_id,
            created=created,
            model=model_name,
            choices=[StreamChoice(delta=DeltaContent(role="assistant"))],
        )
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        async for chunk in llm_provider.stream(
            messages=messages,
            model=model_name,
            **kwargs,
        ):
            text = chunk.get("content", "")
            if text:
                data_chunk = ChatCompletionChunk(
                    id=chunk_id,
                    created=created,
                    model=model_name,
                    choices=[StreamChoice(delta=DeltaContent(content=text))],
                )
                yield f"data: {data_chunk.model_dump_json()}\n\n"

        final_chunk = ChatCompletionChunk(
            id=chunk_id,
            created=created,
            model=model_name,
            choices=[StreamChoice(delta=DeltaContent(), finish_reason="stop")],
        )
        yield f"data: {final_chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
    )


@router.get("/v1/models", response_model=ModelListResponse)
async def list_models(
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List available models (agents + LLM provider models)."""
    user_id, permissions = current_user_data
    models: list[ModelObject] = []

    # List agents as models (namespace/name format)
    result = await db.execute(select(Agent).where(Agent.is_active == True))
    agents = result.scalars().all()

    for agent in agents:
        perm = f"sinas.agents/{agent.namespace}/{agent.name}.chat:all"
        if check_permission(permissions, perm):
            models.append(ModelObject(
                id=f"{agent.namespace}/{agent.name}",
                owned_by="sinas-agent",
            ))

    # List LLM provider models
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.is_active == True)
    )
    providers = result.scalars().all()

    for provider in providers:
        # Add default model
        if provider.default_model:
            models.append(ModelObject(
                id=provider.default_model,
                owned_by=f"sinas-provider-{provider.name}",
            ))
        # Add additional models from config
        for model_name in (provider.config or {}).get("models", []):
            if model_name != provider.default_model:
                models.append(ModelObject(
                    id=model_name,
                    owned_by=f"sinas-provider-{provider.name}",
                ))

    return ModelListResponse(data=models)


@router.get("/v1/models/{model_id:path}", response_model=ModelObject)
async def get_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get a single model info."""
    user_id, permissions = current_user_data

    # Check if it's an agent
    if "/" in model_id:
        parts = model_id.split("/", 1)
        agent = await Agent.get_by_name(db, parts[0], parts[1])
        if agent:
            perm = f"sinas.agents/{agent.namespace}/{agent.name}.chat:all"
            if check_permission(permissions, perm):
                return ModelObject(
                    id=model_id,
                    owned_by="sinas-agent",
                )

    # Check LLM providers
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.is_active == True)
    )
    for provider in result.scalars().all():
        if provider.default_model == model_id:
            return ModelObject(
                id=model_id,
                owned_by=f"sinas-provider-{provider.name}",
            )
        if model_id in (provider.config or {}).get("models", []):
            return ModelObject(
                id=model_id,
                owned_by=f"sinas-provider-{provider.name}",
            )

    raise HTTPException(404, f"Model '{model_id}' not found")
