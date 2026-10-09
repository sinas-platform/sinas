"""Per-agent control over how much an Anthropic model thinks (effort).

On current Claude models thinking is ON by default even when no `thinking`
parameter is sent, and `output_config.effort` is the only control over how
much the model thinks and spends: `thinking: {type: "enabled", budget_tokens}`
is rejected with a 400 on Claude Sonnet 5 / Opus 5 and later, and
`{type: "disabled"}` on Opus 5.5 and Sonnet 5.5. Sinas never sent effort, so
every Anthropic agent thought at the model's default (`high` on most) with no
way to turn it down — which was blocking a client whose research agents spent
far more on thinking than their tasks needed.

These pin: the override is validated, it reaches every request path an agent
can take (first turn, streamed tool follow-ups, provider batches), it merges
into output_config rather than replacing it, and it never reaches a provider
that has no such setting.
"""

from types import SimpleNamespace

import pytest

from app.providers import AnthropicProvider, OpenAIProvider
from app.providers.factory import (
    EFFORT_LEVELS,
    _apply_overrides,
    validate_provider_overrides,
)


# --------------------------------------------------------------- validation


class TestValidation:
    @pytest.mark.parametrize("level", sorted(EFFORT_LEVELS))
    def test_every_documented_level_is_accepted(self, level):
        assert validate_provider_overrides({"effort": level}) == []

    def test_the_levels_are_exactly_the_api_ones(self):
        """A typo'd level would reach the API and 400 every request."""
        assert EFFORT_LEVELS == {"low", "medium", "high", "xhigh", "max"}

    @pytest.mark.parametrize(
        "value", ["minimal", "none", "off", "LOW", "", "extra_high"]
    )
    def test_unknown_levels_are_rejected_with_the_allowed_list(self, value):
        [error] = validate_provider_overrides({"effort": value})
        assert "effort" in error
        assert "low" in error and "max" in error

    @pytest.mark.parametrize("value", [1, True, None, ["low"], {"level": "low"}])
    def test_non_strings_are_rejected(self, value):
        assert validate_provider_overrides({"effort": value}) != []

    def test_combines_with_prompt_caching(self):
        assert validate_provider_overrides({"effort": "low", "prompt_caching": False}) == []


# ------------------------------------------------------------- application


class TestApplication:
    def test_sets_effort_on_an_anthropic_provider(self):
        provider = AnthropicProvider(api_key="k")
        assert provider.effort is None  # default: send nothing
        _apply_overrides(provider, {"effort": "low"})
        assert provider.effort == "low"

    def test_ignored_by_providers_without_the_setting(self):
        provider = OpenAIProvider(api_key="k")
        _apply_overrides(provider, {"effort": "low"})
        assert not hasattr(provider, "effort")

    def test_an_invalid_stored_value_never_reaches_the_provider(self):
        """Rows written before validation existed, or edited in the DB, are
        re-checked at use rather than trusted."""
        provider = AnthropicProvider(api_key="k")
        _apply_overrides(provider, {"effort": "ultra"})
        assert provider.effort is None

    def test_prompt_caching_still_applies(self):
        """The spec shape changed for effort; the existing key must not regress."""
        provider = AnthropicProvider(api_key="k", enable_prompt_caching=True)
        _apply_overrides(provider, {"prompt_caching": False, "effort": "medium"})
        assert provider.enable_prompt_caching is False
        assert provider.effort == "medium"


# ------------------------------------------------ what reaches the request


def _usage():
    return SimpleNamespace(
        input_tokens=10, output_tokens=5,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )


def _capturing_provider(effort):
    provider = AnthropicProvider(api_key="k", enable_prompt_caching=False)
    provider.effort = effort
    captured = {}

    async def fake_create(**params):
        captured["create"] = params
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=_usage(),
            stop_reason="end_turn",
        )

    class _EmptyStream:
        def __init__(self, params):
            captured["stream"] = params

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    provider.client = SimpleNamespace(
        messages=SimpleNamespace(create=fake_create, stream=lambda **p: _EmptyStream(p))
    )
    return provider, captured


MESSAGES = [{"role": "user", "content": "hello"}]


class TestRequestParams:
    async def test_complete_sends_effort(self):
        provider, captured = _capturing_provider("low")
        await provider.complete(messages=MESSAGES, model="claude-sonnet-5")
        assert captured["create"]["output_config"] == {"effort": "low"}

    async def test_stream_sends_effort(self):
        """Streaming is the main chat path and every tool follow-up."""
        provider, captured = _capturing_provider("medium")
        async for _ in provider.stream(messages=MESSAGES, model="claude-sonnet-5"):
            pass
        assert captured["stream"]["output_config"] == {"effort": "medium"}

    def test_batch_items_send_effort(self):
        provider, _ = _capturing_provider("low")
        params = provider._build_batch_params(
            {"messages": MESSAGES, "model": "claude-sonnet-5"}
        )
        assert params["output_config"] == {"effort": "low"}

    async def test_no_override_sends_no_output_config(self):
        """Unset must mean the model's own default — not an explicit value, and
        not an empty object the API might reject."""
        provider, captured = _capturing_provider(None)
        await provider.complete(messages=MESSAGES, model="claude-haiku-4-5")
        assert "output_config" not in captured["create"]

        provider, captured = _capturing_provider(None)
        async for _ in provider.stream(messages=MESSAGES, model="claude-haiku-4-5"):
            pass
        assert "output_config" not in captured["stream"]

    def test_effort_merges_into_an_existing_output_config(self):
        """output_config also carries structured-output `format`; setting one
        must never erase the other."""
        provider = AnthropicProvider(api_key="k")
        provider.effort = "low"
        params = {"output_config": {"format": {"type": "json_schema"}}}
        provider._apply_output_config(params)
        assert params["output_config"] == {
            "format": {"type": "json_schema"},
            "effort": "low",
        }

    async def test_no_thinking_parameter_is_ever_sent(self):
        """The trap this avoids: `budget_tokens` 400s on the models the client
        runs, and `disabled` 400s on Opus 5.5 / Sonnet 5.5. Effort is the
        control; thinking stays whatever the model defaults to."""
        provider, captured = _capturing_provider("low")
        await provider.complete(messages=MESSAGES, model="claude-sonnet-5")
        assert "thinking" not in captured["create"]
