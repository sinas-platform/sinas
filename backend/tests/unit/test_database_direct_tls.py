"""TLS of the direct (non-pgbouncer) database connections.

DATABASE_DIRECT_SSLMODE / DATABASE_DIRECT_SSLROOTCERT (the Helm chart's
postgres.external.tls) reach both drivers that bypass pgbouncer: psycopg2
for migrations (libpq query parameters on the URL) and asyncpg for startup
DDL (an `ssl=` argument). Unset must leave both exactly as before.
"""

from __future__ import annotations

import ssl
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import Settings

BASE = {
    "database_user": "sinas",
    "database_password": "pw",
    "database_host": "pgbouncer",
    "database_direct_host": "db.example.internal",
    "database_port": "5432",
    "database_name": "sinas",
}


@pytest.fixture(scope="module")
def ca_file(tmp_path_factory) -> Path:
    """A throwaway self-signed CA certificate (PEM)."""
    d = tmp_path_factory.mktemp("ca")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=test-ca", "-keyout", str(d / "ca.key"), "-out", str(d / "ca.crt"),
        ],
        check=True,
        capture_output=True,
    )
    return d / "ca.crt"


def test_unset_keeps_url_and_driver_defaults():
    s = Settings(**BASE)
    assert s.get_database_direct_url == "postgresql://sinas:pw@db.example.internal:5432/sinas"
    assert s.database_direct_asyncpg_ssl() is None


def test_blank_mode_is_unset():
    s = Settings(**BASE, database_direct_sslmode="  ")
    assert s.database_direct_sslmode is None
    assert "?" not in s.get_database_direct_url


def test_invalid_mode_refused():
    with pytest.raises(ValidationError, match="DATABASE_DIRECT_SSLMODE"):
        Settings(**BASE, database_direct_sslmode="verify_full")


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "require"])
def test_plain_modes_pass_through(mode):
    s = Settings(**BASE, database_direct_sslmode=mode)
    url = make_url(s.get_database_direct_url)
    assert url.host == "db.example.internal"
    assert dict(url.query) == {"sslmode": mode}
    assert s.database_direct_asyncpg_ssl() == mode


@pytest.mark.parametrize("mode", ["verify-ca", "verify-full"])
def test_verify_modes_with_ca(mode, ca_file):
    s = Settings(
        **BASE, database_direct_sslmode=mode, database_direct_sslrootcert=str(ca_file)
    )
    # libpq (psycopg2) gets sslmode + sslrootcert, path not percent-encoded
    # (alembic's configparser would choke on "%").
    url = s.get_database_direct_url
    assert f"sslrootcert={ca_file}" in url
    assert dict(make_url(url).query) == {"sslmode": mode, "sslrootcert": str(ca_file)}

    # asyncpg: a context trusting exactly that CA.
    ctx = s.database_direct_asyncpg_ssl()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is (mode == "verify-full")
    assert [c["subject"] for c in ctx.get_ca_certs()] == [((("commonName", "test-ca"),),)]


def test_verify_mode_without_ca_uses_driver_lookup():
    """No CA path: both drivers fall back to their own lookup
    (PGSSLROOTCERT / ~/.postgresql/root.crt), consistently."""
    s = Settings(**BASE, database_direct_sslmode="verify-full")
    assert dict(make_url(s.get_database_direct_url).query) == {"sslmode": "verify-full"}
    assert s.database_direct_asyncpg_ssl() == "verify-full"


def test_rootcert_ignored_without_mode(ca_file):
    s = Settings(**BASE, database_direct_sslrootcert=str(ca_file))
    assert "?" not in s.get_database_direct_url
    assert s.database_direct_asyncpg_ssl() is None


def test_alembic_url_survives_configparser(ca_file, tmp_path):
    """alembic/env.py doubles "%" before set_main_option; a URL with one
    must round-trip through Alembic's Config unchanged."""
    from alembic.config import Config

    s = Settings(**{**BASE, "database_password": "p%40ss"}, database_direct_sslmode="require")
    cfg = Config()
    cfg.set_main_option("sqlalchemy.url", s.get_database_direct_url.replace("%", "%%"))
    assert cfg.get_main_option("sqlalchemy.url") == s.get_database_direct_url
