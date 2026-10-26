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
                            Off by default: Memorystore is reached by IP
                            and verified against its own per-instance CA.

Query parameters on REDIS_URL: `db`, `ssl_ca_certs`, `ssl_cert_reqs`,
`ssl_check_hostname`, plus redis-py's connection options
(`socket_timeout`, `health_check_interval`, ...), which the redis-py
clients honour and arq ignores.
"""
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from arq.connections import ArqRedis, RedisSettings, create_pool
from redis.asyncio import Redis
from redis.asyncio.connection import URL_QUERY_ARGUMENT_PARSERS

from app.core.config import settings

logger = logging.getLogger(__name__)

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

    check_hostname = (
        ssl_check_hostname
        if ssl_check_hostname is not None
        else _to_bool(query.get("ssl_check_hostname", False))
    )

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
        ssl=parsed.scheme == "rediss",
        ssl_ca_certs=ssl_ca_certs or query.get("ssl_ca_certs") or None,
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


def get_redis_config() -> RedisConnectionConfig:
    """The platform's Redis connection settings (from app settings)."""
    return parse_redis_config(
        settings.redis_url,
        password=settings.redis_password or None,
        username=settings.redis_username or None,
        ssl_ca_certs=settings.redis_ssl_ca_certs or None,
        ssl_check_hostname=settings.redis_ssl_check_hostname,
    )


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
