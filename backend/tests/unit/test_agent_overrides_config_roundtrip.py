"""Provider overrides survive config re-apply and export (#200 review).

Teams that manage agents as config — packages included — change settings by
editing the file and re-applying. The agent hash left providerOverrides out,
so a change to ONLY `providerOverrides.effort` hashed identically, was skipped
as unchanged, and silently left the old effort in effect: the new control
would have been a no-op for exactly the teams that most need it. Export
dropped the field too, so an exported agent re-imported at model defaults.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.services.package_service import PackageService
from app.services.resource_serializers import serialize_agent

PACKAGE = """
apiVersion: sinas.co/v1
kind: SinasPackage
metadata:
  name: {pkg}
package:
  name: {pkg}
  version: "{version}"
spec:
  agents:
    - namespace: {ns}
      name: researcher
      systemPrompt: You research things.
{overrides}
"""


def _yaml(pkg, ns, version, overrides=None):
    block = ""
    if overrides:
        lines = "\n".join(f"        {k}: {v}" for k, v in overrides.items())
        block = f"      providerOverrides:\n{lines}"
    return PACKAGE.format(pkg=pkg, ns=ns, version=version, overrides=block)


async def _overrides(db: AsyncSession, ns: str):
    """The stored value, read as a column rather than through a cached (and
    possibly expired) ORM object."""
    return (
        await db.execute(
            select(Agent.provider_overrides).where(
                Agent.namespace == ns, Agent.name == "researcher"
            )
        )
    ).scalar_one()


class TestReapply:
    async def test_changing_only_effort_is_applied(self, db: AsyncSession, admin_user):
        """The Greptile scenario, and the client's deployment path."""
        pkg, ns = f"pkg-{uuid.uuid4().hex[:8]}", f"ns{uuid.uuid4().hex[:6]}"
        svc = PackageService(db)

        await svc.install(_yaml(pkg, ns, "1.0.0", {"effort": "high"}), str(admin_user.id))
        assert await _overrides(db, ns) == {"effort": "high"}

        await svc.install(_yaml(pkg, ns, "1.0.1", {"effort": "low"}), str(admin_user.id))
        assert await _overrides(db, ns) == {"effort": "low"}, (
            "an effort-only change was skipped as unchanged"
        )

    async def test_adding_an_override_to_an_agent_that_had_none(
        self, db: AsyncSession, admin_user
    ):
        pkg, ns = f"pkg-{uuid.uuid4().hex[:8]}", f"ns{uuid.uuid4().hex[:6]}"
        svc = PackageService(db)

        await svc.install(_yaml(pkg, ns, "1.0.0"), str(admin_user.id))
        assert not await _overrides(db, ns)

        await svc.install(_yaml(pkg, ns, "1.0.1", {"effort": "low"}), str(admin_user.id))
        assert await _overrides(db, ns) == {"effort": "low"}

    async def test_an_invalid_level_in_config_is_refused(self, db: AsyncSession, admin_user):
        """Config goes through the same validator as the API."""
        pkg, ns = f"pkg-{uuid.uuid4().hex[:8]}", f"ns{uuid.uuid4().hex[:6]}"
        try:
            await PackageService(db).install(
                _yaml(pkg, ns, "1.0.0", {"effort": "ultra"}), str(admin_user.id)
            )
        except Exception:
            return  # refused outright is fine
        assert ((await _overrides(db, ns)) or {}).get("effort") != "ultra"


class TestExport:
    def test_export_carries_the_overrides(self):
        agent = Agent(
            namespace="ns", name="a", system_prompt="x",
            provider_overrides={"effort": "low", "prompt_caching": False},
        )
        exported = serialize_agent(agent)
        assert exported["providerOverrides"] == {"effort": "low", "prompt_caching": False}

    def test_agents_without_overrides_export_unchanged(self):
        """No empty key appearing in every existing export."""
        agent = Agent(namespace="ns", name="a", system_prompt="x", provider_overrides=None)
        assert "providerOverrides" not in serialize_agent(agent)
