"""Per-agent provider overrides (Agent.provider_overrides).

Whitelisted behavior settings only — the first key is prompt_caching, which
retires the "duplicate the provider as X-Uncached" pattern.
"""

from types import SimpleNamespace

from app.providers import AnthropicProvider, OpenAIProvider
from app.providers.factory import (
    AGENT_OVERRIDABLE,
    _apply_overrides,
    validate_provider_overrides,
)


class TestValidation:
    def test_none_and_valid(self):
        assert validate_provider_overrides(None) == []
        assert validate_provider_overrides({"prompt_caching": False}) == []
        assert validate_provider_overrides({"prompt_caching": True}) == []

    def test_unknown_key_rejected(self):
        errors = validate_provider_overrides({"api_key": "steal-me"})
        assert len(errors) == 1
        assert "api_key" in errors[0]
        # The error names what IS allowed
        assert "prompt_caching" in errors[0]

    def test_wrong_type_rejected(self):
        errors = validate_provider_overrides({"prompt_caching": "false"})
        assert len(errors) == 1
        assert "bool" in errors[0]

    def test_non_dict_rejected(self):
        assert validate_provider_overrides("prompt_caching=false") != []

    def test_whitelist_never_contains_connection_settings(self):
        """Guard rail: an agent override must not be able to redirect the
        provider's credentials."""
        forbidden = {"api_key", "base_url", "api_endpoint", "azure_endpoint"}
        assert not forbidden & set(AGENT_OVERRIDABLE)


class TestApplication:
    def test_disables_caching_on_anthropic(self):
        provider = AnthropicProvider(api_key="k")  # caching defaults to on
        assert provider.enable_prompt_caching is True
        _apply_overrides(provider, {"prompt_caching": False})
        assert provider.enable_prompt_caching is False

    def test_enables_caching_over_provider_config(self):
        provider = AnthropicProvider(api_key="k", enable_prompt_caching=False)
        _apply_overrides(provider, {"prompt_caching": True})
        assert provider.enable_prompt_caching is True

    def test_ignored_on_providers_without_the_attr(self):
        provider = OpenAIProvider(api_key="k")  # OpenAI caching is automatic
        _apply_overrides(provider, {"prompt_caching": False})
        assert not hasattr(provider, "enable_prompt_caching")

    def test_none_is_noop(self):
        provider = AnthropicProvider(api_key="k")
        _apply_overrides(provider, None)
        assert provider.enable_prompt_caching is True


class TestConfigApply:
    def test_valid_overrides_pass_through(self):
        from app.services.config_apply.agents import _validated_overrides

        cfg = SimpleNamespace(
            namespace="default", name="a",
            providerOverrides={"prompt_caching": False},
        )
        errors: list[str] = []
        assert _validated_overrides(cfg, errors) == {"prompt_caching": False}
        assert errors == []

    def test_invalid_overrides_become_apply_errors(self):
        from app.services.config_apply.agents import _validated_overrides

        cfg = SimpleNamespace(
            namespace="default", name="a",
            providerOverrides={"base_url": "https://evil.example"},
        )
        errors: list[str] = []
        assert _validated_overrides(cfg, errors) is None
        assert len(errors) == 1
        assert "default/a" in errors[0]

    def test_absent_is_none(self):
        from app.services.config_apply.agents import _validated_overrides

        cfg = SimpleNamespace(namespace="default", name="a", providerOverrides=None)
        errors: list[str] = []
        assert _validated_overrides(cfg, errors) is None
        assert errors == []


class TestAgentAPI:
    async def test_round_trip_and_validation(self, client, test_user, db):
        from tests.conftest import auth_headers

        headers = auth_headers(test_user)
        create = await client.post(
            "/api/v1/agents",
            headers=headers,
            json={
                "namespace": "default",
                "name": "override_agent",
                "provider_overrides": {"prompt_caching": False},
            },
        )
        assert create.status_code == 201, create.text
        assert create.json()["provider_overrides"] == {"prompt_caching": False}

        # Unknown key → validation error, not silent drop
        bad = await client.post(
            "/api/v1/agents",
            headers=headers,
            json={
                "namespace": "default",
                "name": "override_agent2",
                "provider_overrides": {"base_url": "https://evil.example"},
            },
        )
        assert bad.status_code == 422

        # Update flips it; {} clears back to inherit
        upd = await client.put(
            "/api/v1/agents/default/override_agent",
            headers=headers,
            json={"provider_overrides": {"prompt_caching": True}},
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["provider_overrides"] == {"prompt_caching": True}

        cleared = await client.put(
            "/api/v1/agents/default/override_agent",
            headers=headers,
            json={"provider_overrides": {}},
        )
        assert cleared.status_code == 200
        assert cleared.json()["provider_overrides"] is None
