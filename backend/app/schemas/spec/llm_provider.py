"""LLM provider spec.

Config writes `type`, `endpoint`, `apiKey`, `defaultModel` and a top-level
`models` list; the REST API and the database use `provider_type`,
`api_endpoint`, `api_key`, `default_model`, and keep the models inside
`config`. The spec takes both and holds the database shape.
"""

from typing import Any, Optional

from pydantic import Field, field_validator, model_validator

from app.schemas.spec.base import SpecModel

_CONFIG_NAMES = {
    "type": "provider_type",
    "endpoint": "api_endpoint",
    "apiKey": "api_key",
    "defaultModel": "default_model",
}


class LLMProviderSpec(SpecModel):
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

    @field_validator("api_key", "api_endpoint", "default_model", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("config", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return {} if value is None else value

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
        out["isDefault"] = self.is_default
        out["isActive"] = self.is_active
        return out
