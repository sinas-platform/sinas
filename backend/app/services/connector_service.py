"""Connector service — executes HTTP operations in-process."""
import asyncio
import base64
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from jinja2 import Template
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.encryption import encryption_service
from app.models.connector import Connector
from app.models.connector_oauth_token import ConnectorOAuthToken
from app.models.secret import Secret

logger = logging.getLogger(__name__)


class ConnectorAuthError(Exception):
    """Raised when a connector's auth cannot be resolved and the request must NOT be sent.

    Distinct from a transport/HTTP error: this means we deliberately refuse to send an
    unauthenticated request (e.g. an OAuth user token is missing or expired). The message
    is safe to surface to the caller and usually tells them to reconnect their account.
    """

# Connection pool limits
MAX_CONNECTIONS = 200          # Total across all hosts
MAX_CONNECTIONS_PER_HOST = 20  # Per individual host
MAX_CONCURRENT_REQUESTS = 100  # Semaphore limit

# OAuth 2.0 client-credentials token caching
OAUTH_TOKEN_TTL_SKEW = 60      # Refresh this many seconds before the token actually expires
OAUTH_DEFAULT_TTL = 3600       # Assumed lifetime when the token response omits expires_in


class ConnectorService:
    """Executes connector operations in-process via httpx with connection pooling."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        # OAuth client-credentials token cache: cache_key -> (access_token, expires_at_monotonic)
        self._oauth_tokens: dict[str, tuple[str, float]] = {}
        # Per-cache-key locks to avoid stampeding the token endpoint on concurrent misses
        self._oauth_locks: dict[str, asyncio.Lock] = {}
        # Per-(connector, user) locks: single-flight the authorization-code refresh within
        # this process so a fan-out of concurrent calls doesn't open N DB sessions all
        # blocking on the same row lock (pool exhaustion). See _refresh_authorization_code_token.
        self._refresh_locks: dict[str, asyncio.Lock] = {}

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client with connection pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=MAX_CONNECTIONS,
                    max_keepalive_connections=MAX_CONNECTIONS_PER_HOST,
                ),
                follow_redirects=True,
            )
        return self._client

    async def close(self):
        """Close the shared client. Called on shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def execute_operation(
        self,
        db: AsyncSession,
        connector: Connector,
        operation_name: str,
        parameters: dict[str, Any],
        user_token: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute a connector operation and return the response."""
        operation = connector.get_operation(operation_name)
        if not operation:
            raise ValueError(f"Operation '{operation_name}' not found on connector '{connector.namespace}/{connector.name}'")

        # Resolve auth (private secrets override shared for this user)
        auth_headers, auth_query = await self._resolve_auth(
            db, connector.auth, user_token, user_id, connector_id=connector.id
        )

        # Build request
        method = operation["method"]
        path = self._render_path(operation["path"], parameters)
        url = connector.base_url.rstrip("/") + path

        mapping = operation.get("request_body_mapping", "json")
        request_headers = {**connector.headers, **auth_headers}
        json_body = None
        query_params = None

        # Extract path params from Jinja2 template to exclude from body/query
        path_param_names = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", operation["path"]))
        non_path_params = {k: v for k, v in parameters.items() if k not in path_param_names}

        if mapping == "json":
            json_body = non_path_params or None
        elif mapping == "query":
            query_params = non_path_params
        elif mapping == "path_and_json":
            json_body = non_path_params or None
        elif mapping == "path_and_query":
            query_params = non_path_params

        # A GET/HEAD must not carry a request body. Even an empty `{}` makes httpx set
        # Content-Type/Content-Length, which strict gateways (e.g. the Google Frontend in
        # front of the Spotify API) reject as a malformed request. If a GET operation
        # mapped params to the body, send them as query params instead.
        if method.upper() in ("GET", "HEAD") and json_body is not None:
            query_params = {**(query_params or {}), **json_body}
            json_body = None

        # Auth may contribute query params (e.g. an api_key with position="query")
        if auth_query:
            query_params = {**(query_params or {}), **auth_query}

        # Execute with retry, respecting concurrency limit
        retry_config = connector.retry or {}
        max_attempts = retry_config.get("max_attempts", 1)
        backoff = retry_config.get("backoff", "none")
        timeout = connector.timeout_seconds

        last_error = None
        for attempt in range(max_attempts):
            try:
                async with self._semaphore:
                    start = time.monotonic()
                    client = self._get_client()
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=request_headers,
                        json=json_body,
                        params=query_params,
                        timeout=float(timeout),
                    )
                    elapsed_ms = (time.monotonic() - start) * 1000

                # Parse response (outside semaphore — no need to hold it during parsing)
                response_mapping = operation.get("response_mapping", "json")
                if response_mapping == "json":
                    try:
                        body = response.json()
                    except Exception:
                        body = response.text
                else:
                    body = response.text

                result = {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": body,
                    "elapsed_ms": round(elapsed_ms, 1),
                }
                logger.info(f"Connector {connector.namespace}/{connector.name}/{operation_name}: {response.status_code} in {elapsed_ms:.0f}ms")
                return result

            except httpx.TimeoutException as e:
                # Timeouts are not retried — the API is too slow, retrying won't help
                logger.error(f"Connector {connector.namespace}/{connector.name}/{operation_name} timed out after {timeout}s")
                raise
            except Exception as e:
                logger.error(f"Connector {connector.namespace}/{connector.name}/{operation_name} attempt {attempt+1} failed: {e}")
                last_error = e
                if attempt < max_attempts - 1:
                    delay = self._backoff_delay(attempt, backoff)
                    if delay > 0:
                        await asyncio.sleep(delay)
                else:
                    raise

        raise last_error  # Should not reach here

    async def _resolve_auth(
        self, db: AsyncSession, auth_config: dict[str, Any], user_token: Optional[str],
        user_id: Optional[str] = None, connector_id: Optional[Any] = None,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Resolve auth config into (headers, query_params). Private secrets override shared.

        Most auth types contribute a header; an api_key with position="query" contributes
        a query parameter instead. The query dict is empty for all other types.
        """
        auth_type = auth_config.get("type", "none")

        if auth_type == "none":
            return {}, {}

        if auth_type == "sinas_token":
            if not user_token:
                logger.warning("sinas_token auth requested but no user token available")
                return {}, {}
            return {"Authorization": f"Bearer {user_token}"}, {}

        if auth_type == "oauth2_client_credentials":
            token = await self._get_client_credentials_token(db, auth_config, user_id)
            if not token:
                # Fail closed: sending the request unauthenticated would surface the
                # provider's raw 401 as a "successful" result. Make the failure explicit.
                raise ConnectorAuthError(
                    "Could not obtain an OAuth token for this connector. "
                    "Check the connector's token URL, client ID, and client secret."
                )
            return {"Authorization": f"Bearer {token}"}, {}

        if auth_type == "oauth2_authorization_code":
            token = await self._get_authorization_code_token(db, connector_id, auth_config, user_id)
            if not token:
                raise ConnectorAuthError(
                    "This connector is not connected for your account, or its authorization "
                    "has expired. Reconnect the connector and try again."
                )
            return {"Authorization": f"Bearer {token}"}, {}

        # All other types require a secret
        secret_name = auth_config.get("secret")
        if not secret_name:
            logger.warning(f"Auth type '{auth_type}' requires a secret but none configured")
            return {}, {}

        secret_value = await self._resolve_secret_value(db, secret_name, user_id)
        if secret_value is None:
            logger.warning(f"Secret '{secret_name}' not found for connector auth")
            return {}, {}

        if auth_type == "bearer":
            return {"Authorization": f"Bearer {secret_value}"}, {}
        elif auth_type == "basic":
            encoded = base64.b64encode(secret_value.encode()).decode()
            return {"Authorization": f"Basic {encoded}"}, {}
        elif auth_type == "api_key":
            # Header is the default and the historical behavior; only an explicit
            # position="query" sends the key as a query parameter. (Case-insensitive so a
            # stray "Query" doesn't silently fall back to header.)
            if str(auth_config.get("position") or "header").lower() == "query":
                param_name = auth_config.get("param_name") or "api_key"
                return {}, {param_name: secret_value}
            header_name = auth_config.get("header") or "X-Api-Key"
            return {header_name: secret_value}, {}

        return {}, {}

    async def _resolve_secret_value(
        self, db: AsyncSession, secret_name: str, user_id: Optional[str] = None
    ) -> Optional[str]:
        """Resolve a Secret by name to its decrypted value. Private overrides shared."""
        secret = None
        if user_id:
            result = await db.execute(
                select(Secret).where(
                    and_(Secret.name == secret_name, Secret.user_id == user_id, Secret.visibility == "private")
                )
            )
            secret = result.scalar_one_or_none()

        if not secret:
            result = await db.execute(
                select(Secret).where(
                    and_(Secret.name == secret_name, Secret.visibility == "shared")
                )
            )
            secret = result.scalar_one_or_none()

        if not secret:
            return None

        return encryption_service.decrypt(secret.encrypted_value)

    async def _get_client_credentials_token(
        self, db: AsyncSession, auth_config: dict[str, Any], user_id: Optional[str] = None
    ) -> Optional[str]:
        """Fetch (and cache) an OAuth 2.0 client-credentials access token.

        The client secret is resolved from the Secret named by `auth_config["secret"]`
        (private overrides shared, same as other auth types). Tokens are cached in-process
        keyed by (user, endpoint, client, scope) until shortly before they expire.
        """
        token_url = auth_config.get("token_url")
        client_id = auth_config.get("client_id")
        secret_name = auth_config.get("secret")
        if not token_url or not client_id or not secret_name:
            logger.warning(
                "oauth2_client_credentials auth requires token_url, client_id, and secret"
            )
            return None

        scopes = auth_config.get("scopes") or []
        scope_str = " ".join(scopes) if isinstance(scopes, list) else str(scopes)
        client_auth_method = auth_config.get("client_auth_method") or "body"
        token_params = auth_config.get("token_params")

        # Distinct creds/scope/endpoint/extra-params get distinct cache entries — anything
        # that changes the minted token must be in the key or two connectors would alias
        # each other's tokens (e.g. same client_id but different `audience` in token_params,
        # or a secret rotated to a different Secret name). user_id is included because a
        # private secret override yields a different (per-user) token.
        token_params_key = json.dumps(token_params, sort_keys=True) if isinstance(token_params, dict) else ""
        cache_key = "|".join(
            [
                user_id or "shared",
                token_url,
                client_id,
                scope_str,
                client_auth_method,
                secret_name,
                token_params_key,
            ]
        )

        # Fast path: a still-valid cached token.
        cached = self._oauth_tokens.get(cache_key)
        if cached and cached[1] > time.monotonic():
            return cached[0]

        lock = self._oauth_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            # Re-check under the lock — another coroutine may have refreshed while we waited.
            cached = self._oauth_tokens.get(cache_key)
            if cached and cached[1] > time.monotonic():
                return cached[0]

            client_secret = await self._resolve_secret_value(db, secret_name, user_id)
            if client_secret is None:
                logger.warning(f"OAuth client secret '{secret_name}' not found")
                return None

            data = {"grant_type": "client_credentials"}
            if scope_str:
                data["scope"] = scope_str
            if isinstance(token_params, dict):
                data.update({k: str(v) for k, v in token_params.items()})

            payload = await self._post_token_request(
                token_url, data, client_id, client_secret, client_auth_method,
                response_paths=auth_config.get("token_response_paths"),
            )
            if not payload:
                return None

            access_token = payload.get("access_token")
            if not access_token:
                logger.error(f"OAuth token endpoint {token_url} response missing access_token")
                return None

            ttl = self._parse_expires_in(payload.get("expires_in"))
            if ttl is None:
                ttl = OAUTH_DEFAULT_TTL
            expires_at = time.monotonic() + max(ttl - OAUTH_TOKEN_TTL_SKEW, 1)
            self._oauth_tokens[cache_key] = (access_token, expires_at)
            logger.info(
                f"Obtained OAuth client-credentials token from {token_url} (ttl={ttl}s)"
            )
            return access_token

    # ------------------------------------------------------------------
    # OAuth 2.0 authorization-code grant (per-user tokens)
    # ------------------------------------------------------------------

    @staticmethod
    def oauth_redirect_uri() -> str:
        """Public callback URL the provider redirects the browser back to.

        Must exactly match the redirect URI registered with the OAuth provider.
        """
        return f"{settings.public_base_url}/auth/connectors/oauth/callback"

    def build_authorize_url(
        self, auth_config: dict[str, Any], state: str, code_challenge: str
    ) -> Optional[str]:
        """Build the provider authorization URL to redirect the user's browser to."""
        authorize_url = auth_config.get("authorize_url")
        client_id = auth_config.get("client_id")
        if not authorize_url or not client_id:
            return None
        scopes = auth_config.get("scopes") or []
        scope_str = " ".join(scopes) if isinstance(scopes, list) else str(scopes)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": self.oauth_redirect_uri(),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if scope_str:
            params["scope"] = scope_str
        sep = "&" if "?" in authorize_url else "?"
        return f"{authorize_url}{sep}{urlencode(params)}"

    @staticmethod
    def _dig(payload: Any, path: str) -> Any:
        """Simple dot-path lookup into a dict tree ("authed_user.access_token").

        No JSONPath engine by design — nesting is the only quirk providers
        actually have. Returns None on any miss."""
        node = payload
        for part in path.split("."):
            if not isinstance(node, dict):
                return None
            node = node.get(part)
        return node

    # Response fields a provider may relocate, and the configured path key
    # (snake_case, as stored on the auth config) that points at each.
    _TOKEN_PATH_FIELDS = (
        ("access_token", "access_token"),
        ("refresh_token", "refresh_token"),
        ("expires_in", "expires_in"),
        ("scope", "scope"),
    )

    def _apply_token_response_paths(
        self, payload: dict[str, Any], response_paths: Optional[dict[str, Any]]
    ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """Normalize a token-endpoint payload via configured response paths.

        Returns (normalized_payload, error). Defaults reproduce standard
        OAuth exactly: token fields at the top level, failure = a non-empty
        RFC 6749 top-level `error`. Nothing provider-specific lives in code —
        Slack's `ok: false` / `authed_user.access_token` shape is purely
        connector config (issue #109).
        """
        paths = response_paths or {}

        # --- success / error detection ---
        error_path = paths.get("error") or "error"
        desc_path = paths.get("error_description") or "error_description"
        success_flag = paths.get("success_flag")
        failed = (
            not self._dig(payload, success_flag)
            if success_flag
            # Standard shape: an error field present AND non-empty means failure,
            # even on HTTP 200 (some providers do that too).
            else bool(payload.get("error"))
        )
        if failed:
            code = self._dig(payload, error_path)
            desc = self._dig(payload, desc_path)
            parts = [str(p) for p in (code, desc) if p]
            return None, "; ".join(parts) or "provider reported failure"

        # --- relocate token fields to their standard top-level names ---
        if not any(paths.get(k) for _, k in self._TOKEN_PATH_FIELDS):
            return payload, None
        normalized = dict(payload)
        for field, path_key in self._TOKEN_PATH_FIELDS:
            configured = paths.get(path_key)
            if configured:
                value = self._dig(payload, configured)
                if value is None:
                    # Configured path is authoritative: a miss means absent,
                    # not "fall back to wherever the top level points" (for
                    # Slack that would silently store the BOT token as the
                    # user token).
                    normalized.pop(field, None)
                else:
                    normalized[field] = value
        return normalized, None

    async def _post_token_request(
        self, token_url: str, data: dict[str, str], client_id: str,
        client_secret: str, client_auth_method: str,
        response_paths: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """POST a token request (code exchange or refresh); return the parsed,
        normalized JSON payload — token fields guaranteed at their standard
        top-level names, provider errors already surfaced as failures."""
        headers = {"Accept": "application/json"}
        if client_auth_method == "basic":
            encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        else:  # "body" — client_secret_post
            data = {**data, "client_id": client_id, "client_secret": client_secret}

        try:
            async with self._semaphore:
                client = self._get_client()
                resp = await client.post(token_url, data=data, headers=headers, timeout=30.0)
        except Exception as e:
            logger.error(f"OAuth token request to {token_url} failed: {e}")
            return None

        if resp.status_code != 200:
            logger.error(
                f"OAuth token endpoint {token_url} returned {resp.status_code}: {resp.text[:200]}"
            )
            return None
        try:
            payload = resp.json()
        except Exception:
            logger.error(f"OAuth token endpoint {token_url} returned a non-JSON body")
            return None

        normalized, provider_error = self._apply_token_response_paths(
            payload, response_paths
        )
        if provider_error is not None:
            # e.g. Slack's HTTP 200 + {"ok": false, "error": "invalid_code"}
            logger.error(
                f"OAuth token endpoint {token_url} reported failure: {provider_error}"
            )
            return None
        return normalized

    @staticmethod
    def _parse_expires_in(expires_in: Any) -> Optional[int]:
        """Coerce a token response's `expires_in` to an int, or None if absent/invalid."""
        if expires_in is None:
            return None
        try:
            return int(expires_in)
        except (TypeError, ValueError):
            return None

    def _store_token_fields(self, row: ConnectorOAuthToken, payload: dict[str, Any]) -> None:
        """Copy a token-endpoint payload onto a ConnectorOAuthToken row (encrypting tokens)."""
        row.encrypted_access_token = encryption_service.encrypt(payload["access_token"])
        # Providers may omit refresh_token on refresh; keep the existing one if so.
        new_refresh = payload.get("refresh_token")
        if new_refresh:
            row.encrypted_refresh_token = encryption_service.encrypt(new_refresh)
        if payload.get("scope"):
            row.scope = payload["scope"]
        if payload.get("token_type"):
            row.token_type = payload["token_type"]

        ttl = self._parse_expires_in(payload.get("expires_in"))

        if ttl is not None:
            row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        elif row.encrypted_refresh_token:
            # No expiry given but we CAN refresh: assume a conservative lifetime so the
            # token is proactively refreshed, rather than trusted forever and then failing
            # with a dead bearer that never triggers a refresh (RFC 6749 lets refresh
            # responses omit expires_in). Never overwrite a known expiry with None here.
            row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=OAUTH_DEFAULT_TTL)
        # else: no expiry and no way to refresh — leave expires_at as-is (best effort).

    async def exchange_authorization_code(
        self, db: AsyncSession, connector: Connector, user_id: str, code: str, code_verifier: str
    ) -> bool:
        """Exchange an authorization code for tokens and persist them for this user."""
        auth_config = connector.auth or {}
        token_url = auth_config.get("token_url")
        client_id = auth_config.get("client_id")
        secret_name = auth_config.get("secret")
        if not token_url or not client_id or not secret_name:
            logger.warning("oauth2_authorization_code requires token_url, client_id, and secret")
            return False

        client_secret = await self._resolve_secret_value(db, secret_name, user_id)
        if client_secret is None:
            logger.warning(f"OAuth client secret '{secret_name}' not found")
            return False

        payload = await self._post_token_request(
            token_url,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.oauth_redirect_uri(),
                "code_verifier": code_verifier,
            },
            client_id,
            client_secret,
            auth_config.get("client_auth_method") or "body",
            response_paths=auth_config.get("token_response_paths"),
        )
        if not payload or not payload.get("access_token"):
            return False

        row = await self._get_token_row(db, connector.id, user_id)
        if row is None:
            row = ConnectorOAuthToken(connector_id=connector.id, user_id=user_id, encrypted_access_token="")
            db.add(row)
        self._store_token_fields(row, payload)
        await db.flush()
        logger.info(f"Stored OAuth token for connector {connector.namespace}/{connector.name} user={user_id}")
        return True

    async def _get_token_row(
        self, db: AsyncSession, connector_id: Any, user_id: str
    ) -> Optional[ConnectorOAuthToken]:
        result = await db.execute(
            select(ConnectorOAuthToken).where(
                and_(
                    ConnectorOAuthToken.connector_id == connector_id,
                    ConnectorOAuthToken.user_id == user_id,
                )
            )
        )
        return result.scalar_one_or_none()

    def _token_still_valid(self, row: ConnectorOAuthToken) -> bool:
        """True if the stored access token is safe to use now (skew-aware)."""
        if row.expires_at is None:
            # No expiry known — assume the stored token is usable.
            return bool(row.encrypted_access_token)
        skew = timedelta(seconds=OAUTH_TOKEN_TTL_SKEW)
        return row.expires_at - skew > datetime.now(timezone.utc)

    async def _get_authorization_code_token(
        self, db: AsyncSession, connector_id: Any, auth_config: dict[str, Any], user_id: Optional[str]
    ) -> Optional[str]:
        """Return a valid access token for this user, refreshing if it has (nearly) expired.

        The common case (a still-valid token) takes NO lock and reads on the caller's
        session, so concurrent executions never serialize. Only an actual refresh takes a
        short row lock (see _refresh_authorization_code_token) to keep concurrent refreshers
        of the same (connector, user) from racing on a rotating refresh token.
        """
        if not connector_id or not user_id:
            logger.warning("oauth2_authorization_code auth requires a connector and user context")
            return None

        row = await self._get_token_row(db, connector_id, user_id)
        if row is None:
            logger.warning(f"No OAuth token stored for connector {connector_id} user {user_id}")
            return None

        if self._token_still_valid(row):
            return encryption_service.decrypt(row.encrypted_access_token)

        if not row.encrypted_refresh_token:
            logger.warning(f"OAuth token for connector {connector_id} expired and has no refresh token")
            return None

        return await self._refresh_authorization_code_token(connector_id, auth_config, user_id)

    async def _refresh_authorization_code_token(
        self, connector_id: Any, auth_config: dict[str, Any], user_id: str
    ) -> Optional[str]:
        """Refresh a per-user token under a row lock, in its own short-lived transaction.

        Two layers of serialization:
        - A process-local asyncio lock keyed by (connector, user) single-flights the refresh
          within this process, so a fan-out of concurrent calls doesn't each open a DB
          session and block on the row lock (which would pin N pool connections for up to
          the token POST's 30s timeout). Losers wait on the asyncio lock — holding no DB
          connection — then the double-check below returns the winner's freshly-stored token.
        - `SELECT ... FOR UPDATE` on the single (connector, user) row then serializes across
          processes/containers so a rotating refresh token isn't spent twice.
        """
        from app.core.database import AsyncSessionLocal

        lock = self._refresh_locks.setdefault(f"{connector_id}|{user_id}", asyncio.Lock())
        async with lock, AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(ConnectorOAuthToken)
                    .where(
                        and_(
                            ConnectorOAuthToken.connector_id == connector_id,
                            ConnectorOAuthToken.user_id == user_id,
                        )
                    )
                    .with_for_update()
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return None

                # Double-checked locking: a sibling request may have just refreshed.
                if self._token_still_valid(row):
                    return encryption_service.decrypt(row.encrypted_access_token)
                if not row.encrypted_refresh_token:
                    return None

                token_url = auth_config.get("token_url")
                client_id = auth_config.get("client_id")
                secret_name = auth_config.get("secret")
                client_secret = (
                    await self._resolve_secret_value(session, secret_name, user_id) if secret_name else None
                )
                if not token_url or not client_id or client_secret is None:
                    logger.warning("OAuth refresh requires token_url, client_id, and secret")
                    return None

                refresh_token = encryption_service.decrypt(row.encrypted_refresh_token)
                payload = await self._post_token_request(
                    token_url,
                    {"grant_type": "refresh_token", "refresh_token": refresh_token},
                    client_id,
                    client_secret,
                    auth_config.get("client_auth_method") or "body",
                    # Same paths as the code exchange — refresh responses are
                    # just as nonstandard on these providers.
                    response_paths=auth_config.get("token_response_paths"),
                )
                if not payload or not payload.get("access_token"):
                    logger.warning(f"OAuth refresh failed for connector {connector_id} user {user_id}")
                    return None

                self._store_token_fields(row, payload)
                return payload["access_token"]

    def _render_path(self, path_template: str, parameters: dict[str, Any]) -> str:
        """Render Jinja2 path template with parameters."""
        if "{{" not in path_template:
            return path_template
        try:
            template = Template(path_template)
            return template.render(**parameters)
        except Exception:
            return path_template

    def _backoff_delay(self, attempt: int, strategy: str) -> float:
        """Calculate backoff delay in seconds."""
        if strategy == "exponential":
            return min(2 ** attempt * 0.5, 30.0)
        elif strategy == "linear":
            return min((attempt + 1) * 1.0, 30.0)
        return 0.0


connector_service = ConnectorService()
