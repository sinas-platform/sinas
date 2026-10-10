"""Roles: one applier for a role's definition on every channel.

No role or permission change was in the change history; config replaced
permissions non-atomically and never cleared them; package upgrades never
removed a role they dropped; a config user could only hold roles the same
config declared; and the API could rename or delete a default role (Admins,
Users, GuestUsers), which login and bootstrap find by name. Memberships stay
bindings, outside the definition.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_revision import ConfigRevision
from app.models.user import Role, RolePermission, UserRole
from app.schemas.config import SinasConfig
from app.services.config_apply.service import ConfigApplyService
from tests.conftest import auth_headers


def _name() -> str:
    return f"role-{uuid.uuid4().hex[:8]}"


def _yaml(name: str, perms: dict[str, bool] | None = None, **extra) -> dict:
    perms = perms if perms is not None else {"sinas.agents/crm/*.chat:all": True}
    return {"name": name, "description": "CRM", "permissions": [{"key": k, "value": v} for k, v in perms.items()], **extra}


async def _apply(db, owner, config_name="cfg", dry_run=False, **spec):
    config = SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": config_name},
        "spec": spec,
    })
    svc = ConfigApplyService(
        db, config_name, owner_user_id=str(owner.id), managed_by="config", auto_commit=False
    )
    return await svc.apply_config(config, dry_run=dry_run)


async def _role(db: AsyncSession, name: str) -> Role | None:
    row = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if row is not None:
        await db.refresh(row)
    return row


async def _perms(db: AsyncSession, role: Role) -> dict[str, bool]:
    return dict((await db.execute(
        select(RolePermission.permission_key, RolePermission.permission_value).where(RolePermission.role_id == role.id)
    )).all())


async def _actions(db: AsyncSession, name: str) -> list[str]:
    return list((await db.execute(
        select(ConfigRevision.action)
        .where(ConfigRevision.resource_kind == "roles", ConfigRevision.resource_key == name)
        .order_by(ConfigRevision.id)
    )).scalars())


class TestConfigApply:
    async def test_create_unchanged_update_and_clear(self, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, roles=[_yaml(name)])).success
        again = await _apply(db, admin_user, roles=[_yaml(name)])
        assert again.success and not again.summary.updated, again.summary
        assert (await _apply(db, admin_user, roles=[_yaml(name, {"sinas.queries/crm/*.execute:all": True})])).success
        role = await _role(db, name)
        assert await _perms(db, role) == {"sinas.queries/crm/*.execute:all": True}
        # permissions: [] clears them now (it never did).
        assert (await _apply(db, admin_user, roles=[_yaml(name, {})])).success
        assert await _perms(db, role) == {}
        assert await _actions(db, name) == ["create", "update", "update"]

    async def test_default_roles_are_left_alone(self, db: AsyncSession, admin_user):
        from app.core.auth import initialize_default_roles

        await initialize_default_roles(db)
        before = await _perms(db, await _role(db, "Users"))
        result = await _apply(db, admin_user, roles=[_yaml("Users", {"sinas.everything:all": True})])
        assert result.success and result.warnings
        assert await _perms(db, await _role(db, "Users")) == before

    async def test_a_config_user_can_hold_a_role_declared_elsewhere(self, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, config_name="roles", roles=[_yaml(name)])).success
        email = f"u-{uuid.uuid4().hex[:8]}@example.com"
        result = await _apply(db, admin_user, config_name="people", users=[{"email": email, "roles": [name]}])
        assert result.success and not result.warnings, result.warnings
        role = await _role(db, name)
        members = (await db.execute(select(UserRole).where(UserRole.role_id == role.id))).scalars().all()
        assert len(members) == 1


class TestRest:
    async def test_definition_changes_are_recorded(self, client, db: AsyncSession, admin_user):
        name, h = _name(), auth_headers(admin_user)
        r = await client.post("/api/v1/roles", json={"name": name, "permissions": {"a.b:all": True}}, headers=h)
        assert r.status_code == 201, r.text
        r = await client.post("/api/v1/roles", json={"name": name}, headers=h)
        assert r.status_code == 400 and "already exists" in r.text
        assert (await client.post(f"/api/v1/roles/{name}/permissions", json={"permission_key": "c.d:all", "permission_value": True}, headers=h)).status_code == 200
        r = await client.delete(f"/api/v1/roles/{name}/permissions", params={"permission_key": "a.b:all"}, headers=h)
        assert r.status_code == 204
        assert await _perms(db, await _role(db, name)) == {"c.d:all": True}
        r = await client.patch(f"/api/v1/roles/{name}", json={"description": "d"}, headers=h)
        assert r.status_code == 200 and r.json()["description"] == "d"
        assert (await client.delete(f"/api/v1/roles/{name}", headers=h)).status_code == 204
        assert await _actions(db, name) == ["create", "update", "update", "update", "delete"]

    async def test_a_deleted_role_restores_without_its_members(self, client, db: AsyncSession, admin_user, test_user):
        name, h = _name(), auth_headers(admin_user)
        await client.post("/api/v1/roles", json={"name": name, "permissions": {"a.b:all": True}}, headers=h)
        role = await _role(db, name)
        original = role.id
        db.add(UserRole(role_id=role.id, user_id=test_user.id, active=True))
        await db.flush()
        assert (await client.delete(f"/api/v1/roles/{name}", headers=h)).status_code == 204
        deleted = (await db.execute(
            select(ConfigRevision.id).where(
                ConfigRevision.resource_kind == "roles", ConfigRevision.resource_key == name,
                ConfigRevision.action == "delete",
            )
        )).scalar_one()
        r = await client.post(f"/api/v1/config/history/{deleted}/restore", headers=h)
        assert r.status_code == 200, r.text
        role = await _role(db, name)
        assert role.id == original and await _perms(db, role) == {"a.b:all": True}
        # The definition comes back; who held it is a binding, not restored.
        assert (await db.execute(select(UserRole).where(UserRole.role_id == role.id))).first() is None

    async def test_default_roles_cant_be_renamed_or_deleted(self, client, db: AsyncSession, admin_user):
        from app.core.auth import initialize_default_roles

        await initialize_default_roles(db)
        h = auth_headers(admin_user)
        for name in ("Admins", "Users", "GuestUsers"):
            assert (await client.patch(f"/api/v1/roles/{name}", json={"name": f"{name}2"}, headers=h)).status_code == 400
            assert (await client.delete(f"/api/v1/roles/{name}", headers=h)).status_code == 400
        # Admins permissions stay locked, as before.
        r = await client.post("/api/v1/roles/Admins/permissions", json={"permission_key": "x.y:all", "permission_value": True}, headers=h)
        assert r.status_code == 403
        # Users stays editable (description, permissions), and unmanaged.
        r = await client.patch("/api/v1/roles/Users", json={"description": "Everyone"}, headers=h)
        assert r.status_code == 200, r.text
        assert (await _role(db, "Users")).managed_by is None


def _package(pkg: str, version: str, roles: list[str]) -> str:
    lines = [
        "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
        "package:", f"  name: {pkg}", f'  version: "{version}"', "spec:",
        "  roles:" + ("" if roles else " []"),
    ]
    for r in roles:
        lines += [
            f"    - name: {r}", "      permissions:",
            f"        - {{key: 'sinas.agents/{pkg}/*.chat:all', value: true}}",
        ]
    return "\n".join(lines) + "\n"


class TestPackages:
    async def test_upgrade_prunes_a_dropped_role_and_its_memberships(self, db: AsyncSession, admin_user, test_user):
        from app.services.package_service import PackageService

        pkg = f"p{uuid.uuid4().hex[:8]}"
        keep, drop = f"{pkg}-keep", f"{pkg}-drop"
        service = PackageService(db)
        _, first = await service.install(
            _package(pkg, "1.0.0", [keep, drop]), str(admin_user.id), allow_broad_role_permissions=True
        )
        assert first.success, first.errors
        dropped = await _role(db, drop)
        db.add(UserRole(role_id=dropped.id, user_id=test_user.id, active=True))
        await db.flush()
        _, second = await service.install(
            _package(pkg, "2.0.0", [keep]), str(admin_user.id), allow_broad_role_permissions=True
        )
        assert second.success, second.errors
        assert second.summary.deleted == {"roles": 1}
        assert await _role(db, drop) is None
        assert (await db.execute(select(UserRole).where(UserRole.role_id == dropped.id))).first() is None
        counts = await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert counts.get("roles") == 1
        assert await _actions(db, keep) == ["create", "delete"]
