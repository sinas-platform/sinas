"""Per-run provider credentials: the provider side (no database).

Pinned here: a provider's `config.run_credential` is checked when it is
written (keys, header names, prefix, provider types `header: null` can't
work for); on the wire, every provider type presents the credential where it
should — in place of its own key with `header: null` (exactly one value, the
run's, over the SDK's default), or in the named header with the configured
key left as it is — and nothing private leaves the process; the value never
sits in an SDK's request options; and it never shows in an error, the
envelope, a usage row or a log line, even when a gateway echoes it back.

No network calls: providers talk to an in-memory transport.
"""
import asyncio
import json
import logging

import anthropic
import httpx
import openai
import pytest

from app.middleware.request_logger import redact_body
from app.providers import (
    AnthropicProvider,
    AzureOpenAIProvider,
    GeminiProvider,
    MistralProvider,
    OllamaProvider,
    OpenAIProvider,
    ollama_provider,
)
from app.providers import run_credentials as rc
from app.providers import tracking
from app.providers.anthropic_provider import anthropic_client
from app.providers.clients import BUDGET_RULES_HEADER, RUN_CREDENTIAL_HEADER
from app.providers.errors import (
    CODES,
    INVALID_REQUEST,
    PROVIDER_CREDENTIAL_REQUIRED,
    LLMError,
    install_error_handler,
)
from app.providers.openai_provider import openai_client
from app.providers.tracking import UsageTrackingProvider
from app.schemas.spec.llm_provider import LLMProviderSpec

SENTINEL = "rc-sentinel-7f3a9c2e51d84b06"
SERVICE_KEY = "service-key-0001"
MESSAGES = [{"role": "user", "content": "hi"}]


# ── settings ────────────────────────────────────────────────────────────────


def test_settings_are_checked_and_completed():
    assert rc.validate_settings({"enabled": True}, "openai") == {
        "enabled": True, "header": None, "prefix": "", "required": False,
    }
    assert rc.validate_settings(
        {"enabled": True, "header": "X-Budget-Key", "prefix": "Bearer ", "required": True}, "ollama"
    ) == {"enabled": True, "header": "X-Budget-Key", "prefix": "Bearer ", "required": True}
    assert rc.validate_settings(None, "openai") is None


@pytest.mark.parametrize(
    "value, problem",
    [
        ("yes", "object"),
        ({"enabled": True, "headr": "x"}, "unknown key"),
        ({"enabled": "true"}, "true or false"),
        ({"enabled": True, "required": 1}, "true or false"),
        ({"required": True}, "needs run_credential.enabled"),
        ({"enabled": True, "header": "bad name"}, "header name"),
        ({"enabled": True, "header": 5}, "header name"),
        ({"enabled": True, "header": "Host"}, "reserved"),
        ({"enabled": True, "header": "Content-Type"}, "reserved"),
        ({"enabled": True, "header": "traceparent"}, "reserved"),
        ({"enabled": True, "header": "x-sinas-anything"}, "reserved"),
        ({"enabled": True, "header": "anthropic-version"}, "reserved"),
        ({"enabled": True, "prefix": "Bearer "}, "named header only"),
        ({"enabled": True, "header": "X-K", "prefix": "a\nb"}, "printable"),
        ({"enabled": True, "header": "X-K", "prefix": "x" * 65}, "at most"),
    ],
)
def test_invalid_settings_are_refused(value, problem):
    with pytest.raises(ValueError, match=problem):
        rc.validate_settings(value, "openai")


@pytest.mark.parametrize("name", ["Authorization", "x-api-key", "api-key", "X-Goog-Api-Key", "Proxy-Authorization"])
def test_a_credential_header_may_be_named_unlike_in_templates(name):
    # Reserved for identity header templates; the point of a run credential.
    assert rc.validate_settings({"enabled": True, "header": name}, "anthropic")["header"] == name


def test_header_null_needs_a_provider_type_with_a_key_of_its_own():
    with pytest.raises(ValueError, match="can't be null for provider type 'ollama'"):
        rc.validate_settings({"enabled": True}, "ollama")
    for kind in ("openai", "azure", "azure_openai", "anthropic", "gemini", "mistral"):
        assert rc.validate_settings({"enabled": True}, kind)
    # Disabled: nothing to check against the type.
    assert rc.validate_settings({"enabled": False}, "ollama")


def test_a_header_is_a_template_or_the_credential_not_both():
    with pytest.raises(ValueError, match="also a header template"):
        rc.validate_settings({"enabled": True, "header": "X-Key"}, "openai", {"x-key": "{{user.id}}"})


def test_the_provider_spec_checks_run_credential():
    spec = LLMProviderSpec(
        name="gw", provider_type="openai", config={"run_credential": {"enabled": True, "required": True}}
    )
    assert spec.config["run_credential"] == {
        "enabled": True, "header": None, "prefix": "", "required": True,
    }
    with pytest.raises(ValueError, match="can't be null"):
        LLMProviderSpec(name="gw", provider_type="ollama", config={"run_credential": {"enabled": True}})
    with pytest.raises(ValueError, match="unknown key"):
        LLMProviderSpec(name="gw", provider_type="openai", config={"run_credential": {"on": True}})
    with pytest.raises(ValueError, match="also a header template"):
        LLMProviderSpec(
            name="gw", provider_type="openai",
            config={"headers": {"x-key": "{{user.id}}"}, "run_credential": {"enabled": True, "header": "X-Key"}},
        )
    assert "run_credential" not in LLMProviderSpec(
        name="gw", provider_type="openai", config={"run_credential": None}
    ).config


def test_a_stored_setting_that_no_longer_validates_never_falls_back_when_it_asked_not_to():
    assert rc.settings_of({"run_credential": {"enabled": True, "bogus": 1}}, "openai").enabled is False
    broken_required = rc.settings_of({"run_credential": {"enabled": True, "required": True, "x": 1}}, "openai")
    assert broken_required.enabled is False and broken_required.required is True
    assert rc.settings_of({}, "openai") == rc.RunCredentialSettings()


@pytest.mark.parametrize("value", [None, "", "   ", " lead", "trail ", "a b", "tab\tx", "nl\nx", "café", 5, "x" * 4097])
def test_a_credential_must_be_a_header_safe_value(value):
    with pytest.raises(ValueError) as caught:
        rc.check_credential(value)
    if isinstance(value, str) and value.strip():
        assert value.strip() not in str(caught.value)  # never quoted


@pytest.mark.parametrize("value", [0, -1, 2592001, 1.5, "60", True])
def test_expires_in_out_of_range_is_refused(value):
    with pytest.raises(ValueError, match="expires_in"):
        rc.check_expires_in(value)


def test_expires_in_defaults_to_a_day():
    assert rc.check_expires_in(None) == 86400
    assert rc.check_expires_in(1) == 1 and rc.check_expires_in(2592000) == 2592000


def test_credential_headers_per_provider_type():
    on = rc.RunCredentialSettings(enabled=True)
    assert rc.credential_headers(on, "openai", "k") == {"Authorization": "Bearer k"}
    assert rc.credential_headers(on, "gemini", "k") == {"Authorization": "Bearer k"}
    assert rc.credential_headers(on, "mistral", "k") == {"Authorization": "Bearer k"}
    assert rc.credential_headers(on, "anthropic", "k") == {"X-Api-Key": "k"}
    assert rc.credential_headers(on, "azure", "k") == {"api-key": "k"}
    named = rc.RunCredentialSettings(enabled=True, header="X-Budget-Key", prefix="sk-")
    assert rc.credential_headers(named, "ollama", "k") == {"X-Budget-Key": "sk-k"}


def test_the_holder_never_shows_the_value():
    holder = rc.RunCredentialHeaders({"Authorization": f"Bearer {SENTINEL}"}, SENTINEL)
    assert SENTINEL not in repr(holder)
    assert holder.placeholders() == {"Authorization": rc.PLACEHOLDER}
    assert set(holder.secrets()) == {SENTINEL, f"Bearer {SENTINEL}"}
    assert rc.holder_for(holder.ref) is holder


# ── on the wire: every provider type ───────────────────────────────────────

_HttpxClient = httpx.AsyncClient


class _Wire:
    def __init__(self, status=200, body=None):
        self.sent = []
        self.status = status
        self.body = body

    def _answer(self, request, response_class):
        self.sent.append(request)
        if self.status != 200:
            return response_class(self.status, json=self.body or {"error": {"message": "refused"}})
        payload = json.loads(request.content or b"{}")
        if "/api/chat" in request.url.path:  # ollama
            if payload.get("stream"):
                line = json.dumps({"message": {"content": "ok"}, "done": True, "done_reason": "stop"})
                return response_class(200, content=(line + "\n").encode())
            return response_class(200, json={"message": {"content": "ok"}, "done": True})
        if "/messages" in request.url.path:  # anthropic
            if payload.get("stream"):
                return response_class(400, json={"error": {"message": "no stream in this test"}})
            return response_class(200, json={
                "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
                "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })
        if payload.get("stream"):
            chunk = {
                "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            }
            text = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
            return response_class(200, content=text.encode(), headers={"content-type": "text/event-stream"})
        return response_class(200, json={
            "id": "c", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def client(self, **kwargs):
        return _HttpxClient(transport=httpx.MockTransport(lambda r: self._answer(r, httpx.Response)), **kwargs)

    def client2(self):
        import httpx2

        return httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: self._answer(r, httpx2.Response)))


def _openai_like(cls, wire):
    provider = cls(api_key=SERVICE_KEY, base_url="http://gateway.test/v1")
    provider.client = openai_client(
        api_key=SERVICE_KEY, base_url="http://gateway.test/v1", http_client=wire.client(), max_retries=0
    )
    return provider


def _azure(wire, mp):
    provider = AzureOpenAIProvider(api_key=SERVICE_KEY, azure_endpoint="http://gateway.test")
    provider.client = openai_client(
        openai.AsyncAzureOpenAI, api_key=SERVICE_KEY, api_version=provider.api_version,
        azure_endpoint="http://gateway.test", http_client=wire.client(), max_retries=0,
    )
    return provider


def _anthropic(wire, mp):
    provider = AnthropicProvider(api_key=SERVICE_KEY, base_url="http://gateway.test")
    provider.client = anthropic_client(
        api_key=SERVICE_KEY, base_url="http://gateway.test", http_client=wire.client2(), max_retries=0
    )
    return provider


def _ollama(wire, mp):
    mp.setattr(ollama_provider.httpx, "AsyncClient", wire.client)
    return OllamaProvider(base_url="http://gateway.test")


PROVIDERS = {
    "openai": lambda wire, mp: _openai_like(OpenAIProvider, wire),
    "azure": _azure,
    "gemini": lambda wire, mp: _openai_like(GeminiProvider, wire),
    "mistral": lambda wire, mp: _openai_like(MistralProvider, wire),
    "anthropic": _anthropic,
    "ollama": _ollama,
}
# Where each type sends its own key, and how (header: null replaces it).
NATIVE = {
    "openai": ("authorization", f"Bearer {SERVICE_KEY}", f"Bearer {SENTINEL}"),
    "azure": ("api-key", SERVICE_KEY, SENTINEL),
    "gemini": ("authorization", f"Bearer {SERVICE_KEY}", f"Bearer {SENTINEL}"),
    "mistral": ("authorization", f"Bearer {SERVICE_KEY}", f"Bearer {SENTINEL}"),
    "anthropic": ("x-api-key", SERVICE_KEY, SENTINEL),
}


def _attach(provider, kind, settings):
    provider.run_credential = rc.RunCredentialHeaders(
        rc.credential_headers(settings, kind, SENTINEL), SENTINEL
    )


async def _complete(provider):
    await provider.complete(MESSAGES, model="m", max_tokens=1)


async def _stream(provider):
    async for _ in provider.stream(MESSAGES, model="m", max_tokens=1):
        pass


def _run(provider, call):
    async def go():
        try:
            await call(provider)
        except (openai.BadRequestError, anthropic.BadRequestError, httpx.HTTPStatusError):
            pass

    asyncio.run(go())


def _no_private_headers(request):
    assert RUN_CREDENTIAL_HEADER not in request.headers
    assert BUDGET_RULES_HEADER not in request.headers
    assert rc.PLACEHOLDER not in str(request.headers)


@pytest.mark.parametrize("call", [_complete, _stream], ids=["complete", "stream"])
@pytest.mark.parametrize("kind", NATIVE)
def test_header_null_replaces_the_providers_own_key(kind, call, monkeypatch):
    wire = _Wire()
    provider = PROVIDERS[kind](wire, monkeypatch)
    provider.budget_rules = [{"status": 403}]
    _attach(provider, kind, rc.RunCredentialSettings(enabled=True))
    _run(provider, call)
    assert wire.sent
    name, _, expected = NATIVE[kind]
    for request in wire.sent:
        # One value, the run's: the SDK's default key is gone.
        assert request.headers.get_list(name) == [expected]
        assert SERVICE_KEY not in str(request.headers)
        _no_private_headers(request)


@pytest.mark.parametrize("call", [_complete, _stream], ids=["complete", "stream"])
@pytest.mark.parametrize("kind", PROVIDERS)
def test_a_named_header_carries_it_and_the_configured_key_stays(kind, call, monkeypatch):
    wire = _Wire()
    provider = PROVIDERS[kind](wire, monkeypatch)
    provider.identity_headers = {"x-customer-id": "org-42"}
    _attach(provider, kind, rc.RunCredentialSettings(enabled=True, header="X-Budget-Key", prefix="Bearer "))
    _run(provider, call)
    assert wire.sent
    for request in wire.sent:
        assert request.headers.get_list("x-budget-key") == [f"Bearer {SENTINEL}"]
        assert request.headers["x-customer-id"] == "org-42"
        if kind in NATIVE:
            name, own, _ = NATIVE[kind]
            assert request.headers.get_list(name) == [own]
        _no_private_headers(request)


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_a_named_credential_header_replaces_an_sdk_default_of_that_name(kind, monkeypatch):
    # Anthropic behind a gateway that reads Authorization: the SDK's
    # x-api-key (the configured key) stays, Authorization is the run's.
    wire = _Wire()
    provider = PROVIDERS[kind](wire, monkeypatch)
    _attach(provider, kind, rc.RunCredentialSettings(enabled=True, header="authorization", prefix="Bearer "))
    _run(provider, _complete)
    (request,) = wire.sent
    assert request.headers.get_list("authorization") == [f"Bearer {SENTINEL}"]


@pytest.mark.parametrize("kind", ["openai", "anthropic", "azure"])
def test_the_value_never_sits_in_the_sdks_request_options(kind, monkeypatch, caplog):
    # The SDKs log their request options (extra headers included) at DEBUG.
    wire = _Wire()
    provider = PROVIDERS[kind](wire, monkeypatch)
    _attach(provider, kind, rc.RunCredentialSettings(enabled=True))
    with caplog.at_level(logging.DEBUG):
        _run(provider, _complete)
    assert wire.sent
    assert SENTINEL not in caplog.text


def test_a_request_whose_credential_is_gone_fails_closed(monkeypatch):
    wire = _Wire()
    provider = PROVIDERS["openai"](wire, monkeypatch)
    _attach(provider, "openai", rc.RunCredentialSettings(enabled=True))
    rc._holders.pop(provider.run_credential.ref)  # as if the holder was collected
    with pytest.raises(Exception):
        asyncio.run(_complete(provider))
    assert not wire.sent  # nothing went out: neither the placeholder nor the configured key


def test_without_a_run_credential_nothing_changes(monkeypatch):
    wire = _Wire()
    provider = PROVIDERS["openai"](wire, monkeypatch)
    _run(provider, _complete)
    (request,) = wire.sent
    assert request.headers.get_list("authorization") == [f"Bearer {SERVICE_KEY}"]
    _no_private_headers(request)


# ── redaction ───────────────────────────────────────────────────────────────


class _Echo(OpenAIProvider):
    """A gateway that quotes the key it was sent in its error."""


@pytest.mark.parametrize("status", [400, 401, 402, 429, 500])
def test_an_echoed_credential_never_leaves_in_the_error_or_the_usage_row(status, monkeypatch, caplog):
    body = {"error": {"message": f"key Bearer {SENTINEL} was refused ({SENTINEL})", "type": "x"}}
    wire = _Wire(status=status, body=body)
    inner = PROVIDERS["openai"](wire, monkeypatch)
    _attach(inner, "openai", rc.RunCredentialSettings(enabled=True))
    rows = []

    async def fake_insert(values):
        rows.append(values)
        return True

    monkeypatch.setattr(tracking, "_insert_usage", fake_insert)
    provider = UsageTrackingProvider(inner, provider_name="gw", provider_type="openai")

    async def go():
        try:
            await provider.complete(MESSAGES, model="m", max_tokens=1)
        except LLMError as e:
            logging.getLogger("test").exception("call failed")
            return e
        finally:
            await asyncio.sleep(0)

    with caplog.at_level(logging.DEBUG):
        error = asyncio.run(go())
    assert isinstance(error, LLMError)
    assert SENTINEL not in str(error)
    assert SENTINEL not in json.dumps(error.to_dict())
    assert SENTINEL not in json.dumps(error.event_fields())
    assert error.__cause__ is None and error.__suppress_context__
    assert SENTINEL not in caplog.text
    (row,) = rows
    assert SENTINEL not in (row["error"] or "")
    assert row["run_credential"] is True


def test_a_successful_call_records_that_a_run_credential_was_used_never_which(monkeypatch):
    wire = _Wire()
    inner = PROVIDERS["openai"](wire, monkeypatch)
    _attach(inner, "openai", rc.RunCredentialSettings(enabled=True))
    rows = []

    async def fake_insert(values):
        rows.append(values)
        return True

    monkeypatch.setattr(tracking, "_insert_usage", fake_insert)
    provider = UsageTrackingProvider(inner, provider_name="gw", provider_type="openai")

    async def go():
        await provider.complete(MESSAGES, model="m", max_tokens=1)
        await asyncio.sleep(0)

    asyncio.run(go())
    (row,) = rows
    assert row["run_credential"] is True
    assert SENTINEL not in json.dumps(row, default=str)

    rows.clear()
    plain = UsageTrackingProvider(PROVIDERS["openai"](_Wire(), monkeypatch), provider_name="gw")
    asyncio.run(_complete(plain))
    assert rows and rows[0]["run_credential"] is False


def test_ollama_error_logs_are_scrubbed(monkeypatch, caplog):
    wire = _Wire(status=400, body={"error": f"bad key {SENTINEL}"})
    provider = PROVIDERS["ollama"](wire, monkeypatch)
    _attach(provider, "ollama", rc.RunCredentialSettings(enabled=True, header="X-Budget-Key"))
    with caplog.at_level(logging.ERROR):
        _run(provider, lambda p: p.complete(MESSAGES, model="m", max_tokens=1))
    assert wire.sent and "Response:" in caplog.text
    assert SENTINEL not in caplog.text


def test_request_logs_never_hold_a_credential():
    body = {
        "content": "hi",
        "provider_credentials": {"gw": {"credential": SENTINEL, "expires_in": 60}, "other": None},
    }
    assert SENTINEL not in json.dumps(redact_body(body))
    assert SENTINEL not in json.dumps(redact_body({"provider_credentials": {"gw": SENTINEL}}))
    assert SENTINEL not in json.dumps(redact_body({"provider_credentials": [SENTINEL]}))
    assert SENTINEL not in json.dumps(redact_body({"credential": SENTINEL}))


# ── the error code ──────────────────────────────────────────────────────────


def test_provider_credential_required_is_a_400_never_retried():
    assert CODES[PROVIDER_CREDENTIAL_REQUIRED] == (400, False)
    error = LLMError(PROVIDER_CREDENTIAL_REQUIRED, "missing")
    assert error.http_status == 400 and error.retryable is False
    assert error.to_dict() == {
        "detail": "provider_credential_required: missing", "code": PROVIDER_CREDENTIAL_REQUIRED,
        "retryable": False,
    }


def test_invalid_request_can_be_the_callers_400():
    assert LLMError(INVALID_REQUEST, "upstream").http_status == 502
    mine = LLMError(INVALID_REQUEST, "bad provider_credentials", status=400)
    assert mine.http_status == 400 and mine.to_dict()["code"] == INVALID_REQUEST
    assert mine.to_dict()["retryable"] is False
    scrubbed = LLMError(INVALID_REQUEST, f"x {SENTINEL}", status=400).scrubbed([SENTINEL])
    assert scrubbed.http_status == 400 and SENTINEL not in str(scrubbed)


def test_the_handler_answers_with_the_override_status():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    api = FastAPI()
    install_error_handler(api)

    @api.get("/x")
    async def x():
        raise LLMError(INVALID_REQUEST, "nope", status=400)

    r = TestClient(api).get("/x")
    assert r.status_code == 400
    assert r.json() == {"detail": "invalid_request: nope", "code": INVALID_REQUEST, "retryable": False}
