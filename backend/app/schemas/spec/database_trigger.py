"""Database trigger (CDC) spec."""

from typing import Any, ClassVar, Literal, Optional

from pydantic import Field, model_validator

from app.schemas.spec.base import SpecModel
from app.schemas.spec.references import accept_config_reference, config_reference

# The poller quotes identifiers ("schema"."table"), so a double quote is the
# one character that can break out of them.
IDENTIFIER_PATTERN = r'^[^"]+$'


class DatabaseTriggerSpec(SpecModel):
    WHOLE_SPEC_FIELDS: ClassVar[frozenset[str]] = frozenset({"target_type", "target_name"})

    name: str = Field(min_length=1, max_length=255)
    connection_name: str = Field(min_length=1)
    schema_name: str = Field(default="public", min_length=1, max_length=255, pattern=IDENTIFIER_PATTERN)
    table_name: str = Field(min_length=1, max_length=255, pattern=IDENTIFIER_PATTERN)
    # Config accepted an empty list, and the poller ignores operations anyway,
    # so an empty list stays valid here (the REST schema still refuses it).
    operations: list[Literal["INSERT", "UPDATE"]] = Field(default_factory=lambda: ["INSERT", "UPDATE"])
    target_type: Literal["function", "pipeline"] = "function"
    # No pattern: config never had one, and a function's namespace is
    # whatever its own create accepted. A wrong one fails the reference check.
    target_namespace: str = Field(default="default", min_length=1, max_length=255)
    target_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    poll_column: str = Field(min_length=1, max_length=255, pattern=IDENTIFIER_PATTERN)
    # 0 made the poll loop spin against the external database; a batch of 0
    # never advanced the bookmark. Upper bounds stay with the REST schema:
    # config has always allowed more.
    poll_interval_seconds: int = Field(default=10, ge=1)
    batch_size: int = Field(default=100, ge=1)
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def _accept_config_shape(cls, data: Any) -> Any:
        return accept_config_reference(data, "triggers")

    def whole_spec_problems(self) -> list[str]:
        if not self.target_name:
            return [f"{self.target_type}_name is required for {self.target_type}-target triggers"]
        return []

    @model_validator(mode="after")
    def _whole_spec(self) -> "DatabaseTriggerSpec":
        problems = self.whole_spec_problems()
        if problems:
            raise ValueError(problems[0])
        return self

    @property
    def target(self) -> str:
        return f"{self.target_namespace}/{self.target_name}"

    def to_config(self) -> dict[str, Any]:
        out = {
            "name": self.name,
            "connectionName": self.connection_name,
            "schemaName": self.schema_name,
            "tableName": self.table_name,
            "operations": list(self.operations),
            "targetType": self.target_type if self.target_type != "function" else None,
            **config_reference(self.target_type, self.target_namespace, self.target_name),
            "pollColumn": self.poll_column,
            "pollIntervalSeconds": self.poll_interval_seconds,
            "batchSize": self.batch_size,
            "isActive": self.is_active,
        }
        return {key: value for key, value in out.items() if value is not None}
