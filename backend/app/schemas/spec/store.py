"""Store spec."""

from typing import Any, Optional

from pydantic import Field, field_validator

from app.schemas.spec.base import SpecModel


class StoreSpec(SpecModel):
    namespace: str = Field(default="default", min_length=1, max_length=100, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    # JSON Schema every state in the store is checked against (strict) or
    # merely described by. Same field name as the REST and config forms.
    schema: dict[str, Any] = Field(default_factory=dict)
    strict: bool = False
    # REST only ever accepted private/shared; config never checked. Kept as
    # lenient as config was, so a config that applied before still applies.
    default_visibility: str = "private"
    encrypted: bool = False

    @field_validator("description", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("schema", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return {} if value is None else value

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"

    def to_config(self) -> dict[str, Any]:
        # As the export always was: an empty schema is left out.
        return {k: v for k, v in super().to_config().items() if v != {}}
