"""Redis connection management for queue and caching.

Every Redis client in the platform (the API's shared client, the arq pools
and workers, the scheduler, the CDC poller, the stream relay, the shared
worker manager) is built from ONE parsed configuration, so they all agree
on host, database, credentials and TLS:

    REDIS_URL               redis://[user[:password]@]host[:port][/db]
                            rediss://...   TLS (e.g. Memorystore in-transit
                            encryption, ElastiCache, Azure Cache)
                            unix:///path/to/redis.sock[?db=N]
    REDIS_PASSWORD          AUTH password; overrides the URL's. Taken
                            verbatim (no URL encoding), so an AUTH string
                            with special characters needs no escaping.
    REDIS_USERNAME          ACL username; overrides the URL's.
    REDIS_SSL_CA_CERTS      Path to a PEM bundle of CA certificates the
                            server certificate is verified against (the
                            Memorystore server CA). Empty = system trust
                            store.
    REDIS_SSL_CHECK_HOSTNAME  Also match the certificate to the host name.
                            Unset = auto: on with the system trust store,
                            off with a private CA (Memorystore is reached
                            by IP and verified against its own CA).

Query parameters on REDIS_URL: `db`, `ssl_ca_certs`, `ssl_cert_reqs`,
`ssl_check_hostname`, plus redis-py's connection options
(`socket_timeout`, `health_check_interval`, ...), which the redis-py
clients honour and arq ignores.
"""
import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

from arq.connections import ArqRedis, RedisSettings, create_pool
from redis.asyncio import Redis
from redis.asyncio.connection import URL_QUERY_ARGUMENT_PARSERS

from app.core.config import settings

logger = logging.getLogger(__name__)


def _annotate_without_arguments(self: Any, exception: Exception, number: int, command: Any) -> None:
    """redis-py's Pipeline.annotate_exception, minus the arguments.

    redis-py prefixes an error from a pipeline (and every arq enqueue,
    finish and start is one) with the whole command: "Command # 1 (SET
    arq:job:<id> <pickled job, user token included> PX ...) of pipeline
    caused error: ...". That text travels wherever str(e) goes: logs, the
    Redis job status, a sub-agent's tool result (chat history, the LLM
    provider, spans), the DLQ. Keep the command name and the key it names
    (`_key_for_error`), never the values."""
    parts = [_text(part) for part in list(command)[:2]]
    if not parts:
        parts = ["?"]
    if parts[0].upper() in ("EVAL", "EVALSHA", "FCALL"):
        parts = parts[:1]  # [1] is the script or function, not a key
    elif len(parts) == 2:
        parts[1] = _key_for_error(parts[1])
    detail = exception.args[0] if exception.args else ""
    exception.args = (
        f"Command # {number} ({' '.join(parts)}) of pipeline caused error: {detail}",
    ) + exception.args[1:]


# Keys whose name ends in a secret or personal value: an OAuth/SSO state or
# one-time code, an email (rate limits). In error text only their prefix.
_KEYS_WITH_SECRET_NAMES = ("connector_oauth_state:", "sso_state:", "sso_code:", "sinas:ratelimit:")


def _text(value: Any) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def _key_for_error(key: str) -> str:
    """A key name for an error message: as is (ids only: arq:job:<id>,
    sinas:stream:<channel>, chat:...:<chat id>), except a key named after
    a secret or personal value, which keeps only its prefix."""
    for prefix in _KEYS_WITH_SECRET_NAMES:
        if key.startswith(prefix):
            return f"{prefix}…"
    return key[:200]


def _install_pipeline_error_annotation() -> None:
    """Applied once, at import: every Redis client in the process (the
    shared one, create_redis_client's, arq's pools) builds its pipelines from
    these classes."""
    import redis.asyncio.client as async_client
    import redis.client as sync_client

    async_client.Pipeline.annotate_exception = _annotate_without_arguments  # type: ignore[method-assign]
    sync_client.Pipeline.annotate_exception = _annotate_without_arguments  # type: ignore[method-assign]


_install_pipeline_error_annotation()

_redis_client: Redis | None = None
_arq_pool: ArqRedis | None = None

_SCHEMES = ("redis", "rediss", "unix")
_CERT_REQS = ("none", "optional", "required")
# Query parameters handled here rather than passed through to redis-py.
_OWN_QUERY_PARAMS = {"db", "ssl_ca_certs", "ssl_cert_reqs", "ssl_check_hostname"}


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


@dataclass(frozen=True)
class RedisConnectionConfig:
    """Parsed Redis connection settings, shared by redis-py and arq."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    unix_socket_path: str | None = None
    ssl: bool = False
    ssl_ca_certs: str | None = None
    ssl_cert_reqs: str = "required"
    ssl_check_hostname: bool = False
    # Further redis-py connection options from the URL's query string.
    extra: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        """Where this connects, for logs. Never includes credentials."""
        if self.unix_socket_path:
            where = f"unix://{self.unix_socket_path}"
        else:
            where = f"{'rediss' if self.ssl else 'redis'}://{self.host}:{self.port}"
        return f"{where}/{self.db}"


def parse_redis_config(
    url: str,
    *,
    password: str | None = None,
    username: str | None = None,
    ssl_ca_certs: str | None = None,
    ssl_check_hostname: bool | None = None,
) -> RedisConnectionConfig:
    """Parse a Redis URL plus the separate credential/TLS settings.

    Explicit arguments win over what the URL carries. URL credentials are
    percent-decoded (a `@` in a password is written `%40`); the separate
    `password` is used as-is.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _SCHEMES:
        raise ValueError(
            f"REDIS_URL must use one of the schemes redis://, rediss://, unix:// "
            f"(got {parsed.scheme!r})"
        )

    query = {k: unquote(v[0]) for k, v in parse_qs(parsed.query).items() if v}

    db = 0
    if "db" in query:
        db = int(query["db"])
    elif parsed.scheme != "unix" and parsed.path.strip("/"):
        db = int(unquote(parsed.path).strip("/"))

    url_username = unquote(parsed.username) if parsed.username else None
    url_password = unquote(parsed.password) if parsed.password else None

    cert_reqs = query.get("ssl_cert_reqs", "required").lower()
    if cert_reqs not in _CERT_REQS:
        raise ValueError(f"ssl_cert_reqs must be one of {', '.join(_CERT_REQS)}")

    ca_certs = ssl_ca_certs or query.get("ssl_ca_certs") or None
    is_ssl = parsed.scheme == "rediss"
    if is_ssl and ca_certs and not os.path.isfile(ca_certs):
        raise ValueError(
            f"REDIS_SSL_CA_CERTS points at {ca_certs!r}, which is not a readable "
            "file. Mount the Redis server CA there (Helm: "
            "redis.external.tls.caSecret / caConfigMap) or unset it."
        )

    # Hostname check: explicit setting > URL query flag > auto. Auto = on
    # with the system trust store (otherwise ANY publicly issued certificate
    # would pass), off with a private CA (Memorystore: reached by IP,
    # verified against its own per-instance CA).
    if ssl_check_hostname is not None:
        check_hostname = ssl_check_hostname
    elif "ssl_check_hostname" in query:
        check_hostname = _to_bool(query["ssl_check_hostname"])
    else:
        check_hostname = ca_certs is None and cert_reqs != "none"
    if check_hostname and cert_reqs == "none":
        raise ValueError("ssl_check_hostname cannot be on with ssl_cert_reqs=none")

    extra: dict[str, Any] = {}
    for name, value in query.items():
        if name in _OWN_QUERY_PARAMS:
            continue
        parser = URL_QUERY_ARGUMENT_PARSERS.get(name)
        extra[name] = parser(value) if parser else value

    config = RedisConnectionConfig(
        host=unquote(parsed.hostname) if parsed.hostname else "localhost",
        port=parsed.port or 6379,
        db=db,
        username=username or url_username,
        password=password or url_password,
        unix_socket_path=unquote(parsed.path) if parsed.scheme == "unix" else None,
        ssl=is_ssl,
        ssl_ca_certs=ca_certs,
        ssl_cert_reqs=cert_reqs,
        ssl_check_hostname=check_hostname,
        extra=extra,
    )

    if not config.ssl and config.ssl_ca_certs:
        logger.warning(
            "REDIS_SSL_CA_CERTS is set but REDIS_URL uses redis:// — the "
            "connection is NOT encrypted. Use rediss:// for TLS."
        )
    if config.ssl and config.ssl_cert_reqs != "required":
        logger.warning(
            "Redis TLS certificate verification is %r: the server's identity "
            "is not checked. Provide the server CA (REDIS_SSL_CA_CERTS) instead.",
            config.ssl_cert_reqs,
        )
    return config


# sha256 of the setting values -> parsed config. Keyed on a digest, not the
# values, so the AUTH string (REDIS_PASSWORD or inside REDIS_URL) is not
# kept around as a dict key.
_config_cache: dict[str, RedisConnectionConfig] = {}


def _settings_digest(values: tuple) -> str:
    return hashlib.sha256(repr(values).encode()).hexdigest()


def get_redis_config() -> RedisConnectionConfig:
    """The platform's Redis connection settings (from app settings).

    Parsed once per distinct setting values (so the insecure-config warnings
    are logged once, not per client); a changed setting is still picked up.
    """
    values = (
        settings.redis_url,
        settings.redis_password or None,
        settings.redis_username or None,
        settings.redis_ssl_ca_certs or None,
        settings.redis_ssl_check_hostname,
    )
    digest = _settings_digest(values)
    config = _config_cache.get(digest)
    if config is None:
        url, password, username, ca_certs, check_hostname = values
        config = parse_redis_config(
            url,
            password=password,
            username=username,
            ssl_ca_certs=ca_certs,
            ssl_check_hostname=check_hostname,
        )
        _config_cache.clear()  # only the current settings matter
        _config_cache[digest] = config
    return config


def redis_client_kwargs(config: RedisConnectionConfig) -> dict[str, Any]:
    """Keyword arguments for a redis-py client built from `config`."""
    kwargs: dict[str, Any] = dict(config.extra)
    kwargs.update(db=config.db, username=config.username, password=config.password)
    if config.unix_socket_path:
        kwargs["unix_socket_path"] = config.unix_socket_path
    else:
        kwargs.update(host=config.host, port=config.port)
    if config.ssl:
        kwargs.update(
            ssl=True,
            ssl_cert_reqs=config.ssl_cert_reqs,
            ssl_ca_certs=config.ssl_ca_certs,
            # Pinned rather than left to the library default, which a
            # redis-py upgrade may flip (and break IP-addressed Memorystore).
            ssl_check_hostname=config.ssl_check_hostname,
        )
    return kwargs


def create_redis_client(
    *, decode_responses: bool = True, max_connections: int | None = None
) -> Redis:
    """A new async Redis client for the platform's Redis.

    Use this instead of Redis.from_url(settings.redis_url): it honours the
    separate password, username and TLS settings.
    """
    kwargs = redis_client_kwargs(get_redis_config())
    kwargs["decode_responses"] = decode_responses
    if max_connections is not None:
        kwargs["max_connections"] = max_connections
    return Redis(**kwargs)


def arq_redis_settings(config: RedisConnectionConfig) -> RedisSettings:
    """arq RedisSettings built from `config`."""
    return RedisSettings(
        host=config.host,
        port=config.port,
        unix_socket_path=config.unix_socket_path,
        database=config.db,
        username=config.username,
        password=config.password,
        ssl=config.ssl,
        ssl_ca_certs=config.ssl_ca_certs,
        ssl_cert_reqs=config.ssl_cert_reqs,
        ssl_check_hostname=config.ssl_check_hostname,
    )


def get_redis_settings() -> RedisSettings:
    """arq RedisSettings for the platform's Redis."""
    return arq_redis_settings(get_redis_config())


async def get_redis() -> Redis:
    """Get async Redis client (lazy-init with connection pool)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = create_redis_client(decode_responses=True, max_connections=20)
    return _redis_client


async def get_arq_pool() -> ArqRedis:
    """Get arq Redis pool for enqueuing jobs."""
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(get_redis_settings())
    return _arq_pool


async def close_redis() -> None:
    """Cleanup Redis connections on shutdown."""
    global _redis_client, _arq_pool
    if _arq_pool is not None:
        await _arq_pool.aclose()
        _arq_pool = None
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
    logger.info("Redis connections closed")


async def warn_if_redis_nearly_full(redis: Any, process: str) -> Optional[float]:
    """Log a warning when Redis memory is at 90% of maxmemory or more (an
    error once it is full and refuses writes). Returns the percentage (None:
    no maxmemory, or INFO unreadable). Never raises."""
    try:
        memory = await redis.info("memory")
    except Exception:
        logger.warning(f"{process}: could not read Redis memory info", exc_info=True)
        return None
    used = int(memory.get("used_memory") or 0)
    maxmemory = int(memory.get("maxmemory") or 0)
    if not maxmemory:
        return None
    percent = round(used / maxmemory * 100, 1)
    policy = memory.get("maxmemory_policy")
    if percent >= 100 and policy == "noeviction":
        logger.error(
            f"{process}: Redis is FULL ({percent}% of maxmemory, policy noeviction): it refuses "
            "writes, so jobs can't start or finish and requests that write fail with 503 "
            "redis_out_of_memory. Raise maxmemory (Helm: redis.maxmemory; Compose: "
            "REDIS_MAXMEMORY) or free memory."
        )
    elif percent >= 90:
        logger.warning(
            f"{process}: Redis memory at {percent}% of maxmemory (policy {policy}); "
            "raise maxmemory before it fills up."
        )
    return percent


REDIS_OOM_DETAIL = (
    "Redis is out of memory (maxmemory reached) and refuses writes, so this "
    "request could not be completed. Stored data is kept; an operator must "
    "raise Redis' maxmemory (Helm: redis.maxmemory; Compose: REDIS_MAXMEMORY)."
)


def install_redis_oom_handler(app: Any, *, openai_format: bool = False) -> None:
    """Answer a request that hit a full Redis with a 503 that says so.

    The in-cluster Redis runs maxmemory-policy noeviction: at maxmemory it
    refuses writes ("OOM command not allowed") instead of evicting queued
    jobs or locks. Without this a refused write is an anonymous 500."""
    from fastapi.responses import JSONResponse
    from redis.exceptions import ResponseError

    async def _handler(request: Any, exc: ResponseError) -> JSONResponse:
        if not is_redis_oom(exc):
            raise exc  # any other Redis error: a 500, as before
        logger.error(f"Redis is full: {redis_oom_summary(exc)} ({request.method} {request.url.path})")
        if openai_format:
            body: dict[str, Any] = {
                "error": {
                    "message": REDIS_OOM_DETAIL,
                    "type": "server_error",
                    "param": None,
                    "code": "redis_out_of_memory",
                }
            }
        else:
            body = {"detail": REDIS_OOM_DETAIL, "code": "redis_out_of_memory"}
        return JSONResponse(body, status_code=503, headers={"Retry-After": "30"})

    app.add_exception_handler(ResponseError, _handler)


def is_redis_oom(exc: BaseException) -> bool:
    """A write Redis refused at maxmemory. redis-py raises OutOfMemoryError
    for a plain command; inside a Lua script Redis 6.2 reports it as a
    script error ("Error running script ... -OOM command not allowed")."""
    from redis.exceptions import OutOfMemoryError, ResponseError

    return isinstance(exc, OutOfMemoryError) or (
        isinstance(exc, ResponseError) and "OOM command not allowed" in str(exc)
    )


def redis_oom_summary(exc: BaseException) -> str:
    """What was refused, for a log line: the command name, never the
    arguments. redis-py's pipeline errors quote the whole command ("Command
    # 1 (SET arq:result:<id> <pickled job, tokens and all> PX ...)")."""
    import re

    match = re.match(r"Command # \d+ \((\S+)", str(exc))
    if match:
        return f"{match.group(1).upper()} refused: out of memory (maxmemory reached)"
    if "script" in str(exc).lower():
        return "a write in a Lua script refused: out of memory (maxmemory reached)"
    return "write refused: out of memory (maxmemory reached)"
