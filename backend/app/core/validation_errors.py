"""Request-validation (422) responses that never echo a secret.

FastAPI's default 422 handler returns every error with its `input`: the value
that failed, which for a missing body field is the whole request body. A
request that carries a credential (a run's `provider_credentials`, a
password, an API key, a credential header) and fails validation anywhere
would get that credential back in `detail[].input`, where clients that record
error responses keep it.

This handler answers in the same shape, with each `input` masked the way the
request log masks bodies (app/middleware/request_logger.py), and masked whole
when the error's location is itself a credential (a sensitive field or
header, or anything inside a secret mapping).
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.middleware.request_logger import (
    _REDACTED,
    _SECRET_MAPPINGS,
    _SENSITIVE_KEYS,
    redact_body,
)

# Header names (as FastAPI reports them in `loc`) whose value is a credential.
_SENSITIVE_HEADERS = frozenset({
    "authorization", "proxy-authorization", "x-api-key", "api-key", "cookie",
    "x-sinas-provider-credential",
})


def _secret_location(loc: Any) -> bool:
    for part in loc if isinstance(loc, (list, tuple)) else ():
        if not isinstance(part, str):
            continue
        lowered = part.lower()
        dashed = lowered.replace("_", "-")
        if (
            lowered in _SENSITIVE_KEYS
            or lowered in _SECRET_MAPPINGS
            or dashed in _SENSITIVE_HEADERS
            or "credential" in lowered
            or "password" in lowered
        ):
            return True
    return False


def scrub_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The errors as they may be returned: no credential in any `input`."""
    out = []
    for error in errors:
        error = dict(error)
        if "input" in error:
            if _secret_location(error.get("loc")):
                error["input"] = _REDACTED
            else:
                error["input"] = redact_body(error["input"])
        out.append(error)
    return out


async def _handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = scrub_validation_errors(jsonable_encoder(exc.errors()))
    return JSONResponse(status_code=422, content={"detail": errors})


def install_validation_error_handler(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _handler)
