"""Redis connection settings: REDIS_URL parsing, credentials and TLS, and
that the redis-py clients and the arq settings agree (app/core/redis.py)."""
import pytest
from redis.asyncio.connection import SSLConnection

from app.core import redis as redis_core
from app.core.redis import (
    arq_redis_settings,
    parse_redis_config,
    redis_client_kwargs,
)


def test_default_url_is_unchanged():
    c = parse_redis_config("redis://redis:6379/0")
    assert (c.host, c.port, c.db) == ("redis", 6379, 0)
    assert c.username is None and c.password is None
    assert c.ssl is False and c.ssl_ca_certs is None

    kwargs = redis_client_kwargs(c)
    assert kwargs == {"host": "redis", "port": 6379, "db": 0, "username": None, "password": None}

    s = arq_redis_settings(c)
    assert (s.host, s.port, s.database, s.password, s.ssl) == ("redis", 6379, 0, None, False)


def test_port_and_db_defaults():
    c = parse_redis_config("redis://cache")
    assert (c.host, c.port, c.db) == ("cache", 6379, 0)
    assert parse_redis_config("redis://cache:6390/3").db == 3
    # arq's ?db= form wins over the path
    assert parse_redis_config("redis://cache/1?db=5").db == 5


def test_special_character_password_in_url_is_percent_decoded():
    # "p@ss:w/rd#%?" percent-encoded in the URL
    c = parse_redis_config("redis://:p%40ss%3Aw%2Frd%23%25%3F@cache:6379/0")
    assert c.password == "p@ss:w/rd#%?"
    assert c.host == "cache"
    assert redis_client_kwargs(c)["password"] == "p@ss:w/rd#%?"
    assert arq_redis_settings(c).password == "p@ss:w/rd#%?"


def test_separate_password_is_verbatim_and_wins():
    raw = "a%40b@c:d/e?f#g"  # used as-is, NOT url-decoded
    c = parse_redis_config("redis://:from-url@cache:6379/0", password=raw)
    assert c.password == raw
    assert redis_client_kwargs(c)["password"] == raw
    assert arq_redis_settings(c).password == raw


def test_username_from_url_and_override():
    c = parse_redis_config("redis://app%2Duser:secret@cache:6379/0")
    assert (c.username, c.password) == ("app-user", "secret")
    c = parse_redis_config("redis://app:secret@cache/0", username="default")
    assert c.username == "default"
    assert arq_redis_settings(c).username == "default"
    assert redis_client_kwargs(c)["username"] == "default"


def test_password_never_in_repr_or_description():
    c = parse_redis_config("rediss://:hunter2@10.0.0.3:6378/0", password="s3cr3t-auth")
    assert "s3cr3t-auth" not in repr(c)
    assert "s3cr3t-auth" not in c.describe()
    assert c.describe() == "rediss://10.0.0.3:6378/0"


def test_rediss_with_ca_memorystore_shape():
    c = parse_redis_config(
        "rediss://10.1.2.3:6378/0",
        password="auth-string",
        ssl_ca_certs="/etc/sinas/redis-ca/ca.crt",
    )
    assert c.ssl is True
    assert (c.host, c.port) == ("10.1.2.3", 6378)
    assert c.ssl_cert_reqs == "required"
    assert c.ssl_check_hostname is False

    kwargs = redis_client_kwargs(c)
    assert kwargs["ssl"] is True
    assert kwargs["ssl_ca_certs"] == "/etc/sinas/redis-ca/ca.crt"
    assert kwargs["ssl_cert_reqs"] == "required"
    assert kwargs["ssl_check_hostname"] is False
    assert kwargs["password"] == "auth-string"

    s = arq_redis_settings(c)
    assert s.ssl is True
    assert s.ssl_ca_certs == "/etc/sinas/redis-ca/ca.crt"
    assert s.ssl_cert_reqs == "required"
    assert s.ssl_check_hostname is False
    assert (s.host, s.port, s.database, s.password) == ("10.1.2.3", 6378, 0, "auth-string")


def test_rediss_without_ca_uses_system_trust_store():
    c = parse_redis_config("rediss://cache.example.com:6380/0", ssl_check_hostname=True)
    kwargs = redis_client_kwargs(c)
    assert kwargs["ssl"] is True and kwargs["ssl_ca_certs"] is None
    assert kwargs["ssl_check_hostname"] is True
    assert arq_redis_settings(c).ssl_check_hostname is True


def test_tls_query_params():
    c = parse_redis_config(
        "rediss://cache:6378/2?ssl_ca_certs=/ca.pem&ssl_check_hostname=true&socket_timeout=5"
    )
    assert c.db == 2
    assert c.ssl_ca_certs == "/ca.pem"
    assert c.ssl_check_hostname is True
    # redis-py connection options pass through to the redis-py clients
    assert redis_client_kwargs(c)["socket_timeout"] == 5.0
    # explicit settings win over the query string
    c = parse_redis_config("rediss://cache/0?ssl_ca_certs=/a.pem", ssl_ca_certs="/b.pem")
    assert c.ssl_ca_certs == "/b.pem"


def test_invalid_input_rejected():
    with pytest.raises(ValueError):
        parse_redis_config("http://cache:6379/0")
    with pytest.raises(ValueError):
        parse_redis_config("rediss://cache/0?ssl_cert_reqs=maybe")


def test_ca_with_plain_redis_warns(caplog):
    with caplog.at_level("WARNING", logger="app.core.redis"):
        c = parse_redis_config("redis://cache/0", ssl_ca_certs="/ca.pem")
    assert c.ssl is False
    assert "NOT encrypted" in caplog.text
    assert "ssl" not in redis_client_kwargs(c)


def test_unix_socket():
    c = parse_redis_config("unix:///var/run/redis.sock?db=2")
    kwargs = redis_client_kwargs(c)
    assert kwargs["unix_socket_path"] == "/var/run/redis.sock"
    assert kwargs["db"] == 2 and "host" not in kwargs
    assert arq_redis_settings(c).unix_socket_path == "/var/run/redis.sock"


def test_settings_feed_every_client(monkeypatch):
    """create_redis_client() and get_redis_settings() both read the app
    settings: URL, separate password/username, CA and hostname check."""
    s = redis_core.settings
    monkeypatch.setattr(s, "redis_url", "rediss://10.9.8.7:6378/1")
    monkeypatch.setattr(s, "redis_password", "p@ss/w:rd")
    monkeypatch.setattr(s, "redis_username", "default")
    monkeypatch.setattr(s, "redis_ssl_ca_certs", "/etc/sinas/redis-ca/ca.crt")
    monkeypatch.setattr(s, "redis_ssl_check_hostname", False)

    arq = redis_core.get_redis_settings()
    assert (arq.host, arq.port, arq.database) == ("10.9.8.7", 6378, 1)
    assert (arq.username, arq.password) == ("default", "p@ss/w:rd")
    assert arq.ssl and arq.ssl_ca_certs == "/etc/sinas/redis-ca/ca.crt"

    # Built, never connected: the pool is inspected, not used.
    pool = redis_core.create_redis_client().connection_pool
    assert pool.connection_class is SSLConnection
    kw = pool.connection_kwargs
    assert (kw["host"], kw["port"], kw["db"]) == ("10.9.8.7", 6378, 1)
    assert (kw["username"], kw["password"]) == ("default", "p@ss/w:rd")
    assert kw["ssl_ca_certs"] == "/etc/sinas/redis-ca/ca.crt"
    assert kw["ssl_cert_reqs"] == "required"
    assert kw["decode_responses"] is True


def test_default_settings_build_plain_client(monkeypatch):
    s = redis_core.settings
    monkeypatch.setattr(s, "redis_url", "redis://redis:6379/0")
    monkeypatch.setattr(s, "redis_password", None)
    monkeypatch.setattr(s, "redis_username", None)
    monkeypatch.setattr(s, "redis_ssl_ca_certs", None)
    client = redis_core.create_redis_client(max_connections=7)
    pool = client.connection_pool
    assert pool.connection_class is not SSLConnection
    assert pool.max_connections == 7
    assert pool.connection_kwargs["host"] == "redis"
