"""Schedule spec."""

from typing import Any, ClassVar, Literal, Optional

from pydantic import Field, field_validator, model_validator

from app.schemas.spec.base import SpecModel

ScheduleType = Literal["function", "agent", "pipeline"]

# The union of what a schedule's targets allow: function namespaces use
# [a-zA-Z0-9_], agent and pipeline namespaces also allow "-". Narrower than
# this and a schedule on a legitimately named agent ("customer-support/triage")
# can't be expressed — which, with all-or-nothing applies, fails the whole
# config (and a startup auto-apply, the boot).
NAMESPACE_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_-]*$"

# Config YAML references the target with one field per schedule type, holding
# "namespace/name". The REST API splits it into target_namespace/target_name.
_CONFIG_REF_FIELD: dict[str, str] = {
    "function": "functionName",
    "agent": "agentName",
    "pipeline": "pipelineName",
}
_REF_KEYS = (
    "functionName", "agentName", "pipelineName",
    "function_name", "agent_name", "pipeline_name",
)


class ScheduleSpec(SpecModel):
    WHOLE_SPEC_FIELDS: ClassVar[frozenset[str]] = frozenset({"schedule_type", "content"})

    name: str = Field(min_length=1, max_length=255)
    schedule_type: ScheduleType = "function"
    target_namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=NAMESPACE_PATTERN
    )
    target_name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    cron_expression: str = Field(min_length=1)
    timezone: str = "UTC"
    input_data: dict[str, Any] = Field(default_factory=dict)
    content: Optional[str] = None
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def _accept_config_target_reference(cls, data: Any) -> Any:
        """Accept the config shape: `scheduleType` plus `functionName` /
        `agentName` / `pipelineName` holding "namespace/name" (namespace
        defaults to "default"). Existing YAML keeps parsing unchanged."""
        if not isinstance(data, dict):
            return data
        # The config path used to store scheduleType unvalidated, so existing
        # YAML may say "Agent" or "Function". Accept any case; store lowercase.
        for type_key in ("scheduleType", "schedule_type"):
            if isinstance(data.get(type_key), str):
                data = {**data, type_key: data[type_key].lower()}
        refs = {key: data[key] for key in _REF_KEYS if data.get(key)}
        if not refs:
            return {key: value for key, value in data.items() if key not in _REF_KEYS}

        data = {key: value for key, value in data.items() if key not in _REF_KEYS}
        schedule_type = data.get("scheduleType", data.get("schedule_type", "function"))
        camel = _CONFIG_REF_FIELD.get(schedule_type)
        if camel is None:
            return data  # let the Literal report the bad schedule type
        snake = {"functionName": "function_name", "agentName": "agent_name",
                 "pipelineName": "pipeline_name"}[camel]
        ref = refs.get(camel) or refs.get(snake)
        if not ref:
            raise ValueError(f"{camel} is required for {schedule_type} schedules")
        namespace, name = ref.split("/", 1) if "/" in ref else ("default", ref)
        data["target_namespace"] = namespace
        data["target_name"] = name
        return data

    @field_validator("cron_expression")
    @classmethod
    def _valid_cron(cls, value: str) -> str:
        # Previously enforced on the REST channel only: config YAML could
        # apply a schedule the scheduler then failed to load.
        from croniter import croniter

        if not croniter.is_valid(value):
            raise ValueError("Invalid cron expression")
        return value

    def whole_spec_problems(self) -> list[str]:
        problems = []
        if self.schedule_type == "agent" and not self.content:
            problems.append("content is required for agent schedules")
        return problems

    @model_validator(mode="after")
    def _whole_spec(self) -> "ScheduleSpec":
        problems = self.whole_spec_problems()
        if problems:
            raise ValueError(problems[0])
        return self

    @property
    def target(self) -> str:
        return f"{self.target_namespace}/{self.target_name}"

    def to_config(self) -> dict[str, Any]:
        """Config YAML form — the shape `resource_serializers.serialize_schedule`
        always exported, now including `description` (which config apply used
        to accept and then silently drop)."""
        # Rows predating validation may hold a miscased or unknown type. Export
        # must not crash on one: normalise the case, and for a type that is
        # still unknown leave the reference out, as the old serializer did.
        schedule_type = (self.schedule_type or "").lower()
        ref_field = _CONFIG_REF_FIELD.get(schedule_type)
        out = {
            "name": self.name,
            "scheduleType": schedule_type if ref_field else self.schedule_type,
            **({ref_field: self.target} if ref_field else {}),
            "description": self.description,
            "content": self.content,
            "cronExpression": self.cron_expression,
            "isActive": self.is_active,
            "timezone": self.timezone,
            "inputData": self.input_data or None,
        }
        return {key: value for key, value in out.items() if value is not None}
