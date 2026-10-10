"""Dependency spec: a Python package approved for functions."""

from typing import Any, Optional

from pydantic import Field, field_validator, model_validator

from app.schemas.spec.base import SpecModel


class DependencySpec(SpecModel):
    # Config never checked the name (the REST API does); kept that lenient.
    package_name: str = Field(min_length=1, max_length=255)
    # Left out, an existing dependency keeps its pinned version.
    version: Optional[str] = Field(default=None, max_length=50)

    @model_validator(mode="before")
    @classmethod
    def _split_pin(cls, data: Any) -> Any:
        # "package==1.2.3" in one field: name and version, as config allowed.
        if not isinstance(data, dict):
            return data
        data = dict(data)
        name_key = "packageName" if "packageName" in data else "package_name"
        name = data.get(name_key)
        if isinstance(name, str) and "==" in name:
            data[name_key], pinned = name.split("==", 1)
            if not data.get("version"):
                data["version"] = pinned
        return data

    @field_validator("version", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @property
    def key(self) -> str:
        return self.package_name
