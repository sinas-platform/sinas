"""Tests for webhook agent targets and raw response mode."""
import json
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.function import Function
from app.models.webhook import Webhook
from tests.conftest import auth_headers


# =========================================================================
# API schema validation
# =========================================================================


class TestWebhookCreateSchema:
    def test_legacy_function_webhook_defaults(self):
        from app.schemas.webhook import WebhookCreate

        w = WebhookCreate(path="stripe/payment", function_name="process")
        assert w.target_type == "function"
        assert w.response_mode == "sync"

    def test_agent_webhook(self):
        from app.schemas.webhook import WebhookCreate

        w = WebhookCreate(
            path="jira/issue-created",
            target_type="agent",
            agent_namespace="jira",
            agent_name="triage",
            message_template="New issue {{ issue.key }}",
            session_key_template="jira-{{ issue.key }}",
            response_mode="async",
        )
        assert w.agent_name == "triage"

    def test_function_webhook_requires_function_name(self):
        from app.schemas.webhook import WebhookCreate

        with pytest.raises(ValidationError, match="function_name"):
            WebhookCreate(path="p")

    def test_agent_webhook_requires_agent_name(self):
        from app.schemas.webhook import WebhookCreate

        with pytest.raises(ValidationError, match="agent_name"):
            WebhookCreate(path="p", target_type="agent", message_template="m")

    def test_agent_webhook_requires_message_template(self):
        from app.schemas.webhook import WebhookCreate

        with pytest.raises(ValidationError, match="message_template"):
            WebhookCreate(path="p", target_type="agent", agent_name="a")

    def test_raw_mode_rejected_for_agent_targets(self):
        from app.schemas.webhook import WebhookCreate

        with pytest.raises(ValidationError, match="raw"):
            WebhookCreate(
                path="p",
                target_type="agent",
                agent_name="a",
                message_template="m",
                response_mode="raw",
            )

    def test_raw_mode_allowed_for_function_targets(self):
        from app.schemas.webhook import WebhookCreate

        w = WebhookCreate(path="slack/events", function_name="handler", response_mode="raw")
        assert w.response_mode == "raw"


# =========================================================================
# Config YAML schema validation
# =========================================================================


class TestWebhookConfigYaml:
    def test_function_webhook(self):
        from app.schemas.config import WebhookConfig

        c = WebhookConfig(path="slack/events", functionName="slack/handler", responseMode="raw")
        assert c.targetType == "function"

    def test_agent_webhook(self):
        from app.schemas.config import WebhookConfig

        c = WebhookConfig(
            path="jira/issue-created",
            targetType="agent",
            agentName="jira/triage",
            messageTemplate="New issue {{ issue.key }}",
            sessionKeyTemplate="jira-{{ issue.key }}",
            responseMode="async",
        )
        assert c.agentName == "jira/triage"

    def test_function_name_required_for_function_targets(self):
        from app.schemas.config import WebhookConfig

        with pytest.raises(ValidationError, match="functionName"):
            WebhookConfig(path="p")

    def test_agent_fields_required_for_agent_targets(self):
        from app.schemas.config import WebhookConfig

        with pytest.raises(ValidationError, match="agentName"):
            WebhookConfig(path="p", targetType="agent")
        with pytest.raises(ValidationError, match="messageTemplate"):
            WebhookConfig(path="p", targetType="agent", agentName="a/b")

    def test_raw_mode_rejected_for_agent_targets(self):
        from app.schemas.config import WebhookConfig

        with pytest.raises(ValidationError, match="raw"):
            WebhookConfig(
                path="p",
                targetType="agent",
                agentName="a/b",
                messageTemplate="m",
                responseMode="raw",
            )

    def test_invalid_response_mode_rejected(self):
        from app.schemas.config import WebhookConfig

        with pytest.raises(ValidationError, match="responseMode"):
            WebhookConfig(path="p", functionName="f", responseMode="banana")


# =========================================================================
# Webhook template rendering
# =========================================================================


class TestWebhookTemplateRendering:
    def test_renders_nested_payload(self):
        from app.services.template_renderer import render_webhook_template

        out = render_webhook_template(
            "New issue {{ issue.key }}: {{ issue.fields.summary }}",
            {"issue": {"key": "AB-1", "fields": {"summary": "Boom"}}},
        )
        assert out == "New issue AB-1: Boom"

    def test_undefined_variables_render_empty(self):
        from app.services.template_renderer import render_webhook_template

        assert render_webhook_template("x{{ missing }}y", {}) == "xy"

    def test_undefined_chains_render_empty(self):
        from app.services.template_renderer import render_webhook_template

        assert render_webhook_template("x{{ a.b.c.d }}y", {}) == "xy"

    def test_no_html_escaping(self):
        from app.services.template_renderer import render_webhook_template

        assert render_webhook_template("{{ v }}", {"v": "<b>&</b>"}) == "<b>&</b>"


# =========================================================================
# Raw response building and dedup replay
# =========================================================================


class TestRawResponse:
    def test_dict_result_is_json_body(self):
        from app.api.runtime.endpoints.webhooks import _build_raw_response

        resp, cache = _build_raw_response({"challenge": "abc"})
        assert resp.status_code == 200
        assert json.loads(resp.body) == {"challenge": "abc"}
        assert resp.media_type == "application/json"

    def test_string_result_is_text_plain(self):
        from app.api.runtime.endpoints.webhooks import _build_raw_response

        resp, _ = _build_raw_response("hello")
        assert resp.body == b"hello"
        assert resp.media_type == "text/plain"

    def test_control_keys_set_status_headers_body(self):
        from app.api.runtime.endpoints.webhooks import _build_raw_response

        resp, cache = _build_raw_response(
            {"_status": 201, "_headers": {"X-A": "1"}, "_body": {"ok": True}}
        )
        assert resp.status_code == 201
        assert resp.headers["x-a"] == "1"
        assert json.loads(resp.body) == {"ok": True}

    def test_cache_entry_replays_identically(self):
        from app.api.runtime.endpoints.webhooks import _build_raw_response, _replay_cached

        resp, cache = _build_raw_response({"_status": 201, "_headers": {"X-A": "1"}, "_body": "ok"})
        replayed = _replay_cached(json.dumps(cache))
        assert replayed.status_code == 201
        assert replayed.headers["x-a"] == "1"
        assert replayed.body == b"ok"
        assert replayed.media_type == "text/plain"

    def test_legacy_envelope_cache_replays_as_json(self):
        from app.api.runtime.endpoints.webhooks import _replay_cached

        cached = json.dumps({"success": True, "execution_id": "e", "result": 1})
        replayed = _replay_cached(cached)
        assert replayed.status_code == 200
        assert json.loads(replayed.body)["success"] is True


# =========================================================================
# Serializer / config round-trip
# =========================================================================


class TestSerializeWebhook:
    def _webhook(self, **kw):
        defaults = dict(
            path="p",
            target_type="function",
            function_namespace="default",
            function_name="fn",
            agent_namespace=None,
            agent_name=None,
            message_template=None,
            session_key_template=None,
            http_method="POST",
            requires_auth=True,
            description=None,
            default_values=None,
            response_mode="sync",
            dedup=None,
        )
        defaults.update(kw)
        return type("W", (), defaults)()

    def test_function_webhook_export_has_no_target_type(self):
        from app.schemas.config import WebhookConfig
        from app.services.resource_serializers import serialize_webhook

        s = serialize_webhook(self._webhook())
        assert "targetType" not in s
        assert s["functionName"] == "default/fn"
        WebhookConfig(**s)  # round-trips

    def test_agent_webhook_round_trip(self):
        from app.schemas.config import WebhookConfig
        from app.services.resource_serializers import serialize_webhook

        s = serialize_webhook(
            self._webhook(
                target_type="agent",
                function_name=None,
                agent_namespace="jira",
                agent_name="triage",
                message_template="m {{x}}",
                session_key_template="k-{{y}}",
                response_mode="async",
            )
        )
        assert s["targetType"] == "agent"
        assert s["agentName"] == "jira/triage"
        assert "functionName" not in s
        cfg = WebhookConfig(**s)
        assert cfg.messageTemplate == "m {{x}}"
        assert cfg.sessionKeyTemplate == "k-{{y}}"


# =========================================================================
# Config parser reference validation
# =========================================================================


CONFIG_HEADER = """
apiVersion: sinas.co/v1
kind: SinasConfig
metadata:
  name: test
spec:
"""


class TestConfigParserWebhookRefs:
    async def test_agent_webhook_ref_valid(self):
        from app.services.config_parser import ConfigParser

        yaml_str = CONFIG_HEADER + """
  agents:
    - namespace: jira
      name: triage
  webhooks:
    - path: jira/issue-created
      targetType: agent
      agentName: jira/triage
      messageTemplate: "New {{ issue.key }}"
"""
        config, validation = await ConfigParser.parse_and_validate(yaml_str)
        assert validation.errors == [], [str(e) for e in validation.errors]
        assert config is not None

    async def test_agent_webhook_ref_undefined(self):
        from app.services.config_parser import ConfigParser

        yaml_str = CONFIG_HEADER + """
  webhooks:
    - path: jira/issue-created
      targetType: agent
      agentName: jira/missing
      messageTemplate: "m"
"""
        _, validation = await ConfigParser.parse_and_validate(yaml_str)
        assert any("jira/missing" in str(e) for e in validation.errors)

    async def test_function_webhook_ref_with_namespace(self):
        from app.services.config_parser import ConfigParser

        yaml_str = CONFIG_HEADER + """
  functions:
    - namespace: slack
      name: handler
      code: "def main(input): return input"
  webhooks:
    - path: slack/events
      functionName: slack/handler
      responseMode: raw
"""
        config, validation = await ConfigParser.parse_and_validate(yaml_str)
        assert validation.errors == [], [str(e) for e in validation.errors]

    async def test_agent_schedule_ref_validated(self):
        """scheduleType: agent schedules must validate agentName (regression:
        this used to KeyError on the missing functionName)."""
        from app.services.config_parser import ConfigParser

        yaml_str = CONFIG_HEADER + """
  agents:
    - namespace: ops
      name: reporter
  schedules:
    - name: daily-report
      scheduleType: agent
      agentName: ops/reporter
      content: "Write the daily report"
      cronExpression: "0 9 * * *"
"""
        config, validation = await ConfigParser.parse_and_validate(yaml_str)
        assert validation.errors == [], [str(e) for e in validation.errors]

    async def test_agent_schedule_ref_undefined(self):
        from app.services.config_parser import ConfigParser

        yaml_str = CONFIG_HEADER + """
  schedules:
    - name: daily-report
      scheduleType: agent
      agentName: ops/missing
      content: "hi"
      cronExpression: "0 9 * * *"
"""
        _, validation = await ConfigParser.parse_and_validate(yaml_str)
        assert any("ops/missing" in str(e) for e in validation.errors)


# =========================================================================
# CRUD API
# =========================================================================


class TestWebhookCrudApi:
    async def _create_agent(self, db: AsyncSession, user) -> Agent:
        agent = Agent(
            user_id=user.id,
            namespace="jira",
            name=f"triage-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        db.add(agent)
        await db.flush()
        await db.refresh(agent)
        return agent

    async def test_create_agent_webhook(self, client, db, admin_user):
        agent = await self._create_agent(db, admin_user)
        resp = await client.post(
            "/api/v1/webhooks",
            json={
                "path": f"jira/created-{uuid.uuid4().hex[:8]}",
                "target_type": "agent",
                "agent_namespace": "jira",
                "agent_name": agent.name,
                "message_template": "New issue {{ issue.key }}",
                "session_key_template": "jira-{{ issue.key }}",
                "response_mode": "async",
            },
            headers=auth_headers(admin_user),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["target_type"] == "agent"
        assert data["agent_namespace"] == "jira"
        assert data["agent_name"] == agent.name
        assert data["function_name"] is None

    async def test_create_agent_webhook_unknown_agent_404(self, client, admin_user):
        resp = await client.post(
            "/api/v1/webhooks",
            json={
                "path": f"x-{uuid.uuid4().hex[:8]}",
                "target_type": "agent",
                "agent_name": "does-not-exist",
                "message_template": "m",
            },
            headers=auth_headers(admin_user),
        )
        assert resp.status_code == 404

    async def test_create_agent_webhook_raw_mode_422(self, client, admin_user):
        resp = await client.post(
            "/api/v1/webhooks",
            json={
                "path": "p",
                "target_type": "agent",
                "agent_name": "a",
                "message_template": "m",
                "response_mode": "raw",
            },
            headers=auth_headers(admin_user),
        )
        assert resp.status_code == 422


# =========================================================================
# Runtime execution
# =========================================================================


async def _create_function(db: AsyncSession, user) -> Function:
    fn = Function(
        user_id=user.id,
        namespace="slack",
        name=f"handler-{uuid.uuid4().hex[:8]}",
        code="def main(input): return input",
        input_schema={},
        output_schema={},
        is_active=True,
    )
    db.add(fn)
    await db.flush()
    await db.refresh(fn)
    return fn


class TestRuntimeRawMode:
    async def test_raw_mode_returns_unwrapped_body(self, client, db, test_user, monkeypatch):
        fn = await _create_function(db, test_user)
        path = f"slack/events-{uuid.uuid4().hex[:8]}"
        db.add(
            Webhook(
                user_id=test_user.id,
                path=path,
                function_namespace=fn.namespace,
                function_name=fn.name,
                http_method="POST",
                requires_auth=False,
                response_mode="raw",
            )
        )
        await db.flush()

        from app.services.queue_service import queue_service

        async def fake_enqueue_and_wait(**kwargs):
            return {"challenge": kwargs["input_data"]["challenge"]}

        monkeypatch.setattr(queue_service, "enqueue_and_wait", fake_enqueue_and_wait)

        resp = await client.post(
            f"/webhooks/{path}",
            json={"type": "url_verification", "challenge": "3eZbrw1a"},
        )
        assert resp.status_code == 200
        # The body IS the function result — no envelope
        assert resp.json() == {"challenge": "3eZbrw1a"}

    async def test_raw_mode_string_result_is_text_plain(self, client, db, test_user, monkeypatch):
        fn = await _create_function(db, test_user)
        path = f"slack/plain-{uuid.uuid4().hex[:8]}"
        db.add(
            Webhook(
                user_id=test_user.id,
                path=path,
                function_namespace=fn.namespace,
                function_name=fn.name,
                http_method="POST",
                requires_auth=False,
                response_mode="raw",
            )
        )
        await db.flush()

        from app.services.queue_service import queue_service

        async def fake_enqueue_and_wait(**kwargs):
            return "3eZbrw1a"

        monkeypatch.setattr(queue_service, "enqueue_and_wait", fake_enqueue_and_wait)

        resp = await client.post(f"/webhooks/{path}", json={})
        assert resp.status_code == 200
        assert resp.text == "3eZbrw1a"
        assert resp.headers["content-type"].startswith("text/plain")

    async def test_sync_mode_envelope_unchanged(self, client, db, test_user, monkeypatch):
        fn = await _create_function(db, test_user)
        path = f"slack/sync-{uuid.uuid4().hex[:8]}"
        db.add(
            Webhook(
                user_id=test_user.id,
                path=path,
                function_namespace=fn.namespace,
                function_name=fn.name,
                http_method="POST",
                requires_auth=False,
                response_mode="sync",
            )
        )
        await db.flush()

        from app.services.queue_service import queue_service

        async def fake_enqueue_and_wait(**kwargs):
            return {"anything": 1}

        monkeypatch.setattr(queue_service, "enqueue_and_wait", fake_enqueue_and_wait)

        resp = await client.post(f"/webhooks/{path}", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["result"] == {"anything": 1}
        assert "execution_id" in data


class TestRuntimeAgentTarget:
    async def _setup(self, db: AsyncSession, user, **webhook_kw):
        agent = Agent(
            user_id=user.id,
            namespace="jira",
            name=f"triage-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        db.add(agent)
        await db.flush()
        await db.refresh(agent)

        path = f"jira/created-{uuid.uuid4().hex[:8]}"
        webhook = Webhook(
            user_id=user.id,
            path=path,
            target_type="agent",
            function_namespace="default",
            function_name=None,
            agent_namespace=agent.namespace,
            agent_name=agent.name,
            message_template="New issue {{ issue.key }}: {{ issue.fields.summary }}",
            session_key_template="jira-{{ issue.key }}",
            http_method="POST",
            requires_auth=False,
            response_mode="async",
            **webhook_kw,
        )
        db.add(webhook)
        await db.flush()
        return agent, path

    async def test_async_agent_webhook_enqueues_message(
        self, client, db, test_user, monkeypatch
    ):
        agent, path = await self._setup(db, test_user)

        # Keep the handler's commit inside the test transaction
        monkeypatch.setattr(db, "commit", db.flush)

        from app.services.queue_service import queue_service

        captured = {}

        async def fake_enqueue_agent_message(**kwargs):
            captured.update(kwargs)
            return "job-123"

        monkeypatch.setattr(queue_service, "enqueue_agent_message", fake_enqueue_agent_message)

        payload = {"issue": {"key": "AB-1", "fields": {"summary": "It broke"}}}
        resp = await client.post(f"/webhooks/{path}", json=payload)
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["job_id"] == "job-123"
        assert data["chat_id"]

        # Message rendered from the payload
        assert captured["content"] == "New issue AB-1: It broke"
        assert captured["agent"] == f"{agent.namespace}/{agent.name}"
        assert captured["trigger_type"] == "webhook"

        # Chat carries the rendered session key
        from app.models.chat import Chat

        chat = await db.get(Chat, uuid.UUID(data["chat_id"]))
        assert chat is not None
        assert chat.session_key == "jira-AB-1"
        assert chat.agent_id == agent.id

    async def test_session_key_reuses_chat(self, client, db, test_user, monkeypatch):
        agent, path = await self._setup(db, test_user)
        monkeypatch.setattr(db, "commit", db.flush)

        from app.services.queue_service import queue_service

        async def fake_enqueue_agent_message(**kwargs):
            return "job-x"

        monkeypatch.setattr(queue_service, "enqueue_agent_message", fake_enqueue_agent_message)

        payload = {"issue": {"key": "AB-2", "fields": {"summary": "first"}}}
        resp1 = await client.post(f"/webhooks/{path}", json=payload)
        resp2 = await client.post(f"/webhooks/{path}", json=payload)
        assert resp1.status_code == 202 and resp2.status_code == 202
        assert resp1.json()["chat_id"] == resp2.json()["chat_id"]

        # A different issue key starts a fresh conversation
        other = await client.post(
            f"/webhooks/{path}",
            json={"issue": {"key": "AB-3", "fields": {"summary": "other"}}},
        )
        assert other.json()["chat_id"] != resp1.json()["chat_id"]

    async def test_undefined_template_variables_do_not_fail(
        self, client, db, test_user, monkeypatch
    ):
        agent, path = await self._setup(db, test_user)
        monkeypatch.setattr(db, "commit", db.flush)

        from app.services.queue_service import queue_service

        captured = {}

        async def fake_enqueue_agent_message(**kwargs):
            captured.update(kwargs)
            return "job-y"

        monkeypatch.setattr(queue_service, "enqueue_agent_message", fake_enqueue_agent_message)

        # Payload missing everything the template references: the webhook must
        # still succeed (undefined variables never raise), and a PARTIAL render
        # keeps the author's framing while APPENDING the raw payload — the
        # template's instructions are the point, and replacing them with bare
        # JSON handed the agent data with zero guidance (field report from the
        # integration packages: multi-event templates legitimately reference
        # fields absent per event type).
        resp = await client.post(f"/webhooks/{path}", json={"unrelated": True})
        assert resp.status_code == 202, resp.text
        content = captured["content"]
        assert content.startswith("New issue")          # author framing kept
        assert "Full event payload:" in content         # data appended
        assert "unrelated" in content                   # nothing lost

    async def test_fully_empty_render_falls_back_to_payload(
        self, client, db, test_user, monkeypatch
    ):
        agent, path = await self._setup(db, test_user)

        # Make the whole template render empty
        from sqlalchemy import select as sa_select

        result = await db.execute(sa_select(Webhook).where(Webhook.path == path))
        webhook = result.scalar_one()
        webhook.message_template = "{{ missing }}"
        await db.flush()

        monkeypatch.setattr(db, "commit", db.flush)

        from app.services.queue_service import queue_service

        captured = {}

        async def fake_enqueue_agent_message(**kwargs):
            captured.update(kwargs)
            return "job-z"

        monkeypatch.setattr(queue_service, "enqueue_agent_message", fake_enqueue_agent_message)

        resp = await client.post(f"/webhooks/{path}", json={"unrelated": True})
        assert resp.status_code == 202, resp.text
        # Empty render falls back to the JSON payload so the agent gets context
        assert "unrelated" in captured["content"]


# =========================================================================
# Authorization (PR #111 review): triggering a webhook runs AS THE OWNER, so
# ownership — not the caller's access to the target — is what authorizes it.
# =========================================================================


async def _user_with_perms(db: AsyncSession, perms: list[str]):
    """A user whose role grants exactly `perms`."""
    from app.models.user import Role, RolePermission, User, UserRole

    role = Role(name=f"role-{uuid.uuid4().hex[:8]}", description="perm fixture")
    db.add(role)
    await db.flush()
    await db.refresh(role)
    for key in perms:
        db.add(RolePermission(role_id=role.id, permission_key=key, permission_value=True))

    user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    await db.flush()
    await db.refresh(user)
    db.add(UserRole(role_id=role.id, user_id=user.id, active=True))
    await db.flush()
    return user


async def _agent_webhook(db: AsyncSession, owner, *, requires_auth: bool):
    agent = Agent(
        user_id=owner.id,
        namespace="jira",
        name=f"triage-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    db.add(agent)
    await db.flush()
    await db.refresh(agent)

    path = f"jira/auth-{uuid.uuid4().hex[:8]}"
    db.add(
        Webhook(
            user_id=owner.id,
            path=path,
            target_type="agent",
            function_namespace="default",
            function_name=None,
            agent_namespace=agent.namespace,
            agent_name=agent.name,
            message_template="Issue {{ key }}",
            http_method="POST",
            requires_auth=requires_auth,
            response_mode="async",
        )
    )
    await db.flush()
    return agent, path


class TestWebhookOwnershipAuthorization:
    async def test_non_owner_with_chat_all_is_rejected(self, db: AsyncSession, client):
        """`sinas.agents/*/*.chat:all` is a DEFAULT grant for every user, so a
        target-permission check would let any authenticated user drive someone
        else's webhook — and it runs as the owner, with the owner's secrets."""
        owner = await _user_with_perms(db, ["sinas.agents/*/*.chat:all"])
        other = await _user_with_perms(db, ["sinas.agents/*/*.chat:all"])
        _agent, path = await _agent_webhook(db, owner, requires_auth=True)

        resp = await client.post(
            f"/webhooks/{path}", json={"key": "AB-1"}, headers=auth_headers(other)
        )
        assert resp.status_code == 403, resp.text

    async def test_owner_is_allowed(self, db: AsyncSession, client, monkeypatch):
        """Regression guard: the ownership rule must not lock out the owner."""
        owner = await _user_with_perms(db, ["sinas.agents/*/*.chat:all"])
        _agent, path = await _agent_webhook(db, owner, requires_auth=True)

        monkeypatch.setattr(db, "commit", db.flush)
        from app.services.queue_service import queue_service

        async def fake_enqueue_agent_message(**kwargs):
            return "job-owner"

        monkeypatch.setattr(queue_service, "enqueue_agent_message", fake_enqueue_agent_message)

        resp = await client.post(
            f"/webhooks/{path}", json={"key": "AB-1"}, headers=auth_headers(owner)
        )
        assert resp.status_code == 202, resp.text

    async def test_owner_without_chat_permission_is_rejected(
        self, db: AsyncSession, client
    ):
        """Permissions can be narrowed after the webhook is created; the target
        check must be re-evaluated per request, not trusted from creation."""
        owner = await _user_with_perms(db, [])  # no chat permission at all
        _agent, path = await _agent_webhook(db, owner, requires_auth=False)

        resp = await client.post(f"/webhooks/{path}", json={"key": "AB-1"})
        assert resp.status_code == 403, resp.text

    async def test_deactivated_owner_cannot_fire_public_webhook(
        self, db: AsyncSession, client
    ):
        """Soft-deleting a user revokes their API keys and tokens (#102). A
        public webhook that keeps minting tokens for them would re-open exactly
        the access that deletion was supposed to close."""
        owner = await _user_with_perms(db, ["sinas.agents/*/*.chat:all"])
        _agent, path = await _agent_webhook(db, owner, requires_auth=False)

        owner.is_active = False
        await db.flush()

        resp = await client.post(f"/webhooks/{path}", json={"key": "AB-1"})
        assert resp.status_code == 403, resp.text
