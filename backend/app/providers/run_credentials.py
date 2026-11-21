"""Per-run provider credentials: a caller's own key for a chat's LLM calls.

A deployment can put a gateway in front of a model API that meters spend per
presented key, one key per end user. The service that starts a run then
supplies that user's key, and every LLM call belonging to the run (the chat's
later messages, approvals, resumes, background jobs, wakeups, compaction, its
sub-agents' chats) presents it instead of the provider's configured key.

Opt-in per provider, in its config:

    config:
      run_credential:
        enabled: true
        header: null        # null: in place of the provider's own key
        prefix: ""          # with a named header: sent as prefix + credential
        required: false     # true: never fall back to the configured key

- `header: null`: the credential takes the place of the provider's own key,
  in the header that provider type sends it in (NATIVE_HEADERS): `Authorization:
  Bearer <credential>` for OpenAI-style APIs (OpenAI, Gemini's and Mistral's
  OpenAI-compatible endpoints), `x-api-key` for Anthropic, `api-key` for Azure
  OpenAI. Ollama has no key of its own, so it needs a named header.
- `header: "X-Some-Header"`: the credential goes in that header (after
  `prefix`), and the provider's configured key still authenticates the call.
  Credential headers (`Authorization`, `x-api-key`, ...) may be named, unlike
  in identity header templates; framing, trace context and Sinas's private
  headers may not.
- `required: false`: a chat without a credential for the provider (or with an
  expired one) is served with the configured key. `required: true`: the call
  fails with `provider_credential_required` instead.

A credential is given per chat (`provider_credentials` on chat create,
invoke, and message send), bound to the chat by provider name, stored
encrypted (app/models/chat_provider_credential.py) and never returned. It is
resolved per call, in `create_provider`, from the call's usage context; the
OpenAI-compatible adapter can also give one for a single request
(`X-Sinas-Provider-Credential`), held in REQUEST_CREDENTIAL for that request
only.

The value travels to the HTTP client as a reference, never as itself: the
provider adds a private header naming a `RunCredentialHeaders` holder (and a
placeholder value in each credential header), and the client's request hook
(`clients._apply_run_credential`) swaps in the real values as the request is
sent. An SDK's request options, debug logs and retries never see it, and a
request that somehow skipped the hook carries the placeholder, not the
configured key.
"""
from __future__ import annotations

import re
import secrets
import weakref
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional

# expires_in (seconds): default and bounds.
DEFAULT_EXPIRES_IN = 86400
MAX_EXPIRES_IN = 2592000
MAX_CREDENTIAL_LENGTH = 4096
MAX_PREFIX_LENGTH = 64

SETTINGS_KEY = "run_credential"
_SETTINGS_FIELDS = ("enabled", "header", "prefix", "required")

# The header each provider type sends its own key in, and how. `header: null`
# replaces it; a type missing here can't use `header: null`.
NATIVE_HEADERS: dict[str, tuple[str, str]] = {
    "openai": ("Authorization", "Bearer "),
    "gemini": ("Authorization", "Bearer "),
    "mistral": ("Authorization", "Bearer "),
    "anthropic": ("X-Api-Key", ""),
    "azure": ("api-key", ""),
    "azure_openai": ("api-key", ""),
}

_HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,99}$")
# A named credential header may be a credential header (that is its point),
# but never one that frames the request, carries trace context, or is an
# SDK's or Sinas's own.
_FORBIDDEN_HEADERS = frozenset({
    "host", "content-length", "content-type", "content-encoding", "transfer-encoding",
    "connection", "keep-alive", "te", "trailer", "upgrade", "expect", "accept",
    "accept-encoding", "user-agent", "cookie", "set-cookie", "forwarded", "x-forwarded-for",
    "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "idempotency-key", "x-should-retry",
    "traceparent", "tracestate", "baggage", "b3", "uber-trace-id", "sentry-trace",
    "x-cloud-trace-context", "grpc-trace-bin", "x-amzn-trace-id",
})
_FORBIDDEN_PREFIXES = (
    "x-sinas-", "anthropic-", "x-stainless-", "sec-", "x-b3-", "uberctx-", "ot-tracer-",
    "x-datadog-",
)

#: Sent in place of a credential in an SDK's request options; replaced by
#: the client's request hook. Never valid as a key.
PLACEHOLDER = "sinas-run-credential"

#: A credential for one request, set by the OpenAI-compatible adapter
#: (`X-Sinas-Provider-Credential`): (provider name, credential). Wins over the
#: chat's stored one for that provider; never stored.
REQUEST_CREDENTIAL: ContextVar[Optional[tuple[str, str]]] = ContextVar(
    "sinas_request_provider_credential", default=None
)


@dataclass(frozen=True)
class RunCredentialSettings:
    enabled: bool = False
    header: Optional[str] = None
    prefix: str = ""
    required: bool = False


def validate_settings(
    value: Any, provider_type: Optional[str] = None, identity_headers: Any = None
) -> Optional[dict[str, Any]]:
    """A provider's `config.run_credential`, checked: raises ValueError naming
    the first problem; returns it with every field set (None for none)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("run_credential must be an object with enabled, header, prefix and required")
    unknown = set(value) - set(_SETTINGS_FIELDS)
    if unknown:
        raise ValueError(
            f"run_credential: unknown key(s) {', '.join(sorted(map(str, unknown)))}; "
            f"allowed: {', '.join(_SETTINGS_FIELDS)}"
        )
    enabled = value.get("enabled", False)
    required = value.get("required", False)
    header = value.get("header")
    prefix = value.get("prefix", "")
    if prefix is None:
        prefix = ""
    for name, flag in (("enabled", enabled), ("required", required)):
        if not isinstance(flag, bool):
            raise ValueError(f"run_credential.{name} must be true or false")
    if required and not enabled:
        raise ValueError("run_credential.required needs run_credential.enabled")
    if header is not None:
        if not isinstance(header, str) or not _HEADER_NAME.match(header):
            raise ValueError(
                "run_credential.header must be a header name (letters, digits and '-', at most "
                "100 characters) or null"
            )
        lowered = header.lower()
        if lowered in _FORBIDDEN_HEADERS or lowered.startswith(_FORBIDDEN_PREFIXES):
            raise ValueError(
                f"run_credential.header '{header}' can't carry a credential: framing, trace "
                "context and SDK or Sinas headers are reserved"
            )
        if isinstance(identity_headers, dict) and any(
            isinstance(n, str) and n.lower() == lowered for n in identity_headers
        ):
            raise ValueError(
                f"run_credential.header '{header}' is also a header template (config.headers)"
            )
    if not isinstance(prefix, str) or len(prefix) > MAX_PREFIX_LENGTH or not all(
        0x20 <= ord(c) <= 0x7E for c in prefix
    ):
        raise ValueError(
            f"run_credential.prefix must be printable ASCII, at most {MAX_PREFIX_LENGTH} characters"
        )
    if prefix and header is None:
        raise ValueError(
            "run_credential.prefix applies to a named header only; with header null the "
            "credential is sent the way the provider sends its own key"
        )
    if enabled and header is None and provider_type is not None:
        if provider_type.lower() not in NATIVE_HEADERS:
            raise ValueError(
                f"run_credential.header can't be null for provider type '{provider_type}': it "
                "sends no key of its own to replace. Name the header the credential goes in."
            )
    return {"enabled": enabled, "header": header, "prefix": prefix, "required": required}


def settings_of(provider_config: Any, provider_type: Optional[str] = None) -> RunCredentialSettings:
    """A provider's run credential settings as stored. A value that fails
    validation (written by hand into the database) is read as disabled, but
    as required when it says so, so that it never falls back to the
    configured key by mistake."""
    raw = provider_config.get(SETTINGS_KEY) if isinstance(provider_config, dict) else None
    if raw is None:
        return RunCredentialSettings()
    try:
        checked = validate_settings(raw, provider_type, provider_config.get("headers"))
    except ValueError:
        import logging

        logging.getLogger(__name__).warning("LLM provider run_credential setting ignored: invalid")
        required = isinstance(raw, dict) and raw.get("required") is True
        return RunCredentialSettings(enabled=False, required=required)
    return RunCredentialSettings(**checked) if checked else RunCredentialSettings()


def check_credential(value: Any) -> str:
    """A credential as given, checked. Raises ValueError with a message that
    never quotes it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("credential is empty")
    if len(value) > MAX_CREDENTIAL_LENGTH:
        raise ValueError(f"credential is longer than {MAX_CREDENTIAL_LENGTH} characters")
    if value != value.strip() or not all(0x21 <= ord(c) <= 0x7E for c in value):
        raise ValueError(
            "credential must be printable ASCII without spaces or control characters"
        )
    return value


def check_expires_in(value: Any) -> int:
    """expires_in (seconds), defaulted and checked. Raises ValueError."""
    if value is None:
        return DEFAULT_EXPIRES_IN
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expires_in must be a whole number of seconds")
    if not 1 <= value <= MAX_EXPIRES_IN:
        raise ValueError(f"expires_in must be between 1 and {MAX_EXPIRES_IN} seconds")
    return value


def credential_headers(
    settings: RunCredentialSettings, provider_type: str, credential: str
) -> dict[str, str]:
    """The header(s) a call presents the credential in."""
    if settings.header:
        return {settings.header: f"{settings.prefix}{credential}"}
    native = NATIVE_HEADERS.get(provider_type.lower())
    if native is None:  # validated away; never sent unattached
        raise ValueError(f"provider type '{provider_type}' has no key header to replace")
    name, scheme = native
    return {name: f"{scheme}{credential}"}


# ── The value's way to the HTTP client ─────────────────────────────────────

_holders: "weakref.WeakValueDictionary[str, RunCredentialHeaders]" = weakref.WeakValueDictionary()


class RunCredentialHeaders:
    """The credential headers of one provider instance, registered under a
    random reference for the client's request hook. Lives as long as the
    provider that holds it; a request whose holder is gone fails."""

    __slots__ = ("_headers", "_credential", "ref", "__weakref__")

    def __init__(self, headers: dict[str, str], credential: str):
        self._headers = dict(headers)
        self._credential = credential
        self.ref = secrets.token_urlsafe(18)
        _holders[self.ref] = self

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._headers)

    def placeholders(self) -> dict[str, str]:
        """What an SDK's request options carry instead of the values."""
        return {name: PLACEHOLDER for name in self._headers}

    def secrets(self) -> list[str]:
        """Every form of the credential a response or error could echo: the
        header values, and the bare credential behind a prefix or scheme."""
        values = {self._credential, *self._headers.values()}
        return sorted((v for v in values if v), key=len, reverse=True)

    def __repr__(self) -> str:  # never the values
        return f"<RunCredentialHeaders {sorted(self._headers)}>"


def holder_for(ref: str) -> Optional[RunCredentialHeaders]:
    return _holders.get(ref)
