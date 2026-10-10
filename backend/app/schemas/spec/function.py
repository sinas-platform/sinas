"""Function spec."""

from typing import Any, Optional

from pydantic import Field, field_validator

from app.schemas.spec.base import SpecModel


class FunctionSpec(SpecModel):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    # Config never checked the code or the schemas (REST does: Python syntax,
    # a "type" key). Kept that lenient so configs that applied still apply.
    code: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    icon: Optional[str] = Field(default=None, max_length=512)
    timeout: Optional[int] = None
    shared_pool: bool = False
    requires_approval: bool = False
    is_active: bool = True

    @field_validator("description", "icon", mode="before")
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
