"""Skill spec."""

from pydantic import Field

from app.schemas.spec.base import SpecModel


class SkillSpec(SpecModel):
    # No "/": agents reference a skill as "ns/name", split on the first one.
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=255)
    # Both NOT NULL. Config never required them non-empty; the REST schema
    # does, and still checks that first.
    description: str
    content: str
    is_active: bool = True

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"
