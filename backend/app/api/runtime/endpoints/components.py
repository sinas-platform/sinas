"""Runtime component endpoints - rendering, proxy, and scoped resource access."""
import html
import json
from pathlib import Path
from urllib.parse import quote
import time
import uuid
from typing import Any, Optional

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used, via_api_key
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.component import Component
from app.models.component_share import ComponentShare
from app.models.function import Function
from app.models.query import Query
from app.models.execution import TriggerType
from app.schemas.component import ProxyExecuteRequest, StateProxyRequest
from app.services.component_access import (
    SESSION_MAX_SECONDS,
    TOKEN_TTL_SECONDS,
    generate_component_access_token,
    share_is_live,
)
from app.services.content_tokens import generate_component_render_token
from app.services.database_pool import DatabasePoolManager
from app.services.queue_service import queue_service
from app.services.user_context import load_user_context, query_param_context

router = APIRouter()


def _script_json(value: Any) -> str:
    """JSON safe to place inside a <script> element: "</script>" (or "<!--")
    in a string must not end the element. Input comes from the URL, a share
    link or an agent's tool call."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


# The component's code is its author's, not Sinas's: it runs sandboxed in an
# opaque origin, so it can't reach the console's storage (or the parent page)
# even where the console is served from the API's origin. Its API calls are
# then cross-origin, which CORS (allow_origins=*, no credentials) permits.
_SANDBOX_CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads"


def _html_response(html: str) -> HTMLResponse:
    return HTMLResponse(
        content=html,
        headers={
            "Content-Security-Policy": _SANDBOX_CSP,
            # The page embeds an access token, and its URL a render token.
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


_RUNTIME_JS = (Path(__file__).resolve().parents[3] / "services" / "component_runtime.js").read_text()


def _build_html_shell(
    component: Component,
    input_vars: dict,
    access_token: Optional[str] = None,
    theme: Optional[str] = None,
) -> str:
    """The page a component renders in: its own HTML as the body, after a
    plain base (system font, the browser's light/dark colours) and the
    `sinas` client. Nothing is built or bundled.

    `access_token` is a component access token for the viewer (None for
    share links, whose anonymous viewers can only see static components).
    `theme` ("light"/"dark") is the embedding page's; without it the page
    follows the viewer's system setting."""
    config = {
        "component": {"namespace": component.namespace, "name": component.name},
        "input": input_vars,
        "tokenTtlSeconds": TOKEN_TTL_SECONDS,
    }
    color_scheme = theme if theme in ("light", "dark") else "light dark"
    title = html.escape(component.title or component.name)
    # The runtime is ours (no user data): only "</script" needs breaking up.
    runtime = _RUNTIME_JS.replace("</script", "<\\/script")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{ color-scheme: {color_scheme}; }}
  body {{ margin: 0; padding: 16px; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.5; }}
</style>
<script>
  window.__SINAS_CONFIG__ = {_script_json(config)};
  window.__SINAS_AUTH_TOKEN__ = {_script_json(access_token)};
</script>
<script>
{runtime}
</script>
</head>
<body>
{component.source_code}
</body>
</html>"""


@router.get(
    "/components/{namespace}/{name}/render",
    response_class=HTMLResponse,
    tags=["runtime-components"],
)
async def render_component(
    namespace: str,
    name: str,
    token: Optional[str] = None,
    input: Optional[str] = None,
    theme: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Render a component as an HTML page (for iframe embedding). `theme`
    (light/dark) matches the embedding page; otherwise the system's.

    Authenticates via a signed render token (?token=), not Authorization headers,
    since iframes cannot send headers. Follows the same pattern as file serve tokens.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Missing render token")

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired render token")

    if payload.get("purpose") != "component_render":
        raise HTTPException(status_code=401, detail="Invalid token purpose")

    if payload.get("namespace") != namespace or payload.get("name") != name:
        raise HTTPException(status_code=403, detail="Token does not match requested component")

    # Load component directly (token already proves authorization)
    component = await Component.get_by_name(db, namespace, name)
    if not component or not component.is_active:
        raise HTTPException(status_code=404, detail="Component not found")


    # Parse input vars from query param
    input_vars = {}
    if input:
        try:
            input_vars = json.loads(input)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in 'input' query parameter")

    access_token = generate_component_access_token(
        payload["sub"], namespace, name, api_key_id=payload.get("api_key_id")
    )
    return _html_response(_build_html_shell(component, input_vars, access_token, theme))


@router.post(
    "/components/{ns}/{name}/access-token",
    tags=["runtime-components"],
)
async def renew_component_access_token(
    ns: str,
    name: str,
    request: Request,
    current_user_data=Depends(get_current_user_with_permissions),
):
    """A fresh component access token, for the rendered page to keep working
    past an hour. Only a component token for this component renews (the
    route allowlist sees to the component), and only for a working day after
    the render that started the session."""
    scope = getattr(request.state, "component_scope", None)
    if scope is None:
        raise HTTPException(status_code=403, detail="Only a component token can be renewed")
    if time.time() - scope.session_start > SESSION_MAX_SECONDS:
        raise HTTPException(status_code=401, detail="Component session expired; reload the page")
    user_id, _ = current_user_data
    return {
        "token": generate_component_access_token(
            user_id, scope.namespace, scope.name,
            session_start=scope.session_start, share_id=scope.share_id,
            api_key_id=scope.api_key_id,
        ),
        "expires_in": TOKEN_TTL_SECONDS,
    }


async def _open_share(db: AsyncSession, token: str, mode: Optional[str] = None):
    """The share link and its component, counting one view — atomically, so
    concurrent loads can't exceed max_views. 404/410 as the link warrants."""
    share = await ComponentShare.get_by_token(db, token)
    if not share or (mode is not None and share.mode != mode):
        raise HTTPException(status_code=404, detail="Share link not found")
    if not share_is_live(share):
        raise HTTPException(status_code=410, detail="Share link has expired")
    counted = (
        await db.execute(
            update(ComponentShare)
            .where(
                ComponentShare.id == share.id,
                or_(
                    ComponentShare.max_views.is_(None),
                    ComponentShare.view_count < ComponentShare.max_views,
                ),
            )
            .values(view_count=ComponentShare.view_count + 1)
            .returning(ComponentShare.id)
        )
    ).scalar_one_or_none()
    if counted is None:
        raise HTTPException(status_code=410, detail="Share link has reached maximum views")
    component = (
        await db.execute(
            select(Component).where(
                Component.id == share.component_id, Component.is_active == True  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not component:
        raise HTTPException(status_code=404, detail="Component not found")
    return share, component


@router.get(
    "/components/shared/{token}",
    response_class=HTMLResponse,
    tags=["runtime-components"],
)
async def render_shared_component(
    token: str,
    theme: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Render a component through a share link (no sign-in needed).

    - snapshot: the link's inputs, no live access
    - creator: live access as the link's creator, capped to what the
      component declares, read only unless the link allows writes; ends
      the moment the link is revoked or expires
    - viewer: needs a signed-in Sinas user, so the link opens in the console
      (which renders it for that user)
    """
    share = await ComponentShare.get_by_token(db, token)
    if share is not None and share.mode == "viewer":
        return RedirectResponse(
            f"{settings.public_console_url}/shared/{quote(token, safe='')}", status_code=302
        )
    share, component = await _open_share(db, token)
    access_token = None
    if share.mode == "creator":
        access_token = generate_component_access_token(
            str(share.created_by), component.namespace, component.name, share_id=str(share.id)
        )
    return _html_response(
        _build_html_shell(component, share.input_data or {}, access_token, theme)
    )


@router.post(
    "/components/shared/{token}/open",
    tags=["runtime-components"],
)
async def open_viewer_share(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """A "viewer" share link, for the signed-in user opening it: what the
    console needs to render it with that user's own permissions (capped to
    what the component declares, like any component page)."""
    user_id, _ = current_user_data
    if via_api_key(request):
        # The page would act with the key owner's permissions, beyond the key's.
        raise HTTPException(status_code=403, detail="Open shared components signed in, not with an API key")
    share, component = await _open_share(db, token, mode="viewer")
    return {
        "namespace": component.namespace,
        "name": component.name,
        "title": component.title or component.name,
        "input": share.input_data or {},
        "render_token": generate_component_render_token(
            component.namespace, component.name, user_id
        ),
    }


# --- Proxy Endpoints ---
# These provide scoped access to SINAS resources for components


async def _get_component_or_404(
    db: AsyncSession, namespace: str, name: str
) -> Component:
    """Get active component by namespace/name or raise 404."""
    result = await db.execute(
        select(Component).where(
            Component.namespace == namespace,
            Component.name == name,
            Component.is_active == True,
        )
    )
    component = result.scalar_one_or_none()
    if not component:
        raise HTTPException(status_code=404, detail="Component not found")
    return component


@router.post(
    "/components/{ns}/{name}/proxy/queries/{q_ns}/{q_name}/execute",
    tags=["runtime-components"],
)
async def proxy_query_execute(
    ns: str,
    name: str,
    q_ns: str,
    q_name: str,
    body: ProxyExecuteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Execute a query through the component proxy (scoped to enabled_queries)."""
    user_id, permissions = current_user_data
    component = await _get_component_or_404(db, ns, name)

    query_ref = f"{q_ns}/{q_name}"
    if query_ref not in component.enabled_queries:
        raise HTTPException(
            status_code=403,
            detail=f"Query '{query_ref}' is not enabled for this component",
        )

    query = await Query.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="execute",
        namespace=q_ns, name=q_name,
    )
    scope = getattr(request.state, "component_scope", None)
    if scope is not None and scope.read_only and query.operation != "read":
        raise HTTPException(status_code=403, detail="This share link is read only")

    set_permission_used(request, f"sinas.queries/{q_ns}/{q_name}.execute")

    # Validate input
    if query.input_schema and query.input_schema.get("properties"):
        try:
            jsonschema.validate(instance=body.input, schema=query.input_schema)
        except jsonschema.ValidationError as e:
            raise HTTPException(status_code=400, detail=f"Input validation error: {e.message}")

    params = {**body.input}
    user_ctx = await load_user_context(db, user_id)
    params.update(query_param_context(user_ctx))

    start_time = time.time()
    pool_manager = DatabasePoolManager.get_instance()
    result = await pool_manager.execute_query(
        db=db,
        connection_id=str(query.database_connection_id),
        sql=query.sql,
        params=params,
        operation=query.operation,
        timeout_ms=query.timeout_ms,
        max_rows=query.max_rows,
    )
    duration_ms = int((time.time() - start_time) * 1000)

    if query.operation == "read":
        return {
            "success": True,
            "operation": query.operation,
            "data": result.get("rows", []),
            "row_count": result.get("row_count", 0),
            "duration_ms": duration_ms,
        }
    else:
        return {
            "success": True,
            "operation": query.operation,
            "data": result.get("rows"),
            "row_count": result.get("row_count"),
            "affected_rows": result.get("affected_rows", 0),
            "duration_ms": duration_ms,
        }


@router.post(
    "/components/{ns}/{name}/proxy/functions/{fn_ns}/{fn_name}/execute",
    tags=["runtime-components"],
)
async def proxy_function_execute(
    ns: str,
    name: str,
    fn_ns: str,
    fn_name: str,
    body: ProxyExecuteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Execute a function through the component proxy (scoped to enabled_functions)."""
    user_id, permissions = current_user_data
    component = await _get_component_or_404(db, ns, name)

    scope = getattr(request.state, "component_scope", None)
    if scope is not None and scope.read_only:
        raise HTTPException(status_code=403, detail="This share link is read only")

    func_ref = f"{fn_ns}/{fn_name}"
    if func_ref not in component.enabled_functions:
        raise HTTPException(
            status_code=403,
            detail=f"Function '{func_ref}' is not enabled for this component",
        )

    function = await Function.get_by_name(db, fn_ns, fn_name)
    if not function:
        raise HTTPException(status_code=404, detail="Function not found")

    permission = f"sinas.functions/{fn_ns}/{fn_name}.execute:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to execute this function")

    set_permission_used(request, permission)

    execution_id = str(uuid.uuid4())

    try:
        result = await queue_service.enqueue_and_wait(
            function_namespace=fn_ns,
            function_name=fn_name,
            input_data=body.input,
            execution_id=execution_id,
            trigger_type=TriggerType.API.value,
            trigger_id=f"component:{ns}/{name}",
            user_id=user_id,
            timeout=body.timeout,
        )
        return {"status": "success", "execution_id": execution_id, "result": result}
    except TimeoutError:
        return {
            "status": "timeout",
            "execution_id": execution_id,
            "error": "Function execution timed out.",
        }
    except Exception as e:
        return {"status": "error", "execution_id": execution_id, "error": str(e)}


# Page size the state proxy's "list" reads the store API in.
STATE_LIST_PAGE = 1000


def _enabled_store(component: Component, store_ns: str, store_name: Optional[str]) -> dict:
    """The enabled_stores entry a proxy call addresses. The SDK names a store
    by namespace alone ("states/{ns}"), from before states lived in stores;
    that still works while the component enables a single store there."""
    entries = [e for e in component.enabled_stores or [] if isinstance(e, dict) and e.get("store")]
    if store_name is not None:
        matches = [e for e in entries if e["store"] == f"{store_ns}/{store_name}"]
    else:
        matches = [e for e in entries if e["store"].split("/", 1)[0] == store_ns]
    if not matches:
        ref = f"{store_ns}/{store_name}" if store_name else store_ns
        raise HTTPException(
            status_code=403, detail=f"Store '{ref}' is not enabled for this component"
        )
    if len({e["store"] for e in matches}) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"Several stores in '{store_ns}' are enabled for this component; "
            f"address one as states/{store_ns}/{{name}}",
        )
    return matches[0]


@router.post(
    "/components/{ns}/{name}/proxy/states/{state_ns}",
    tags=["runtime-components"],
)
@router.post(
    "/components/{ns}/{name}/proxy/states/{state_ns}/{store_name}",
    tags=["runtime-components"],
)
async def proxy_state(
    ns: str,
    name: str,
    state_ns: str,
    body: StateProxyRequest,
    request: Request,
    store_name: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Access a store's states through the component proxy: the store must be
    enabled for the component (writes need readwrite), and the call then goes
    through the store API itself — its permissions, encryption and schema."""
    from app.api.runtime.endpoints import stores
    from app.schemas.state import StateCreate, StateUpdate

    component = await _get_component_or_404(db, ns, name)
    entry = _enabled_store(component, state_ns, store_name)
    scope = getattr(request.state, "component_scope", None)
    if body.action in ("set", "delete") and scope is not None and scope.read_only:
        raise HTTPException(status_code=403, detail="This share link is read only")
    if body.action in ("set", "delete") and entry.get("access") != "readwrite":
        raise HTTPException(
            status_code=403,
            detail=f"Store '{entry['store']}' is read-only for this component",
        )
    store_ns, store_nm = entry["store"].split("/", 1)
    user = current_user_data

    if body.action in ("get", "set", "delete") and not body.key:
        raise HTTPException(status_code=400, detail=f"'key' is required for {body.action} action")

    if body.action == "get":
        try:
            state = await stores.get_state(store_ns, store_nm, body.key, request, db, user)
        except HTTPException as e:
            if e.status_code == 404 and "not found in store" in str(e.detail):
                return {"found": False, "key": body.key, "value": None}
            raise
        return {"found": True, "key": state.key, "value": state.value}

    if body.action == "list":
        # Every state, as the proxy always returned (it has no paging).
        items, page = [], STATE_LIST_PAGE
        while True:
            states = await stores.list_states(
                store_ns, store_nm, request, search=None, tags=None, owner=None,
                skip=len(items), limit=page, db=db, current_user_data=user,
            )
            items += [{"key": st.key, "value": st.value} for st in states]
            if len(states) < page:
                return {"items": items}

    if body.action == "set":
        if body.value is None:
            # The store API reads a null value as "leave it unchanged".
            raise HTTPException(status_code=400, detail="'value' is required for set action")
        try:
            await stores.update_state(
                store_ns, store_nm, body.key, request,
                StateUpdate(value=body.value, visibility=body.visibility), db, user,
            )
        except HTTPException as e:
            if e.status_code != 404:
                raise
            await stores.create_state(
                store_ns, store_nm, request,
                StateCreate(key=body.key, value=body.value, visibility=body.visibility), db, user,
            )
        return {"success": True, "key": body.key}

    if body.action == "delete":
        try:
            await stores.delete_state(store_ns, store_nm, body.key, request, db, user)
        except HTTPException as e:
            if e.status_code != 404:
                raise
        return {"success": True, "key": body.key}

    raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
