"""Creating a role defines authority; it must not grant it (#167).

`POST /roles` used to add the creator as an active member. The escalation is
delayed and invisible: a user creates an empty role and silently joins it, an
admin later attaches permissions to what looks like an unassigned role, and
the creator holds them from that moment — including through any API key they
own, whose effective permissions are capped by the owner's *live* ones.
Config-applied and package-shipped roles already define without binding.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, RolePermission, UserRole
from tests.conftest import auth_headers


async def _members(db: AsyncSession, role_id) -> list[UserRole]:
    return list(
        (await db.execute(select(UserRole).where(UserRole.role_id == role_id))).scalars()
    )


class TestRoleCreationMembership:
    async def test_creator_is_not_made_a_member(self, client, db: AsyncSession, admin_user):
        name = f"role-{uuid.uuid4().hex[:8]}"

        response = await client.post(
            "/api/v1/roles",
            json={"name": name, "description": "a container of authority"},
            headers=auth_headers(admin_user),
        )
        assert response.status_code in (200, 201), response.text

        role = (
            await db.execute(select(Role).where(Role.name == name))
        ).scalar_one_or_none()
        assert role is not None
        assert await _members(db, role.id) == [], (
            "creating a role must not enrol anyone, least of all silently"
        )

    async def test_inline_permissions_still_land_on_the_role(
        self, client, db: AsyncSession, admin_user
    ):
        """Definition still works — it just binds no one."""
        name = f"role-{uuid.uuid4().hex[:8]}"

        response = await client.post(
            "/api/v1/roles",
            json={
                "name": name,
                "description": "service role",
                "permissions": {"sinas.agents/acme/*.chat:all": True},
            },
            headers=auth_headers(admin_user),
        )
        assert response.status_code in (200, 201), response.text

        role = (await db.execute(select(Role).where(Role.name == name))).scalar_one()
        perms = (
            await db.execute(
                select(RolePermission).where(RolePermission.role_id == role.id)
            )
        ).scalars().all()
        assert {p.permission_key: p.permission_value for p in perms} == {
            "sinas.agents/acme/*.chat:all": True
        }
        assert await _members(db, role.id) == []
