"""Identity provider spec (docs/design/oidc-sso.md).

Config YAML (camelCase) and the REST API (snake_case) describe a provider
with the same shape; the aliases on SpecModel translate.
"""

import re
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from app.core.secret_refs import secret_ref_name
from app.schemas.spec.base import SpecModel

DEFAULT_SCOPES = ["openid", "email", "profile"]

_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def normalize_domain(value: str) -> str:
    """"@Example.com " -> "example.com"; anything that isn't a domain is refused."""
    domain = value.strip().lower().lstrip("@")
    if not _DOMAIN.match(domain):
        raise ValueError(f"'{value}' is not an email domain")
    return domain


def _domains(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        domain = normalize_domain(value)
        if domain not in seen:
            seen.append(domain)
    return seen


def check_issuer(value: str) -> str:
    """https, or http for a local IdP (or anywhere with OIDC_ALLOW_HTTP); no
    credentials, query or fragment (discovery appends to the path)."""
    from app.core.config import settings

    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("issuer must be an http(s) URL")
    if parts.scheme == "http" and parts.hostname not in _LOCAL_HOSTS and not settings.oidc_allow_http:
        raise ValueError("issuer must use https (set OIDC_ALLOW_HTTP for a lab IdP)")
    if parts.username or parts.password:
        raise ValueError("issuer must not carry credentials")
    if parts.query or parts.fragment:
        raise ValueError("issuer must not have a query or fragment")
    return value


class RoleMappingSpec(SpecModel):
    """One rule: a matcher (exactly one of them) and the roles it grants."""

    group: Optional[str] = Field(default=None, min_length=1, max_length=512)  # a value in the groups claim
    email_domain: Optional[str] = None  # the user's email domain
    roles: list[str] = Field(min_length=1)

    @field_validator("email_domain")
    @classmethod
    def _domain(cls, value: Optional[str]) -> Optional[str]:
        return normalize_domain(value) if value is not None else None

    @field_validator("roles")
    @classmethod
    def _roles(cls, value: list[str]) -> list[str]:
        roles = [r.strip() for r in value]
        if any(not r for r in roles):
            raise ValueError("role names can't be empty")
        return list(dict.fromkeys(roles))

    @model_validator(mode="after")
    def _one_matcher(self) -> "RoleMappingSpec":
        if (self.group is None) == (self.email_domain is None):
            raise ValueError("a role mapping needs exactly one of group or emailDomain")
        return self

    def matches(self, groups: set[str], email_domain: Optional[str]) -> bool:
        if self.group is not None:
            return self.group in groups
        return email_domain is not None and email_domain == self.email_domain


class ClaimMappingSpec(SpecModel):
    """Copy a claim into a user custom field at every login.

    `claim` is a dotted path into the verified ID token claims (filled in
    from userinfo), like emailClaim: `org_id`, `organization.id`, or a key
    holding dots as is (`https://example.com/org_id`). `field` is the
    users.custom_fields key it lands in, an identifier.

    `target: session` puts the value in the login's session context instead
    (the tokens it issues carry it; app/providers/attribution.py), for a
    claim that says what this session is for (the organization chosen at
    sign-in) rather than who the user is. Left out (or `user`): a custom
    field."""

    claim: str = Field(min_length=1, max_length=512)
    field: str = Field(min_length=1, max_length=64)
    target: Optional[Literal["user", "session"]] = None

    @field_validator("target")
    @classmethod
    def _target(cls, value: Optional[str]) -> Optional[str]:
        return None if value == "user" else value  # the default, left out

    @field_validator("claim")
    @classmethod
    def _claim(cls, value: str) -> str:
        value = value.strip()
        if not value or value.startswith(".") or value.endswith(".") or any(c.isspace() for c in value):
            raise ValueError(f"'{value}' is not a claim path")
        return value

    @field_validator("field")
    @classmethod
    def _field(cls, value: str) -> str:
        from app.providers.attribution import FIELD_KEY

        value = value.strip()
        if not FIELD_KEY.match(value):
            raise ValueError(
                f"'{value}' is not a custom field name: letters, digits and _, not starting with a digit"
            )
        return value


class IdentityProviderSpec(SpecModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=255)
    issuer: str = Field(min_length=1, max_length=2048)
    client_id: str = Field(min_length=1, max_length=512)
    # The name of a shared Secret holding the client secret, never the
    # value. None: a public client (PKCE only).
    client_secret: Optional[str] = Field(default=None, max_length=255)
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    email_claim: str = Field(default="email", min_length=1, max_length=512)
    groups_claim: str = Field(default="groups", min_length=1, max_length=512)
    role_mappings: list[RoleMappingSpec] = Field(default_factory=list)
    # Claims copied into the user's custom fields at every login.
    claim_mappings: list[ClaimMappingSpec] = Field(default_factory=list, max_length=50)
    allowed_domains: list[str] = Field(default_factory=list)
    auto_provision: bool = False
    default_role: Optional[str] = Field(default=None, max_length=255)
    enforce_for_domains: list[str] = Field(default_factory=list)
    session_hours: int = Field(default=12, ge=1, le=24 * 90)
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            # The console sends "" for none.
            for field in ("client_secret", "clientSecret", "default_role", "defaultRole"):
                if data.get(field) == "":
                    data[field] = None
        return data

    @field_validator("client_secret", mode="before")
    @classmethod
    def _client_secret_name(cls, value: Any) -> Any:
        # A Secret reference: `{{ NAME }}` is stored as `NAME`.
        return secret_ref_name(value) or None

    @field_validator("issuer")
    @classmethod
    def _issuer(cls, value: str) -> str:
        return check_issuer(value.strip())

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, value: list[str]) -> list[str]:
        scopes = list(dict.fromkeys(s.strip() for s in value if s.strip()))
        if "openid" not in scopes:
            raise ValueError("scopes must include openid")
        if any(" " in s for s in scopes):
            raise ValueError("one scope per entry")
        return scopes

    @field_validator("allowed_domains", "enforce_for_domains")
    @classmethod
    def _domain_list(cls, value: list[str]) -> list[str]:
        return _domains(value)

    @field_validator("claim_mappings")
    @classmethod
    def _one_claim_per_field(cls, value: list[ClaimMappingSpec]) -> list[ClaimMappingSpec]:
        seen: set[str] = set()
        for mapping in value:
            if mapping.field in seen:
                raise ValueError(f"custom field '{mapping.field}' is mapped twice")
            seen.add(mapping.field)
        return value

    def canonical(self) -> dict[str, Any]:
        state = super().canonical()
        # Left out while unset, so providers from before claim mappings keep
        # their checksum and history.
        if not self.claim_mappings:
            state.pop("claim_mappings", None)
        return state

    @property
    def key(self) -> str:
        return self.name

    def role_names(self) -> set[str]:
        names = {role for rule in self.role_mappings for role in rule.roles}
        if self.default_role:
            names.add(self.default_role)
        return names

    def to_config(self) -> dict[str, Any]:
        out = super().to_config()
        for field in ("roleMappings", "claimMappings"):
            if not out.get(field):
                out.pop(field, None)
        for field in ("allowedDomains", "enforceForDomains"):
            if not out.get(field):
                out.pop(field, None)
        return out
