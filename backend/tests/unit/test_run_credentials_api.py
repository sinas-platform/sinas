"""Per-run provider credentials through the API, against the database.

Pinned here: `provider_credentials` on chat create, invoke and message send
(and the stream) needs sinas.chats.assign_provider_credential:own (403,
nothing stored) and is validated whole (400 invalid_request, nothing
stored); it is stored encrypted, never returned (only
`provider_credentials_set`), replaced by sending it again, removed by null;
every LLM call of the chat presents it — through the real create_provider
and SDK, to an in-memory gateway — including a sub-agent's chat that
inherits it; a provider that requires one fails closed with
provider_credential_required (missing vs expired); one that doesn't falls
back to its configured key; the OpenAI-compatible adapter's header applies to
one request and is stored nowhere; provider batch mode refuses run
credentials; expired rows are cleaned up; and the value never shows in a
response, the chat, or a log line, even when the gateway echoes it.
"""
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update

from app.core import encryption
from app.core.config import settings
from app.core.database import get_db
from app.models import LLMProvider
from app.models.agent import Agent
from app.models.chat import Chat, Message
from app.models.chat_provider_credential import ChatProviderCredential
from app.models.user import Role, RolePermission, User, UserRole
from app.providers import create_provider, openai_provider, tracking
from app.providers.errors import INVALID_REQUEST, PROVIDER_CREDENTIAL_REQUIRED, LLMError
from app.services import provider_credentials
from tests.conftest import auth_headers

SENTINEL = "rc-api-sentinel-5be1d07c93aa"
SERVICE_KEY = "service-key-api-0001"
PERMISSION = "sinas.chats.assign_provider_credential:own"


class Gateway:
    """An OpenAI-compatible model API in memory: records every request."""

    def __init__(self):
        self.sent: list[httpx.Request] = []
        self.status = 200
        self.body = None

    def _answer(self, request):
        self.sent.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json=self.body)
        payload = json.loads(request.content or b"{}")
        if payload.get("stream"):
            chunk = {
                "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            }
            usage = {
                "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m", "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            text = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(usage)}\n\ndata: [DONE]\n\n"
            return httpx.Response(200, content=text.encode(), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def client(self, *args, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer))

    def authorizations(self):
        return [r.headers.get_list("authorization") for r in self.sent]


@pytest.fixture
def gateway(monkeypatch):
    gw = Gateway()
    monkeypatch.setattr(openai_provider, "DefaultAsyncHttpxClient", gw.client)
    # One key for the platform's encryption (the provider's key, the chats'
    # credentials), as ENCRYPTION_KEY gives a deployment.
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(encryption, "encryption_service", encryption.EncryptionService())
    monkeypatch.setattr(settings, "compaction_enabled", False)
    rows = []

    async def fake_insert(values):
        rows.append(values)
        return True

    monkeypatch.setattr(tracking, "_insert_usage", fake_insert)
    gw.usage_rows = rows
    return gw


async def _provider(db, run_credential=None, *, default=False):
    name = f"gw-{uuid.uuid4().hex[:8]}"
    config = {"models": [f"{name}-model"]}
    if run_credential is not None:
        config["run_credential"] = run_credential
    if default:
        await db.execute(update(LLMProvider).values(is_default=False))
    provider = LLMProvider(
        name=name, provider_type="openai", api_key=encryption.encryption_service.encrypt(SERVICE_KEY),
        api_endpoint="http://gateway.test/v1", default_model=f"{name}-model", config=config,
        is_active=True, is_default=default,
    )
    db.add(provider)
    await db.flush()
    return provider


async def _agent(db, user, provider):
    agent = Agent(
        namespace="rc", name=f"a{uuid.uuid4().hex[:8]}", user_id=user.id,
        llm_provider_id=provider.id if provider else None, model=provider.default_model if provider else None,
        is_active=True,
    )
    db.add(agent)
    await db.flush()
    return agent


async def _user(db, *, may_assign=True):
    role = Role(name=f"rc-{uuid.uuid4().hex[:8]}", description="t")
    db.add(role)
    await db.flush()
    perms = ["sinas.agents/*/*.chat:all", "sinas.users.read:own"]
    if may_assign:
        perms.append(PERMISSION)
    for key in perms:
        db.add(RolePermission(role_id=role.id, permission_key=key, permission_value=True))
    user = User(email=f"rc-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    await db.flush()
    db.add(UserRole(role_id=role.id, user_id=user.id, active=True))
    await db.flush()
    return user


def _creds(provider, credential=SENTINEL, expires_in=None):
    entry = {"credential": credential}
    if expires_in is not None:
        entry["expires_in"] = expires_in
    return {provider.name: entry}


async def _rows(db, chat_id=None):
    stmt = select(ChatProviderCredential)
    if chat_id is not None:
        stmt = stmt.where(ChatProviderCredential.chat_id == uuid.UUID(str(chat_id)))
    return (await db.execute(stmt.execution_options(populate_existing=True))).scalars().all()


async def _new_chat(client, user, agent, **body):
    r = await client.post(f"/agents/{agent.namespace}/{agent.name}/chats", json=body, headers=auth_headers(user))
    return r


# ── giving one ──────────────────────────────────────────────────────────────


async def test_a_chat_is_created_with_a_credential_stored_encrypted_and_never_returned(client, db, gateway):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    agent = await _agent(db, user, provider)

    r = await _new_chat(client, user, agent, provider_credentials=_creds(provider, expires_in=3600))
    assert r.status_code == 200, r.text
    assert SENTINEL not in r.text
    (entry,) = r.json()["provider_credentials_set"]
    assert entry["provider"] == provider.name
    expires_at = datetime.fromisoformat(entry["expires_at"])
    assert timedelta(minutes=59) < expires_at - datetime.now(UTC) <= timedelta(hours=1)

    (row,) = await _rows(db, r.json()["id"])
    assert SENTINEL not in row.ciphertext
    assert encryption.encryption_service.decrypt(row.ciphertext) == SENTINEL
    assert row.set_by == user.id and SENTINEL not in repr(row)

    chat_id = r.json()["id"]
    for path in (f"/chats/{chat_id}", "/chats"):
        got = await client.get(path, headers=auth_headers(user))
        assert got.status_code == 200 and SENTINEL not in got.text
    detail = (await client.get(f"/chats/{chat_id}", headers=auth_headers(user))).json()
    assert [e["provider"] for e in detail["provider_credentials_set"]] == [provider.name]
    listed = (await client.get("/chats", headers=auth_headers(user))).json()
    assert [c["provider_credentials_set"][0]["provider"] for c in listed if c["id"] == chat_id] == [provider.name]


async def test_every_call_of_the_chat_presents_it(client, db, gateway):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    agent = await _agent(db, user, provider)
    chat_id = (await _new_chat(client, user, agent, provider_credentials=_creds(provider))).json()["id"]

    r = await client.post(f"/chats/{chat_id}/messages", json={"content": "hi"}, headers=auth_headers(user))
    assert r.status_code == 200, r.text
    r = await client.post(f"/chats/{chat_id}/messages/stream", json={"content": "again"}, headers=auth_headers(user))
    assert r.status_code == 200 and "event: done" in r.text, r.text
    assert gateway.sent
    assert all(a == [f"Bearer {SENTINEL}"] for a in gateway.authorizations())
    assert gateway.usage_rows and all(row["run_credential"] is True for row in gateway.usage_rows)
    assert SENTINEL not in json.dumps(gateway.usage_rows, default=str)


async def test_invoke_takes_one_too(client, db, gateway):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True, "header": "X-Budget-Key", "prefix": "key-"})
    agent = await _agent(db, user, provider)
    r = await client.post(
        f"/agents/{agent.namespace}/{agent.name}/invoke",
        json={"message": "hi", "provider_credentials": _creds(provider)},
        headers=auth_headers(user),
    )
    assert r.status_code == 200, r.text
    assert SENTINEL not in r.text
    (request,) = gateway.sent
    # Named header: the credential there, the configured key as always.
    assert request.headers.get_list("x-budget-key") == [f"key-{SENTINEL}"]
    assert request.headers.get_list("authorization") == [f"Bearer {SERVICE_KEY}"]
    assert len(await _rows(db, r.json()["chat_id"])) == 1


async def test_a_message_can_rotate_or_remove_it(client, db, gateway):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    agent = await _agent(db, user, provider)
    chat_id = (await _new_chat(client, user, agent, provider_credentials=_creds(provider, expires_in=60))).json()["id"]
    (before,) = await _rows(db, chat_id)
    first_expiry = before.expires_at

    rotated = "rc-api-rotated-0b9d"
    r = await client.post(
        f"/chats/{chat_id}/messages",
        json={"content": "hi", "provider_credentials": _creds(provider, rotated, expires_in=7200)},
        headers=auth_headers(user),
    )
    assert r.status_code == 200, r.text
    (row,) = await _rows(db, chat_id)
    assert encryption.encryption_service.decrypt(row.ciphertext) == rotated
    assert row.expires_at > first_expiry
    assert gateway.authorizations() == [[f"Bearer {rotated}"]]

    r = await client.post(
        f"/chats/{chat_id}/messages",
        json={"content": "hi", "provider_credentials": {provider.name: None}},
        headers=auth_headers(user),
    )
    assert r.status_code == 200, r.text
    assert await _rows(db, chat_id) == []
    # Not required: the configured key serves the call.
    assert gateway.authorizations()[-1] == [f"Bearer {SERVICE_KEY}"]
    detail = (await client.get(f"/chats/{chat_id}", headers=auth_headers(user))).json()
    assert detail["provider_credentials_set"] == []


# ── refusing one ────────────────────────────────────────────────────────────


async def test_without_the_permission_it_is_403_and_nothing_is_stored(client, db, gateway):
    user = await _user(db, may_assign=False)
    provider = await _provider(db, {"enabled": True})
    agent = await _agent(db, user, provider)
    chats_before = (await db.execute(select(Chat.id).where(Chat.user_id == user.id))).all()

    r = await _new_chat(client, user, agent, provider_credentials=_creds(provider))
    assert r.status_code == 403 and PERMISSION in r.text and SENTINEL not in r.text
    assert (await db.execute(select(Chat.id).where(Chat.user_id == user.id))).all() == chats_before

    chat_id = (await _new_chat(client, user, agent)).json()["id"]
    for path, body in (
        (f"/chats/{chat_id}/messages", {"content": "hi"}),
        (f"/chats/{chat_id}/messages/stream", {"content": "hi"}),
        (f"/agents/{agent.namespace}/{agent.name}/invoke", {"message": "hi"}),
    ):
        r = await client.post(path, json={**body, "provider_credentials": _creds(provider)}, headers=auth_headers(user))
        assert r.status_code == 403, (path, r.text)
    assert await _rows(db, chat_id) == []
    assert not gateway.sent  # no turn ran


@pytest.mark.parametrize(
    "case",
    ["unknown provider", "not enabled", "empty", "blank", "zero ttl", "ttl too long", "ttl not a number",
     "not an object", "unknown key"],
)
async def test_a_malformed_entry_is_400_invalid_request_and_nothing_is_stored(client, db, gateway, case):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    plain = await _provider(db)
    agent = await _agent(db, user, provider)
    creds = {
        "unknown provider": {"no-such-provider": {"credential": SENTINEL}},
        "not enabled": _creds(plain),
        "empty": _creds(provider, ""),
        "blank": _creds(provider, "   "),
        "zero ttl": _creds(provider, expires_in=0),
        "ttl too long": _creds(provider, expires_in=2592001),
        "ttl not a number": {provider.name: {"credential": SENTINEL, "expires_in": "soon"}},
        "not an object": {provider.name: SENTINEL},
        "unknown key": {provider.name: {"credential": SENTINEL, "expiresIn": 60}},
    }[case]
    # A valid entry alongside a bad provider is not stored either: all or nothing.
    body = {**_creds(provider), **creds} if case in ("unknown provider", "not enabled") else creds
    r = await _new_chat(client, user, agent, provider_credentials=body)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == INVALID_REQUEST and r.json()["retryable"] is False
    assert r.json()["detail"].startswith("invalid_request: ")
    assert SENTINEL not in r.text
    assert (await db.execute(select(Chat.id).where(Chat.user_id == user.id))).all() == []

    chat_id = (await _new_chat(client, user, agent)).json()["id"]
    r = await client.post(
        f"/chats/{chat_id}/messages", json={"content": "hi", "provider_credentials": body}, headers=auth_headers(user)
    )
    assert r.status_code == 400 and r.json()["code"] == INVALID_REQUEST
    assert await _rows(db, chat_id) == []
    assert not gateway.sent


# ── required: fail closed ───────────────────────────────────────────────────


async def test_a_provider_that_requires_one_refuses_a_chat_without(client, db, gateway):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True, "required": True})
    agent = await _agent(db, user, provider)
    chat_id = (await _new_chat(client, user, agent)).json()["id"]

    r = await client.post(f"/chats/{chat_id}/messages", json={"content": "hi"}, headers=auth_headers(user))
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == PROVIDER_CREDENTIAL_REQUIRED and body["retryable"] is False
    assert "has no run credential" in body["detail"]
    assert not gateway.sent  # never the configured key

    r = await client.post(f"/chats/{chat_id}/messages/stream", json={"content": "hi"}, headers=auth_headers(user))
    assert r.status_code == 200
    error = next(json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ") and "code" in line)
    assert error["code"] == PROVIDER_CREDENTIAL_REQUIRED
    assert not gateway.sent


async def test_an_expired_credential_is_absent_and_says_so(client, db, gateway):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True, "required": True})
    agent = await _agent(db, user, provider)
    chat_id = (await _new_chat(client, user, agent, provider_credentials=_creds(provider))).json()["id"]
    await db.execute(
        update(ChatProviderCredential)
        .where(ChatProviderCredential.chat_id == uuid.UUID(chat_id))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    detail = (await client.get(f"/chats/{chat_id}", headers=auth_headers(user))).json()
    assert detail["provider_credentials_set"] == []
    r = await client.post(f"/chats/{chat_id}/messages", json={"content": "hi"}, headers=auth_headers(user))
    assert r.status_code == 400 and r.json()["code"] == PROVIDER_CREDENTIAL_REQUIRED
    assert "expired" in r.json()["detail"]
    assert not gateway.sent

    # Sending a new one with the message lets the turn run.
    r = await client.post(
        f"/chats/{chat_id}/messages",
        json={"content": "hi", "provider_credentials": _creds(provider)},
        headers=auth_headers(user),
    )
    assert r.status_code == 200, r.text
    assert gateway.authorizations() == [[f"Bearer {SENTINEL}"]]


async def test_not_required_an_expired_one_falls_back_to_the_configured_key(db, gateway):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    chat = Chat(user_id=user.id, title="t")
    db.add(chat)
    await db.flush()
    changes = await provider_credentials.parse_request(db, _creds(provider), {PERMISSION: True})
    await provider_credentials.apply_changes(db, chat.id, changes, user.id)
    llm = await create_provider(provider.name, db=db, usage_context={"chat_id": str(chat.id)})
    assert llm.run_credential is not None

    await db.execute(update(ChatProviderCredential).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    llm = await create_provider(provider.name, db=db, usage_context={"chat_id": str(chat.id)})
    assert llm.run_credential is None


async def test_calls_outside_the_chat_pipeline_resolve_it_from_the_chat_id(db, gateway):
    # Compaction (and any background call) passes the chat id and runs on
    # the default provider: the chat's credential for it applies.
    user = await _user(db)
    provider = await _provider(db, {"enabled": True, "required": True}, default=True)
    chat = Chat(user_id=user.id, title="t")
    db.add(chat)
    await db.flush()
    with pytest.raises(LLMError) as caught:
        await create_provider(None, None, db, usage_context={"chat_id": str(chat.id), "source": "compaction"})
    assert caught.value.code == PROVIDER_CREDENTIAL_REQUIRED and caught.value.http_status == 400

    changes = await provider_credentials.parse_request(db, _creds(provider), {PERMISSION: True})
    await provider_credentials.apply_changes(db, chat.id, changes, user.id)
    llm = await create_provider(None, None, db, usage_context={"chat_id": str(chat.id), "source": "compaction"})
    await llm.complete([{"role": "user", "content": "summarize"}], model="m")
    assert gateway.authorizations() == [[f"Bearer {SENTINEL}"]]


# ── sub-agents ──────────────────────────────────────────────────────────────


async def test_a_sub_agents_chat_inherits_the_parents_credentials(db, gateway):
    from app.services.tool_execution import prepare_agent_delegation

    user = await _user(db)
    provider = await _provider(db, {"enabled": True, "required": True})
    parent_agent = await _agent(db, user, provider)
    sub_agent = await _agent(db, user, provider)
    parent = Chat(user_id=user.id, agent_id=parent_agent.id, title="parent")
    db.add(parent)
    await db.flush()
    changes = await provider_credentials.parse_request(db, _creds(provider), {PERMISSION: True})
    await provider_credentials.apply_changes(db, parent.id, changes, user.id)

    prep = await prepare_agent_delegation(db, str(user.id), str(sub_agent.id), {"prompt": "go"}, parent_chat_id=parent.id)
    sub_chat = prep["sub_chat"]
    (row,) = await _rows(db, sub_chat.id)
    assert row.provider_name == provider.name
    assert encryption.encryption_service.decrypt(row.ciphertext) == SENTINEL

    llm = await create_provider(provider.name, db=db, usage_context={"chat_id": str(sub_chat.id)})
    await llm.complete([{"role": "user", "content": "x"}], model="m")
    assert gateway.authorizations() == [[f"Bearer {SENTINEL}"]]

    # The parent rotates; a resumed sub-chat takes the parent's current one.
    rotated = "rc-api-rotated-sub-77"
    changes = await provider_credentials.parse_request(db, _creds(provider, rotated), {PERMISSION: True})
    await provider_credentials.apply_changes(db, parent.id, changes, user.id)
    await prepare_agent_delegation(
        db, str(user.id), str(sub_agent.id), {"prompt": "again", "_chat_id": str(sub_chat.id)}, parent_chat_id=parent.id
    )
    (row,) = await _rows(db, sub_chat.id)
    assert encryption.encryption_service.decrypt(row.ciphertext) == rotated

    # The parent removes it; a resumed sub-chat loses it too: required, so
    # its calls fail closed instead of presenting the removed key.
    changes = await provider_credentials.parse_request(db, {provider.name: None}, {PERMISSION: True})
    await provider_credentials.apply_changes(db, parent.id, changes, user.id)
    await prepare_agent_delegation(
        db, str(user.id), str(sub_agent.id), {"prompt": "once more", "_chat_id": str(sub_chat.id)},
        parent_chat_id=parent.id,
    )
    assert await _rows(db, sub_chat.id) == []
    with pytest.raises(LLMError) as caught:
        await create_provider(provider.name, db=db, usage_context={"chat_id": str(sub_chat.id)})
    assert caught.value.code == PROVIDER_CREDENTIAL_REQUIRED


async def test_a_request_only_credential_is_not_delegated(db, gateway):
    from app.providers.run_credentials import REQUEST_CREDENTIAL
    from app.services.tool_execution import prepare_agent_delegation

    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    other = await _provider(db, {"enabled": True})
    same = await _agent(db, user, provider)
    elsewhere = await _agent(db, user, other)
    parent = Chat(user_id=user.id, title="parent")
    db.add(parent)
    await db.flush()

    token = REQUEST_CREDENTIAL.set((provider.name, SENTINEL))
    try:
        refused = await prepare_agent_delegation(db, str(user.id), str(same.id), {"prompt": "go"}, parent_chat_id=parent.id)
        allowed = await prepare_agent_delegation(
            db, str(user.id), str(elsewhere.id), {"prompt": "go"}, parent_chat_id=parent.id
        )
    finally:
        REQUEST_CREDENTIAL.reset(token)
    assert "error" in refused and "provider_credentials" in refused["message"]
    assert SENTINEL not in json.dumps(refused)
    assert "sub_chat" in allowed
    assert await _rows(db) == []  # stored nowhere


# ── the OpenAI-compatible adapter ──────────────────────────────────────────


@pytest.fixture
async def adapter(app, db):
    from app.main import adapters_openai_app

    async def _override_get_db():
        yield db

    adapters_openai_app.dependency_overrides[get_db] = _override_get_db
    yield
    adapters_openai_app.dependency_overrides.clear()


async def test_the_adapter_header_applies_to_one_passthrough_request_and_is_stored_nowhere(client, db, gateway, adapter):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True, "required": True})
    body = {"model": provider.default_model, "messages": [{"role": "user", "content": "hi"}]}
    headers = {**auth_headers(user), "X-Sinas-Provider-Credential": SENTINEL}

    r = await client.post("/adapters/openai/v1/chat/completions", json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert SENTINEL not in r.text
    assert gateway.authorizations() == [[f"Bearer {SENTINEL}"]]
    assert await _rows(db) == []

    # The next request without it: required, so refused, never the configured key.
    r = await client.post("/adapters/openai/v1/chat/completions", json=body, headers=auth_headers(user))
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == PROVIDER_CREDENTIAL_REQUIRED
    assert len(gateway.sent) == 1

    r = await client.post(
        "/adapters/openai/v1/chat/completions", json={**body, "stream": True}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert gateway.authorizations()[-1] == [f"Bearer {SENTINEL}"]


@pytest.mark.parametrize("stream", [False, True], ids=["sync", "stream"])
async def test_the_adapter_header_applies_to_an_agents_provider(client, db, gateway, adapter, stream):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True, "required": True})
    agent = await _agent(db, user, provider)
    r = await client.post(
        "/adapters/openai/v1/chat/completions",
        json={"model": f"{agent.namespace}/{agent.name}", "stream": stream,
              "messages": [{"role": "user", "content": "hi"}]},
        headers={**auth_headers(user), "X-Sinas-Provider-Credential": SENTINEL},
    )
    assert r.status_code == 200, r.text
    assert SENTINEL not in r.text
    assert gateway.sent and all(a == [f"Bearer {SENTINEL}"] for a in gateway.authorizations())
    assert await _rows(db) == []


async def test_the_adapter_header_follows_the_same_rules(client, db, gateway, adapter):
    provider = await _provider(db, {"enabled": True})
    plain = await _provider(db)
    body = {"model": provider.default_model, "messages": [{"role": "user", "content": "hi"}]}

    no_permission = await _user(db, may_assign=False)
    r = await client.post(
        "/adapters/openai/v1/chat/completions", json=body,
        headers={**auth_headers(no_permission), "X-Sinas-Provider-Credential": SENTINEL},
    )
    assert r.status_code == 403 and SENTINEL not in r.text

    user = await _user(db)
    for model, value in ((plain.default_model, SENTINEL), (provider.default_model, "")):
        r = await client.post(
            "/adapters/openai/v1/chat/completions", json={**body, "model": model},
            headers={**auth_headers(user), "X-Sinas-Provider-Credential": value},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == INVALID_REQUEST and SENTINEL not in r.text
    assert not gateway.sent


# ── redaction end to end ────────────────────────────────────────────────────


async def test_a_gateway_echoing_the_credential_never_gets_it_out(client, db, gateway, caplog):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    agent = await _agent(db, user, provider)
    chat_id = (await _new_chat(client, user, agent, provider_credentials=_creds(provider))).json()["id"]
    gateway.status = 400
    gateway.body = {"error": {"message": f"invalid key 'Bearer {SENTINEL}' for {SENTINEL}", "type": "invalid_request_error"}}

    with caplog.at_level(logging.DEBUG):
        r = await client.post(f"/chats/{chat_id}/messages", json={"content": "hi"}, headers=auth_headers(user))
        s = await client.post(f"/chats/{chat_id}/messages/stream", json={"content": "hi"}, headers=auth_headers(user))
    assert r.status_code == 502 and r.json()["code"] == INVALID_REQUEST
    assert SENTINEL not in r.text and SENTINEL not in s.text
    assert "[redacted]" in r.json()["detail"]
    transcript = (await db.execute(select(Message.content).where(Message.chat_id == uuid.UUID(chat_id)))).scalars().all()
    assert transcript and not any(SENTINEL in (c or "") for c in transcript)
    assert SENTINEL not in caplog.text
    assert gateway.usage_rows and not any(SENTINEL in (row.get("error") or "") for row in gateway.usage_rows)


# ── provider batch mode ────────────────────────────────────────────────────


async def test_provider_batch_mode_refuses_a_provider_that_requires_one(client, db, gateway):
    user = await _user(db)
    provider = await _provider(db, {"enabled": True, "required": True})
    agent = await _agent(db, user, provider)
    r = await client.post(
        f"/agents/{agent.namespace}/{agent.name}/chats/batch",
        json={"inputs": [{"message": "a"}], "execution_mode": "provider"},
        headers=auth_headers(user),
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == INVALID_REQUEST and "run credential" in r.json()["detail"]
    assert not gateway.sent


async def test_provider_batch_mode_refuses_chats_carrying_one(db, gateway):
    from app.services import batch_service

    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    chat = Chat(user_id=user.id, title="t")
    db.add(chat)
    await db.flush()
    await batch_service._refuse_chats_with_run_credentials(db, [str(chat.id)])  # none: fine
    changes = await provider_credentials.parse_request(db, _creds(provider), {PERMISSION: True})
    await provider_credentials.apply_changes(db, chat.id, changes, user.id)
    with pytest.raises(LLMError) as caught:
        await batch_service._refuse_chats_with_run_credentials(db, [str(chat.id)])
    assert caught.value.code == INVALID_REQUEST and caught.value.http_status == 400


# ── housekeeping ────────────────────────────────────────────────────────────


async def test_expired_credentials_are_cleaned_up(db, gateway, monkeypatch):
    from app.scheduler.jobs import cleanup_expired_provider_credentials as job

    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    chats = []
    for _ in range(2):
        chat = Chat(user_id=user.id, title="t")
        db.add(chat)
        await db.flush()
        changes = await provider_credentials.parse_request(db, _creds(provider), {PERMISSION: True})
        await provider_credentials.apply_changes(db, chat.id, changes, user.id)
        chats.append(chat)
    await db.execute(
        update(ChatProviderCredential)
        .where(ChatProviderCredential.chat_id == chats[0].id)
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )

    @contextlib.asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(job, "AsyncSessionLocal", session)
    assert await job.cleanup_expired_provider_credentials() >= 1
    assert await _rows(db, chats[0].id) == []
    assert len(await _rows(db, chats[1].id)) == 1


async def test_credentials_go_with_their_chat(db, gateway):
    from app.services.chat_deletion import delete_chats_and_rows

    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    chat = Chat(user_id=user.id, title="t")
    db.add(chat)
    await db.flush()
    changes = await provider_credentials.parse_request(db, _creds(provider), {PERMISSION: True})
    await provider_credentials.apply_changes(db, chat.id, changes, user.id)
    await delete_chats_and_rows(db, [chat.id])
    assert await _rows(db, chat.id) == []


# ── provider configuration ─────────────────────────────────────────────────


async def test_the_provider_api_validates_run_credential(client, admin_user):
    name = f"gw-{uuid.uuid4().hex[:8]}"
    base = {"name": name, "provider_type": "ollama", "api_endpoint": "http://gateway.test"}
    r = await client.post(
        "/api/v1/llm-providers", json={**base, "config": {"run_credential": {"enabled": True}}},
        headers=auth_headers(admin_user),
    )
    assert r.status_code == 422 and "can't be null" in r.text
    r = await client.post(
        "/api/v1/llm-providers",
        json={**base, "config": {"run_credential": {"enabled": True, "header": "X-Budget-Key"}}},
        headers=auth_headers(admin_user),
    )
    assert r.status_code == 201, r.text
    assert r.json()["config"]["run_credential"] == {
        "enabled": True, "header": "X-Budget-Key", "prefix": "", "required": False,
    }
    r = await client.patch(
        f"/api/v1/llm-providers/{r.json()['id']}",
        json={"config": {"run_credential": {"enabled": True, "header": "Host"}}},
        headers=auth_headers(admin_user),
    )
    assert r.status_code == 422 and "reserved" in r.text


def test_the_permission_is_registered():
    from app.core.permission_registry import PERMISSION_REGISTRY

    entry = next(e for e in PERMISSION_REGISTRY if e["resource"] == "chats")
    assert "assign_provider_credential" in entry["actions"]


async def test_a_request_invalid_elsewhere_does_not_echo_the_credential(client, db, gateway):
    """FastAPI's default 422 returns the failing `input`, which for a missing
    body field is the whole body: the credential must not come back."""
    user = await _user(db)
    provider = await _provider(db, {"enabled": True})
    agent = await _agent(db, user, provider)
    chat_id = (await _new_chat(client, user, agent)).json()["id"]

    # `content` missing: the error's input is the whole body
    r = await client.post(
        f"/chats/{chat_id}/messages",
        json={"provider_credentials": _creds(provider)},
        headers=auth_headers(user),
    )
    assert r.status_code == 422, r.text
    assert SENTINEL not in r.text
    assert any(e["loc"][-1] == "content" for e in r.json()["detail"])
    # a wrongly typed field elsewhere, credential alongside
    r = await client.post(
        f"/agents/{agent.namespace}/{agent.name}/chats",
        json={"provider_credentials": _creds(provider), "expires_in": "soon"},
        headers=auth_headers(user),
    )
    assert r.status_code == 422, r.text
    assert SENTINEL not in r.text
    assert not await _rows(db)


def test_validation_errors_are_scrubbed_by_location_and_content():
    from app.core.validation_errors import scrub_validation_errors

    errors = scrub_validation_errors([
        {"loc": ["body", "content"], "msg": "Field required", "type": "missing",
         "input": {"provider_credentials": {"p": {"credential": SENTINEL}}, "title": "t"}},
        {"loc": ["header", "x-sinas-provider-credential"], "msg": "bad", "type": "x", "input": SENTINEL},
        {"loc": ["body", "password"], "msg": "too short", "type": "x", "input": SENTINEL},
        {"loc": ["body", "title"], "msg": "bad", "type": "x", "input": "kept"},
    ])
    assert SENTINEL not in json.dumps(errors)
    assert errors[0]["input"]["title"] == "t"
    assert errors[3]["input"] == "kept"
