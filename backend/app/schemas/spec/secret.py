"""Secret spec (shared secrets: the ones config and packages declare)."""

from typing import Any, Optional

from pydantic import Field, field_validator

from app.schemas.spec.base import SpecModel


class SecretSpec(SpecModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    # Write-only. Left out, an existing secret keeps its value; a new one
    # can't be created without one.
    value: Optional[str] = None

    @field_validator("description", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @property
    def key(self) -> str:
        return self.name

    def canonical(self) -> dict[str, Any]:
        """The value never leaves as itself: history and the stored checksum
        get a keyed stand-in (stable, so a changed value still shows)."""
        from app.services.resources.history import redact

        state = super().canonical()
        if self.value is not None:
            state["value"] = redact(self.value)
        return state

    def to_config(self) -> dict[str, Any]:
        # Exported as names and descriptions only, as always.
        return self.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"value"})
