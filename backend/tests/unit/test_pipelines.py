"""Unit tests for the pipelines feature: mapping resolution, definition
validation, and the pure runner helpers.

Covers the invariants the ADR calls out:
- `.$` keys are JMESPath, plain keys are literals, missing paths yield null;
- invalid definitions are rejected at save time with actionable messages;
- cursor/perUser/asTool/output constraints;
- load-step identifier quoting and effective concurrency defaults.

Runner semantics that need Redis + Postgres (single-flight, cursor
commit/hold, fan-out) are integration-tested on the dev stack.
"""
import types

import pytest

from app.services import pipeline_mapping as pm
from app.services.pipeline_validation import validate_pipeline_definition


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------

CTX = {
    "input": {"query": "hello", "n": 3},
    "steps": {
        "fetch": {"output": {"statusCode": 200, "body": {"historyId": "777", "items": [1, 2]}}},
    },
    "cursor": "42",
    "run": {"id": "r1", "triggerType": "SCHEDULE", "userId": "u1"},
}


def test_literal_values_pass_through():
    assert pm.resolve_template({"a": 1, "b": "text", "c": {"d": True}}, CTX) == {
        "a": 1, "b": "text", "c": {"d": True}
    }


def test_expression_keys_are_evaluated_and_renamed():
    out = pm.resolve_template({"startHistoryId.$": "cursor", "q.$": "input.query"}, CTX)
    assert out == {"startHistoryId": "42", "q": "hello"}


def test_nested_and_list_templates():
    out = pm.resolve_template(
        {"outer": {"inner.$": "steps.fetch.output.body.historyId"}, "arr": [{"x.$": "input.n"}]},
        CTX,
    )
    assert out == {"outer": {"inner": "777"}, "arr": [{"x": 3}]}


def test_missing_path_yields_none():
    assert pm.resolve_template({"gone.$": "steps.nope.output"}, CTX) == {"gone": None}


def test_whole_field_expression_via_resolve_field():
    step = {"input.$": "steps.fetch.output.body"}
    assert pm.resolve_field(step, "input", CTX) == {"historyId": "777", "items": [1, 2]}


def test_resolve_field_literal_and_default():
    assert pm.resolve_field({"input": {"a.$": "cursor"}}, "input", CTX) == {"a": "42"}
    assert pm.resolve_field({}, "input", CTX, default={}) == {}


def test_jmespath_projection():
    out = pm.evaluate_expression("steps.fetch.output.body.items[*]", CTX)
    assert out == [1, 2]


def test_validate_template_rejects_bad_expression():
    errors = pm.validate_template({"x.$": "steps.[unclosed"}, "steps[0].input")
    assert len(errors) == 1 and "invalid JMESPath" in errors[0]


def test_validate_template_rejects_literal_and_expression_for_same_key():
    errors = pm.validate_template({"x": 1, "x.$": "cursor"}, "input")
    assert any("pick literal or expression" in e for e in errors)


# ---------------------------------------------------------------------------
# Definition validation
# ---------------------------------------------------------------------------


def _connector_step(**over):
    step = {
        "name": "fetch",
        "type": "connector",
        "connector": "google/gmail",
        "operation": "list-history",
    }
    step.update(over)
    return step


def test_valid_minimal_pipeline():
    assert validate_pipeline_definition([_connector_step()]) == []


def test_steps_must_be_nonempty():
    assert validate_pipeline_definition([]) == ["steps must be a non-empty list"]


def test_unknown_step_type_rejected():
    errors = validate_pipeline_definition([{"name": "x", "type": "webhook"}])
    assert any("type must be one of" in e for e in errors)


def test_unknown_keys_rejected():
    errors = validate_pipeline_definition([_connector_step(imput={"a": 1})])
    assert any("unknown keys" in e for e in errors)


def test_duplicate_step_names_rejected():
    errors = validate_pipeline_definition([_connector_step(), _connector_step()])
    assert any("duplicate step names" in e for e in errors)


def test_bad_resource_ref_rejected():
    errors = validate_pipeline_definition([_connector_step(connector="no-slash")])
    assert any("namespace/name" in e for e in errors)


def test_input_and_input_expr_mutually_exclusive():
    errors = validate_pipeline_definition(
        [_connector_step(**{"input": {"a": 1}, "input.$": "input"})]
    )
    assert any("both 'input' and 'input.$'" in e for e in errors)


def test_cursor_requires_param_and_valid_path():
    errors = validate_pipeline_definition(
        [_connector_step(cursor={"param": "start", "path": "body.["})]
    )
    assert any("invalid JMESPath" in e for e in errors)

    errors = validate_pipeline_definition([_connector_step(cursor={"path": "body.h"})])
    assert any("cursor.param is required" in e for e in errors)


def test_at_most_one_cursor_step():
    s1 = _connector_step(cursor={"param": "a", "path": "body.h"})
    s2 = _connector_step(name="second", cursor={"param": "b", "path": "body.h"})
    errors = validate_pipeline_definition([s1, s2])
    assert any("at most one step may declare cursor" in e for e in errors)


def test_retry_bounds():
    errors = validate_pipeline_definition(
        [_connector_step(retry={"maxAttempts": 99, "backoff": "cubic"})]
    )
    assert any("maxAttempts" in e for e in errors)
    assert any("backoff" in e for e in errors)


def test_load_step_requires_expressions_and_valid_table():
    errors = validate_pipeline_definition([
        {"name": "land", "type": "load", "connection": "db", "table": "bad-table;drop"}
    ])
    assert any("table" in e for e in errors)
    assert any("primaryKey.$" in e for e in errors)
    assert any("items.$" in e for e in errors)

    ok = validate_pipeline_definition([
        {
            "name": "land", "type": "load", "connection": "db",
            "table": "public.rows", "primaryKey.$": "item.id", "items.$": "input.items",
        }
    ])
    assert ok == []


def test_agent_step_message_expression():
    ok = validate_pipeline_definition([
        {"name": "triage", "type": "agent", "agent": "gmail/triage", "message.$": "input.text"}
    ])
    assert ok == []


def test_paginate_reserved():
    errors = validate_pipeline_definition([_connector_step(paginate={"param": "p"})])
    assert any("not supported yet" in e for e in errors)


def test_as_tool_requires_schema_and_description():
    errors = validate_pipeline_definition([_connector_step()], as_tool=True)
    assert any("inputSchema" in e for e in errors)
    assert any("description" in e for e in errors)

    ok = validate_pipeline_definition(
        [_connector_step()],
        as_tool=True,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        description="Search things",
    )
    assert ok == []


def test_per_user_shape():
    errors = validate_pipeline_definition([_connector_step()], per_user={"connector": "nope"})
    assert any("perUser.connector" in e for e in errors)

    errors = validate_pipeline_definition(
        [_connector_step()],
        per_user={"connector": "google/gmail", "disableAfterFailures": 0},
    )
    assert any("disableAfterFailures" in e for e in errors)

    ok = validate_pipeline_definition(
        [_connector_step()],
        per_user={"connector": "google/gmail", "disableAfterFailures": 5},
    )
    assert ok == []


def test_output_mapping_validation():
    errors = validate_pipeline_definition(
        [_connector_step()], output_mapping={"output": 1, "output.$": "cursor"}
    )
    assert any("both" in e for e in errors)

    errors = validate_pipeline_definition(
        [_connector_step()], output_mapping={"output.$": "steps.["}
    )
    assert any("invalid JMESPath" in e for e in errors)

    assert validate_pipeline_definition(
        [_connector_step()], output_mapping={"output.$": "steps.fetch.output.body"}
    ) == []


def test_step_cap():
    steps = [_connector_step(name=f"s{i}") for i in range(33)]
    errors = validate_pipeline_definition(steps)
    assert any("too many steps" in e for e in errors)


# ---------------------------------------------------------------------------
# Pure runner helpers
# ---------------------------------------------------------------------------


def test_quote_table():
    from app.services.pipeline_runner import _quote_table

    assert _quote_table("rows") == '"rows"'
    assert _quote_table("public.rows") == '"public"."rows"'


def test_backoff_delay():
    from app.services.pipeline_runner import _backoff_delay

    assert _backoff_delay(0, "none") == 0.0
    assert _backoff_delay(0, "exponential") == 0.5
    assert _backoff_delay(2, "linear") == 3.0


def _pipeline_stub(steps, concurrency=None, per_user=None):
    from app.models.pipeline import Pipeline

    stub = types.SimpleNamespace(
        steps=steps, concurrency=concurrency, per_user=per_user, id="pid"
    )
    stub.get_cursor_step = lambda: Pipeline.get_cursor_step(stub)
    stub.effective_concurrency = lambda: Pipeline.effective_concurrency(stub)
    return stub


def test_effective_concurrency_defaults():
    cursor_step = {"name": "a", "type": "connector", "cursor": {"param": "p", "path": "x"}}
    assert _pipeline_stub([cursor_step]).effective_concurrency() == "single"
    assert _pipeline_stub([{"name": "a", "type": "query"}]).effective_concurrency() == "parallel"
    assert _pipeline_stub([cursor_step], concurrency="parallel").effective_concurrency() == "parallel"


def test_lock_keys_scope_per_user():
    from app.services.pipeline_runner import _lock_key, _pending_key

    shared = _pipeline_stub([], per_user=None)
    per_user = _pipeline_stub([], per_user={"connector": "g/gmail"})
    assert _lock_key(shared, "u1") == "sinas:pipeline:lock:pid"
    assert _lock_key(per_user, "u1") == "sinas:pipeline:lock:pid:u1"
    assert _lock_key(per_user, "u2") != _lock_key(per_user, "u1")
    assert _pending_key(per_user, "u1").endswith(":u1")


# ---------------------------------------------------------------------------
# API schema (PipelineCreate) surfaces validation errors
# ---------------------------------------------------------------------------


def test_pipeline_create_schema_rejects_invalid_definition():
    from app.schemas.pipeline import PipelineCreate

    with pytest.raises(ValueError, match="duplicate step names"):
        PipelineCreate(
            name="p", steps=[_connector_step(), _connector_step()],
        )


def test_pipeline_create_schema_accepts_valid_definition():
    from app.schemas.pipeline import PipelineCreate

    p = PipelineCreate(
        namespace="gmail",
        name="inbox-triage",
        steps=[
            _connector_step(cursor={"param": "startHistoryId", "path": "body.historyId"}),
            {"name": "land", "type": "load", "connection": "db", "table": "msgs",
             "primaryKey.$": "item.id", "items.$": "steps.fetch.output.body.items"},
        ],
        per_user={"connector": "google/gmail"},
    )
    assert p.steps[0]["cursor"]["param"] == "startHistoryId"


# ---------------------------------------------------------------------------
# Config-layer round-trip pieces
# ---------------------------------------------------------------------------


def test_pipeline_config_output_mapping_aliases():
    from app.schemas.config import PipelineConfig

    cfg = PipelineConfig.model_validate({
        "name": "p", "steps": [_connector_step()], "output.$": "steps.fetch.output.body",
    })
    assert cfg.output_mapping() == {"output.$": "steps.fetch.output.body"}

    cfg2 = PipelineConfig.model_validate({
        "name": "p", "steps": [_connector_step()], "output": {"fixed": True},
    })
    assert cfg2.output_mapping() == {"output": {"fixed": True}}

    cfg3 = PipelineConfig.model_validate({"name": "p", "steps": [_connector_step()]})
    assert cfg3.output_mapping() is None


def test_serialize_pipeline_round_trips_dollar_keys():
    from app.services.resource_serializers import serialize_pipeline

    pipeline = types.SimpleNamespace(
        namespace="gmail",
        name="inbox-triage",
        description="d",
        input_schema={"type": "object", "properties": {}},
        steps=[_connector_step(**{"input.$": "input"})],
        per_user={"connector": "google/gmail", "disableAfterFailures": 5},
        as_tool=True,
        tool_description="td",
        sync_timeout_seconds=120,
        concurrency=None,
        disable_after_failures=None,
        output_mapping={"output.$": "steps.fetch.output.body"},
        is_active=True,
    )
    out = serialize_pipeline(pipeline)
    assert out["steps"][0]["input.$"] == "input"
    assert out["output.$"] == "steps.fetch.output.body"
    assert out["perUser"]["disableAfterFailures"] == 5
    assert "cursorValue" not in out and "cursor_value" not in out
    assert "syncTimeoutSeconds" not in out  # default elided

    # And the exported dict parses back through the config schema.
    from app.schemas.config import PipelineConfig

    cfg = PipelineConfig.model_validate(out)
    assert cfg.output_mapping() == {"output.$": "steps.fetch.output.body"}
    assert cfg.steps == pipeline.steps


# ---------------------------------------------------------------------------
# Webhook pipeline target (post-#111)
# ---------------------------------------------------------------------------


def test_webhook_schema_pipeline_target_requires_name():
    from app.schemas.webhook import WebhookCreate

    with pytest.raises(ValueError, match="pipeline_name is required"):
        WebhookCreate(path="x", target_type="pipeline")


def test_webhook_schema_pipeline_target_rejects_raw():
    from app.schemas.webhook import WebhookCreate

    with pytest.raises(ValueError, match="raw"):
        WebhookCreate(
            path="x", target_type="pipeline", pipeline_name="p", response_mode="raw"
        )


def test_webhook_schema_pipeline_target_valid():
    from app.schemas.webhook import WebhookCreate

    hook = WebhookCreate(
        path="crm/contact-updated",
        target_type="pipeline",
        pipeline_namespace="crm",
        pipeline_name="upsert-contact",
        response_mode="async",
    )
    assert hook.pipeline_namespace == "crm"
    # message_template is an agent-target concern only
    assert hook.message_template is None


def test_webhook_config_pipeline_target_round_trip():
    import types

    from app.schemas.config import WebhookConfig
    from app.services.resource_serializers import serialize_webhook

    cfg = WebhookConfig.model_validate({
        "path": "crm/contact-updated",
        "targetType": "pipeline",
        "pipelineName": "crm/upsert-contact",
        "responseMode": "async",
        "requiresAuth": False,
    })
    assert cfg.pipelineName == "crm/upsert-contact"

    webhook = types.SimpleNamespace(
        path="crm/contact-updated",
        target_type="pipeline",
        function_namespace="default",
        function_name=None,
        agent_namespace=None,
        agent_name=None,
        pipeline_namespace="crm",
        pipeline_name="upsert-contact",
        message_template=None,
        session_key_template=None,
        http_method="POST",
        requires_auth=False,
        description=None,
        default_values=None,
        response_mode="async",
        dedup=None,
    )
    out = serialize_webhook(webhook)
    assert out["targetType"] == "pipeline"
    assert out["pipelineName"] == "crm/upsert-contact"
    assert "functionName" not in out and "agentName" not in out
    # Export parses back through the config schema
    WebhookConfig.model_validate(out)


def test_webhook_config_pipeline_target_requires_pipeline_name():
    from app.schemas.config import WebhookConfig

    with pytest.raises(ValueError, match="pipelineName"):
        WebhookConfig.model_validate({"path": "x", "targetType": "pipeline"})
