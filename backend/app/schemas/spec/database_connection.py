"""Database connection spec.

Table annotations are not part of it: they're a separate, additive layer
(config adds them after the connection applies; the console edits them on
their own).
"""

from typing import Any, Optional

from pydantic import Field, field_validator, model_validator

from app.schemas.spec.base import SpecModel


class DatabaseConnectionSpec(SpecModel):
    name: str = Field(min_length=1, max_length=100)
    connection_type: str = Field(min_length=1, max_length=50)
    host: str = Field(min_length=1, max_length=500)
    port: int
    database: str = Field(min_length=1)
    username: str = Field(min_length=1)
    # Write-only. Left out (or blank), an existing connection keeps it.
    password: Optional[str] = None
    ssl_mode: Optional[str] = None
    # Pool sizes and other driver settings.
    config: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = False
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def _without_annotations(cls, data: Any) -> Any:
        if isinstance(data, dict) and "annotations" in data:
            data = {k: v for k, v in data.items() if k != "annotations"}
        return data

    @field_validator("password", "ssl_mode", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("config", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return {} if value is None else value

    @property
    def key(self) -> str:
        return self.name

    def canonical(self) -> dict[str, Any]:
        """The password never leaves as itself (see SecretSpec)."""
        from app.services.resources.history import redact

        state = super().canonical()
        if self.password is not None:
            state["password"] = redact(self.password)
        return state

    def to_config(self) -> dict[str, Any]:
        out = self.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"password"})
        if out.get("config") == {}:
            del out["config"]
        return out
