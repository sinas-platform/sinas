"""Package uninstall must not trip foreign keys on used resources (#63).

`uninstall` removes managed resources with Core bulk `delete()` statements,
which bypass the ORM's delete-orphan cascades. Two children of those tables
have no ON DELETE rule of their own, so uninstalling a package whose
functions had ever been versioned — i.e. any package that had been used —
failed the whole operation with a ForeignKeyViolationError and left the
package installed. Chats referencing a package agent are the same shape.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.chat import Chat
from app.models.function import Function, FunctionVersion
from app.models.package import Package
from app.models.user import User
from app.services.package_service import PackageService

PACKAGE_YAML = """
apiVersion: sinas.co/v1
kind: SinasPackage
metadata:
  name: {pkg}
package:
  name: {pkg}
  version: "1.0.0"
spec:
  connectors:
    - namespace: {ns}
      name: probe
      baseUrl: https://example.com
"""


async def _installed_package(db: AsyncSession, owner: User) -> tuple[str, str]:
    """Install a trivial package and return (package_name, managed_by)."""
    pkg = f"pkg-{uuid.uuid4().hex[:8]}"
    await PackageService(db).install(
        PACKAGE_YAML.format(pkg=pkg, ns=f"ns{uuid.uuid4().hex[:6]}"), str(owner.id)
    )
    return pkg, f"pkg:{pkg}"


async def _used_function(db: AsyncSession, owner: User, managed_by: str) -> Function:
    """A package function that has been versioned, as any used one has."""
    fn = Function(
        user_id=owner.id,
        namespace=f"ns{uuid.uuid4().hex[:6]}",
        name="send_email",
        code="def handler(input, context): return {}",
        input_schema={},
        output_schema={},
        managed_by=managed_by,
    )
    db.add(fn)
    await db.flush()
    db.add(
        FunctionVersion(
            function_id=fn.id,
            version=1,
            code=fn.code,
            input_schema={},
            output_schema={},
            created_by=owner.id,
        )
    )
    await db.flush()
    return fn


async def _chatted_agent(db: AsyncSession, owner: User, managed_by: str) -> tuple[Agent, Chat]:
    agent = Agent(
        user_id=owner.id,
        namespace=f"ns{uuid.uuid4().hex[:6]}",
        name="assistant",
        system_prompt="you are helpful",
        managed_by=managed_by,
    )
    db.add(agent)
    await db.flush()
    chat = Chat(
        user_id=owner.id,
        agent_id=agent.id,
        agent_namespace=agent.namespace,
        agent_name=agent.name,
        title="a conversation worth keeping",
    )
    db.add(chat)
    await db.flush()
    return agent, chat


class TestUninstallWithUsedResources:
    async def test_uninstall_succeeds_for_a_versioned_function(
        self, db: AsyncSession, admin_user: User
    ):
        pkg, managed_by = await _installed_package(db, admin_user)
        fn = await _used_function(db, admin_user, managed_by)

        counts = await PackageService(db).uninstall(pkg)

        assert counts.get("functions") == 1
        assert (
            await db.execute(select(Function).where(Function.id == fn.id))
        ).scalar_one_or_none() is None
        # The versions go with the function they belong to
        versions = (
            await db.execute(
                select(FunctionVersion).where(FunctionVersion.function_id == fn.id)
            )
        ).scalars().all()
        assert versions == []
        assert (
            await db.execute(select(Package).where(Package.name == pkg))
        ).scalar_one_or_none() is None

    async def test_uninstall_keeps_chats_and_only_drops_the_agent_link(
        self, db: AsyncSession, admin_user: User
    ):
        """A conversation belongs to the user, not to the package."""
        pkg, managed_by = await _installed_package(db, admin_user)
        agent, chat = await _chatted_agent(db, admin_user, managed_by)

        await PackageService(db).uninstall(pkg)

        assert (
            await db.execute(select(Agent).where(Agent.id == agent.id))
        ).scalar_one_or_none() is None
        surviving = (
            await db.execute(select(Chat).where(Chat.id == chat.id))
        ).scalar_one_or_none()
        assert surviving is not None, "uninstalling a package must not delete user chats"
        await db.refresh(surviving)
        assert surviving.agent_id is None
        # The names stay as a record of what the chat was with
        assert surviving.agent_name == agent.name

    async def test_unrelated_resources_are_untouched(
        self, db: AsyncSession, admin_user: User
    ):
        """The pre-clear is scoped to the package, not to every function."""
        pkg, managed_by = await _installed_package(db, admin_user)
        await _used_function(db, admin_user, managed_by)
        other = await _used_function(db, admin_user, "pkg:some-other-package")

        await PackageService(db).uninstall(pkg)

        assert (
            await db.execute(select(Function).where(Function.id == other.id))
        ).scalar_one_or_none() is not None
        other_versions = (
            await db.execute(
                select(FunctionVersion).where(FunctionVersion.function_id == other.id)
            )
        ).scalars().all()
        assert len(other_versions) == 1
