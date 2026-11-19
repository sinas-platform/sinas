"""FastAPI middleware for comprehensive request logging to ClickHouse."""
import json
import re
import time
import uuid

from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.clickhouse_logger import clickhouse_logger

# Paths whose last segment is a credential: logged with the secret masked,
# or anyone who may read request logs could replay it.
_SECRET_PATH_PREFIXES = ("/watch/",)


def redact_path(path: str) -> str:
    for prefix in _SECRET_PATH_PREFIXES:
        if path.startswith(prefix) and len(path) > len(prefix):
            rest = path[len(prefix):]
            secret, sep, tail = rest.partition("/")
            return f"{prefix}[redacted]{sep}{tail}" if secret else path
    return path


_SENSITIVE_KEYS = frozenset({
    "password", "api_key", "secret", "token", "refresh_token", "access_token", "otp",
    "code_verifier", "reset_token", "setup_token", "new_password", "current_password",
    "otp_code", "credential",
})
# Whole mappings whose values are credentials (an MCP server's or a
# connector's request headers, a chat's run provider credentials), and
# strings that may carry one in their userinfo or query (an MCP server's url).
_SECRET_MAPPINGS = frozenset({"headers", "provider_credentials"})
_URL_KEYS = frozenset({"url"})
_REDACTED = "***REDACTED***"


def _safe_url(value: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(value)
    except ValueError:
        return _REDACTED
    if not parts.scheme and not parts.netloc:
        return value
    host = parts.netloc.rsplit("@", 1)[-1]
    if host != parts.netloc:
        host = "***@" + host
    return urlunsplit((parts.scheme, host, parts.path, "***" if parts.query else "", ""))


def _is_sensitive_param(raw_name: str) -> bool:
    """Whether a raw query parameter name names a credential, read the way
    the browser does (URLSearchParams: percent-decoded, `+` as a space),
    case-insensitively: `tok%65n`, `%74oken` and `TOKEN` all name `token`."""
    from urllib.parse import unquote_plus

    return unquote_plus(raw_name).casefold() in _SENSITIVE_KEYS


# How deep URLs nested in query values (`?next=/a?next=/b?token=...`) are
# followed. Past it the rest of the value is masked whole: fail closed, and
# never recurse without bound on attacker-supplied input.
_MAX_NESTED_URLS = 8


def _redact_query_string(query: str, depth: int = 0) -> str:
    pairs = []
    for pair in query.split("&"):
        name, sep, value = pair.partition("=")
        if sep and _is_sensitive_param(name):
            pairs.append(f"{name}={_REDACTED}")
        elif "?" in value:  # a URL inside a value (`?next=/ui/?token=...`)
            if depth >= _MAX_NESTED_URLS:
                pairs.append(f"{name}{sep}{_REDACTED}")
            else:
                pairs.append(f"{name}{sep}{redact_url_query(value, depth + 1)}")
        else:
            pairs.append(pair)
    return "&".join(pairs)


def redact_query_params(query_string: str) -> dict[str, str]:
    """Query parameters as they may be stored, credential-bearing ones
    (`?token=`, `?api_key=`..., however their names are encoded) masked."""
    params: dict[str, str] = {}
    for pair in query_string.split("&") if query_string else ():
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        if _is_sensitive_param(k):
            params[k] = _REDACTED
        else:
            params[k] = redact_url_query(v) if "?" in v else v
    return params


# A query string inside a URL, a path or a whole log line.
_QUERY_IN_TEXT = re.compile(r"\?([^#\s\"]*)")


def redact_url_query(text: str, depth: int = 0) -> str:
    """A URL, path-with-query or log line with credential-bearing query
    values masked: `/ui/?token=abc` (or `?tok%65n=abc`) ->
    `/ui/?token=***REDACTED***`."""
    return _QUERY_IN_TEXT.sub(lambda m: "?" + _redact_query_string(m.group(1), depth), text)


class RedactQueryTokensFilter:
    """logging filter for uvicorn's access log: the console (served by this
    process in the lite profile) is opened as `/ui/?token=<setup link>`, and
    that line must not land in the logs with a usable token."""

    def filter(self, record) -> bool:
        args = record.args
        if isinstance(args, tuple) and args:
            record.args = tuple(redact_url_query(a) if isinstance(a, str) else a for a in args)
        elif isinstance(record.msg, str):
            record.msg = redact_url_query(record.msg)
        return True


def redact_body(body):
    """The request body as it may be stored: credential-bearing values
    masked at every depth (the sensitive keys, every value of a `headers`
    mapping, the userinfo and query of a `url`), whoever may later read
    the logs."""
    if isinstance(body, dict):
        out = {}
        for key, value in body.items():
            lowered = key.lower() if isinstance(key, str) else key
            if lowered in _SENSITIVE_KEYS:
                out[key] = _REDACTED
            elif lowered in _SECRET_MAPPINGS and isinstance(value, dict):
                out[key] = {k: _REDACTED for k in value}
            elif lowered == "provider_credentials" and value is not None:
                out[key] = _REDACTED  # malformed (not an object): masked whole
            elif lowered in _URL_KEYS and isinstance(value, str):
                out[key] = _safe_url(value)
            else:
                out[key] = redact_body(value)
        return out
    if isinstance(body, list):
        return [redact_body(v) for v in body]
    return body


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
        path = redact_path(scope["path"])
        headers_dict = dict(scope.get("headers", []))

        # Decode headers
        user_agent = headers_dict.get(b"user-agent", b"").decode("latin1")
        referer = redact_url_query(headers_dict.get(b"referer", b"").decode("latin1"))
        content_type = headers_dict.get(b"content-type", b"").decode("latin1")

        application = headers_dict.get(b"x-application", b"").decode("latin1")

        # Get client IP
        client = scope.get("client")
        ip_address = client[0] if client else None

        # Get query params
        query_string = scope.get("query_string", b"").decode("latin1")
        query_params = redact_query_params(query_string)

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
            path.startswith("/auth/")
            or path.startswith("/api/auth/")
            or "/login" in path
            or "/verify-otp" in path
            or "/refresh" in path
            or "/logout" in path
        )

        # Max request body size to log (skip large payloads like file uploads)
        MAX_LOG_BODY_BYTES = 32_768  # 32KB

        if body_parts and method in ["POST", "PUT", "PATCH"] and not is_auth_endpoint:
            full_body = b"".join(body_parts)
            if full_body and "application/json" in content_type:
                if len(full_body) > MAX_LOG_BODY_BYTES:
                    request_body = {"_truncated": True, "_size": len(full_body)}
                else:
                    try:
                        request_body = redact_body(json.loads(full_body.decode()))
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
            # Buffers the row; it's written to ClickHouse in the background.
            await clickhouse_logger.log_request(
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
                metadata={"application": application} if application else None,
            )
