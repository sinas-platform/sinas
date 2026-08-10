"""Configurable OAuth token-response paths (#109).

Nonstandard providers (Slack) nest tokens (`authed_user.access_token`) and
signal errors on HTTP 200 (`{"ok": false, "error": ...}`). Both the token
locations and success/error detection are connector config — nothing
provider-specific in code, and defaults reproduce RFC 6749 exactly.
"""

import types

import pytest

from app.schemas.config import (
    TOKEN_RESPONSE_PATH_FIELD_MAP,
    TokenResponsePathsConfig,
)
from app.services.connector_service import connector_service

# The Slack oauth.v2.access shape from the issue: top-level access_token is
# the BOT token; the user token lives under authed_user.
SLACK_SUCCESS = {
    "ok": True,
    "access_token": "xoxb-bot-token",
    "token_type": "bot",
    "authed_user": {
        "id": "U123",
        "access_token": "xoxp-user-token",
        "scope": "search:read",
        "token_type": "user",
    },
}

SLACK_PATHS = {
    "access_token": "authed_user.access_token",
    "scope": "authed_user.scope",
    "success_flag": "ok",
    "error": "error",
}


@pytest.fixture(autouse=True)
def _identity_encryption(monkeypatch):
    fake = types.SimpleNamespace(encrypt=lambda v: v, decrypt=lambda v: v)
    monkeypatch.setattr("app.services.connector_service.encryption_service", fake)


class TestDig:
    def test_nested_and_missing(self):
        dig = connector_service._dig
        assert dig(SLACK_SUCCESS, "authed_user.access_token") == "xoxp-user-token"
        assert dig(SLACK_SUCCESS, "ok") is True
        assert dig(SLACK_SUCCESS, "authed_user.nope") is None
        assert dig(SLACK_SUCCESS, "access_token.deeper") is None  # str, not dict
        assert dig({}, "a.b.c") is None


class TestApplyPaths:
    def test_no_paths_standard_payload_unchanged(self):
        payload = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
        normalized, err = connector_service._apply_token_response_paths(payload, None)
        assert err is None
        assert normalized == payload

    def test_no_paths_rfc6749_error_detected(self):
        """Standard top-level error means failure even on HTTP 200."""
        normalized, err = connector_service._apply_token_response_paths(
            {"error": "invalid_grant", "error_description": "expired"}, None
        )
        assert normalized is None
        assert "invalid_grant" in err and "expired" in err

    def test_success_flag_false_surfaces_provider_error(self):
        normalized, err = connector_service._apply_token_response_paths(
            {"ok": False, "error": "invalid_code"}, SLACK_PATHS
        )
        assert normalized is None
        assert "invalid_code" in err

    def test_nested_paths_relocate_user_token(self):
        normalized, err = connector_service._apply_token_response_paths(
            SLACK_SUCCESS, SLACK_PATHS
        )
        assert err is None
        assert normalized["access_token"] == "xoxp-user-token"
        assert normalized["scope"] == "search:read"

    def test_configured_path_miss_does_not_fall_back_to_top_level(self):
        """A user-scope-only Slack response with no authed_user token must
        NOT silently store the top-level BOT token as the user token."""
        payload = {"ok": True, "access_token": "xoxb-bot-token", "authed_user": {"id": "U1"}}
        normalized, err = connector_service._apply_token_response_paths(
            payload, SLACK_PATHS
        )
        assert err is None
        assert "access_token" not in normalized


class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _FakeHttp:
    def __init__(self, resp):
        self._resp = resp

    async def post(self, url, data=None, headers=None, timeout=None):
        return self._resp


class TestPostTokenRequest:
    async def _post(self, monkeypatch, resp, paths=None):
        monkeypatch.setattr(connector_service, "_get_client", lambda: _FakeHttp(resp))
        return await connector_service._post_token_request(
            "https://slack.com/api/oauth.v2.access",
            {"grant_type": "authorization_code", "code": "c"},
            "cid",
            "csecret",
            "body",
            response_paths=paths,
        )

    async def test_http_200_provider_error_fails(self, monkeypatch):
        payload = await self._post(
            monkeypatch, _FakeResp(200, {"ok": False, "error": "invalid_code"}), SLACK_PATHS
        )
        assert payload is None

    async def test_success_returns_normalized_payload(self, monkeypatch):
        payload = await self._post(monkeypatch, _FakeResp(200, SLACK_SUCCESS), SLACK_PATHS)
        assert payload["access_token"] == "xoxp-user-token"

    async def test_non_200_still_fails(self, monkeypatch):
        payload = await self._post(
            monkeypatch, _FakeResp(500, {"whatever": 1}), SLACK_PATHS
        )
        assert payload is None

    async def test_standard_provider_regression(self, monkeypatch):
        """No configured paths: standard responses behave exactly as before."""
        std = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
        payload = await self._post(monkeypatch, _FakeResp(200, std), None)
        assert payload == std


class TestSlackUserTokenExpiry:
    def test_no_expiry_no_refresh_token_stays_forever_valid(self):
        """Pinning test from the issue: Slack user tokens don't expire and
        carry no refresh token — expires_at must stay None (valid until
        revoked), not get a synthetic TTL that would force a refresh that
        can never succeed."""
        normalized, _ = connector_service._apply_token_response_paths(
            SLACK_SUCCESS, SLACK_PATHS
        )
        row = types.SimpleNamespace(
            encrypted_access_token="",
            encrypted_refresh_token="",
            scope=None,
            token_type=None,
            expires_at=None,
        )
        connector_service._store_token_fields(row, normalized)
        assert row.encrypted_access_token == "xoxp-user-token"
        assert row.expires_at is None


class TestExchangePassesPaths:
    async def test_exchange_forwards_token_response_paths(self, monkeypatch):
        captured = {}

        async def fake_post(url, data, client_id, client_secret, method, response_paths=None):
            captured["response_paths"] = response_paths
            return {"access_token": "tok"}

        async def fake_secret(db, name, user_id):
            return "csecret"

        async def fake_row(db, connector_id, user_id):
            return types.SimpleNamespace(
                encrypted_access_token="",
                encrypted_refresh_token="",
                scope=None,
                token_type=None,
                expires_at=None,
            )

        monkeypatch.setattr(connector_service, "_post_token_request", fake_post)
        monkeypatch.setattr(connector_service, "_resolve_secret_value", fake_secret)
        monkeypatch.setattr(connector_service, "_get_token_row", fake_row)

        connector = types.SimpleNamespace(
            id="c1",
            namespace="default",
            name="slack",
            auth={
                "type": "oauth2_authorization_code",
                "token_url": "https://slack.com/api/oauth.v2.access",
                "client_id": "cid",
                "secret": "SLACK_SECRET",
                "token_response_paths": SLACK_PATHS,
            },
        )

        class _Db:
            def add(self, row):
                pass

            async def flush(self):
                pass

        ok = await connector_service.exchange_authorization_code(
            _Db(), connector, "u1", "code", "verifier"
        )
        assert ok is True
        assert captured["response_paths"] == SLACK_PATHS


class TestConfigRoundTrip:
    def test_camel_snake_round_trip_is_lossless(self):
        from app.services.resource_serializers import _camelize_token_response_paths

        cfg = TokenResponsePathsConfig(
            accessToken="authed_user.access_token",
            scope="authed_user.scope",
            successFlag="ok",
            error="error",
        )
        # config (camel) → stored (snake), as config-apply does
        stored = {
            snake: getattr(cfg, camel)
            for camel, snake in TOKEN_RESPONSE_PATH_FIELD_MAP
            if getattr(cfg, camel) is not None
        }
        assert stored == SLACK_PATHS

        # stored (snake) → export (camel), as the serializer does
        exported = _camelize_token_response_paths(stored)
        assert exported == {
            "accessToken": "authed_user.access_token",
            "scope": "authed_user.scope",
            "successFlag": "ok",
            "error": "error",
        }
