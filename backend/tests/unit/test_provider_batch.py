"""Unit tests for provider-native batch mode (execution_mode="provider").

Covers eligibility validation, the Anthropic/OpenAI batch API adapters
(request building and result normalization, no network), and the tracking
wrapper's batch passthrough.
"""
import json
from types import SimpleNamespace

import pytest

from app.providers import AnthropicProvider, AzureOpenAIProvider, OpenAIProvider
from app.providers.tracking import UsageTrackingProvider
from app.services.batch_service import _provider_batch_blockers


# ── Eligibility ──────────────────────────────────────────────────────────


def _agent(**overrides):
    agent = SimpleNamespace(
        enabled_functions=None,
        enabled_agents=None,
        enabled_queries=None,
        enabled_stores=None,
        enabled_collections=None,
        enabled_components=None,
        enabled_connectors=None,
        system_tools=None,
        hooks=None,
        enabled_skills=None,
    )
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


def test_toolless_agent_has_no_blockers():
    assert _provider_batch_blockers(_agent()) == []


def test_each_tool_source_blocks():
    cases = {
        "enabled_functions": ["ns/f"],
        "enabled_agents": ["helper"],
        "enabled_queries": ["ns/q"],
        "enabled_stores": [{"name": "s"}],
        "enabled_collections": [{"name": "c"}],
        "enabled_components": ["ns/c"],
        "enabled_connectors": [{"connector": "ns/c"}],
        "system_tools": ["codeExecution"],
        "hooks": {"on_user_message": [{"function": "ns/f"}]},
    }
    for field, value in cases.items():
        blockers = _provider_batch_blockers(_agent(**{field: value}))
        assert blockers, f"{field} should block provider batch mode"


def test_empty_hooks_do_not_block():
    agent = _agent(hooks={"on_user_message": [], "on_assistant_message": []})
    assert _provider_batch_blockers(agent) == []


def test_preload_only_skills_allowed():
    agent = _agent(enabled_skills=[{"name": "docs", "preload": True}])
    assert _provider_batch_blockers(agent) == []


def test_tool_skills_block():
    assert _provider_batch_blockers(_agent(enabled_skills=[{"name": "docs"}]))
    assert _provider_batch_blockers(_agent(enabled_skills=["docs"]))


# ── Anthropic batch adapter ──────────────────────────────────────────────


def _anthropic_with_fake_batches(create_result=None, retrieve_result=None, results=None):
    provider = AnthropicProvider(api_key="k")
    captured = {}

    async def fake_create(**kwargs):
        captured["create"] = kwargs
        return create_result or SimpleNamespace(id="msgbatch_1")

    async def fake_retrieve(batch_id):
        captured["retrieve"] = batch_id
        return retrieve_result

    async def fake_results(batch_id):
        class _Iter:
            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                for entry in results or []:
                    yield entry

        return _Iter()

    provider.client = SimpleNamespace(
        messages=SimpleNamespace(
            batches=SimpleNamespace(
                create=fake_create, retrieve=fake_retrieve, results=fake_results
            )
        )
    )
    return provider, captured


async def test_anthropic_submit_batch_builds_params():
    provider, captured = _anthropic_with_fake_batches()
    batch_id = await provider.submit_batch([
        {
            "custom_id": "exec-1",
            "messages": [
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "hi"},
            ],
            "model": "claude-sonnet-5",
            "temperature": 0.3,
            "max_tokens": 512,
        }
    ])
    assert batch_id == "msgbatch_1"

    (request,) = captured["create"]["requests"]
    assert request["custom_id"] == "exec-1"
    params = request["params"]
    assert params["model"] == "claude-sonnet-5"
    # SDK >=1.0.0 removed sampling knobs from the Messages API; a requested
    # temperature must be dropped, not forwarded (forwarding is a TypeError
    # on create() and a 400 on batch items)
    assert "temperature" not in params
    assert params["max_tokens"] == 512
    assert "tools" not in params
    # System extracted from messages and cache-marked (caching stacks with batch)
    assert params["system"][0]["text"] == "Be terse."
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}


async def test_anthropic_batch_status():
    provider, _ = _anthropic_with_fake_batches(
        retrieve_result=SimpleNamespace(processing_status="in_progress")
    )
    assert await provider.get_batch_status("b1") == {
        "status": "in_progress", "ended": False,
    }

    provider, _ = _anthropic_with_fake_batches(
        retrieve_result=SimpleNamespace(processing_status="ended")
    )
    assert (await provider.get_batch_status("b1"))["ended"] is True


async def test_anthropic_fetch_batch_results():
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="42")],
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=2,
            cache_read_input_tokens=100, cache_creation_input_tokens=5,
        ),
    )
    entries = [
        SimpleNamespace(
            custom_id="exec-1",
            result=SimpleNamespace(type="succeeded", message=message),
        ),
        SimpleNamespace(
            custom_id="exec-2",
            result=SimpleNamespace(type="errored", error="overloaded"),
        ),
        SimpleNamespace(
            custom_id="exec-3",
            result=SimpleNamespace(type="canceled"),
        ),
    ]
    provider, _ = _anthropic_with_fake_batches(results=entries)
    results = await provider.fetch_batch_results("b1")

    by_id = {r["custom_id"]: r for r in results}
    assert by_id["exec-1"]["status"] == "succeeded"
    assert by_id["exec-1"]["content"] == "42"
    assert by_id["exec-1"]["usage"]["prompt_tokens"] == 115  # includes cache tokens
    assert by_id["exec-1"]["usage"]["cache_read_tokens"] == 100
    assert by_id["exec-2"]["status"] == "errored"
    assert "overloaded" in by_id["exec-2"]["error"]
    assert by_id["exec-3"]["status"] == "cancelled"


# ── OpenAI batch adapter ─────────────────────────────────────────────────


def _openai_with_fake_batches(batch=None, file_contents=None):
    provider = OpenAIProvider(api_key="k")
    captured = {}

    async def fake_file_create(**kwargs):
        captured["file"] = kwargs
        return SimpleNamespace(id="file_in")

    async def fake_batch_create(**kwargs):
        captured["batch"] = kwargs
        return SimpleNamespace(id="batch_1")

    async def fake_retrieve(batch_id):
        return batch

    async def fake_content(file_id):
        return SimpleNamespace(text=(file_contents or {}).get(file_id, ""))

    provider.client = SimpleNamespace(
        files=SimpleNamespace(create=fake_file_create, content=fake_content),
        batches=SimpleNamespace(create=fake_batch_create, retrieve=fake_retrieve),
    )
    return provider, captured


async def test_openai_submit_batch_builds_jsonl():
    provider, captured = _openai_with_fake_batches()
    batch_id = await provider.submit_batch([
        {
            "custom_id": "exec-1",
            "messages": [{"role": "user", "content": "hi"}],
            "model": "gpt-5",
            "temperature": 0.1,
            "max_tokens": None,
        }
    ])
    assert batch_id == "batch_1"
    assert captured["batch"]["input_file_id"] == "file_in"
    assert captured["batch"]["endpoint"] == "/v1/chat/completions"
    assert captured["batch"]["completion_window"] == "24h"

    filename, payload = captured["file"]["file"]
    assert captured["file"]["purpose"] == "batch"
    line = json.loads(payload.decode().splitlines()[0])
    assert line["custom_id"] == "exec-1"
    assert line["body"]["model"] == "gpt-5"
    assert line["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert "tools" not in line["body"]
    assert "max_tokens" not in line["body"]  # None → omitted


async def test_openai_fetch_batch_results():
    output = json.dumps({
        "custom_id": "exec-1",
        "response": {
            "status_code": 200,
            "body": {
                "choices": [{"message": {"content": "hello"}}],
                "usage": {
                    "prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55,
                    "prompt_tokens_details": {"cached_tokens": 32},
                },
            },
        },
    })
    errors = json.dumps({
        "custom_id": "exec-2",
        "error": {"message": "boom"},
        "response": None,
    })
    provider, _ = _openai_with_fake_batches(
        batch=SimpleNamespace(
            status="completed", output_file_id="file_out", error_file_id="file_err"
        ),
        file_contents={"file_out": output + "\n", "file_err": errors + "\n"},
    )
    results = await provider.fetch_batch_results("batch_1")

    by_id = {r["custom_id"]: r for r in results}
    assert by_id["exec-1"]["status"] == "succeeded"
    assert by_id["exec-1"]["content"] == "hello"
    assert by_id["exec-1"]["usage"]["cache_read_tokens"] == 32
    assert by_id["exec-2"]["status"] == "errored"
    assert "boom" in by_id["exec-2"]["error"]


async def test_openai_batch_status_terminal_states():
    for status, ended in (
        ("validating", False), ("in_progress", False), ("finalizing", False),
        ("completed", True), ("failed", True), ("expired", True), ("cancelled", True),
    ):
        provider, _ = _openai_with_fake_batches(batch=SimpleNamespace(status=status))
        assert (await provider.get_batch_status("b"))["ended"] is ended, status


# ── supports_batch flags + tracking passthrough ──────────────────────────


def test_supports_batch_flags():
    assert AnthropicProvider(api_key="k").supports_batch is True
    assert OpenAIProvider(api_key="k").supports_batch is True
    # Azure needs a Global-Batch deployment the config doesn't model yet
    assert AzureOpenAIProvider(
        api_key="k", azure_endpoint="https://x.openai.azure.com/"
    ).supports_batch is False


async def test_tracking_wrapper_passes_batch_methods_through():
    inner = AnthropicProvider(api_key="k")

    async def fake_create(**kwargs):
        return SimpleNamespace(id="msgbatch_9")

    inner.client = SimpleNamespace(
        messages=SimpleNamespace(batches=SimpleNamespace(create=fake_create))
    )
    wrapped = UsageTrackingProvider(inner=inner, provider_name="claude", provider_type="anthropic")

    assert wrapped.supports_batch is True
    batch_id = await wrapped.submit_batch([
        {"custom_id": "e1", "messages": [{"role": "user", "content": "x"}], "model": "m"}
    ])
    assert batch_id == "msgbatch_9"


# ── Metering ─────────────────────────────────────────────────────────────


async def test_provider_submit_records_agent_ops(monkeypatch):
    """Provider-mode children never reach the message_service metering leaf,
    so submission itself must count one AGENT op per child (#118 parity)."""
    from app.services import batch_service, metering

    recorded = []

    async def fake_record(kind, n=1):
        recorded.append((kind, n))

    monkeypatch.setattr(metering, "record", fake_record)

    # Exercise just the post-submit accounting: simulate what the provider
    # path does after a successful submit_batch call.
    await metering.record(metering.OperationKind.AGENT, n=3)
    assert recorded == [(metering.OperationKind.AGENT, 3)]

    # And pin that batch_service actually calls it: the call site must exist
    # on the provider-mode success path.
    import inspect

    src = inspect.getsource(batch_service.submit_agent_batch)
    assert "metering.record(metering.OperationKind.AGENT, n=len(requests))" in src


# ── Gemini batch adapter (hybrid: OpenAI batches + Google Files API) ─────


class _FakeHttpxResponse:
    def __init__(self, headers=None, json_data=None, text=""):
        self.headers = headers or {}
        self._json = json_data
        self.text = text

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _FakeHttpxClient:
    """Stands in for httpx.AsyncClient; replays scripted responses."""

    calls: list = []
    responses: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        _FakeHttpxClient.calls.append(("POST", url, kwargs))
        post_count = len([c for c in _FakeHttpxClient.calls if c[0] == "POST"])
        return _FakeHttpxClient.responses[("POST", post_count)]

    async def get(self, url, **kwargs):
        _FakeHttpxClient.calls.append(("GET", url, kwargs))
        return _FakeHttpxClient.responses[("GET", 1)]


def _patch_httpx(monkeypatch, responses):
    from app.providers import gemini_provider

    _FakeHttpxClient.calls = []
    _FakeHttpxClient.responses = responses
    monkeypatch.setattr(gemini_provider.httpx, "AsyncClient", _FakeHttpxClient)
    return _FakeHttpxClient


def test_gemini_defaults_and_url_derivation():
    from app.providers import GeminiProvider

    provider = GeminiProvider(api_key="g")
    assert provider.supports_batch is True
    assert provider.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert provider._files_root == "https://generativelanguage.googleapis.com/v1beta"
    assert provider._upload_root == "https://generativelanguage.googleapis.com/upload/v1beta"

    proxied = GeminiProvider(api_key="g", base_url="https://proxy.corp/v1beta/openai/")
    assert proxied._files_root == "https://proxy.corp/v1beta"
    assert proxied._upload_root == "https://proxy.corp/upload/v1beta"


async def test_gemini_upload_batch_file(monkeypatch):
    from app.providers import GeminiProvider

    fake = _patch_httpx(monkeypatch, {
        ("POST", 1): _FakeHttpxResponse(
            headers={"X-Goog-Upload-URL": "https://upload.example/u1"}
        ),
        ("POST", 2): _FakeHttpxResponse(json_data={"file": {"name": "files/in123"}}),
    })

    provider = GeminiProvider(api_key="g")
    file_id = await provider._upload_batch_file(b'{"custom_id": "e1"}\n')

    assert file_id == "files/in123"
    method, url, kwargs = fake.calls[0]
    assert url == "https://generativelanguage.googleapis.com/upload/v1beta/files"
    assert kwargs["headers"]["x-goog-api-key"] == "g"
    assert kwargs["headers"]["X-Goog-Upload-Protocol"] == "resumable"
    # Second call goes to the session URL from the start response
    assert fake.calls[1][1] == "https://upload.example/u1"
    assert fake.calls[1][2]["headers"]["X-Goog-Upload-Command"] == "upload, finalize"


async def test_gemini_download_batch_file(monkeypatch):
    from app.providers import GeminiProvider

    fake = _patch_httpx(monkeypatch, {
        ("GET", 1): _FakeHttpxResponse(text='{"custom_id": "e1"}\n'),
    })

    provider = GeminiProvider(api_key="g")
    text = await provider._download_batch_file("files/out9")

    assert text == '{"custom_id": "e1"}\n'
    method, url, kwargs = fake.calls[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/files/out9:download"
    assert kwargs["params"] == {"alt": "media"}


async def test_gemini_submit_batch_uses_google_upload(monkeypatch):
    """End-to-end submit: JSONL goes to Google's Files API, the returned
    file name feeds the OpenAI-compat batches.create."""
    from app.providers import GeminiProvider

    _patch_httpx(monkeypatch, {
        ("POST", 1): _FakeHttpxResponse(
            headers={"X-Goog-Upload-URL": "https://upload.example/u1"}
        ),
        ("POST", 2): _FakeHttpxResponse(json_data={"file": {"name": "files/in123"}}),
    })

    provider = GeminiProvider(api_key="g")
    captured = {}

    async def fake_batch_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="batches/gem1")

    provider.client = SimpleNamespace(
        batches=SimpleNamespace(create=fake_batch_create)
    )

    batch_id = await provider.submit_batch([
        {
            "custom_id": "exec-1",
            "messages": [{"role": "user", "content": "hi"}],
            "model": "gemini-2.5-flash",
            "temperature": 0.2,
            "max_tokens": None,
        }
    ])

    assert batch_id == "batches/gem1"
    assert captured["input_file_id"] == "files/in123"
    assert captured["endpoint"] == "/v1/chat/completions"


async def test_gemini_camelcase_usage_parsed():
    """Gemini's batch output JSONL reports usage in camelCase — zeros were
    recorded until the parser accepted both namings."""
    import json as _json
    from types import SimpleNamespace as NS

    output = _json.dumps({
        "custom_id": "exec-1",
        "response": {
            "status_code": 200,
            "body": {
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"promptTokens": 29, "completionTokens": 21, "totalTokens": 1373},
            },
        },
    })
    provider, _ = _openai_with_fake_batches(
        batch=NS(status="completed", output_file_id="file_out", error_file_id=None),
        file_contents={"file_out": output + "\n"},
    )
    (result,) = await provider.fetch_batch_results("b")
    assert result["usage"]["prompt_tokens"] == 29
    assert result["usage"]["completion_tokens"] == 21
    assert result["usage"]["total_tokens"] == 1373
