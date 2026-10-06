"""Query spec.

The REST API names the connection by id, config by name; the spec holds the
name (what config and export use) and the applier resolves it.
"""

from typing import Any, Literal, Optional

from pydantic import Field, field_validator

from app.schemas.spec.base import SpecModel


class QuerySpec(SpecModel):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    connection_name: str = Field(min_length=1)
    # Anything but "read" ran through the write path: a SELECT returned no
    # rows, only a count, and got no LIMIT. Case is forgiven ("Read").
    operation: Literal["read", "write"]
    sql: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    # 0 timed out every call; max 0 rows returned nothing.
    timeout_ms: int = Field(default=5000, ge=1)
    max_rows: int = Field(default=1000, ge=1)
    is_active: bool = True

    @field_validator("operation", mode="before")
    @classmethod
    def _lower(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("input_schema", "output_schema", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return {} if value is None else value

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"
