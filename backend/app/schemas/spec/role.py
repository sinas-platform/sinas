"""Role spec: a role's definition — name, description, email domain and its
permission map. Who holds the role (memberships, API keys) is a binding, not
part of the definition: config, packages and history leave it alone here.

Config writes permissions as a list of {key, value}; the REST API and the
spec as a {key: value} map.
"""

from typing import Any, Optional

from pydantic import Field, field_validator

from app.schemas.spec.base import SpecModel


class RoleSpec(SpecModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    email_domain: Optional[str] = Field(default=None, max_length=255)
    permissions: dict[str, bool] = Field(default_factory=dict)

    @field_validator("permissions", mode="before")
    @classmethod
    def _list_is_map(cls, value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, list):
            return {
                (p["key"] if isinstance(p, dict) else p.key): (p["value"] if isinstance(p, dict) else p.value)
                for p in value
            }
        return value

    @property
    def key(self) -> str:
        return self.name

    def to_config(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.description is not None:
            out["description"] = self.description
        if self.email_domain is not None:
            out["emailDomain"] = self.email_domain
        out["permissions"] = [{"key": k, "value": v} for k, v in sorted(self.permissions.items())]
        return out
