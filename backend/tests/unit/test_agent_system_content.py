"""Unit tests for build_agent_system_content — the shared system-prompt
builder used by both the initial LLM call and the tool-loop follow-up.

Regression coverage for the bug where follow-up calls dropped preloaded
skills (and, for promptless agents, the output-schema instruction) because
the follow-up path rebuilt the system prompt with its own inline logic.
"""
from types import SimpleNamespace

import pytest

from app.services.conversation_history import build_agent_system_content


class _FakeSkillConverter:
    def __init__(self, content=""):
        self._content = content
        self.calls = []

    async def get_preloaded_skills_content(self, db, enabled_skills):
        self.calls.append(enabled_skills)
        return self._content


def _agent(**overrides):
    agent = SimpleNamespace(
        system_prompt="You are a helpful assistant.",
        enabled_skills=None,
        output_schema=None,
    )
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


@pytest.mark.asyncio
async def test_plain_system_prompt():
    content = await build_agent_system_content(None, _agent(), _FakeSkillConverter())
    assert content == "You are a helpful assistant."


@pytest.mark.asyncio
async def test_template_rendering_with_variables():
    agent = _agent(system_prompt="Hello {{ name }}.")
    content = await build_agent_system_content(
        None, agent, _FakeSkillConverter(), {"name": "Kjeld"}
    )
    assert content == "Hello Kjeld."


@pytest.mark.asyncio
async def test_preloaded_skills_appended():
    agent = _agent(enabled_skills=[{"name": "docs", "preload": True}])
    converter = _FakeSkillConverter("How to write docs...")
    content = await build_agent_system_content(None, agent, converter)
    assert content.startswith("You are a helpful assistant.")
    assert "# Preloaded Skills" in content
    assert "How to write docs..." in content
    assert converter.calls == [[{"name": "docs", "preload": True}]]


@pytest.mark.asyncio
async def test_output_schema_instruction_appended():
    agent = _agent(output_schema={"properties": {"answer": {"type": "string"}}})
    content = await build_agent_system_content(None, agent, _FakeSkillConverter())
    assert "valid JSON matching this exact schema" in content
    assert '"answer"' in content


@pytest.mark.asyncio
async def test_skills_and_schema_without_system_prompt():
    """Agents with no system_prompt still get skills + schema content —
    the old follow-up path dropped everything in this case."""
    agent = _agent(
        system_prompt=None,
        enabled_skills=[{"name": "docs", "preload": True}],
        output_schema={"properties": {"x": {}}},
    )
    content = await build_agent_system_content(
        None, agent, _FakeSkillConverter("Skill body")
    )
    assert "Skill body" in content
    assert "valid JSON" in content


@pytest.mark.asyncio
async def test_template_error_falls_back_to_raw_prompt():
    agent = _agent(system_prompt="Broken {% if %}")
    content = await build_agent_system_content(
        None, agent, _FakeSkillConverter(), {"name": "x"}
    )
    assert content == "Broken {% if %}"
