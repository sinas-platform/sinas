"""OpenAI SDK-compatible adapter endpoints."""
import asyncio
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.permissions import check_permission
from app.core.streaming import logged_stream
from app.models.agent import Agent
from app.models.chat import Chat
from app.models.llm_provider import LLMProvider
from app.models.user import User
from app.providers import create_provider
from app.providers.errors import INTERNAL_ERROR, LLMError, error_code, openai_error_body
from app.services import sync_turns
from app.services.deferred_completions import ROUND_SUSPENDED
from app.services.message_service import MessageService
from app.services.stream_thinking import ThinkingFilter

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


def _sse_error(error: LLMError) -> str:
    """A failed call or turn on a stream, as OpenAI sends one: a `data:` line
    of `{"error": {...}}` (the error envelope in OpenAI's format), which the
    OpenAI SDKs raise as an APIError."""
    return f"data: {json.dumps(openai_error_body(error))}\n\n"


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
            user_id=user_id,
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
        from app.providers.attribution import new_chat_attribution

        chat = Chat(
            user_id=user_id,
            agent_id=agent.id,
            agent_namespace=agent.namespace,
            agent_name=agent.name,
            title=f"OpenAI adapter: {agent.namespace}/{agent.name}",
            attribution=await new_chat_attribution(db, user_id),
        )
        db.add(chat)
        await db.commit()
        await db.refresh(chat)

    chat_id_str = str(chat.id)
    message_service = MessageService(db)
    budget = float(agent.default_job_timeout or settings.function_timeout or 300)

    # A chat parked on ask_user questions: the OpenAI wire format has no way
    # to answer one but a message, so the message answers the oldest.
    question = None
    if chat_id:
        open_questions = await sync_turns.open_questions(db, chat_id_str)
        question = open_questions[0] if open_questions else None

    if request.stream:
        return await _agent_stream(
            message_service, chat_id_str, user_id, user_token,
            last_user_msg, model_name, chat_id_str, request.include_thinking,
            db=db, budget=budget, answering=question,
        )

    started = asyncio.get_running_loop().time()
    try:
        if question is not None:
            answered = await _answer(db, chat_id_str, question, last_user_msg, user_token)
            outcome = await sync_turns.settle_answer(db, chat_id_str, answered, budget=budget)
        else:
            turn = await message_service.run_turn(
                chat_id=chat_id_str,
                user_id=user_id,
                user_token=user_token,
                content=last_user_msg,
            )
            outcome = await sync_turns.settle_turn(
                db,
                chat_id_str,
                status=turn.status,
                message=turn.message,
                channel_id=turn.channel_id,
                usage_parts=[],
                budget=budget - (asyncio.get_running_loop().time() - started),
            )
    except sync_turns.TurnFailed as e:
        raise HTTPException(502, f"The agent's turn failed: {e}")

    resp = ChatCompletionResponse(
        model=model_name,
        choices=[Choice(message=ChoiceMessage(content=_render_outcome(outcome)))],
    )
    return JSONResponse(
        content=resp.model_dump(),
        headers={"X-Chat-ID": chat_id_str, "X-Sinas-Turn-Status": outcome.status},
    )


async def _answer(db, chat_id: str, question: dict, answer: str, user_token: str):
    try:
        return await sync_turns.answer_question(
            db, chat_id, question["tool_call_id"], answer, user_token=user_token
        )
    except (sync_turns.AnswerNotFound, sync_turns.AnswerConflict):
        raise HTTPException(409, "The question this message answers was resolved meanwhile; send it again")


def _render_questions(questions: list[dict]) -> str:
    """Open ask_user questions as assistant text: the only way an OpenAI
    client sees them. Its next message in the chat answers the first."""
    parts = []
    for q in questions:
        text = q.get("question") or ""
        if q.get("options"):
            text += "\nOptions: " + " / ".join(q["options"])
        parts.append(text)
    return "\n\n".join(parts)


_STILL_WORKING = (
    "[The agent is still working on this in the background; the result will be "
    "in this chat when it is done.]"
)
_DELIVERED = "[Your message was passed to the agent's ongoing turn in this chat.]"


def _render_outcome(outcome: "sync_turns.TurnOutcome") -> str:
    """A synchronous turn's outcome as one assistant message."""
    if outcome.status == "completed":
        return outcome.reply
    if outcome.status == "input_required":
        notice = _render_questions(outcome.pending_inputs)
    elif outcome.status == "injected":
        return _DELIVERED
    else:
        notice = _STILL_WORKING
    return f"{outcome.reply}\n\n{notice}" if outcome.reply else notice


async def _agent_stream(
    message_service: MessageService,
    chat_id: str,
    user_id: str,
    user_token: str,
    content: str,
    model_name: str,
    response_chat_id: str,
    include_thinking: bool = False,
    *,
    db: AsyncSession,
    budget: float,
    answering: Optional[dict] = None,
):
    """Stream agent response in OpenAI SSE format.

    A turn whose round parks on ask_user ends with the question(s) as text;
    one that parks on background work is followed on the round's channel
    within `budget`. `answering`: the open question this message answers
    (the chat was parked on it); the resumed turn is streamed instead."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    started = asyncio.get_running_loop().time()
    # Answered before the stream starts, so a refusal is a plain HTTP error.
    answered = (
        await _answer(db, chat_id, answering, content, user_token) if answering is not None else None
    )

    async def generate():
        import time

        created = int(time.time())
        show = ThinkingFilter(include_thinking)

        def sse(delta: DeltaContent, finish_reason: Optional[str] = None) -> str:
            data_chunk = ChatCompletionChunk(
                id=chunk_id,
                created=created,
                model=model_name,
                choices=[StreamChoice(delta=delta, finish_reason=finish_reason)],
            )
            return f"data: {data_chunk.model_dump_json()}\n\n"

        def deltas(chunk: dict):
            chunk = show(chunk)
            if chunk is None:
                return
            thinking = chunk.get("thinking")
            # "" counts: the model has started thinking, text or not.
            if thinking is not None:
                yield sse(DeltaContent(reasoning_content=thinking))
            text = chunk.get("content", "")
            if text and isinstance(text, str):
                yield sse(DeltaContent(content=text))

        # First chunk with role
        yield sse(DeltaContent(role="assistant"))

        questions: list[dict] = []
        follow: Optional[tuple[str, str]] = None  # (channel, from id)
        if answered is not None:
            if answered.resumed or answered.resume_pending or not await sync_turns.open_questions(db, chat_id):
                follow = (answered.channel_id, answered.follow_from)
            else:
                questions = await sync_turns.open_questions(db, chat_id)
        else:
            asked = False
            suspension = None
            async for chunk in message_service.send_message_stream(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                content=content,
            ):
                if not isinstance(chunk, dict):
                    continue
                kind = chunk.get("type")
                if kind == "error" and error_code(chunk):
                    # The turn failed (its reason is in the chat too): the
                    # stream ends on it, in OpenAI's error format.
                    yield _sse_error(LLMError.from_event(chunk))
                    yield "data: [DONE]\n\n"
                    return
                if kind == "input_required":
                    asked = True
                elif kind == ROUND_SUSPENDED:
                    suspension = chunk
                elif kind == "injected":
                    yield sse(DeltaContent(content=_DELIVERED))
                    continue
                for line in deltas(chunk):
                    yield line
            if suspension is not None:
                if asked:
                    questions = await sync_turns.open_questions(db, chat_id)
                elif suspension.get("channel_id"):
                    follow = (suspension["channel_id"], "0")

        if follow is not None and follow[0]:
            remaining = budget - (asyncio.get_running_loop().time() - started)
            follower = sync_turns.RoundFollower(follow[0], timeout=remaining, last_id=follow[1])
            async for event in follower.events():
                for line in deltas(event):
                    yield line
            if follower.status == "input_required":
                questions = await sync_turns.open_questions(db, chat_id)
            elif follower.status == "failed":
                if error_code(follower.error_event or {}):
                    yield _sse_error(LLMError.from_event(follower.error_event))
                    yield "data: [DONE]\n\n"
                    return
                yield sse(DeltaContent(content=f"\n\n[The agent's turn failed: {follower.error}]"))
            elif follower.status != "completed":
                yield sse(DeltaContent(content=f"\n\n{_STILL_WORKING}"))
        if questions:
            yield sse(DeltaContent(content="\n\n" + _render_questions(questions)))

        # Final chunk
        yield sse(DeltaContent(), finish_reason="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        logged_stream(generate(), "OpenAI adapter stream"),
        media_type="text/event-stream",
        headers={"X-Chat-ID": response_chat_id},
    )


async def _passthrough_mode(
    request: ChatCompletionRequest,
    provider: LLMProvider,
    model_name: str,
    user_id: str,
    db: AsyncSession,
):
    """Handle passthrough mode: direct LLM provider call."""
    llm_provider = await create_provider(
        provider.name,
        model_name,
        db,
        usage_context={"user_id": user_id, "source": "openai_adapter_passthrough"},
    )

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
            llm_provider, messages, model_name, kwargs, request.include_thinking
        )
    else:
        response = await llm_provider.complete(
            messages=messages,
            model=model_name,
            **kwargs,
        )
        content = response.get("content", "")
        usage = response.get("usage") or {}
        return ChatCompletionResponse(
            model=model_name,
            choices=[Choice(message=ChoiceMessage(content=content))],
            usage=UsageInfo(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
        )


async def _passthrough_stream(
    llm_provider,
    messages: list,
    model_name: str,
    kwargs: dict,
    include_thinking: bool = False,
):
    """Stream passthrough response in OpenAI SSE format."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    async def generate():
        import time

        created = int(time.time())
        show = ThinkingFilter(include_thinking)

        first_chunk = ChatCompletionChunk(
            id=chunk_id,
            created=created,
            model=model_name,
            choices=[StreamChoice(delta=DeltaContent(role="assistant"))],
        )
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        try:
            async for chunk in llm_provider.stream(
                messages=messages,
                model=model_name,
                **kwargs,
            ):
                chunk = show(chunk)
                if chunk is None:
                    continue
                thinking = chunk.get("thinking")
                # "" counts: the model has started thinking, text or not.
                if thinking is not None:
                    data_chunk = ChatCompletionChunk(
                        id=chunk_id,
                        created=created,
                        model=model_name,
                        choices=[StreamChoice(delta=DeltaContent(reasoning_content=thinking))],
                    )
                    yield f"data: {data_chunk.model_dump_json()}\n\n"
                text = chunk.get("content", "")
                if text:
                    data_chunk = ChatCompletionChunk(
                        id=chunk_id,
                        created=created,
                        model=model_name,
                        choices=[StreamChoice(delta=DeltaContent(content=text))],
                    )
                    yield f"data: {data_chunk.model_dump_json()}\n\n"
        except Exception as e:
            # The response has started: the failure goes on the stream, in
            # OpenAI's error format, rather than cutting it off.
            logger.warning(f"Passthrough stream to {model_name} failed: {e}")
            typed = e if isinstance(e, LLMError) else LLMError(INTERNAL_ERROR, "The LLM call failed")
            yield _sse_error(typed)
            yield "data: [DONE]\n\n"
            return

        final_chunk = ChatCompletionChunk(
            id=chunk_id,
            created=created,
            model=model_name,
            choices=[StreamChoice(delta=DeltaContent(), finish_reason="stop")],
        )
        yield f"data: {final_chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        logged_stream(generate(), "OpenAI adapter stream"),
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
