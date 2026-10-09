"""Partial updates (REST PATCH) expressed as whole-spec writes."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError

from app.schemas.spec.base import SpecModel

TSpec = TypeVar("TSpec", bound=SpecModel)


class PatchRejected(Exception):
    """The patch introduces invalidity. `detail` is the 422 body."""

    def __init__(self, detail: list[dict[str, Any]]):
        super().__init__(detail)
        self.detail = detail


def patched_spec(stored: TSpec, patch: dict[str, Any]) -> TSpec:
    """The spec a PATCH produces: `patch` (canonical field names) merged over
    the stored state.

    A PATCH may not introduce invalidity, but it is not blocked by invalidity
    it doesn't touch. Rows written before every channel validated must still
    be pausable, renameable or re-described — otherwise the only way to stop a
    broken resource would be to delete it.

    A field counts as touched only if the PATCH changes its value: the console
    sends the whole form, so a stored invalid value comes back unchanged.

    - An error on a field the PATCH didn't change is stored data: tolerated.
    - A whole-spec error is tolerated unless the PATCH changed a field that
      rule reads (WHOLE_SPEC_FIELDS).

    Stored errors can't simply be diffed against merged ones: pydantic skips
    whole-spec validation when a field fails, so a second stored problem stays
    hidden until the first is fixed — and fixing the first would then be blamed
    for the second. That is also why the whole-spec rules are re-run
    explicitly when a field they read changed.
    """
    model = type(stored)
    current = stored.model_dump()
    changed = {key for key, value in patch.items() if current.get(key) != value}
    merged = {**current, **patch}
    try:
        return model.model_validate(merged)
    except ValidationError as error:
        errors = error.errors(include_url=False)

    field_by_alias = {(info.alias or name): name for name, info in model.model_fields.items()}

    def introduced(err: dict) -> bool:
        if err["loc"]:
            field = field_by_alias.get(str(err["loc"][0]), str(err["loc"][0]))
            return field in changed
        return bool(model.WHOLE_SPEC_FIELDS & changed)

    blocking = [
        {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
        for e in errors
        if introduced(e)
    ]
    # Built from typed values, not the dumped dicts: unchanged fields keep the
    # stored objects (a nested spec stays a spec), changed ones are coerced
    # to their field's type.
    values = {field: getattr(stored, field, None) for field in model.model_fields}
    for field in changed & model.model_fields.keys():
        try:
            values[field] = TypeAdapter(model.model_fields[field].annotation).validate_python(
                patch[field]
            )
        except ValidationError:
            values[field] = patch[field]  # reported below if it matters
    spec = model.model_construct(**values)
    if not blocking and model.WHOLE_SPEC_FIELDS & changed:
        blocking = [
            {"loc": [], "msg": problem, "type": "value_error"}
            for problem in spec.whole_spec_problems()
        ]
    if blocking:
        raise PatchRejected(blocking)
    return spec
