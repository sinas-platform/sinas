"""Security and correctness regressions for webhook agent targets (PR #111 review).

Each test pins a specific defect found in review:
  - template sandbox escape (RCE in the API process)
  - partial template renders being accepted as if fully rendered
  - raw-mode `_status` accepting bools / body-less codes
  - dedup TTL key-casing mismatch between the REST and config write paths

The authorization fixes (ownership enforcement, is_active revalidation) are
covered in test_webhook_targets.py where the request fixtures live.
"""

import pytest
from jinja2.exceptions import SecurityError

from app.services.dedup_service import _dedup_ttl
from app.services.resource_serializers import _serialize_dedup
from app.services.template_renderer import (
    render_template,
    render_webhook_template,
    render_webhook_template_checked,
)


# --------------------------------------------------------------------------
# Template sandbox — templates are user-authored but rendered in-process
# --------------------------------------------------------------------------

ESCAPES = [
    "{{ cycler.__init__.__globals__ }}",
    "{{ ''.__class__.__mro__ }}",
    "{{ joiner.__init__.__globals__['os'] }}",
    "{{ namespace.__init__.__globals__ }}",
]


@pytest.mark.parametrize("template", ESCAPES)
def test_webhook_env_blocks_sandbox_escape(template):
    """A webhook template must not reach Python internals: rendering happens in
    the API worker, which holds SECRET_KEY/ENCRYPTION_KEY."""
    with pytest.raises(SecurityError):
        render_webhook_template(template, {})


@pytest.mark.parametrize("template", ESCAPES)
def test_general_env_blocks_sandbox_escape(template):
    """Same for agent prompts / function params (pre-existing path)."""
    with pytest.raises(SecurityError):
        render_template(template, {})


def test_escape_is_not_silently_swallowed_as_a_missing_variable():
    """_LoggingUndefined renders unknown payload fields as '' — it must not do
    that for a sandbox violation, or an attempted escape is indistinguishable
    from a provider renaming a field."""
    with pytest.raises(SecurityError):
        render_webhook_template("{{ cycler.__init__ }}", {})


def test_missing_payload_field_stays_tolerant():
    """The tolerance that motivated the custom Undefined must survive the fix."""
    out = render_webhook_template("New issue {{ issue.key }}", {})
    assert out == "New issue "


# --------------------------------------------------------------------------
# Partial renders — the dangerous case is non-empty-but-incomplete
# --------------------------------------------------------------------------

def test_checked_render_reports_undefined_on_partial_render():
    rendered, had_undefined = render_webhook_template_checked(
        "New issue {{ issue.key }}: {{ issue.fields.summary }}", {}
    )
    assert rendered == "New issue : "  # non-empty, so an emptiness check accepts it
    assert had_undefined is True


def test_checked_render_reports_clean_on_full_render():
    rendered, had_undefined = render_webhook_template_checked(
        "New issue {{ key }}", {"key": "AB-1"}
    )
    assert rendered == "New issue AB-1"
    assert had_undefined is False


def test_partial_session_key_is_detected():
    """'jira-{{ issue.key }}' with no issue renders 'jira-' — truthy, and would
    collapse every malformed delivery into one shared chat."""
    rendered, had_undefined = render_webhook_template_checked("jira-{{ issue.key }}", {})
    assert rendered == "jira-"
    assert had_undefined is True


# --------------------------------------------------------------------------
# Dedup TTL — three-way key mismatch (REST snake / config camel / consumer snake)
# --------------------------------------------------------------------------

def test_dedup_ttl_reads_rest_shape():
    assert _dedup_ttl({"key": "$.id", "ttl_seconds": 3600}) == 3600


def test_dedup_ttl_reads_legacy_config_shape():
    """Rows written by config apply before the fix carry camelCase; they must
    keep their configured TTL rather than silently falling back to 300."""
    assert _dedup_ttl({"key": "$.id", "ttlSeconds": 3600}) == 3600


def test_dedup_ttl_defaults_when_absent_or_invalid():
    assert _dedup_ttl({"key": "$.id"}) == 300
    assert _dedup_ttl({"key": "$.id", "ttl_seconds": "soon"}) == 300
    # bool is an int subclass in Python — must not be accepted as a TTL
    assert _dedup_ttl({"key": "$.id", "ttl_seconds": True}) == 300


def test_dedup_export_emits_config_casing():
    """Export must produce the shape WebhookDedupConfig actually parses, or an
    export -> re-apply round-trip silently resets the TTL to the default."""
    assert _serialize_dedup({"key": "$.id", "ttl_seconds": 3600}) == {
        "key": "$.id",
        "ttlSeconds": 3600,
    }


def test_dedup_export_round_trips_through_config_schema():
    from app.schemas.config import WebhookDedupConfig

    exported = _serialize_dedup({"key": "$.id", "ttl_seconds": 3600})
    parsed = WebhookDedupConfig(**exported)
    assert parsed.ttlSeconds == 3600  # not the 300 default


def test_dedup_export_none_stays_none():
    assert _serialize_dedup(None) is None
    assert _serialize_dedup({}) is None


# --------------------------------------------------------------------------
# PATCH semantics — "absent" vs "explicitly cleared"
# --------------------------------------------------------------------------

def test_update_schema_distinguishes_absent_from_null():
    """`is not None` cannot express 'clear this field', so clearing a session
    key or disabling dedup silently did nothing while reporting success."""
    from app.schemas.webhook import WebhookUpdate

    absent = WebhookUpdate(description="x")
    assert "session_key_template" not in absent.model_fields_set
    assert "dedup" not in absent.model_fields_set

    cleared = WebhookUpdate(session_key_template=None, dedup=None)
    assert "session_key_template" in cleared.model_fields_set
    assert "dedup" in cleared.model_fields_set
