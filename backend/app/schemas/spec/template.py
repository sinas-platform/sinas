"""Template spec."""

from typing import Any, Optional

from pydantic import Field, field_validator

from app.schemas.spec.base import SpecModel


class TemplateSpec(SpecModel):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    title: Optional[str] = None  # email subject, notification title
    # NOT NULL: a PATCH that nulled it used to fail at flush with a 500.
    html_content: str
    text_content: Optional[str] = None
    variable_schema: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True

    @field_validator("description", "title", "text_content", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        # The console sends "" for an empty field; the renderer treats both
        # alike, so store one form and never see "" vs None as a change.
        return None if value == "" else value

    @field_validator("variable_schema", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return {} if value is None else value

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"
