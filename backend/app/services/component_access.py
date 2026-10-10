"""Component access tokens: what a component's code runs with in the browser.

A component is code its author wrote, running in every viewer's browser. It
used to receive the viewer's own access token (handed over by the console on
request), so any component could do anything its viewer could — the
`enabled_*` lists on the component were advisory.

Now the render endpoint embeds a token scoped to that one component, on the
pattern API keys already follow: the viewer's LIVE permissions, capped to the
grants the component declares (its enabled queries, functions, agents and
stores). It is accepted only on the routes a component needs (its own proxy
endpoints and chats with its agents), lives an hour, and can be renewed for
at most a working day after the render that issued it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from jose import JWTError, jwt

from app.core.config import settings
from app.core.permissions import check_permission

PURPOSE = "component_access"
TOKEN_TTL_SECONDS = 3600
# Renewals stop this long after the render that started the session.
SESSION_MAX_SECONDS = 12 * 3600

# Route templates (as FastAPI matched them) a component token may call. The
# component's own routes must name that component; chats are bound to agents
# by the agent chat permission, which the token only holds for the
# component's enabled agents.
_OWN_COMPONENT_ROUTES = re.compile(r"^/components/\{ns\}/\{name\}/(proxy/.+|access-token)$")
_CHAT_ROUTES = frozenset({
    "/agents/{namespace}/{agent_name}/chats",
    "/chats/{chat_id}",
    "/chats/{chat_id}/messages",
    "/chats/{chat_id}/messages/stream",
    "/chats/{chat_id}/stream/{channel_id}",
    "/chats/{chat_id}/approve-tool/{tool_call_id}",
})


@dataclass(frozen=True)
class ComponentScope:
    namespace: str
    name: str
    session_start: int
    # Set for a "creator" share link: the token acts for the link's creator,
    # is checked against the link on every call, and is read only unless the
    # link allows writes.
    share_id: Optional[str] = None
    read_only: bool = False
    api_key_id: Optional[str] = None

    @property
    def ref(self) -> str:
        return f"{self.namespace}/{self.name}"


def generate_component_access_token(
    user_id: str,
    namespace: str,
    name: str,
    session_start: Optional[int] = None,
    share_id: Optional[str] = None,
    api_key_id: Optional[str] = None,
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "namespace": namespace,
        "name": name,
        "purpose": PURPOSE,
        "session_start": session_start or now,
        "exp": now + TOKEN_TTL_SECONDS,
    }
    if share_id:
        payload["share_id"] = str(share_id)
    if api_key_id:
        # Rendered for an API key's request or agent run: acts with the key's
        # permissions (core/auth get_effective_permissions).
        payload["api_key_id"] = str(api_key_id)
    # Internal purpose token, like render and file-serve tokens: HS256.
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def component_token_claims(token: str) -> Optional[dict[str, Any]]:
    """The claims if `token` is a valid component access token, else None
    (an expired one is invalid; anything else is for the other verifiers)."""
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except JWTError:
        return None
    if claims.get("purpose") != PURPOSE:
        return None
    if not all(claims.get(k) for k in ("sub", "namespace", "name", "session_start")):
        return None
    return claims


def route_allowed(route_path: Optional[str], path_params: dict[str, Any], scope: ComponentScope) -> bool:
    if not route_path:
        return False
    if _OWN_COMPONENT_ROUTES.match(route_path):
        return (path_params.get("ns"), path_params.get("name")) == (scope.namespace, scope.name)
    return route_path in _CHAT_ROUTES


def component_grants(component, read_only: bool = False) -> list[str]:
    """Every permission the component's declarations could need. Read only
    (a creator link without writes): queries and store reads — functions and
    agents can change things, and write queries are refused at the proxy."""
    grants: list[str] = []
    for ref in component.enabled_queries or []:
        grants += [f"sinas.queries/{ref}.execute:own", f"sinas.queries/{ref}.execute:all"]
    for entry in component.enabled_stores or []:
        store = entry.get("store") if isinstance(entry, dict) else None
        if not store:
            continue
        actions = ["read_state"]
        if entry.get("access") == "readwrite" and not read_only:
            actions.append("write_state")  # set and delete
        for action in actions:
            grants += [f"sinas.stores/{store}.{action}:own", f"sinas.stores/{store}.{action}:all"]
    if read_only:
        return grants
    for ref in component.enabled_functions or []:
        grants += [f"sinas.functions/{ref}.execute:own", f"sinas.functions/{ref}.execute:all"]
    for ref in component.enabled_agents or []:
        grants += [f"sinas.agents/{ref}.chat:own", f"sinas.agents/{ref}.chat:all"]
    return grants


def scoped_permissions(
    component, user_permissions: dict[str, bool], read_only: bool = False
) -> dict[str, bool]:
    """The viewer's live permissions, capped to the component's grants (as an
    API key's are capped to its owner's)."""
    return {
        grant: True
        for grant in component_grants(component, read_only)
        if check_permission(user_permissions, grant)
    }


def share_is_live(share, now: Optional[float] = None) -> bool:
    """Whether a share link still grants access (not expired). View limits
    count page loads, not the calls a loaded page makes."""
    if share is None:
        return False
    if share.expires_at is not None:
        expires = share.expires_at.timestamp()
        if expires <= (now if now is not None else time.time()):
            return False
    return True
