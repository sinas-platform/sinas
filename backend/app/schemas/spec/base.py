"""Base class for canonical spec models."""

import hashlib
import json
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class SpecModel(BaseModel):
    """A resource's configurable state, valid on every write channel.

    - `alias_generator=to_camel` + `populate_by_name=True`: accepts both the
      REST form (`cron_expression`) and the config form (`cronExpression`).
    - `extra="forbid"`: a misspelt field is an error, not silently ignored.
      Config YAML that used to apply garbage fails here instead.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    # Fields read by rules that span several fields (whole_spec_problems). A
    # partial update that changes none of them cannot have caused such an error.
    WHOLE_SPEC_FIELDS: ClassVar[frozenset[str]] = frozenset()

    def whole_spec_problems(self) -> list[str]:
        """Rules that span fields. Kept callable on its own: pydantic skips
        after-validators once any field has failed, so a partial update over
        a row that already holds an invalid field has to run these itself.
        Subclasses with such rules override this and raise the first problem
        from an after-validator."""
        return []

    def canonical(self) -> dict[str, Any]:
        """Stable snake_case form: what the checksum and change history use."""
        return self.model_dump(mode="json")

    def checksum(self) -> str:
        """SHA-256 of the canonical form. Equal specs, equal checksums."""
        payload = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_config(self) -> dict[str, Any]:
        """The config YAML / export form. Override where the config shape
        differs structurally from the canonical one, not just in casing."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def diff_specs(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """Field-level changes between two canonical dicts: {field: {from, to}}."""
    before, after = before or {}, after or {}
    return {
        field: {"from": before.get(field), "to": after.get(field)}
        for field in sorted(set(before) | set(after))
        if before.get(field) != after.get(field)
    }
