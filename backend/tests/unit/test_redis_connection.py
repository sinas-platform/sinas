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


@pytest.fixture
def ca(tmp_path):
    """A CA file that exists (parse_redis_config checks for it)."""
    path = tmp_path / "ca.crt"
    path.write_text("-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n")
    return str(path)


@pytest.fixture
def redis_settings(monkeypatch):
    """Set the app's Redis settings; everything not given is unset."""

    def apply(**values):
        s = redis_core.settings
        defaults = {
            "redis_url": "redis://redis:6379/0",
            "redis_password": None,
            "redis_username": None,
            "redis_ssl_ca_certs": None,
            "redis_ssl_check_hostname": None,
        }
        defaults.update(values)
        for name, value in defaults.items():
            monkeypatch.setattr(s, name, value)

    return apply


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


def test_rediss_with_ca_memorystore_shape(ca):
    c = parse_redis_config("rediss://10.1.2.3:6378/0", password="auth-string", ssl_ca_certs=ca)
    assert c.ssl is True
    assert (c.host, c.port) == ("10.1.2.3", 6378)
    assert c.ssl_cert_reqs == "required"
    # private CA, reached by IP: hostname check off by default
    assert c.ssl_check_hostname is False

    kwargs = redis_client_kwargs(c)
    assert kwargs["ssl"] is True
    assert kwargs["ssl_ca_certs"] == ca
    assert kwargs["ssl_cert_reqs"] == "required"
    assert kwargs["ssl_check_hostname"] is False
    assert kwargs["password"] == "auth-string"

    s = arq_redis_settings(c)
    assert s.ssl is True
    assert s.ssl_ca_certs == ca
    assert s.ssl_cert_reqs == "required"
    assert s.ssl_check_hostname is False
    assert (s.host, s.port, s.database, s.password) == ("10.1.2.3", 6378, 0, "auth-string")


def test_rediss_without_ca_checks_hostname_by_default():
    """System trust store: without a hostname check ANY publicly issued
    certificate would be accepted, so auto turns it on."""
    c = parse_redis_config("rediss://cache.example.com:6380/0")
    kwargs = redis_client_kwargs(c)
    assert kwargs["ssl"] is True and kwargs["ssl_ca_certs"] is None
    assert kwargs["ssl_check_hostname"] is True
    assert arq_redis_settings(c).ssl_check_hostname is True
    # an explicit False still wins
    assert parse_redis_config("rediss://cache/0", ssl_check_hostname=False).ssl_check_hostname is False


def test_explicit_hostname_check_with_private_ca(ca):
    c = parse_redis_config("rediss://cache/0", ssl_ca_certs=ca, ssl_check_hostname=True)
    assert c.ssl_check_hostname is True


def test_tls_query_params(ca, tmp_path):
    c = parse_redis_config(
        f"rediss://cache:6378/2?ssl_ca_certs={ca}&ssl_check_hostname=true&socket_timeout=5"
    )
    assert c.db == 2
    assert c.ssl_ca_certs == ca
    # the query flag beats the CA-based auto default (which would be off)
    assert c.ssl_check_hostname is True
    # redis-py connection options pass through to the redis-py clients
    assert redis_client_kwargs(c)["socket_timeout"] == 5.0
    # explicit settings win over the query string
    other = tmp_path / "other.crt"
    other.write_text("x")
    c = parse_redis_config(f"rediss://cache/0?ssl_ca_certs={ca}", ssl_ca_certs=str(other))
    assert c.ssl_ca_certs == str(other)
    c = parse_redis_config("rediss://cache/0?ssl_check_hostname=false", ssl_check_hostname=True)
    assert c.ssl_check_hostname is True


def test_missing_ca_file_is_a_clear_error(tmp_path):
    missing = str(tmp_path / "nope.crt")
    with pytest.raises(ValueError, match="REDIS_SSL_CA_CERTS") as exc:
        parse_redis_config("rediss://cache:6378/0", ssl_ca_certs=missing)
    assert missing in str(exc.value)


def test_invalid_input_rejected():
    with pytest.raises(ValueError):
        parse_redis_config("http://cache:6379/0")
    with pytest.raises(ValueError):
        parse_redis_config("rediss://cache/0?ssl_cert_reqs=maybe")
    with pytest.raises(ValueError):
        parse_redis_config("rediss://cache/0?ssl_cert_reqs=none", ssl_check_hostname=True)


def test_unverified_tls_has_no_hostname_check_and_warns(caplog):
    with caplog.at_level("WARNING", logger="app.core.redis"):
        c = parse_redis_config("rediss://cache/0?ssl_cert_reqs=none")
    assert c.ssl_check_hostname is False
    assert "not checked" in caplog.text


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


def test_settings_feed_every_client(redis_settings, ca):
    """create_redis_client() and get_redis_settings() both read the app
    settings: URL, separate password/username, CA and hostname check."""
    redis_settings(
        redis_url="rediss://10.9.8.7:6378/1",
        redis_password="p@ss/w:rd",
        redis_username="default",
        redis_ssl_ca_certs=ca,
        redis_ssl_check_hostname=True,
    )

    arq = redis_core.get_redis_settings()
    assert (arq.host, arq.port, arq.database) == ("10.9.8.7", 6378, 1)
    assert (arq.username, arq.password) == ("default", "p@ss/w:rd")
    assert arq.ssl and arq.ssl_ca_certs == ca
    assert arq.ssl_check_hostname is True

    # Built, never connected: the pool is inspected, not used.
    pool = redis_core.create_redis_client().connection_pool
    assert pool.connection_class is SSLConnection
    kw = pool.connection_kwargs
    assert (kw["host"], kw["port"], kw["db"]) == ("10.9.8.7", 6378, 1)
    assert (kw["username"], kw["password"]) == ("default", "p@ss/w:rd")
    assert kw["ssl_ca_certs"] == ca
    assert kw["ssl_cert_reqs"] == "required"
    assert kw["ssl_check_hostname"] is True
    assert kw["decode_responses"] is True


def test_get_redis_config_honours_url_flags(redis_settings, ca):
    """With REDIS_SSL_CHECK_HOSTNAME unset, the URL's query flags apply
    (they used to be overridden by the setting's False default)."""
    redis_settings(redis_url=f"rediss://10.9.8.7:6378/0?ssl_check_hostname=true&ssl_ca_certs={ca}")
    c = redis_core.get_redis_config()
    assert c.ssl_check_hostname is True
    assert c.ssl_ca_certs == ca
    assert redis_core.get_redis_settings().ssl_check_hostname is True

    redis_settings(redis_url="rediss://cache.example.com:6380/0?ssl_check_hostname=false")
    assert redis_core.get_redis_config().ssl_check_hostname is False

    # unset everywhere: auto (system trust store -> on; private CA -> off)
    redis_settings(redis_url="rediss://cache.example.com:6380/0")
    assert redis_core.get_redis_config().ssl_check_hostname is True
    redis_settings(redis_url="rediss://10.9.8.7:6378/0", redis_ssl_ca_certs=ca)
    assert redis_core.get_redis_config().ssl_check_hostname is False

    # the explicit setting beats the URL flag
    redis_settings(
        redis_url="rediss://cache/0?ssl_check_hostname=true", redis_ssl_check_hostname=False
    )
    assert redis_core.get_redis_config().ssl_check_hostname is False


def test_get_redis_config_parses_once(redis_settings, caplog):
    """The parsed config is cached: warnings are logged once, not per client."""
    redis_settings(redis_url="rediss://cache/0?ssl_cert_reqs=none")
    with caplog.at_level("WARNING", logger="app.core.redis"):
        first = redis_core.get_redis_config()
        redis_core.create_redis_client()
        redis_core.get_redis_settings()
        assert redis_core.get_redis_config() is first
    assert caplog.text.count("not checked") == 1
    # a changed setting is picked up
    redis_settings(redis_url="redis://other:6379/0")
    assert redis_core.get_redis_config().host == "other"


def test_default_settings_build_plain_client(redis_settings):
    redis_settings()
    client = redis_core.create_redis_client(max_connections=7)
    pool = client.connection_pool
    assert pool.connection_class is not SSLConnection
    assert pool.max_connections == 7
    assert pool.connection_kwargs["host"] == "redis"


def test_config_cache_does_not_hold_the_password(redis_settings):
    redis_settings(redis_url="redis://:url-secret@cache/0", redis_password="auth-secret")
    redis_core.get_redis_config()
    for key in redis_core._config_cache:
        assert "auth-secret" not in key and "url-secret" not in key


@pytest.mark.parametrize("raw", ["", "  "])
def test_blank_check_hostname_env_means_auto(monkeypatch, raw):
    from app.core.config import Settings

    monkeypatch.setenv("REDIS_SSL_CHECK_HOSTNAME", raw)
    assert Settings().redis_ssl_check_hostname is None
    monkeypatch.setenv("REDIS_SSL_CHECK_HOSTNAME", "true")
    assert Settings().redis_ssl_check_hostname is True
