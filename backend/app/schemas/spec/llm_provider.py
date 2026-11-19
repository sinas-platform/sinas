"""LLM provider spec.

Config writes `type`, `endpoint`, `apiKey`, `defaultModel` and a top-level
`models` list; the REST API and the database use `provider_type`,
`api_endpoint`, `api_key`, `default_model`, and keep the models inside
`config`. The spec takes both and holds the database shape.

Pricing (docs/design/usage-cost.md) is the same on every channel: `pricing`
maps a model to LiteLLM's per-token fields, `modelAliases` (model_aliases)
maps a model as called to the ratecard model it is priced as,
`discount` multiplies deployment-ratecard prices, and `pricingProvider`
(pricing_provider) names the LiteLLM provider family the models are priced
as, for an OpenAI-compatible API of another provider.
"""

import re
from typing import Any, ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.spec.base import SpecModel


class ModelPricing(BaseModel):
    """One model's per-token USD prices, in LiteLLM's field names."""

    model_config = ConfigDict(extra="forbid")

    input_cost_per_token: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    output_cost_per_token: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    cache_read_input_token_cost: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    cache_creation_input_token_cost: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    input_cost_per_token_batches: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    output_cost_per_token_batches: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _priced(self) -> "ModelPricing":
        if self.input_cost_per_token is None and self.output_cost_per_token is None:
            raise ValueError("set input_cost_per_token and/or output_cost_per_token")
        return self

# A LiteLLM provider family: "mistral", "together_ai", "vertex_ai-language-models".
_FAMILY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_CONFIG_NAMES = {
    "type": "provider_type",
    "endpoint": "api_endpoint",
    "apiKey": "api_key",
    "defaultModel": "default_model",
}


class LLMProviderSpec(SpecModel):
    # run_credential is checked against the provider type (whole_spec_problems).
    WHOLE_SPEC_FIELDS: ClassVar[frozenset[str]] = frozenset({"config", "provider_type"})

    name: str = Field(min_length=1, max_length=100)
    provider_type: str = Field(min_length=1, max_length=50)
    # Write-only. Left out, an existing provider keeps its key.
    api_key: Optional[str] = None
    api_endpoint: Optional[str] = Field(default=None, max_length=500)
    default_model: Optional[str] = Field(default=None, max_length=100)
    # Provider-specific settings, including `models` (the selectable models).
    config: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    is_active: bool = True
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)
    model_aliases: dict[str, str] = Field(default_factory=dict)
    # A multiplier on deployment-ratecard prices: 0.8 is 20% off list.
    discount: Optional[float] = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    # The LiteLLM provider family to price models as, in place of the one
    # provider_type maps to. Not checked against the ratecard (it changes);
    # config apply warns when the ratecard has no models for it.
    pricing_provider: Optional[str] = Field(default=None, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def _config_shape(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for config_name, field in _CONFIG_NAMES.items():
            if config_name in data:
                data.setdefault(field, data.pop(config_name))
        if "models" in data:
            data["config"] = {"models": data.pop("models"), **(data.get("config") or {})}
        return data

    @field_validator("api_key", "api_endpoint", "default_model", "pricing_provider", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("config", "pricing", "model_aliases", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return {} if value is None else value

    @field_validator("config")
    @classmethod
    def _request_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        """`headers` (identity header templates, app/providers/attribution.py)
        and `budget_errors` (budget refusal rules, app/providers/errors.py)
        are checked like the rest of a spec (`run_credential` too, in
        `_run_credential`); other keys are the provider type's own."""
        from app.providers.attribution import validate_header_templates
        from app.providers.errors import validate_budget_rules

        out = dict(value)
        if "headers" in out:
            headers = validate_header_templates(out["headers"])
            if headers:
                out["headers"] = headers
            else:
                out.pop("headers")
        if "budget_errors" in out:
            rules = validate_budget_rules(out["budget_errors"])
            if rules:
                out["budget_errors"] = rules
            else:
                out.pop("budget_errors")
        return out

    def whole_spec_problems(self) -> list[str]:
        """`config.run_credential` (per-run credentials,
        app/providers/run_credentials.py), checked against the provider type
        (whose own key `header: null` replaces) and the header templates (a
        header is one or the other)."""
        from app.providers.run_credentials import SETTINGS_KEY, validate_settings

        config = self.config if isinstance(self.config, dict) else {}
        try:
            validate_settings(config.get(SETTINGS_KEY), self.provider_type, config.get("headers"))
        except ValueError as e:
            return [str(e)]
        return []

    @model_validator(mode="after")
    def _run_credential(self) -> "LLMProviderSpec":
        from app.providers.run_credentials import SETTINGS_KEY, validate_settings

        problems = self.whole_spec_problems()
        if problems:
            raise ValueError(problems[0])
        if SETTINGS_KEY in self.config:
            checked = validate_settings(
                self.config[SETTINGS_KEY], self.provider_type, self.config.get("headers")
            )
            if checked is None:
                self.config.pop(SETTINGS_KEY)
            else:
                self.config[SETTINGS_KEY] = checked
        return self

    @field_validator("pricing", "model_aliases")
    @classmethod
    def _model_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        for model, target in value.items():
            if not model.strip() or len(model) > 255:
                raise ValueError(f"invalid model name {model!r}")
            if isinstance(target, str) and (not target.strip() or len(target) > 255):
                raise ValueError(f"invalid alias target for {model!r}: {target!r}")
        return value

    @field_validator("pricing_provider")
    @classmethod
    def _family(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _FAMILY.match(value):
            raise ValueError(
                f"invalid pricing provider {value!r}: a lowercase LiteLLM provider name, "
                "such as 'mistral' or 'together_ai'"
            )
        return value

    @property
    def key(self) -> str:
        return self.name

    def canonical(self) -> dict[str, Any]:
        """The key never leaves as itself: history and the stored checksum
        get a keyed stand-in (stable, so a rotated key still shows)."""
        from app.services.resources.history import redact

        state = super().canonical()
        if self.api_key is not None:
            state["api_key"] = redact(self.api_key)
        # Left out while unset, so providers that never had pricing keep the
        # checksum and history they had before it existed.
        if self.pricing:
            state["pricing"] = {
                model: price.model_dump(mode="json", exclude_none=True)
                for model, price in sorted(self.pricing.items())
            }
        else:
            state.pop("pricing", None)
        if not self.model_aliases:
            state.pop("model_aliases", None)
        if self.discount is None:
            state.pop("discount", None)
        if self.pricing_provider is None:
            state.pop("pricing_provider", None)
        return state

    def to_config(self) -> dict[str, Any]:
        """Config shape, without the key (export adds it only on request)."""
        config = dict(self.config)
        out: dict[str, Any] = {"name": self.name, "type": self.provider_type}
        if self.api_endpoint:
            out["endpoint"] = self.api_endpoint
        out["models"] = list(config.pop("models", []) or [])
        if self.default_model:
            out["defaultModel"] = self.default_model
        if config:
            out["config"] = config
        if self.pricing:
            out["pricing"] = {
                model: price.model_dump(mode="json", exclude_none=True)
                for model, price in self.pricing.items()
            }
        if self.model_aliases:
            out["modelAliases"] = dict(self.model_aliases)
        if self.discount is not None:
            out["discount"] = self.discount
        if self.pricing_provider:
            out["pricingProvider"] = self.pricing_provider
        out["isDefault"] = self.is_default
        out["isActive"] = self.is_active
        return out
