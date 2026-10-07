"""Components: state from the browser, chat tools, and compiles.

- The state proxy still queried `State.namespace`, a column gone since states
  moved into stores: every state call from a component was a 500.
- Chat tool names turned "-" into "_" and were parsed back by splitting on
  the first "_": a component named sales_chart, or in namespace my-ui, was
  "not found".
- A compile interrupted by a restart stayed "compiling" for good, an older
  compile could overwrite a newer one, and an edit took a working component
  offline until its rebuild finished (for good, if the builder was down).
"""

import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.component import Component
from app.models.state import State
from app.models.store import Store
from app.services import component_builder
from app.services.component_tools import ComponentToolConverter
from tests.conftest import auth_headers


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _component(db, owner, **extra) -> Component:
    comp = Component(
        user_id=owner.id, namespace=extra.pop("namespace", f"ui{_uid()}"),
        name=extra.pop("name", "board"), source_code="export default () => null;",
        compiled_bundle="var __SinasComponent__={};",
        compile_status=extra.pop("compile_status", "success"), **extra,
    )
    db.add(comp)
    await db.flush()
    return comp


@pytest_asyncio.fixture
async def stores(db: AsyncSession, admin_user):
    ns = f"crm{_uid()}"
    made = []
    for name in ("notes", "drafts"):
        store = Store(namespace=ns, name=name, user_id=admin_user.id)
        db.add(store)
        made.append(store)
    await db.flush()
    return ns


def _proxy(comp, path: str) -> str:
    return f"/components/{comp.namespace}/{comp.name}/proxy/states/{path}"


class TestStateProxy:
    async def test_set_get_list_delete(self, client, db, admin_user, stores):
        comp = await _component(
            db, admin_user, enabled_stores=[{"store": f"{stores}/notes", "access": "readwrite"}]
        )
        h = auth_headers(admin_user)
        url = _proxy(comp, stores)  # the SDK's form: namespace only
        assert (await client.post(url, json={"action": "get", "key": "k"}, headers=h)).json() == {
            "found": False, "key": "k", "value": None
        }
        r = await client.post(url, json={"action": "set", "key": "k", "value": {"n": 1}}, headers=h)
        assert r.status_code == 200, r.text
        r = await client.post(url, json={"action": "set", "key": "k", "value": {"n": 2}}, headers=h)
        assert r.status_code == 200, r.text
        got = (await client.post(url, json={"action": "get", "key": "k"}, headers=h)).json()
        assert got == {"found": True, "key": "k", "value": {"n": 2}}
        listed = (await client.post(url, json={"action": "list"}, headers=h)).json()
        assert listed == {"items": [{"key": "k", "value": {"n": 2}}]}
        assert (await client.post(url, json={"action": "delete", "key": "k"}, headers=h)).status_code == 200
        rows = (await db.execute(select(State).where(State.key == "k", State.user_id == admin_user.id))).scalars().all()
        assert [row for row in rows if row.store.namespace == stores] == []

    async def test_works_with_the_components_own_token(self, client, db, admin_user, stores):
        """The page calls the proxy with its component-scoped token, whose
        store permissions are capped to the enabled store and its access."""
        from app.services.component_access import generate_component_access_token

        comp = await _component(
            db, admin_user, enabled_stores=[{"store": f"{stores}/notes", "access": "readwrite"}]
        )
        h = {"Authorization": "Bearer " + generate_component_access_token(
            str(admin_user.id), comp.namespace, comp.name
        )}
        url = _proxy(comp, f"{stores}/notes")
        r = await client.post(url, json={"action": "set", "key": "k", "value": {"n": 1}}, headers=h)
        assert r.status_code == 200, r.text
        got = (await client.post(url, json={"action": "get", "key": "k"}, headers=h)).json()
        assert got["value"] == {"n": 1}

    async def test_set_without_a_value_is_refused(self, client, db, admin_user, stores):
        comp = await _component(
            db, admin_user, enabled_stores=[{"store": f"{stores}/notes", "access": "readwrite"}]
        )
        r = await client.post(
            _proxy(comp, stores), json={"action": "set", "key": "k", "value": None},
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 400

    async def test_list_returns_every_state(self, client, db, admin_user, stores, monkeypatch):
        comp = await _component(
            db, admin_user, enabled_stores=[{"store": f"{stores}/notes", "access": "readwrite"}]
        )
        h = auth_headers(admin_user)
        for i in range(5):
            await client.post(_proxy(comp, stores), json={"action": "set", "key": f"k{i}", "value": {"i": i}}, headers=h)
        monkeypatch.setattr("app.api.runtime.endpoints.components.STATE_LIST_PAGE", 2)
        listed = (await client.post(_proxy(comp, stores), json={"action": "list"}, headers=h)).json()
        assert sorted(item["key"] for item in listed["items"]) == [f"k{i}" for i in range(5)]

    async def test_a_readonly_store_refuses_writes(self, client, db, admin_user, stores):
        comp = await _component(
            db, admin_user, enabled_stores=[{"store": f"{stores}/notes", "access": "readonly"}]
        )
        r = await client.post(
            _proxy(comp, stores), json={"action": "set", "key": "k", "value": {}},
            headers=auth_headers(admin_user),
        )
        assert r.status_code == 403

    async def test_a_store_not_enabled_is_refused(self, client, db, admin_user, stores):
        comp = await _component(db, admin_user, enabled_stores=[])
        r = await client.post(
            _proxy(comp, stores), json={"action": "list"}, headers=auth_headers(admin_user)
        )
        assert r.status_code == 403

    async def test_two_stores_in_a_namespace_need_the_full_name(self, client, db, admin_user, stores):
        comp = await _component(db, admin_user, enabled_stores=[
            {"store": f"{stores}/notes", "access": "readwrite"},
            {"store": f"{stores}/drafts", "access": "readwrite"},
        ])
        h = auth_headers(admin_user)
        assert (await client.post(_proxy(comp, stores), json={"action": "list"}, headers=h)).status_code == 400
        r = await client.post(_proxy(comp, f"{stores}/drafts"), json={"action": "list"}, headers=h)
        assert r.status_code == 200, r.text


class TestChatToolNames:
    @pytest.mark.parametrize("namespace,name", [("my-ui", "sales_chart"), ("ops_2", "a-b_c")])
    async def test_names_round_trip(self, db, admin_user, namespace, name):
        namespace = f"{namespace}{_uid()}"
        comp = await _component(db, admin_user, namespace=namespace, name=name)
        converter = ComponentToolConverter()
        tool_name = converter._component_to_tool(comp)["function"]["name"]
        block = await converter.handle_component_tool_call(db, tool_name, {}, str(admin_user.id))
        assert (block["namespace"], block["name"]) == (namespace, name)

    async def test_a_namespace_with_a_double_underscore(self, db, admin_user):
        namespace = f"ops__east{_uid()}"
        await _component(db, admin_user, namespace=namespace, name="chart")
        block = await ComponentToolConverter().handle_component_tool_call(
            db, f"show_component_{namespace}__chart", {}, str(admin_user.id)
        )
        assert (block["namespace"], block["name"]) == (namespace, "chart")

    async def test_an_ambiguous_name_is_refused_not_guessed(self, db, admin_user):
        u = _uid()
        await _component(db, admin_user, namespace=f"a{u}__b", name="c")
        await _component(db, admin_user, namespace=f"a{u}", name="b__c")
        block = await ComponentToolConverter().handle_component_tool_call(
            db, f"show_component_a{u}__b__c", {}, str(admin_user.id)
        )
        assert block is None

    async def test_an_ambiguous_name_never_falls_back_to_a_third(self, db, admin_user):
        u = _uid()
        await _component(db, admin_user, namespace=f"a{u}__b", name="c")
        await _component(db, admin_user, namespace=f"a{u}", name="b__c")
        await _component(db, admin_user, namespace=f"a{u}_", name="b--c")  # legacy spelling of the same
        block = await ComponentToolConverter().handle_component_tool_call(
            db, f"show_component_a{u}__b__c", {}, str(admin_user.id)
        )
        assert block is None

    async def test_old_chats_open_what_they_always_opened(self, db, admin_user):
        u = _uid()
        await _component(db, admin_user, namespace=f"d{u}", name="a-b")
        await _component(db, admin_user, namespace=f"d{u}", name="a_b")
        block = await ComponentToolConverter().handle_component_tool_call(
            db, f"show_component_d{u}_a_b", {}, str(admin_user.id)
        )
        assert block["name"] == "a-b"  # what the old handler looked up

    async def test_an_old_name_containing_a_double_underscore(self, db, admin_user):
        u = _uid()
        await _component(db, admin_user, namespace=f"d{u}", name="a--b")
        block = await ComponentToolConverter().handle_component_tool_call(
            db, f"show_component_d{u}_a__b", {}, str(admin_user.id)
        )
        assert block["name"] == "a--b"

    async def test_tool_calls_from_existing_chats_still_resolve(self, db, admin_user):
        namespace = f"my-ui{_uid()}"
        await _component(db, admin_user, namespace=namespace, name="chart")
        legacy = f"show_component_{namespace}_chart".replace("-", "_")
        block = await ComponentToolConverter().handle_component_tool_call(db, legacy, {}, str(admin_user.id))
        assert block["namespace"] == namespace


@pytest.fixture
def same_session(db, monkeypatch):
    """compile_component opens its own sessions; give it the test's."""

    @asynccontextmanager
    async def factory():
        yield db

    monkeypatch.setattr("app.core.database.AsyncSessionLocal", factory)
    monkeypatch.setattr(db, "commit", db.flush)


def _builder(monkeypatch, result=None, raises=None, during=None):
    async def compile(self, source):
        if during:
            await during()
        if raises:
            raise raises
        return result

    monkeypatch.setattr(component_builder.ComponentBuilderService, "compile", compile)


class TestCompiles:
    async def test_a_failed_build_keeps_the_last_good_bundle(self, db, admin_user, monkeypatch, same_session):
        comp = await _component(db, admin_user)
        _builder(monkeypatch, {"success": False, "errors": [{"text": "boom", "location": None}]})
        await component_builder.compile_component(comp.id)
        await db.refresh(comp)
        assert comp.compile_status == "error"
        assert comp.compiled_bundle == "var __SinasComponent__={};"

    async def test_an_exception_never_leaves_it_compiling(self, db, admin_user, monkeypatch, same_session):
        comp = await _component(db, admin_user)
        _builder(monkeypatch, raises=RuntimeError("builder exploded"))
        await component_builder.compile_component(comp.id)
        await db.refresh(comp)
        assert comp.compile_status == "error"
        assert "builder exploded" in comp.compile_errors[0]["text"]

    async def test_an_older_compile_does_not_overwrite_a_newer_edit(
        self, db, admin_user, monkeypatch, same_session
    ):
        comp = await _component(db, admin_user)

        async def edit_meanwhile():
            comp.source_code = "export default () => 'v2';"
            await db.flush()

        _builder(monkeypatch, {"success": True, "bundle": "OLD", "sourceMap": None}, during=edit_meanwhile)
        await component_builder.compile_component(comp.id)
        await db.refresh(comp)
        assert comp.compiled_bundle != "OLD"

    async def test_interrupted_compiles_resume_at_startup(self, db, admin_user, monkeypatch, same_session):
        stuck = await _component(db, admin_user, compile_status="compiling")
        scheduled = []
        monkeypatch.setattr(component_builder, "schedule_compile", scheduled.append)
        await component_builder.resume_interrupted_compiles()
        assert stuck.id in scheduled

    @pytest.mark.parametrize("reply", [{"ok": True}, {"success": True}, None])
    async def test_an_unexpected_builder_reply_is_an_error(
        self, db, admin_user, monkeypatch, same_session, reply
    ):
        comp = await _component(db, admin_user)
        _builder(monkeypatch, reply)
        await component_builder.compile_component(comp.id)
        await db.refresh(comp)
        assert comp.compile_status == "error"

    async def test_no_builder_is_a_clear_error(self):
        result = await component_builder.ComponentBuilderService(builder_url="").compile("x")
        assert not result["success"]
        assert "No component builder is configured" in result["errors"][0]["text"]

    async def test_an_edit_keeps_serving_the_last_build(self, client, db, admin_user, monkeypatch):
        monkeypatch.setattr(component_builder, "compile_component", lambda *_: None)
        comp = await _component(db, admin_user)
        r = await client.put(
            f"/api/v1/components/{comp.namespace}/{comp.name}",
            json={"source_code": "export default () => 'v2';"}, headers=auth_headers(admin_user),
        )
        assert r.status_code == 200, r.text
        await db.refresh(comp)
        assert comp.compile_status == "pending"
        assert comp.compiled_bundle == "var __SinasComponent__={};"
