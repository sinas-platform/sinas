"""The shipped way-of-working package (packages/way-of-working.yaml).

What it must hold up: the package applies cleanly through the package
installer (config-apply underneath), the skill lands preloaded in the
exemplar agent's system content, every piece of the loop is switched on, and
the approval rules do what the package comment promises — reads and
workbench edits run unattended, publishing and destructive actions ask.
Re-installing is an idempotent upgrade.
"""
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.package import Package
from app.models.skill import Skill
from app.services import approval_rules
from app.services.conversation_history import build_agent_system_content
from app.services.package_service import PackageService
from app.services.skill_tools import SkillToolConverter
from app.services.system_tool_helpers import has_system_tool

PACKAGE_PATH = (
    Path(__file__).resolve().parents[3] / "packages" / "way-of-working.yaml"
)
PACKAGE_NAME = "way-of-working"
SKILL_REF = "way-of-working/workbench-loop"


def _yaml() -> str:
    return PACKAGE_PATH.read_text()


async def _agent(db: AsyncSession) -> Agent:
    return (
        await db.execute(
            select(Agent).where(Agent.namespace == "way-of-working", Agent.name == "assistant")
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_package_installs_cleanly(db: AsyncSession, admin_user):
    package, result = await PackageService(db).install(_yaml(), str(admin_user.id))
    assert result.success, result.errors
    assert package.name == PACKAGE_NAME

    skill = (
        await db.execute(
            select(Skill).where(Skill.namespace == "way-of-working", Skill.name == "workbench-loop")
        )
    ).scalar_one()
    assert skill.managed_by == f"pkg:{PACKAGE_NAME}"
    assert skill.is_active
    # The skill is the deliverable; the sections are its contract.
    for heading in (
        "## Deliverables are files",
        "## Verify with code, don't eyeball",
        "## Move content by reference",
        "## Collections: check out, then promote",
        "## Ask only when blocked",
        "## End of turn",
    ):
        assert heading in skill.content, heading
    assert '{"$workbench": "<path>"}' in skill.content
    assert "tool_results/" in skill.content

    agent = await _agent(db)
    assert agent.managed_by == f"pkg:{PACKAGE_NAME}"
    for tool in ("workbench", "codeExecution", "askUser", "artifacts"):
        assert has_system_tool(agent.system_tools, tool), tool
    assert agent.enabled_skills == [{"skill": SKILL_REF, "preload": True}]
    assert agent.system_prompt and "workbench" in agent.system_prompt


@pytest.mark.asyncio
async def test_skill_is_preloaded_into_the_system_content(db: AsyncSession, admin_user):
    _, result = await PackageService(db).install(_yaml(), str(admin_user.id))
    assert result.success, result.errors
    agent = await _agent(db)

    content = await build_agent_system_content(db, agent, SkillToolConverter())
    # Prompt first, then the skill under the preloaded-skills header.
    assert content.startswith(agent.system_prompt.strip()[:40])
    assert "# Preloaded Skills" in content
    assert f"# Skill: {SKILL_REF}" in content
    assert "## Verify with code, don't eyeball" in content
    # Preloaded means not also offered as a retrieval tool.
    tools = await SkillToolConverter().get_available_skills(db, agent.enabled_skills)
    assert tools == []


@pytest.mark.asyncio
async def test_tool_approvals_gate_publishing_not_reading(db: AsyncSession, admin_user):
    _, result = await PackageService(db).install(_yaml(), str(admin_user.id))
    assert result.success, result.errors
    agent = await _agent(db)

    def action(tool_name: str, intrinsic_ask: bool = False) -> str:
        return approval_rules.resolve_action(agent.tool_approvals, tool_name, intrinsic_ask, set())

    # Unattended: reads, computation, edits to the chat's own workbench, UI.
    for tool in (
        "workbench_list", "workbench_read", "workbench_write", "workbench_edit",
        "workbench_checkout", "execute_code", "create_artifact", "update_artifact",
        "ask_user", "search_collection_demo_sales", "get_file_demo_sales",
    ):
        assert action(tool) == approval_rules.AUTO, tool
    # Gated: anything that publishes beyond the chat or destroys data.
    for tool in (
        "workbench_promote", "workbench_delete",
        "write_file_demo_sales", "edit_file_demo_sales", "delete_file_demo_sales",
    ):
        assert action(tool) == approval_rules.ASK, tool
    # An intrinsic requires_approval flag still wins for unmatched tools
    # (default auto never overrides a function's own flag).
    assert action("some_function", intrinsic_ask=True) == approval_rules.ASK


@pytest.mark.asyncio
async def test_reinstall_is_an_idempotent_upgrade(db: AsyncSession, admin_user):
    svc = PackageService(db)
    _, first = await svc.install(_yaml(), str(admin_user.id))
    assert first.success, first.errors
    agent_id = (await _agent(db)).id

    _, second = await svc.install(_yaml(), str(admin_user.id))
    assert second.success, second.errors
    assert (await _agent(db)).id == agent_id  # updated in place, not recreated
    packages = (
        await db.execute(select(Package).where(Package.name == PACKAGE_NAME))
    ).scalars().all()
    assert len(packages) == 1
