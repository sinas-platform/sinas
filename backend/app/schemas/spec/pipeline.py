"""Pipeline spec.

Steps and perUser are data, stored exactly as validated (camelCase keys and
`.$` mapping keys intact, see the pipelines ADR). Config writes the output
mapping as top-level `output` / `output.$`; the spec holds it as
`output_mapping`, as the REST API and the database do.
"""

from typing import Any, ClassVar, Optional

from pydantic import Field, field_validator, model_validator

from app.schemas.spec.base import SpecModel


class PipelineSpec(SpecModel):
    WHOLE_SPEC_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "steps", "per_user", "as_tool", "input_schema", "description", "tool_description",
        "concurrency", "output_mapping",
    })

    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    per_user: Optional[dict[str, Any]] = None
    as_tool: bool = False
    tool_description: Optional[str] = None
    sync_timeout_seconds: int = 120
    concurrency: Optional[str] = None
    disable_after_failures: Optional[int] = None
    output_mapping: Optional[dict[str, Any]] = None
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def _config_output(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        expr = data.pop("output.$", None)
        expr = data.pop("outputExpr", expr)
        literal = data.pop("output", None)
        if expr is not None:
            data.setdefault("output_mapping", {"output.$": expr})
        elif literal is not None:
            data.setdefault("output_mapping", {"output": literal})
        return data

    @field_validator("description", "tool_description", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("input_schema", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return {} if value is None else value

    def whole_spec_problems(self) -> list[str]:
        from app.services.pipeline_validation import validate_pipeline_definition

        return validate_pipeline_definition(
            self.steps,
            per_user=self.per_user,
            as_tool=self.as_tool,
            input_schema=self.input_schema,
            description=self.description,
            tool_description=self.tool_description,
            concurrency=self.concurrency,
            output_mapping=self.output_mapping,
        )

    @model_validator(mode="after")
    def _whole_spec(self) -> "PipelineSpec":
        problems = self.whole_spec_problems()
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"

    def to_config(self) -> dict[str, Any]:
        config = super().to_config()
        if config.get("syncTimeoutSeconds") == 120:
            del config["syncTimeoutSeconds"]  # the default, as the export always left it
        if config.get("inputSchema") == {}:
            del config["inputSchema"]
        mapping = config.pop("outputMapping", None) or {}
        config.update(mapping)  # back to top-level output / output.$
        return config
