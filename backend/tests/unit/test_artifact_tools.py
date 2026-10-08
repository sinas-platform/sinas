"""Artifacts: components an agent writes in a chat (system tool "artifacts").

An artifact is an ordinary component in the `artifacts` namespace, owned by
the chat's user, written through ComponentApplier (validated, in history),
shown in the chat at once — and bounded by what the agent itself may reach.
"""

import types

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_user_permissions
from app.models.component import Component
from app.models.config_revision import ConfigRevision
from app.services.artifact_tools import execute_artifact_tool, is_artifact_tool


def _agent(**extra):
    values = dict(
        system_tools=["artifacts"], enabled_queries=["sales/orders"], enabled_functions=[],
        enabled_stores=[{"store": "sales/notes", "access": "readonly"}],
    )
    values.update(extra)
    return types.SimpleNamespace(**values)


async def _call(db, user, tool, agent=None, permissions=None, **arguments):
    if permissions is None:
        permissions = await get_user_permissions(db, str(user.id))
    return await execute_artifact_tool(
        db=db, tool_name=tool, arguments=arguments, user_id=str(user.id),
        permissions=permissions, agent=agent or _agent(),
    )


async def _row(db: AsyncSession, name: str) -> Component:
    row = (await db.execute(
        select(Component).where(Component.namespace == "artifacts", Component.name == name)
    )).scalar_one()
    await db.refresh(row)
    return row


class TestCreate:
    async def test_shows_in_chat_and_is_a_real_component(self, db, admin_user):
        block = await _call(
            db, admin_user, "create_artifact", title="Q3 orders",
            html="<p id='x'></p><script>x.textContent = sinas.input.n</script>",
            input={"n": 3}, queries=["sales/orders"],
        )
        assert block["type"] == "component" and block["render_token"]
        assert block["input"] == {"n": 3}
        assert block["name"].startswith("q3-orders-")
        row = await _row(db, block["name"])
        assert (row.user_id, row.enabled_queries, row.title) == (admin_user.id, ["sales/orders"], "Q3 orders")
        actions = (await db.execute(
            select(ConfigRevision.action).where(
                ConfigRevision.resource_kind == "components",
                ConfigRevision.resource_key == f"artifacts/{block['name']}",
            )
        )).scalars().all()
        assert actions == ["create"]

    async def test_only_with_the_system_tool(self, db, admin_user):
        result = await _call(db, admin_user, "create_artifact", agent=_agent(system_tools=[]), title="t", html="<p/>")
        assert result["error"] == "capability_not_enabled"

    async def test_not_beyond_what_the_agent_may_reach(self, db, admin_user):
        result = await _call(db, admin_user, "create_artifact", title="t", html="<p/>", queries=["hr/salaries"])
        assert result["error"] == "permission_denied"
        result = await _call(
            db, admin_user, "create_artifact", title="t", html="<p/>",
            stores=[{"store": "sales/notes", "access": "readwrite"}],
        )
        assert result["error"] == "permission_denied"  # read-only for the agent

    async def test_needs_the_users_create_permission(self, db, admin_user):
        result = await _call(db, admin_user, "create_artifact", permissions={}, title="t", html="<p/>")
        assert result["error"] == "permission_denied"

    async def test_an_empty_page_is_refused(self, db, admin_user):
        result = await _call(db, admin_user, "create_artifact", title="t", html="")
        assert result["error"] == "validation_error"


class TestUpdate:
    async def test_changes_the_page_and_records_it(self, db, admin_user):
        block = await _call(db, admin_user, "create_artifact", title="Report", html="<p>v1</p>")
        updated = await _call(db, admin_user, "update_artifact", name=block["name"], html="<p>v2</p>")
        assert updated["type"] == "component"
        row = await _row(db, block["name"])
        assert (row.source_code, row.title) == ("<p>v2</p>", "Report")

    async def test_someone_elses_artifact_is_refused(self, db, admin_user, test_user):
        block = await _call(db, admin_user, "create_artifact", title="Mine", html="<p/>")
        result = await _call(
            db, test_user, "update_artifact", name=block["name"], html="<p>x</p>",
            permissions={"sinas.components/*/*.update:own": True},
        )
        assert result["error"] == "permission_denied"

    async def test_an_unknown_artifact(self, db, admin_user):
        result = await _call(db, admin_user, "update_artifact", name="nope-000000", html="<p/>")
        assert result["error"] == "not_found"


def test_tool_names():
    assert is_artifact_tool("create_artifact") and is_artifact_tool("update_artifact")
    assert not is_artifact_tool("show_component_ui__x")


class TestReviewFixes:
    async def test_a_wildcard_grant_covers_its_namespace(self, db, admin_user):
        block = await _call(
            db, admin_user, "create_artifact", agent=_agent(enabled_queries=["sales/*"]),
            title="t", html="<p/>", queries=["sales/orders"],
        )
        assert block["type"] == "component", block

    async def test_an_update_never_keeps_more_than_the_calling_agent_may_reach(self, db, admin_user):
        block = await _call(db, admin_user, "create_artifact", title="t", html="<p/>", queries=["sales/orders"])
        # Another agent, without sales/orders, only changes the title:
        result = await _call(
            db, admin_user, "update_artifact", agent=_agent(enabled_queries=[]),
            name=block["name"], title="renamed",
        )
        assert result["error"] == "permission_denied"
        assert (await _row(db, block["name"])).title == "t"

    async def test_writes_are_committed(self, db, admin_user, monkeypatch):
        """Tool calls run in a session of their own, closed without a commit
        unless the tool commits."""
        commits = []
        original = db.commit

        async def counting_commit():
            commits.append(True)
            await original()

        monkeypatch.setattr(db, "commit", counting_commit)
        block = await _call(db, admin_user, "create_artifact", title="t", html="<p/>")
        await _call(db, admin_user, "update_artifact", name=block["name"], html="<p>2</p>")
        assert len(commits) == 2
