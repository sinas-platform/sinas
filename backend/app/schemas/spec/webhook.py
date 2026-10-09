"""Webhook spec."""

from typing import Any, ClassVar, Literal, Optional

from pydantic import Field, model_validator

from app.schemas.spec.base import SpecModel
from app.schemas.spec.references import accept_config_reference, config_reference

# Config apply took any path, and the runtime route serves any of them, so
# only whitespace (never routable as typed) is refused here. The REST API's
# own create schema stays narrower.
PATH_PATTERN = r"^\S+$"


class WebhookDedupSpec(SpecModel):
    key: str
    # A TTL of 0 or less made every deduplicated call fail in Redis. Config
    # never had an upper bound (the REST schema's 86400 stays there).
    ttl_seconds: int = Field(default=300, ge=1)


class WebhookSpec(SpecModel):
    WHOLE_SPEC_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"target_type", "target_name", "message_template", "response_mode"}
    )

    path: str = Field(min_length=1, max_length=255, pattern=PATH_PATTERN)
    target_type: Literal["function", "agent", "pipeline"] = "function"
    # No pattern: config never had one, and a function's namespace is
    # whatever its own create accepted. A wrong one fails the reference check.
    target_namespace: str = Field(default="default", min_length=1, max_length=255)
    target_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    message_template: Optional[str] = None
    session_key_template: Optional[str] = Field(default=None, max_length=500)
    http_method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = "POST"
    description: Optional[str] = None
    default_values: dict[str, Any] = Field(default_factory=dict)
    # Config state, as for schedules and triggers: a webhook paused in config
    # stays paused, and export keeps it.
    is_active: bool = True
    requires_auth: bool = True
    response_mode: Literal["sync", "async", "raw"] = "sync"
    dedup: Optional[WebhookDedupSpec] = None

    @model_validator(mode="before")
    @classmethod
    def _accept_config_shape(cls, data: Any) -> Any:
        data = accept_config_reference(data, "webhooks")
        if not isinstance(data, dict):
            return data
        for key in ("httpMethod", "http_method"):
            # Config never validated the method: "post" reached the database
            # enum and failed the write. Accept any case.
            if isinstance(data.get(key), str):
                data = {**data, key: data[key].upper()}
        for key in ("defaultValues", "default_values"):
            if key in data and data[key] is None:
                data = {**data, key: {}}
        return data

    def whole_spec_problems(self) -> list[str]:
        problems = []
        if self.target_type in ("function", "pipeline", "agent") and not self.target_name:
            problems.append(f"{self.target_type}_name is required for {self.target_type}-target webhooks")
        if self.target_type == "agent" and not self.message_template:
            problems.append("message_template is required for agent-target webhooks")
        if self.target_type != "function" and self.response_mode == "raw":
            problems.append("response_mode 'raw' is only supported for function-target webhooks")
        return problems

    @model_validator(mode="after")
    def _whole_spec(self) -> "WebhookSpec":
        problems = self.whole_spec_problems()
        if problems:
            raise ValueError(problems[0])
        return self

    def to_config(self) -> dict[str, Any]:
        dedup = self.dedup
        out = {
            "path": self.path,
            # Omitted for function targets, as export always did
            "targetType": self.target_type if self.target_type != "function" else None,
            **config_reference(self.target_type, self.target_namespace, self.target_name),
            "messageTemplate": self.message_template,
            "sessionKeyTemplate": self.session_key_template,
            # A plain string: the stored enum used to be dumped as a python
            # object tag that safe_load refuses, so no export with a webhook
            # could be applied again.
            "httpMethod": str(getattr(self.http_method, "value", self.http_method)),
            "requiresAuth": self.requires_auth,
            "description": self.description,
            "defaultValues": self.default_values or None,
            "responseMode": self.response_mode,
            "dedup": {"key": dedup.key, "ttlSeconds": dedup.ttl_seconds} if dedup else None,
            "isActive": self.is_active,
        }
        return {key: value for key, value in out.items() if value is not None}
