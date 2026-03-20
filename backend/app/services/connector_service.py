"""Connector service — executes HTTP operations in-process."""
import asyncio
import base64
import logging
import re
import time
from typing import Any, Optional

import httpx
from jinja2 import Template
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encryption_service
from app.models.connector import Connector
from app.models.secret import Secret

logger = logging.getLogger(__name__)

# Connection pool limits
MAX_CONNECTIONS = 200          # Total across all hosts
MAX_CONNECTIONS_PER_HOST = 20  # Per individual host
MAX_CONCURRENT_REQUESTS = 100  # Semaphore limit


class ConnectorService:
    """Executes connector operations in-process via httpx with connection pooling."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

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
    ) -> dict[str, Any]:
        """Execute a connector operation and return the response."""
        operation = connector.get_operation(operation_name)
        if not operation:
            raise ValueError(f"Operation '{operation_name}' not found on connector '{connector.namespace}/{connector.name}'")

        # Resolve auth
        auth_headers = await self._resolve_auth(db, connector.auth, user_token)

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
            json_body = non_path_params
        elif mapping == "query":
            query_params = non_path_params
        elif mapping == "path_and_json":
            json_body = non_path_params
        elif mapping == "path_and_query":
            query_params = non_path_params

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

                return {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": body,
                    "elapsed_ms": round(elapsed_ms, 1),
                }

            except Exception as e:
                last_error = e
                if attempt < max_attempts - 1:
                    delay = self._backoff_delay(attempt, backoff)
                    if delay > 0:
                        await asyncio.sleep(delay)
                else:
                    raise

        raise last_error  # Should not reach here

    async def _resolve_auth(
        self, db: AsyncSession, auth_config: dict[str, Any], user_token: Optional[str]
    ) -> dict[str, str]:
        """Resolve auth config to HTTP headers."""
        auth_type = auth_config.get("type", "none")

        if auth_type == "none":
            return {}

        if auth_type == "sinas_token":
            if not user_token:
                logger.warning("sinas_token auth requested but no user token available")
                return {}
            return {"Authorization": f"Bearer {user_token}"}

        # All other types require a secret
        secret_name = auth_config.get("secret")
        if not secret_name:
            logger.warning(f"Auth type '{auth_type}' requires a secret but none configured")
            return {}

        result = await db.execute(select(Secret).where(Secret.name == secret_name))
        secret = result.scalar_one_or_none()
        if not secret:
            logger.warning(f"Secret '{secret_name}' not found for connector auth")
            return {}

        secret_value = encryption_service.decrypt(secret.encrypted_value)

        if auth_type == "bearer":
            return {"Authorization": f"Bearer {secret_value}"}
        elif auth_type == "basic":
            encoded = base64.b64encode(secret_value.encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
        elif auth_type == "api_key":
            header_name = auth_config.get("header", "X-Api-Key")
            return {header_name: secret_value}

        return {}

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
