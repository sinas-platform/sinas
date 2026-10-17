"""Request-log endpoints when ClickHouse is disabled or unreachable.

Convention: every ClickHouse-backed endpoint answers 503 with an explanatory
detail, never a 500 and never a silently empty list. `/info` reports
`features.clickhouse` so clients can tell a deliberate disable (CLICKHOUSE_HOST
empty) from an outage.
"""

import time

import pytest

from app.services.clickhouse_logger import (
    DISABLED_DETAIL,
    UNAVAILABLE_DETAIL,
    ClickHouseLogger,
    clickhouse_logger,
)
from tests.conftest import auth_headers

ENDPOINTS = ["/api/v1/request-logs", "/api/v1/request-logs/stats"]


@pytest.fixture
def clickhouse_disabled(monkeypatch):
    monkeypatch.setattr(clickhouse_logger, "_disabled", True)
    monkeypatch.setattr(clickhouse_logger, "client", None)


@pytest.fixture
def clickhouse_down(monkeypatch):
    """Configured, but the last connection attempt failed: in the retry window."""
    monkeypatch.setattr(clickhouse_logger, "_disabled", False)
    monkeypatch.setattr(clickhouse_logger, "client", None)
    monkeypatch.setattr(clickhouse_logger, "_retry_at", time.monotonic() + 3600)


@pytest.fixture
def clickhouse_drops_connection(monkeypatch):
    """Configured and believed up, but the query itself loses the connection."""
    monkeypatch.setattr(clickhouse_logger, "_disabled", False)
    monkeypatch.setattr(clickhouse_logger, "_retry_at", 0.0)

    async def _query(*_args, **_kwargs):
        raise ConnectionError("ClickHouse not available")

    monkeypatch.setattr(clickhouse_logger, "query", _query)


class TestDisabled:
    @pytest.mark.parametrize("path", ENDPOINTS)
    async def test_admin_gets_503_with_explanation(self, client, admin_user, clickhouse_disabled, path):
        resp = await client.get(path, headers=auth_headers(admin_user))
        assert resp.status_code == 503
        assert resp.json()["detail"] == DISABLED_DETAIL
        assert "CLICKHOUSE_HOST" in resp.json()["detail"]

    @pytest.mark.parametrize("path", ENDPOINTS)
    async def test_own_logs_reader_gets_the_same(self, client, test_user, clickhouse_disabled, path):
        resp = await client.get(path, headers=auth_headers(test_user))
        assert resp.status_code == 503
        assert resp.json()["detail"] == DISABLED_DETAIL

    @pytest.mark.parametrize("path", ENDPOINTS)
    async def test_auth_still_comes_first(self, client, clickhouse_disabled, path):
        resp = await client.get(path)
        assert resp.status_code == 401

    async def test_info_reports_it(self, client, clickhouse_disabled):
        resp = await client.get("/info")
        assert resp.status_code == 200
        assert resp.json()["features"]["clickhouse"] is False


class TestUnreachable:
    @pytest.mark.parametrize("path", ENDPOINTS)
    async def test_in_retry_window_is_503_not_empty(self, client, admin_user, clickhouse_down, path):
        resp = await client.get(path, headers=auth_headers(admin_user))
        assert resp.status_code == 503
        assert resp.json()["detail"] == UNAVAILABLE_DETAIL

    @pytest.mark.parametrize("path", ENDPOINTS)
    async def test_connection_lost_mid_query_is_503(
        self, client, admin_user, clickhouse_drops_connection, path
    ):
        resp = await client.get(path, headers=auth_headers(admin_user))
        assert resp.status_code == 503
        assert resp.json()["detail"] == UNAVAILABLE_DETAIL

    async def test_info_still_reports_enabled(self, client, clickhouse_down):
        # An outage is not a deliberate disable: the flag stays on.
        resp = await client.get("/info")
        assert resp.json()["features"]["clickhouse"] is True


async def test_query_logs_raises_when_unreachable_instead_of_returning_empty(monkeypatch):
    logger = ClickHouseLogger.__new__(ClickHouseLogger)
    logger._disabled = False

    def _query_rows(*_args, **_kwargs):
        raise ConnectionError("ClickHouse not available")

    monkeypatch.setattr(logger, "_query_rows", _query_rows)
    with pytest.raises(ConnectionError):
        await logger.query_logs()
