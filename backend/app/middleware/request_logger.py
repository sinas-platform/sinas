"""FastAPI middleware for comprehensive request logging to ClickHouse."""
import asyncio
import json
import time
import uuid

from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.clickhouse_logger import clickhouse_logger


class RequestLoggerMiddleware:
    """ASGI middleware to log all API requests to ClickHouse with body capture."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Generate request ID
        request_id = str(uuid.uuid4())
        start_time = time.time()

        # Extract request details from scope
        method = scope["method"]
        path = scope["path"]
        headers_dict = dict(scope.get("headers", []))

        # Decode headers
        user_agent = headers_dict.get(b"user-agent", b"").decode("latin1")
        referer = headers_dict.get(b"referer", b"").decode("latin1")
        content_type = headers_dict.get(b"content-type", b"").decode("latin1")

        # Get client IP
        client = scope.get("client")
        ip_address = client[0] if client else None

        # Get query params
        query_string = scope.get("query_string", b"").decode("latin1")
        query_params = {}
        if query_string:
            for pair in query_string.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    query_params[k] = v

        # Cache body for logging
        body_parts = []
        request_body = None

        async def receive_with_caching():
            message = await receive()
            # Cache body chunks
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if body:
                    body_parts.append(body)
            return message

        # Variables to capture from response
        status_code = 200
        response_size = 0

        async def send_with_capturing(message):
            nonlocal status_code, response_size
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                response_size += len(body)
            await send(message)

        # Call the app with caching receive
        await self.app(scope, receive_with_caching, send_with_capturing)

        # After request is processed, parse the cached body
        # Skip logging request bodies for auth endpoints (security best practice)
        is_auth_endpoint = (
            path.startswith("/api/auth/")
            or "/login" in path
            or "/verify-otp" in path
            or "/refresh" in path
            or "/logout" in path
        )

        if body_parts and method in ["POST", "PUT", "PATCH"] and not is_auth_endpoint:
            full_body = b"".join(body_parts)
            if full_body and "application/json" in content_type:
                try:
                    request_body = json.loads(full_body.decode())
                    # Redact sensitive fields
                    if isinstance(request_body, dict):
                        for sensitive_key in [
                            "password",
                            "api_key",
                            "secret",
                            "token",
                            "refresh_token",
                            "access_token",
                            "otp",
                        ]:
                            if sensitive_key in request_body:
                                request_body[sensitive_key] = "***REDACTED***"
                except Exception:
                    pass

        # Calculate response time
        response_time_ms = int((time.time() - start_time) * 1000)

        # Extract user/permission info from scope state if available
        state = scope.get("state", {})
        user_id = state.get("user_id")
        user_email = state.get("user_email")
        permission_used = state.get("permission_used")
        has_permission = state.get("has_permission", True)
        resource_type = state.get("resource_type")
        resource_id = state.get("resource_id")
        group_id = state.get("group_id")
        error_message = state.get("error_message")
        error_type = state.get("error_type")

        # Skip logging health checks to reduce noise
        if path != "/health":
            # Log to ClickHouse (fire-and-forget, don't block response)
            asyncio.create_task(
                clickhouse_logger.log_request(
                    request_id=request_id,
                    user_id=user_id,
                    user_email=user_email,
                    permission_used=permission_used,
                    has_permission=has_permission,
                    method=method,
                    path=path,
                    query_params=query_params,
                    request_body=request_body,
                    user_agent=user_agent,
                    referer=referer,
                    ip_address=ip_address,
                    status_code=status_code,
                    response_time_ms=response_time_ms,
                    response_size_bytes=response_size,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    group_id=group_id,
                    error_message=error_message,
                    error_type=error_type,
                )
            )
