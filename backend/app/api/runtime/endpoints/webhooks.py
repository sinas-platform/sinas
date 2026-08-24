"""Runtime webhook endpoints - execute functions or agents via HTTP."""
import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import set_permission_used, verify_jwt_or_api_key
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.execution import TriggerType
from app.models.webhook import Webhook
from app.services.dedup_service import check_and_mark, store_result
from app.services.queue_service import queue_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Reserved keys for the raw response-control convention:
# a function may return {"_status": 200, "_headers": {...}, "_body": ...}
RAW_CONTROL_KEYS = ("_status", "_headers", "_body")


def _build_raw_response(result: Any) -> tuple[Response, dict[str, Any]]:
    """Build the HTTP response for a raw-mode webhook from a function result.

    Returns (response, cache_entry) where cache_entry is the JSON-serializable
    form used for dedup replay.
    """
    status = 200
    headers: dict[str, str] = {}
    body = result

    if isinstance(result, dict) and any(k in result for k in RAW_CONTROL_KEYS):
        raw_status = result.get("_status", 200)
        # `isinstance(True, int)` is True in Python, so a bool would slip through
        # and become status 1. Range-check as well — an out-of-range code raises
        # inside Starlette, after the handler has returned.
        if isinstance(raw_status, int) and not isinstance(raw_status, bool) and 100 <= raw_status <= 599:
            status = raw_status
        else:
            status = 200
        raw_headers = result.get("_headers")
        if isinstance(raw_headers, dict):
            headers = {str(k): str(v) for k, v in raw_headers.items()}
        body = result.get("_body")

    cache_entry = {"__raw__": {"status": status, "headers": headers, "body": body}}

    return _raw_response(status, headers, body), cache_entry


def _raw_response(status: int, headers: dict[str, str], body: Any) -> Response:
    """Build the response, honouring status codes that must not carry a body.

    204/304 (and 1xx) are body-less per RFC 9110; emitting one makes h11 raise a
    protocol error *after* the handler returns, so the client sees a truncated
    response instead of the status the function asked for.
    """
    if status in (204, 304) or 100 <= status < 200:
        return Response(status_code=status, headers=headers)
    if isinstance(body, str):
        return PlainTextResponse(body, status_code=status, headers=headers)
    return JSONResponse(body, status_code=status, headers=headers)


def _replay_cached(cached: str) -> Response:
    """Rebuild the HTTP response for a deduplicated request from the cache."""
    parsed = json.loads(cached)
    if isinstance(parsed, dict) and "__raw__" in parsed:
        raw = parsed["__raw__"]
        return _raw_response(raw.get("status", 200), raw.get("headers") or {}, raw.get("body"))
    return JSONResponse(parsed, status_code=200)


async def _execute_agent_webhook(
    webhook: Webhook,
    db: AsyncSession,
    user_id: str,
    final_input: Any,
    req_headers: dict[str, str],
):
    """Execute an agent-target webhook: render templates, resolve the chat by
    session key, and either enqueue (async) or wait for the reply (sync)."""
    from app.core.auth import create_access_token
    from app.core.config import settings
    from app.models.agent import Agent
    from app.models.chat import Chat
    from app.models.user import User
    from app.services.message_service import MessageService
    from app.services.template_renderer import render_webhook_template_checked

    # Not scoped by user_id: agents are shared by design (`chat:all` and
    # `read:all` are default grants), unlike functions. Reachability is enforced
    # by the permission check below, not by an ownership filter.
    agent_namespace = webhook.agent_namespace or "default"
    agent = await Agent.get_by_name(db, agent_namespace, webhook.agent_name)
    if not agent:
        raise HTTPException(
            status_code=500,
            detail=f"Webhook target agent '{agent_namespace}/{webhook.agent_name}' not found",
        )

    # Render templates against the request payload (defaults merged in)
    context = final_input if isinstance(final_input, dict) else {"input": final_input}
    message, msg_had_undefined = render_webhook_template_checked(
        webhook.message_template or "", context
    )
    message = message.strip()
    # A partial render must never REPLACE the author's framing — multi-event
    # templates legitimately reference fields absent per event type, and the
    # trailing instructions ("act on this per your triage instructions") are
    # the whole point of the template. Non-empty partial render → keep it and
    # APPEND the raw payload so no data is lost either. Only a fully empty
    # render falls back to bare payload JSON.
    if not message:
        logger.warning(
            "Webhook %s: message template rendered empty; falling back to raw payload",
            webhook.path,
        )
        message = json.dumps(context)
    elif msg_had_undefined:
        logger.info(
            "Webhook %s: message template partially rendered (undefined variables); "
            "appending raw payload",
            webhook.path,
        )
        message = f"{message}\n\nFull event payload:\n{json.dumps(context)}"

    session_key: Optional[str] = None
    if webhook.session_key_template:
        rendered_key, key_had_undefined = render_webhook_template_checked(
            webhook.session_key_template, context
        )
        # A partially-rendered key ('jira-') would be shared by every malformed
        # delivery, merging unrelated events into one conversation. Treat it as
        # no key at all so each such request gets a fresh chat.
        if key_had_undefined:
            logger.warning(
                "Webhook %s: session key template had undefined variables; "
                "using a fresh session for this request",
                webhook.path,
            )
            session_key = None
        else:
            session_key = rendered_key.strip() or None

    # Look up user (needed for the JWT the agent runs with).
    # `is_active` matters as much as existence: a soft-deleted user still has a
    # row, and every other auth path rejects them (core/auth.py). Without this a
    # deactivated user's public webhook would keep firing and keep minting tokens
    # for them, re-opening the access that deleting them was supposed to revoke.
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="Webhook owner is no longer active")

    # Re-check the owner's access to the target on every request. Permissions
    # granted at creation can be narrowed later, and an unauthenticated webhook
    # performs no caller-side permission check at all — so without this, a
    # long-lived public endpoint would keep running on revoked authority.
    from app.core.auth import get_user_permissions

    owner_permissions = await get_user_permissions(db, user_id)
    chat_perm = f"sinas.agents/{agent_namespace}/{webhook.agent_name}.chat:all"
    if not check_permission(owner_permissions, chat_perm):
        raise HTTPException(
            status_code=403,
            detail=f"Webhook owner is no longer authorized to chat with agent '{agent_namespace}/{webhook.agent_name}'",
        )

    token = create_access_token(user_id=user_id, email=user.email)

    # Resolve or create the chat (session-key continuity, like agent invoke)
    chat = None
    if session_key:
        result = await db.execute(
            select(Chat).where(
                Chat.agent_id == agent.id,
                Chat.session_key == session_key,
                Chat.archived == False,
            )
        )
        chat = result.scalar_one_or_none()
        if chat and str(chat.user_id) != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to use this session")

    if not chat:
        chat = Chat(
            user_id=user_id,
            agent_id=agent.id,
            agent_namespace=agent.namespace,
            agent_name=agent.name,
            title=f"webhook:{webhook.path}",
            session_key=session_key,
            job_timeout=agent.default_job_timeout,
        )
        db.add(chat)
        await db.flush()
        await db.refresh(chat)

    chat_id = str(chat.id)

    # Async mode: commit the chat so the queue worker can see it, then enqueue
    if webhook.response_mode == "async":
        await db.commit()
        job_id = await queue_service.enqueue_agent_message(
            chat_id=chat_id,
            user_id=user_id,
            user_token=token,
            content=message,
            channel_id=str(uuid.uuid4()),
            agent=f"{agent.namespace}/{agent.name}",
            # Agent-message jobs use lowercase trigger labels (cf. scheduler's
            # "schedule"), unlike TriggerType enum values used for functions
            trigger_type="webhook",
            job_timeout=agent.default_job_timeout,
        )
        return JSONResponse({"chat_id": chat_id, "job_id": job_id}, status_code=202)

    # Sync mode: run inline and wait for the reply (same as agent invoke)
    message_service = MessageService(db)
    timeout = agent.default_job_timeout or settings.function_timeout or 300
    try:
        response_message = await asyncio.wait_for(
            message_service.send_message(
                chat_id=chat_id,
                user_id=user_id,
                user_token=token,
                content=message,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Agent execution timed out")

    response = {
        "success": True,
        "chat_id": chat_id,
        "reply": response_message.content or "",
    }

    # Cache result for dedup
    if webhook.dedup:
        try:
            await store_result(
                webhook_id=str(webhook.id),
                body=final_input if isinstance(final_input, dict) else {},
                headers=req_headers,
                dedup_config=webhook.dedup,
                result=json.dumps(response),
            )
        except Exception:
            pass  # Non-critical

    return response


async def _execute_pipeline_webhook(
    webhook: Webhook,
    request: Request,
    user_id: str,
    final_input: Any,
    req_headers: dict[str, str],
):
    """Execute a pipeline-target webhook: the request payload becomes the run
    input, executing as the webhook's owner. `sync` waits for the outcome,
    `async` enqueues one run and returns 202. No perUser fan-out here — a
    webhook event is a single occurrence, not a poll tick; fan-out stays with
    schedules and manual ?allUsers runs."""
    import asyncio as _asyncio

    from app.api.runtime.endpoints.functions import _resolve_child_depth
    from app.models.pipeline import Pipeline
    from app.services import pipeline_runner
    from app.services.pipeline_runner import PipelineBusyError

    from app.core.database import AsyncSessionLocal

    pipeline_namespace = webhook.pipeline_namespace or "default"
    async with AsyncSessionLocal() as db:
        pipeline = await Pipeline.get_by_name(db, pipeline_namespace, webhook.pipeline_name)
        if not pipeline or not pipeline.is_active:
            raise HTTPException(
                status_code=500,
                detail=f"Webhook target pipeline '{pipeline_namespace}/{webhook.pipeline_name}' not found or inactive",
            )
        pipeline_id = str(pipeline.id)
        sync_timeout = pipeline.sync_timeout_seconds

    run_input = final_input if isinstance(final_input, dict) else {"input": final_input}

    if webhook.response_mode == "async":
        job_id = await queue_service.enqueue_pipeline_run(
            pipeline_id=pipeline_id,
            run_input=run_input,
            trigger_type=TriggerType.WEBHOOK.value,
            trigger_id=str(webhook.id),
            user_id=user_id,
        )
        return JSONResponse({"run_id": job_id}, status_code=202)

    token = await pipeline_runner.mint_run_token(user_id)
    if not token:
        raise HTTPException(status_code=500, detail="Webhook user not found")

    try:
        outcome = await _asyncio.wait_for(
            pipeline_runner.run_pipeline(
                pipeline_id,
                run_input,
                trigger_type=TriggerType.WEBHOOK.value,
                trigger_id=str(webhook.id),
                user_id=user_id,
                user_token=token,
                exec_depth=_resolve_child_depth(request),
                sync=True,
            ),
            timeout=sync_timeout,
        )
    except PipelineBusyError as e:
        raise HTTPException(
            status_code=409,
            detail=f"A run of this pipeline is already in progress (run_id={e.active_run_id})",
        )
    except _asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=(
                f"Pipeline run exceeded syncTimeoutSeconds ({sync_timeout}s). "
                "Completed steps had side effects; use response_mode 'async' for long runs."
            ),
        )

    if outcome["status"] != "succeeded":
        # The run record is the dead letter; surface its id, never mask failure.
        return JSONResponse(
            {"success": False, "run_id": outcome["run_id"], "error": outcome.get("error")},
            status_code=500,
        )

    response = {"success": True, "run_id": outcome["run_id"], "output": outcome["output"]}

    if webhook.dedup:
        try:
            await store_result(
                webhook_id=str(webhook.id),
                body=final_input if isinstance(final_input, dict) else {},
                headers=req_headers,
                dedup_config=webhook.dedup,
                result=json.dumps(response, default=str),
            )
        except Exception:
            pass  # Non-critical

    return response


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def execute_webhook(
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Execute webhook by triggering the associated function, agent, or pipeline."""
    # Look up webhook configuration
    result = await db.execute(
        select(Webhook).where(
            and_(
                Webhook.path == path,
                Webhook.http_method == request.method,
                Webhook.is_active == True,
            )
        )
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        raise HTTPException(
            status_code=404,
            detail=f"No active webhook found for path '{path}' and method '{request.method}'",
        )

    is_agent_target = webhook.target_type == "agent"
    is_pipeline_target = webhook.target_type == "pipeline"

    # Authenticate if required
    user_id: Optional[str] = None
    if webhook.requires_auth:
        auth_header = request.headers.get("authorization")
        api_key_header = request.headers.get("x-api-key")

        if not auth_header and not api_key_header:
            raise HTTPException(status_code=401, detail="Authorization required")

        try:
            # Build credentials in the format verify_jwt_or_api_key expects
            from fastapi.security import HTTPAuthorizationCredentials

            credentials = None
            if auth_header and auth_header.lower().startswith("bearer "):
                credentials = HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=auth_header[7:]
                )

            user_id, email, permissions = await verify_jwt_or_api_key(
                credentials=credentials,
                x_api_key=api_key_header,
                db=db,
            )

            # Triggering a webhook runs as the webhook's OWNER (their token, their
            # tools, their secrets), so the question is "may this caller trigger
            # this webhook?" — not "may this caller use the target resource?".
            # Answering the latter is unsafe: `sinas.agents/*/*.chat:all` is a
            # default grant for every user, so a target-permission check would
            # let any authenticated user drive someone else's webhook as them.
            # Ownership is therefore required unconditionally.
            if str(webhook.user_id) != user_id:
                set_permission_used(request, f"webhook.execute:own:{webhook.path}", has_perm=False)
                raise HTTPException(
                    status_code=403, detail=f"Not authorized to execute webhook '{path}'"
                )

            # Defence in depth: the caller is the owner, but their access to the
            # target may have been narrowed since the webhook was created, so the
            # target permission is re-checked on every request rather than trusted
            # from creation time.
            if is_agent_target:
                target_perm = (
                    f"sinas.agents/{webhook.agent_namespace}/{webhook.agent_name}.chat:all"
                )
            elif is_pipeline_target:
                target_perm = f"sinas.pipelines/{webhook.pipeline_namespace or 'default'}/{webhook.pipeline_name}.run:own"
                target_perm_all = f"sinas.pipelines/{webhook.pipeline_namespace or 'default'}/{webhook.pipeline_name}.run:all"
                if check_permission(permissions, target_perm_all):
                    target_perm = target_perm_all
            else:
                target_perm = f"sinas.functions/{webhook.function_namespace}/{webhook.function_name}.execute:own"
                target_perm_all = f"sinas.functions/{webhook.function_namespace}/{webhook.function_name}.execute:all"
                if check_permission(permissions, target_perm_all):
                    target_perm = target_perm_all

            if not check_permission(permissions, target_perm):
                set_permission_used(request, target_perm, has_perm=False)
                raise HTTPException(
                    status_code=403, detail=f"Not authorized to execute webhook '{path}'"
                )

            set_permission_used(request, target_perm)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")
    else:
        # Use webhook owner's user_id for unauthenticated webhooks
        user_id = str(webhook.user_id)
        set_permission_used(request, f"webhook.public:{webhook.path}")

    try:
        # Extract the request body as input
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                input_data = await request.json()
            except Exception:
                input_data = {}
        elif request.method == "GET":
            input_data = dict(request.query_params)
        else:
            input_data = {}

        # Merge default values (body overrides defaults)
        if webhook.default_values:
            final_input = {**webhook.default_values, **(input_data if isinstance(input_data, dict) else {"input": input_data})}
        else:
            final_input = input_data

        # Deduplication check (identical for function and agent targets)
        req_headers = dict(request.headers)
        if webhook.dedup:
            is_dup, cached = await check_and_mark(
                webhook_id=str(webhook.id),
                body=final_input if isinstance(final_input, dict) else {},
                headers=req_headers,
                dedup_config=webhook.dedup,
            )
            if is_dup:
                if cached:
                    return _replay_cached(cached)
                # Duplicate arrived before the original finished (or the mode
                # never caches: the async agent path stores no result). A JSON
                # envelope would be wrong for the two modes that exist to
                # control their own response — raw mode owes the provider a
                # verbatim body (Slack's url_verification handshake fails
                # otherwise), and async owes a 202. Answer in the shape the
                # mode promises.
                if webhook.response_mode == "raw":
                    return Response(status_code=204)
                if (is_agent_target or is_pipeline_target) and webhook.response_mode == "async":
                    return JSONResponse({"deduplicated": True}, status_code=202)
                return JSONResponse({"deduplicated": True}, status_code=200)

        # Pipeline target
        if is_pipeline_target:
            return await _execute_pipeline_webhook(
                webhook=webhook,
                request=request,
                user_id=user_id,
                final_input=final_input,
                req_headers=req_headers,
            )

        # Agent target
        if is_agent_target:
            return await _execute_agent_webhook(
                webhook=webhook,
                db=db,
                user_id=user_id,
                final_input=final_input,
                req_headers=req_headers,
            )

        # Function target
        execution_id = str(uuid.uuid4())
        chat_id = request.headers.get("x-chat-id")

        # Async mode: return immediately
        if webhook.response_mode == "async":
            await queue_service.enqueue_function(
                function_namespace=webhook.function_namespace,
                function_name=webhook.function_name,
                input_data=final_input,
                execution_id=execution_id,
                trigger_type=TriggerType.WEBHOOK.value,
                trigger_id=str(webhook.id),
                user_id=user_id,
                chat_id=chat_id,
            )
            return JSONResponse({"execution_id": execution_id}, status_code=202)

        # Sync and raw modes: wait for result
        result = await queue_service.enqueue_and_wait(
            function_namespace=webhook.function_namespace,
            function_name=webhook.function_name,
            input_data=final_input,
            execution_id=execution_id,
            trigger_type=TriggerType.WEBHOOK.value,
            trigger_id=str(webhook.id),
            user_id=user_id,
            chat_id=chat_id,
        )

        # Raw mode: the function's return value IS the response body
        if webhook.response_mode == "raw":
            raw_response, cache_entry = _build_raw_response(result)
            if webhook.dedup:
                try:
                    await store_result(
                        webhook_id=str(webhook.id),
                        body=final_input if isinstance(final_input, dict) else {},
                        headers=req_headers,
                        dedup_config=webhook.dedup,
                        result=json.dumps(cache_entry),
                    )
                except Exception:
                    pass  # Non-critical
            return raw_response

        # Sync mode (default): wrap in the standard envelope
        response = {"success": True, "execution_id": execution_id, "result": result}

        # Cache result for dedup
        if webhook.dedup:
            try:
                await store_result(
                    webhook_id=str(webhook.id),
                    body=final_input if isinstance(final_input, dict) else {},
                    headers=req_headers,
                    dedup_config=webhook.dedup,
                    result=json.dumps(response),
                )
            except Exception:
                pass  # Non-critical

        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Webhook execution failed: {str(e)}")
