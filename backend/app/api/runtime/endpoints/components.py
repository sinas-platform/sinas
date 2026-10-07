"""Runtime component endpoints - rendering, proxy, and scoped resource access."""
import html
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
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


# @sinas/ui is retired (the console vendored what it used). Loaded only for
# components that still import it, with its base styles, so they look as
# they did; everything else renders plain.
_LEGACY_UI = """<script crossorigin src="https://unpkg.com/@sinas/ui@0.2.0/dist/sinas-ui.umd.js"></script>
<script>if (window.SinasUI && window.SinasUI.injectBaseStyles) window.SinasUI.injectBaseStyles();</script>"""


def _uses_legacy_ui(bundle: str) -> bool:
    return 'require("@sinas/ui")' in bundle or "require('@sinas/ui')" in bundle


def _build_html_shell(
    component: Component,
    input_vars: dict,
    access_token: Optional[str] = None,
    theme: Optional[str] = None,
) -> str:
    """Build the HTML shell for rendering a component in an iframe.

    `access_token` is a component access token for the viewer (None for
    share links, whose anonymous viewers can only see static components).
    `theme` ("light"/"dark") is the embedding page's; without it the page
    follows the viewer's system setting."""
    config = {
        "apiBase": "",  # Same origin - proxy endpoints
        "component": {
            "namespace": component.namespace,
            "name": component.name,
            "version": component.version,
        },
        "resources": {
            "enabledAgents": component.enabled_agents,
            "enabledFunctions": component.enabled_functions,
            "enabledQueries": component.enabled_queries,
            "enabledComponents": component.enabled_components,
            "enabledStores": component.enabled_stores,
        },
        "input": input_vars,
    }

    config_json = _script_json(config)
    token_json = _script_json(access_token)
    renew_path = _script_json(
        f"/components/{quote(component.namespace, safe='')}/{quote(component.name, safe='')}/access-token"
    )
    # Renew 5 minutes before expiry; on failure retry every 30s until then.
    ttl_ms, margin_ms, retry_ms = TOKEN_TTL_SECONDS * 1000, 300_000, 30_000
    renew_ms = ttl_ms - margin_ms
    title = html.escape(component.title or component.name)
    css_overrides = component.css_overrides or ""
    bundle = component.compiled_bundle or ""
    color_scheme = theme if theme in ("light", "dark") else "light dark"
    legacy_ui = _LEGACY_UI if _uses_legacy_ui(bundle) else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  /* Plain by design: the browser's own colours (light or dark), system
     font, a little room. Components style themselves; css_overrides last. */
  :root {{ color-scheme: {color_scheme}; }}
  body {{ margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.5; }}
  #root {{ padding: 16px; }}
  {css_overrides}
</style>
</head>
<body>
<div id="root"></div>

<!-- React UMD (globals: React, ReactDOM) and the Sinas SDK (SinasSDK) -->
<script crossorigin src="https://unpkg.com/react@18.3.1/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js"></script>
<script crossorigin src="https://unpkg.com/@sinas/sdk@0.7.0/dist/sinas-sdk.umd.js"></script>
{legacy_ui}
<script>
  // The embedding page's light/dark switches arrive by message (no reload).
  window.addEventListener('message', function(event) {{
    var data = event.data;
    if (event.source !== window.parent || !data || data.type !== 'sinas:theme') return;
    if (data.theme === 'light' || data.theme === 'dark') {{
      document.documentElement.style.colorScheme = data.theme;
    }}
  }});

  // SINAS runtime config
  window.__SINAS_CONFIG__ = {config_json};
  // Scoped to this component: the viewer's permissions, capped to what the
  // component declares. Renewed before it expires (for a working day).
  window.__SINAS_AUTH_TOKEN__ = {token_json};
  (function keepAlive() {{
    if (!window.__SINAS_AUTH_TOKEN__) return;
    var expiresAt = Date.now() + {ttl_ms};
    function ended() {{
      if (document.getElementById('sinas-session-ended')) return;
      var note = document.createElement('div');
      note.id = 'sinas-session-ended';
      note.textContent = 'This session has ended. Reload the page to continue.';
      note.setAttribute('style', 'position:fixed;top:0;left:0;right:0;z-index:10;padding:8px 12px;'
        + 'background:#7f1d1d;color:#fff;font:13px system-ui,sans-serif;text-align:center');
      document.body.appendChild(note);
    }}
    function retry() {{
      // Network trouble or a server error: keep trying while the token lasts.
      if (Date.now() + {retry_ms} < expiresAt) setTimeout(renew, {retry_ms});
      else ended();
    }}
    function renew() {{
      fetch({renew_path}, {{
        method: 'POST',
        headers: {{ 'Authorization': 'Bearer ' + window.__SINAS_AUTH_TOKEN__ }},
      }}).then(function(r) {{
        if (r.status === 401 || r.status === 403) return ended();  // session over
        if (!r.ok) return retry();
        return r.json().then(function(body) {{
          window.__SINAS_AUTH_TOKEN__ = body.token;
          expiresAt = Date.now() + body.expires_in * 1000;
          setTimeout(renew, Math.max(expiresAt - Date.now() - {margin_ms}, 0));
        }});
      }}).catch(retry);
    }}
    setTimeout(renew, {renew_ms});
  }})();

  // Module shim for esbuild IIFE externals (require() calls)
  window.__SINAS_MODULES__ = {{
    "react": window.React,
    "react-dom": window.ReactDOM,
    "react-dom/client": window.ReactDOM,
    "@sinas/sdk": window.SinasSDK,
    "@sinas/ui": window.SinasUI,
  }};
  var require = function(name) {{
    if (window.__SINAS_MODULES__[name]) return window.__SINAS_MODULES__[name];
    console.warn('[SINAS] Module not found:', name);
    return {{}};
  }};
</script>

<!-- Compiled component bundle (IIFE) -->
<script>{bundle}</script>

<script>
(function() {{
  var Component = window.__SinasComponent__ && (window.__SinasComponent__.default || window.__SinasComponent__);
  if (!Component) {{
    document.getElementById('root').innerHTML = '<p style="color:red;padding:1rem;">Component failed to load.</p>';
    return;
  }}

  (function bootstrap() {{
    var root = ReactDOM.createRoot(document.getElementById('root'));
    var input = window.__SINAS_CONFIG__.input || {{}};
    root.render(React.createElement(Component, input));
  }})();
}})();
</script>
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

    # The last good build serves while a new one compiles (or after it failed).
    if not component.compiled_bundle:
        raise HTTPException(
            status_code=422,
            detail=f"Component is not compiled (status: {component.compile_status}). "
            f"Trigger compilation first.",
        )

    # Parse input vars from query param
    input_vars = {}
    if input:
        try:
            input_vars = json.loads(input)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in 'input' query parameter")

    access_token = generate_component_access_token(payload["sub"], namespace, name)
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
            user_id, scope.namespace, scope.name, session_start=scope.session_start
        ),
        "expires_in": TOKEN_TTL_SECONDS,
    }


@router.get(
    "/components/shared/{token}",
    response_class=HTMLResponse,
    tags=["runtime-components"],
)
async def render_shared_component(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Render a component via share token (no JWT needed)."""
    from datetime import datetime, timezone

    share = await ComponentShare.get_by_token(db, token)
    if not share:
        raise HTTPException(status_code=404, detail="Share link not found")

    # Check expiry
    if share.expires_at and share.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Share link has expired")

    # Check max views
    if share.max_views is not None and share.view_count >= share.max_views:
        raise HTTPException(status_code=410, detail="Share link has reached maximum views")

    # Load component
    result = await db.execute(
        select(Component).where(
            Component.id == share.component_id,
            Component.is_active == True,
        )
    )
    component = result.scalar_one_or_none()
    if not component:
        raise HTTPException(status_code=404, detail="Component not found")

    if not component.compiled_bundle:
        raise HTTPException(status_code=422, detail="Component is not compiled")

    # Increment view count
    share.view_count += 1
    await db.flush()

    input_vars = share.input_data or {}
    return _html_response(_build_html_shell(component, input_vars))


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
