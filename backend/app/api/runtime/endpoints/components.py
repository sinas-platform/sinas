"""Runtime component endpoints - rendering, proxy, and scoped resource access."""
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from jose import JWTError, jwt
from pydantic import BaseModel
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
from app.models.user import User
from app.services.database_pool import DatabasePoolManager

router = APIRouter()


def generate_component_render_token(
    namespace: str, name: str, user_id: str, expires_in: int = 3600
) -> str:
    """
    Generate a signed render token for a component (like file serve tokens).

    The token is purpose-scoped and short-lived. It allows the render endpoint
    to authenticate iframe requests without Authorization headers.
    """
    payload = {
        "namespace": namespace,
        "name": name,
        "sub": user_id,
        "purpose": "component_render",
        "exp": int((datetime.now(UTC) + timedelta(seconds=expires_in)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def _build_html_shell(component: Component, input_vars: dict) -> str:
    """Build the HTML shell for rendering a component in an iframe."""
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
            "stateNamespacesReadonly": component.state_namespaces_readonly,
            "stateNamespacesReadwrite": component.state_namespaces_readwrite,
        },
        "input": input_vars,
    }

    config_json = json.dumps(config)
    css_overrides = component.css_overrides or ""
    bundle = component.compiled_bundle or ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{component.title or component.name}</title>
<style>
  #root {{ min-height: 100vh; padding: 16px; }}
  {css_overrides}
</style>
</head>
<body>
<div id="root"></div>

<!-- React UMD (globals: React, ReactDOM) -->
<script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>

<!-- SINAS SDK and UI (globals: SinasSDK, SinasUI) -->
<script crossorigin src="https://unpkg.com/@sinas/sdk@0.1.1/dist/sinas-sdk.umd.js"></script>
<script crossorigin src="https://unpkg.com/@sinas/ui@0.2.0/dist/sinas-ui.umd.js"></script>

<script>
  // SINAS runtime config
  window.__SINAS_CONFIG__ = {config_json};
  window.__SINAS_AUTH_TOKEN__ = null;

  // Listen for auth token from parent (postMessage auth)
  window.addEventListener('message', function(event) {{
    if (event.data && event.data.type === 'sinas:auth') {{
      window.__SINAS_AUTH_TOKEN__ = event.data.token;
      window.dispatchEvent(new CustomEvent('sinas:authenticated'));
    }}
  }});

  // Notify parent we're ready for auth
  if (window.parent !== window) {{
    window.parent.postMessage({{ type: 'sinas:ready', component: '{component.namespace}/{component.name}' }}, '*');
  }}

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

  var booted = false;
  function bootstrap() {{
    if (booted) return;
    booted = true;
    var root = ReactDOM.createRoot(document.getElementById('root'));
    var input = window.__SINAS_CONFIG__.input || {{}};
    root.render(React.createElement(Component, input));
  }}

  // If embedded in iframe, wait for auth; otherwise bootstrap immediately
  if (window.parent !== window) {{
    window.addEventListener('sinas:authenticated', bootstrap, {{ once: true }});
    // Fallback: bootstrap after 3s even without auth (for public components)
    setTimeout(bootstrap, 3000);
  }} else {{
    bootstrap();
  }}
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
    db: AsyncSession = Depends(get_db),
):
    """
    Render a component as an HTML page (for iframe embedding).

    Authenticates via a signed render token (?token=), not Authorization headers,
    since iframes cannot send headers. Follows the same pattern as file serve tokens.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Missing render token")

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
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

    if component.compile_status != "success":
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

    html = _build_html_shell(component, input_vars)
    return HTMLResponse(content=html)


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

    if component.compile_status != "success":
        raise HTTPException(status_code=422, detail="Component is not compiled")

    # Increment view count
    share.view_count += 1
    await db.commit()

    input_vars = share.input_data or {}
    html = _build_html_shell(component, input_vars)
    return HTMLResponse(content=html)


# --- Proxy Endpoints ---
# These provide scoped access to SINAS resources for components


class ProxyExecuteRequest(BaseModel):
    input: dict[str, Any] = {}
    timeout: Optional[int] = None


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
    params["user_id"] = str(user_id)

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user:
        params["user_email"] = user.email

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

    from app.models.execution import TriggerType
    from app.services.queue_service import queue_service

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


class StateProxyRequest(BaseModel):
    action: str  # get, set, delete, list
    key: Optional[str] = None
    value: Optional[dict[str, Any]] = None
    visibility: str = "private"


@router.post(
    "/components/{ns}/{name}/proxy/states/{state_ns}",
    tags=["runtime-components"],
)
async def proxy_state(
    ns: str,
    name: str,
    state_ns: str,
    body: StateProxyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Access state through the component proxy (scoped to enabled state namespaces)."""
    from app.models.state import State

    user_id, permissions = current_user_data
    component = await _get_component_or_404(db, ns, name)

    readonly_ns = component.state_namespaces_readonly or []
    readwrite_ns = component.state_namespaces_readwrite or []
    all_ns = readonly_ns + readwrite_ns

    if state_ns not in all_ns:
        raise HTTPException(
            status_code=403,
            detail=f"State namespace '{state_ns}' is not enabled for this component",
        )

    # Write operations require readwrite access
    if body.action in ("set", "delete") and state_ns not in readwrite_ns:
        raise HTTPException(
            status_code=403,
            detail=f"State namespace '{state_ns}' is read-only for this component",
        )

    if body.action == "get":
        if not body.key:
            raise HTTPException(status_code=400, detail="'key' is required for get action")
        result = await db.execute(
            select(State).where(
                State.namespace == state_ns,
                State.key == body.key,
                State.user_id == user_id,
            )
        )
        state = result.scalar_one_or_none()
        if not state:
            return {"found": False, "key": body.key, "value": None}
        return {"found": True, "key": state.key, "value": state.value}

    elif body.action == "list":
        result = await db.execute(
            select(State).where(
                State.namespace == state_ns,
                State.user_id == user_id,
            )
        )
        states = result.scalars().all()
        return {"items": [{"key": s.key, "value": s.value} for s in states]}

    elif body.action == "set":
        if not body.key:
            raise HTTPException(status_code=400, detail="'key' is required for set action")
        result = await db.execute(
            select(State).where(
                State.namespace == state_ns,
                State.key == body.key,
                State.user_id == user_id,
            )
        )
        state = result.scalar_one_or_none()
        if state:
            state.value = body.value
        else:
            state = State(
                namespace=state_ns,
                key=body.key,
                value=body.value,
                user_id=user_id,
                visibility=body.visibility,
            )
            db.add(state)
        await db.commit()
        return {"success": True, "key": body.key}

    elif body.action == "delete":
        if not body.key:
            raise HTTPException(status_code=400, detail="'key' is required for delete action")
        result = await db.execute(
            select(State).where(
                State.namespace == state_ns,
                State.key == body.key,
                State.user_id == user_id,
            )
        )
        state = result.scalar_one_or_none()
        if state:
            await db.delete(state)
            await db.commit()
        return {"success": True, "key": body.key}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
