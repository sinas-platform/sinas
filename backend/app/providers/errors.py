"""LLM call failures: one error type with a stable code, wherever it surfaces.

Every failed LLM call, and every agent turn that fails, is reported with the
same envelope (docs-mint/api-reference/errors.mdx):

    {"detail": "<code>: <message>", "code": "<code>", "retryable": <bool>,
     "upstream_status": <int>, "retry_after": <seconds>}

`upstream_status` (the model API's own HTTP status) and `retry_after` (how
long the model API asked to wait) only when known. The envelope is the body
of an API error (with the code's HTTP status), the fields of a stream's
`error` event (whose message stays under `error`, as before), and the
`error_code` / `retryable` / `upstream_status` of a failed job, a sub-agent
result and a pipeline step (`errorCode`).

Codes (CODES has the HTTP status and retryability of each):

- `budget_exceeded` (402): a budget, quota or credit balance is exhausted —
  a gateway's per-customer or per-key budget, an account out of credits.
  Retrying won't help until someone raises the budget or it resets, so it is
  never retried, neither by Sinas nor by the SDK under it.
- `rate_limited` (429, retryable): a 429 that isn't a budget refusal.
- `provider_auth_failed` (502): the model API rejected the provider's
  credentials (401/403). Its message is never passed on: it can quote the key.
- `model_not_found` (502): the model API doesn't know the model (404).
- `provider_unavailable` (503, retryable): a 5xx, an overloaded model API, or
  one that could not be reached.
- `provider_timeout` (504, retryable): the model API did not answer in time.
- `context_length_exceeded` (422): the conversation is too long for the
  model's context window.
- `content_filtered` (422): the model API's content filter or safety system
  blocked the call.
- `invalid_request` (502): the model API rejected the request as invalid
  (a 400 or 422 none of the above matched): usually the agent's or the
  provider's configuration, such as a parameter the model doesn't support.
- `streaming_required` (502): the SDK refuses to make a call this long
  without streaming (its "streaming is strongly recommended" guard).
- `provider_not_configured` (503): no LLM provider (or no model) to run the
  agent with: none configured, or the agent's provider is inactive.
- `provider_credential_required` (400): the provider requires a per-run
  credential (`config.run_credential.required`, app/providers/run_credentials.py)
  and the chat has none, or only an expired one. Never retried: the caller
  has to send one (`provider_credentials`) first.
- `provider_error` (502): any other error the model API answered with.
- `job_timeout` (504): the turn did not finish within its job timeout.
- `internal_error` (500): the turn failed for a reason that isn't the model
  API's (a bug, a database error, ...).

Budget and rate detection is deliberately conservative: the HTTP status and
well-known error shapes only, so an ordinary error is never mistaken for a
refusal.

budget_exceeded:
- HTTP 402 Payment Required, whatever the body;
- an error `type` or `code` (OpenAI-style `error.type` / `error.code`, or
  top-level) of `budget_exceeded`, `insufficient_quota`, `billing_error` or
  `billing_hard_limit_reached`;
- an error message containing one of BUDGET_MESSAGE_MARKERS (gateway budget
  errors that come back under another type, such as a 401 or 403);
- or a provider's own rules (`config.budget_errors`), for any other gateway.

rate_limited: HTTP 429 that none of the above matched.
"""
from __future__ import annotations

import json
import logging
import math
import re
from email.utils import parsedate_to_datetime
from collections.abc import Iterable
from typing import Any, Optional

logger = logging.getLogger(__name__)

BUDGET_EXCEEDED = "budget_exceeded"
RATE_LIMITED = "rate_limited"
PROVIDER_AUTH_FAILED = "provider_auth_failed"
MODEL_NOT_FOUND = "model_not_found"
PROVIDER_UNAVAILABLE = "provider_unavailable"
PROVIDER_TIMEOUT = "provider_timeout"
CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
CONTENT_FILTERED = "content_filtered"
INVALID_REQUEST = "invalid_request"
STREAMING_REQUIRED = "streaming_required"
PROVIDER_NOT_CONFIGURED = "provider_not_configured"
PROVIDER_CREDENTIAL_REQUIRED = "provider_credential_required"
PROVIDER_ERROR = "provider_error"
JOB_TIMEOUT = "job_timeout"
INTERNAL_ERROR = "internal_error"

# code -> (Sinas's HTTP status, retryable). Retryable: the same call may well
# succeed later, unchanged.
CODES: dict[str, tuple[int, bool]] = {
    BUDGET_EXCEEDED: (402, False),
    RATE_LIMITED: (429, True),
    PROVIDER_AUTH_FAILED: (502, False),
    MODEL_NOT_FOUND: (502, False),
    PROVIDER_UNAVAILABLE: (503, True),
    PROVIDER_TIMEOUT: (504, True),
    CONTEXT_LENGTH_EXCEEDED: (422, False),
    CONTENT_FILTERED: (422, False),
    INVALID_REQUEST: (502, False),
    STREAMING_REQUIRED: (502, False),
    PROVIDER_NOT_CONFIGURED: (503, False),
    PROVIDER_CREDENTIAL_REQUIRED: (400, False),
    PROVIDER_ERROR: (502, False),
    JOB_TIMEOUT: (504, False),
    INTERNAL_ERROR: (500, False),
}

# The refusals: spend (budget_exceeded) or rate (rate_limited).
REFUSAL_CODES = frozenset({BUDGET_EXCEEDED, RATE_LIMITED})

# Sinas's own HTTP status for each code.
HTTP_STATUS = {code: status for code, (status, _) in CODES.items()}

BUDGET_ERROR_TYPES = frozenset({
    "budget_exceeded",
    "insufficient_quota",
    "billing_error",
    "billing_hard_limit_reached",
})
# Lowercase substrings of an error message that only budget refusals use.
BUDGET_MESSAGE_MARKERS = (
    "exceededbudget",
    "exceededtokenbudget",
    "budget has been exceeded",
    "budget limit exceeded",
    "exceeded budget",
    "credit balance is too low",
    "insufficient credits",
)

# A request too long for the model: error types/codes, and lowercase message
# substrings (OpenAI, Anthropic, Gemini, Mistral, gateways in front of them).
CONTEXT_ERROR_TYPES = frozenset({"context_length_exceeded", "string_above_max_length"})
CONTEXT_MESSAGE_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "prompt is too long",
    "input is too long",
    "exceeds the maximum number of tokens",
    "too many input tokens",
)
# A call a content filter or safety system blocked.
CONTENT_FILTER_TYPES = frozenset({
    "content_filter",
    "content_policy_violation",
    "content_filtered",
    "responsible_ai_policy_violation",
})
CONTENT_FILTER_MESSAGE_MARKERS = (
    "content management policy",
    "content_policy_violation",
    "content filter",
    "blocked due to safety",
    "safety system",
)
# Error types that mean "the model API is having trouble" even on a 200 (an
# `error` event in the middle of an Anthropic stream).
UNAVAILABLE_ERROR_TYPES = frozenset({"overloaded_error", "api_error", "server_error", "unavailable"})
RATE_ERROR_TYPES = frozenset({"rate_limit_error", "resource_exhausted"})
# The SDK's guard against a long call made without streaming.
STREAMING_GUARD_MARKERS = ("streaming is strongly recommended", "streaming is required")

MAX_RULES = 20
_RULE_KEYS = {"status", "type", "code", "message_contains"}


# The envelope's fields, as stream `error` events carry them (beside `error`).
ENVELOPE_KEYS = ("detail", "code", "retryable", "upstream_status", "retry_after")


class LLMError(Exception):
    """A failed LLM call, or agent turn, with a stable `code` (CODES).

    `str()` starts with the code (`rate_limited: ...`), so the code survives
    wherever only the text of an error is kept (a usage row's `error`, a
    chat's error reply)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        upstream_status: Optional[int] = None,
        provider: Optional[str] = None,
        retry_after: Optional[str] = None,
        retry_after_seconds: Optional[float] = None,
        status: Optional[int] = None,
    ):
        self.code = code
        # Sinas's HTTP status when it isn't the code's own: `invalid_request`
        # is a 502 when the model API rejected a request, but a 400 when
        # Sinas rejects the caller's (a malformed `provider_credentials`).
        self.status = status
        self.message = message
        self.upstream_status = upstream_status
        self.provider = provider
        # The model API's Retry-After header, verbatim, and how long it (or
        # its error body: Gemini's RetryInfo.retryDelay) asked to wait.
        self.retry_after = retry_after
        if retry_after_seconds is None and retry_after is not None:
            retry_after_seconds = _parse_retry_after_header(retry_after)
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{code}: {message}")

    @property
    def http_status(self) -> int:
        if self.status is not None:
            return self.status
        return CODES.get(self.code, (502, False))[0]

    def scrubbed(self, secrets: "Iterable[str]") -> "LLMError":
        """This error with every one of `secrets` masked in its message (a
        gateway that echoes the credential it was sent). Itself when none
        occurs."""
        message = scrub(self.message, secrets)
        if message == self.message:
            return self
        clone = type(self)(
            self.code,
            message,
            upstream_status=self.upstream_status,
            provider=self.provider,
            retry_after=self.retry_after,
            retry_after_seconds=self.retry_after_seconds,
            status=self.status,
        )
        return clone

    @property
    def retryable(self) -> bool:
        return CODES.get(self.code, (502, False))[1]

    def to_dict(self) -> dict[str, Any]:
        """The error envelope (module docstring)."""
        out: dict[str, Any] = {"detail": str(self), "code": self.code, "retryable": self.retryable}
        if self.upstream_status is not None:
            out["upstream_status"] = self.upstream_status
        if self.retry_after_seconds is not None:
            out["retry_after"] = self.retry_after_seconds
        return out

    def event_fields(self, limit: int = 300) -> dict[str, Any]:
        """The envelope as the fields of a stream `error` event: the message
        under `error` (as stream errors always had it) and `detail`."""
        detail = str(self)[:limit]
        return {**self.to_dict(), "error": detail, "detail": detail}

    def failure_fields(self) -> dict[str, Any]:
        """The envelope as a failed job's / sub-agent's fields: `error`,
        `error_code`, `retryable` and, when known, `upstream_status`."""
        out: dict[str, Any] = {"error": str(self), "error_code": self.code, "retryable": self.retryable}
        if self.upstream_status is not None:
            out["upstream_status"] = self.upstream_status
        return out

    def retry_after_header(self) -> Optional[str]:
        """A Retry-After header for an API answer, when the error is
        retryable and the model API said how long to wait."""
        if not self.retryable:
            return None
        if self.retry_after:
            return self.retry_after
        if self.retry_after_seconds is not None:
            return str(max(1, math.ceil(self.retry_after_seconds)))
        return None

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> "LLMError":
        """From a stream `error` event (or a job's failure fields) that
        carries a `code`: an LLMRefusalError for a refusal code."""
        default = BUDGET_EXCEEDED if cls is LLMRefusalError else INTERNAL_ERROR
        code = event.get("code") or event.get("error_code") or default
        message = str(event.get("error") or event.get("detail") or code)
        prefix = f"{code}: "
        klass = LLMRefusalError if code in REFUSAL_CODES else LLMError
        return klass(
            code,
            message[len(prefix):] if message.startswith(prefix) else message,
            upstream_status=_int_or_none(event.get("upstream_status")),
            retry_after_seconds=_float_or_none(event.get("retry_after")),
        )


class LLMRefusalError(LLMError):
    """A call the model API or a gateway refused for budget or rate
    (REFUSAL_CODES)."""


REDACTED = "[redacted]"


def scrub(text: str, secrets: "Iterable[str]") -> str:
    """`text` with every occurrence of each secret masked, longest first (a
    prefixed value before the bare one)."""
    if not text:
        return text
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


def contains_secret(text: str, secrets: "Iterable[str]") -> bool:
    return bool(text) and any(s and s in text for s in secrets)


def _int_or_none(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float_or_none(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def error_code(value: Any) -> Optional[str]:
    """The code of an LLMError or of a stream event / failure record that
    carries one (an SDK error's own `code` attribute is the provider's, not
    ours, and doesn't count)."""
    if isinstance(value, LLMError):
        return value.code
    if isinstance(value, dict):
        code = value.get("code") or value.get("error_code")
        return code if isinstance(code, str) and code in CODES else None
    return None


def refusal_code(value: Any) -> Optional[str]:
    """The refusal code (budget_exceeded, rate_limited) of an exception or
    event, if it is one."""
    code = error_code(value)
    return code if code in REFUSAL_CODES else None


# ── Rules ──────────────────────────────────────────────────────────────────


def validate_budget_rules(value: Any) -> list[dict[str, Any]]:
    """A provider's `config.budget_errors`: a list of match rules, each a
    combination of `status` (an int or a list of ints), `type`, `code` and
    `message_contains` (strings); a rule matches an error when every key it
    has matches. Raises ValueError naming the first problem."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("budget_errors must be a list of match rules")
    if len(value) > MAX_RULES:
        raise ValueError(f"at most {MAX_RULES} budget_errors rules")
    out = []
    for i, rule in enumerate(value):
        where = f"budget_errors[{i}]"
        if not isinstance(rule, dict) or not rule:
            raise ValueError(f"{where} must be an object with status, type, code and/or message_contains")
        unknown = set(rule) - _RULE_KEYS
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        status = rule.get("status")
        if status is not None:
            statuses = status if isinstance(status, list) else [status]
            if not statuses or not all(
                isinstance(s, int) and not isinstance(s, bool) and 400 <= s <= 599 for s in statuses
            ):
                raise ValueError(f"{where}: status must be an HTTP error status (400-599) or a list of them")
        for key in ("type", "code", "message_contains"):
            if key in rule and (not isinstance(rule[key], str) or not rule[key].strip() or len(rule[key]) > 200):
                raise ValueError(f"{where}: {key} must be a non-empty string of at most 200 characters")
        out.append(dict(rule))
    return out


def _rule_matches(rule: dict[str, Any], status: Optional[int], types: set[str], message: str) -> bool:
    if "status" in rule:
        statuses = rule["status"] if isinstance(rule["status"], list) else [rule["status"]]
        if status not in statuses:
            return False
    if "type" in rule and rule["type"].lower() not in types:
        return False
    if "code" in rule and rule["code"].lower() not in types:
        return False
    if "message_contains" in rule and rule["message_contains"].lower() not in message:
        return False
    return True


# ── Classification ─────────────────────────────────────────────────────────


def _error_fields(body: Any) -> tuple[set[str], str]:
    """The lowercased type/code/status strings and the message of an error
    body: OpenAI-style `{"error": {"type", "code", "message"}}`, Anthropic's
    `{"type": "error", "error": {...}}`, Google's `{"error": {"code",
    "status", "message"}}` (also as a one-element list), or flat
    `{"detail" / "message"}`."""
    types: set[str] = set()
    messages: list[str] = []
    if isinstance(body, list) and body and isinstance(body[0], dict):
        body = body[0]
    if isinstance(body, dict):
        for container in (body, body.get("error")):
            if not isinstance(container, dict):
                if isinstance(container, str):
                    messages.append(container)
                continue
            for key in ("type", "code", "status"):
                value = container.get(key)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    types.add(str(value).lower())
            for key in ("message", "detail", "error_message"):
                value = container.get(key)
                if isinstance(value, str):
                    messages.append(value)
    elif isinstance(body, str):
        messages.append(body)
    return types, " ".join(messages).lower()


def classify(
    status: Optional[int], body: Any, message: str = "", rules: Optional[list[dict[str, Any]]] = None
) -> Optional[str]:
    """BUDGET_EXCEEDED, RATE_LIMITED or None for an error response."""
    types, body_message = _error_fields(body)
    text = f"{body_message} {(message or '').lower()}"
    for rule in rules or []:
        try:
            if _rule_matches(rule, status, types, text):
                return BUDGET_EXCEEDED
        except Exception:  # a rule written by hand into the database
            continue
    if status == 402:
        return BUDGET_EXCEEDED
    if types & BUDGET_ERROR_TYPES:
        return BUDGET_EXCEEDED
    if status is not None and status >= 400 and any(marker in text for marker in BUDGET_MESSAGE_MARKERS):
        return BUDGET_EXCEEDED
    if status == 429:
        return RATE_LIMITED
    return None


def classify_error(
    status: Optional[int], body: Any, message: str = "", rules: Optional[list[dict[str, Any]]] = None
) -> Optional[str]:
    """The code of an error a model API answered with (an HTTP status, or an
    error event in a stream), or None when it isn't one: a refusal first
    (`classify`), then by the body's error type and message, then by
    status."""
    refusal = classify(status, body, message, rules)
    if refusal is not None:
        return refusal
    types, body_message = _error_fields(body)
    text = f"{body_message} {(message or '').lower()}"
    failed = status is not None and status >= 400
    if failed or types:
        if types & CONTEXT_ERROR_TYPES or any(marker in text for marker in CONTEXT_MESSAGE_MARKERS):
            return CONTEXT_LENGTH_EXCEEDED
        if types & CONTENT_FILTER_TYPES or any(marker in text for marker in CONTENT_FILTER_MESSAGE_MARKERS):
            return CONTENT_FILTERED
    if types & RATE_ERROR_TYPES:
        return RATE_LIMITED
    if types & UNAVAILABLE_ERROR_TYPES:
        return PROVIDER_UNAVAILABLE
    if not failed:
        return None
    if status in (401, 403):
        return PROVIDER_AUTH_FAILED
    if status == 404:
        return MODEL_NOT_FOUND
    if status in (408, 504, 524):
        return PROVIDER_TIMEOUT
    if status == 413:
        return CONTEXT_LENGTH_EXCEEDED
    if status in (400, 422):
        return INVALID_REQUEST
    if status >= 500:
        return PROVIDER_UNAVAILABLE
    return PROVIDER_ERROR


def _transport_code(exc: BaseException) -> Optional[str]:
    """The code of an error raised before (or instead of) an answer: the
    model API timed out or could not be reached, or the SDK's streaming
    guard. By class name, so it covers every SDK (each has its own
    APITimeoutError / APIConnectionError) and httpx flavour."""
    names = {klass.__name__ for klass in type(exc).__mro__}
    if names & {"APITimeoutError", "TimeoutException"}:
        return PROVIDER_TIMEOUT
    if names & {"APIConnectionError", "TransportError", "NetworkError", "RemoteProtocolError"}:
        return PROVIDER_UNAVAILABLE
    if isinstance(exc, ValueError) and any(marker in str(exc).lower() for marker in STREAMING_GUARD_MARKERS):
        return STREAMING_REQUIRED
    return None


def _is_http_exception(exc: BaseException) -> bool:
    """An API's own HTTPException (it has a status_code too): never a model
    API's answer."""
    return any(klass.__name__ == "HTTPException" for klass in type(exc).__mro__)


def _status_of(exc: BaseException) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _body_of(exc: BaseException) -> Any:
    body = getattr(exc, "body", None)
    if body is not None:
        return body
    response = getattr(exc, "response", None)
    if response is None:
        return None
    try:
        return response.json()
    except Exception:
        try:
            return response.text
        except Exception:
            return None


# ── Retry-After ────────────────────────────────────────────────────────────

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s)?\s*$")
_RETRY_IN_MESSAGE = re.compile(r"retry (?:in|after) (\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|seconds?)\b")


def _parse_retry_after_header(value: str) -> Optional[float]:
    """Seconds, or an HTTP date."""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime, timezone

        when = parsedate_to_datetime(value)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def _parse_duration(value: Any) -> Optional[float]:
    """A protobuf Duration as JSON: "57s", "1.5s", or {"seconds", "nanos"}."""
    if isinstance(value, str):
        match = _DURATION.match(value)
        if match:
            amount = float(match.group(1))
            return amount / 1000 if match.group(2) == "ms" else amount
        return None
    if isinstance(value, dict):
        seconds = _float_or_none(value.get("seconds")) or 0.0
        nanos = _float_or_none(value.get("nanos")) or 0.0
        return seconds + nanos / 1e9 if (seconds or nanos) else None
    return None


def _retry_delay_in_body(body: Any, depth: int = 0) -> Optional[float]:
    """Google's `RetryInfo.retryDelay`, wherever the body nests it
    (`error.details[]`, also behind an OpenAI-compatible gateway)."""
    if depth > 6:
        return None
    if isinstance(body, dict):
        if "retryDelay" in body:
            delay = _parse_duration(body["retryDelay"])
            if delay is not None:
                return delay
        values = body.values()
    elif isinstance(body, list):
        values = body
    else:
        return None
    for value in values:
        delay = _retry_delay_in_body(value, depth + 1)
        if delay is not None:
            return delay
    return None


def _retry_after_of(exc: BaseException, body: Any, message: str) -> tuple[Optional[str], Optional[float]]:
    """(the Retry-After header, seconds to wait) of an error response: the
    `retry-after-ms` / `retry-after` headers, Google's RetryInfo in the body,
    or a "retry in 2.5s" in the message."""
    header = None
    seconds = None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            header = headers.get("retry-after")
            millis = headers.get("retry-after-ms")
            if millis is not None:
                seconds = max(0.0, float(millis) / 1000)
        except Exception:
            header = None
    if seconds is None and header is not None:
        seconds = _parse_retry_after_header(header)
    if seconds is None:
        seconds = _retry_delay_in_body(body)
    if seconds is None and message:
        match = _RETRY_IN_MESSAGE.search(message.lower())
        if match:
            amount = float(match.group(1))
            seconds = amount / 1000 if match.group(2).startswith("m") else amount
    return header, seconds


# ── From an exception ──────────────────────────────────────────────────────

_REASONS = {
    BUDGET_EXCEEDED: "refused the call (budget or quota exhausted, ",
    RATE_LIMITED: "refused the call (rate limited, ",
    MODEL_NOT_FOUND: "could not find the model{model} or its endpoint (",
    PROVIDER_UNAVAILABLE: "is unavailable or overloaded (",
    PROVIDER_TIMEOUT: "did not answer in time (",
    CONTEXT_LENGTH_EXCEEDED: "refused the call: the conversation is too long for the model's context window (",
    CONTENT_FILTERED: "refused the call: its content filter or safety system blocked it (",
    INVALID_REQUEST: "rejected the request as invalid (",
    PROVIDER_ERROR: "failed (",
}


def error_from_exception(
    exc: BaseException,
    rules: Optional[list[dict[str, Any]]] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[LLMError]:
    """The LLMError an SDK, HTTP or transport error stands for (an
    LLMRefusalError for a refusal), or None for any other error, or one that
    already is an LLMError."""
    if isinstance(exc, LLMError) or _is_http_exception(exc):
        return None
    where = f"LLM provider '{provider}'" if provider else "The LLM provider"
    status = _status_of(exc)
    body = _body_of(exc) if status is not None or getattr(exc, "body", None) is not None else None
    raw_message = str(getattr(exc, "message", None) or exc)
    code = classify_error(status, body, raw_message, rules) if (status is not None or body is not None) else None
    if code is None:
        code = _transport_code(exc)
        if code is None:
            return None
        if code == STREAMING_REQUIRED:
            message = (
                f"{where}'s SDK refuses this call without streaming (the request may take too "
                "long): lower the agent's max_tokens"
            )
        elif code == PROVIDER_TIMEOUT:
            message = f"{where} did not answer in time ({type(exc).__name__})"
        else:
            message = f"{where} could not be reached ({type(exc).__name__})"
        return LLMError(code, message, provider=provider)

    upstream = status if status is not None and status >= 400 else None
    on = f"HTTP {upstream}" if upstream is not None else "in the stream"
    if code == PROVIDER_AUTH_FAILED:
        # Never the upstream message: an auth error can quote part of the key.
        message = (
            f"{where} rejected its credentials ({on}). An admin can check the provider's "
            "API key under LLM Providers."
        )
    else:
        reason = _REASONS[code].format(model=f" '{model}'" if model else "")
        message = f"{where} {reason}{on}): {raw_message[:500]}"
    header, seconds = _retry_after_of(exc, body, f"{_error_fields(body)[1]} {raw_message}")
    klass = LLMRefusalError if code in REFUSAL_CODES else LLMError
    return klass(
        code,
        message,
        upstream_status=upstream,
        provider=provider,
        retry_after=header,
        retry_after_seconds=seconds,
    )


def refusal_from_exception(
    exc: BaseException, rules: Optional[list[dict[str, Any]]] = None, provider: Optional[str] = None
) -> Optional[LLMRefusalError]:
    """The refusal (budget_exceeded, rate_limited) an SDK or HTTP error
    stands for, or None for any other error (or one that already is a
    refusal)."""
    if _status_of(exc) is None:
        return None
    error = error_from_exception(exc, rules, provider)
    return error if isinstance(error, LLMRefusalError) else None


def turn_error(exc: BaseException) -> LLMError:
    """What a failed turn reports: the LLMError it failed with, the one a raw
    provider error stands for, or `internal_error` with the error's text."""
    if isinstance(exc, LLMError):
        return exc
    return error_from_exception(exc) or LLMError(INTERNAL_ERROR, str(exc)[:500] or type(exc).__name__)


def chat_error_content(error: LLMError) -> str:
    """The assistant reply a failed turn leaves in its chat: what happened,
    with the error (its code first)."""
    if error.retryable or error.code == INTERNAL_ERROR:
        headline = "An error occurred while processing your message. Please try again."
    else:
        headline = "The agent could not process your message."
    return f"{headline}\n\nError: {str(error)[:300]}"


def failure_fields(exc: BaseException) -> dict[str, Any]:
    """A failed job's error for its status record and its parent's report
    (`error`, `error_code`, `retryable`, `upstream_status`). An error that
    isn't an LLM failure keeps its own text as `error`."""
    error = turn_error(exc)
    fields = error.failure_fields()
    if error is not exc and error.code == INTERNAL_ERROR:
        fields["error"] = str(exc)
    return fields


def parse_rules_header(value: Optional[str]) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        rules = json.loads(value)
        return rules if isinstance(rules, list) else []
    except Exception:
        return []


# ── HTTP ───────────────────────────────────────────────────────────────────

# The OpenAI `error.type` that fits each of Sinas's HTTP statuses.
_OPENAI_TYPES = {402: "insufficient_quota", 422: "invalid_request_error", 429: "rate_limit_error"}


def openai_error_body(error: LLMError) -> dict[str, Any]:
    """The envelope in OpenAI's error format, for the OpenAI-compatible
    adapter: `{"error": {"message", "type", "code", "param", ...}}`, with
    `retryable` (and `upstream_status`, `retry_after`) beside them. OpenAI's
    SDKs raise it as an APIError, also as a `data:` line of a stream."""
    envelope = error.to_dict()
    body = {
        "message": envelope.pop("detail"),
        "type": _OPENAI_TYPES.get(error.http_status, "server_error" if error.http_status >= 500 else "invalid_request_error"),
        "param": None,
        **envelope,
    }
    return {"error": body}


def install_error_handler(app: Any, *, openai_format: bool = False) -> None:
    """Answer an LLMError that reaches the API with its own status and the
    envelope (`{"detail": "budget_exceeded: ...", "code": "budget_exceeded",
    "retryable": false}`, 402), not as a generic 500; in OpenAI's error
    format on the OpenAI-compatible adapter. A retryable error the model API
    said to wait on carries Retry-After."""
    from fastapi.responses import JSONResponse

    async def _handler(request: Any, exc: LLMError) -> JSONResponse:
        retry_after = exc.retry_after_header()
        headers = {"Retry-After": retry_after} if retry_after else None
        body = openai_error_body(exc) if openai_format else exc.to_dict()
        return JSONResponse(body, status_code=exc.http_status, headers=headers)

    app.add_exception_handler(LLMError, _handler)


# The name it had when it answered refusals only.
install_refusal_handler = install_error_handler
