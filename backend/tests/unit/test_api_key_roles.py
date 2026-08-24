"""API keys scoped by roles + live permission intersection.

Keys used to return their stored permission dict verbatim at request time
(mint-time snapshot), and the create endpoint never validated the requested
dict against the creator's own permissions — any user who could create a key
could mint one with arbitrary permissions. Both are fixed here:

- mint: explicit grants must be a subset of the creator's permissions
- request time: effective = (explicit ∪ union of linked roles) ∩ owner's LIVE
  permissions, so role edits and owner demotions apply immediately
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import resolve_api_key_permissions, validate_api_key
from app.models import APIKey, APIKeyRole
from app.models.user import Role, RolePermission, User, UserRole

from tests.conftest import auth_headers


@pytest_asyncio.fixture
async def key_user(db: AsyncSession) -> tuple[User, Role]:
    """User whose role can manage API keys and read agents."""
    role = Role(name=f"key-role-{uuid.uuid4().hex[:8]}")
    db.add(role)
    await db.flush()
    for perm in [
        "sinas.api_keys.create:own",
        "sinas.api_keys/*/*.read:own",
        "sinas.api_keys.read:own",
        "sinas.agents/*/*.read:own",
    ]:
        db.add(RolePermission(role_id=role.id, permission_key=perm, permission_value=True))
    user = User(email=f"key-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    await db.flush()
    db.add(UserRole(role_id=role.id, user_id=user.id, active=True))
    await db.flush()
    return user, role


@pytest_asyncio.fixture
async def extra_role(db: AsyncSession) -> Role:
    """A role NOT held by key_user, granting query read."""
    role = Role(name=f"extra-role-{uuid.uuid4().hex[:8]}")
    db.add(role)
    await db.flush()
    db.add(
        RolePermission(
            role_id=role.id, permission_key="sinas.queries/*/*.read:own", permission_value=True
        )
    )
    await db.flush()
    return role


async def _get_key(db: AsyncSession, key_id) -> APIKey:
    return (await db.execute(select(APIKey).where(APIKey.id == key_id))).scalar_one()


class TestMint:
    async def test_explicit_permissions_must_be_subset(self, client, key_user):
        user, _ = key_user
        resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "escalate", "permissions": {"sinas.*:all": True}},
            headers=auth_headers(user),
        )
        assert resp.status_code == 400
        assert "exceed" in resp.json()["detail"]

    async def test_unknown_role_id_rejected(self, client, key_user):
        user, _ = key_user
        resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "ghost", "role_ids": [str(uuid.uuid4())]},
            headers=auth_headers(user),
        )
        assert resp.status_code == 400
        assert "Unknown role ids" in resp.json()["detail"]

    async def test_linked_roles_returned_and_listed(self, client, db, key_user):
        user, role = key_user
        resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "linked", "role_ids": [str(role.id)]},
            headers=auth_headers(user),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert [r["name"] for r in body["roles"]] == [role.name]

        listed = await client.get("/api/v1/api-keys", headers=auth_headers(user))
        assert listed.status_code == 200
        row = next(k for k in listed.json() if k["id"] == body["id"])
        assert [r["name"] for r in row["roles"]] == [role.name]


class TestResolution:
    async def test_linked_key_gets_role_permissions(self, client, db, key_user):
        user, role = key_user
        resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "linked", "role_ids": [str(role.id)]},
            headers=auth_headers(user),
        )
        plain = resp.json()["key"]
        resolved = await validate_api_key(db, plain)
        assert resolved is not None
        _, perms = resolved
        assert perms.get("sinas.agents/*/*.read:own") is True

    async def test_role_edit_applies_without_reminting(self, client, db, key_user):
        user, role = key_user
        resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "tracked", "role_ids": [str(role.id)]},
            headers=auth_headers(user),
        )
        plain = resp.json()["key"]
        _, before = await validate_api_key(db, plain)
        assert "sinas.functions/*/*.read:own" not in before

        db.add(
            RolePermission(
                role_id=role.id,
                permission_key="sinas.functions/*/*.read:own",
                permission_value=True,
            )
        )
        await db.flush()
        _, after = await validate_api_key(db, plain)
        assert after.get("sinas.functions/*/*.read:own") is True

    async def test_role_not_held_by_owner_contributes_nothing(
        self, client, db, key_user, extra_role
    ):
        user, _ = key_user
        resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "ambitious", "role_ids": [str(extra_role.id)]},
            headers=auth_headers(user),
        )
        assert resp.status_code == 201
        plain = resp.json()["key"]
        _, perms = await validate_api_key(db, plain)
        # extra_role grants query read, but the owner does not hold it
        assert "sinas.queries/*/*.read:own" not in perms
        # ...until the owner is assigned the role
        db.add(UserRole(role_id=extra_role.id, user_id=user.id, active=True))
        await db.flush()
        _, perms = await validate_api_key(db, plain)
        assert perms.get("sinas.queries/*/*.read:own") is True

    async def test_owner_demotion_caps_explicit_key_live(self, client, db, key_user):
        user, role = key_user
        resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "explicit", "permissions": {"sinas.agents/*/*.read:own": True}},
            headers=auth_headers(user),
        )
        plain = resp.json()["key"]
        _, perms = await validate_api_key(db, plain)
        assert perms.get("sinas.agents/*/*.read:own") is True

        # Demote the owner: drop the granting permission from their role
        await db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_key == "sinas.agents/*/*.read:own",
            )
        )
        await db.flush()
        _, perms = await validate_api_key(db, plain)
        assert "sinas.agents/*/*.read:own" not in perms

    async def test_deleted_role_drops_off_key(self, client, db, key_user, extra_role):
        user, _ = key_user
        db.add(UserRole(role_id=extra_role.id, user_id=user.id, active=True))
        await db.flush()
        resp = await client.post(
            "/api/v1/api-keys",
            json={"name": "doomed", "role_ids": [str(extra_role.id)]},
            headers=auth_headers(user),
        )
        plain = resp.json()["key"]
        _, perms = await validate_api_key(db, plain)
        assert perms.get("sinas.queries/*/*.read:own") is True

        # Delete the role the way uninstall does: children first, then role
        for child in (RolePermission, UserRole, APIKeyRole):
            await db.execute(delete(child).where(child.role_id == extra_role.id))
        await db.execute(delete(Role).where(Role.id == extra_role.id))
        await db.flush()
        _, perms = await validate_api_key(db, plain)
        assert "sinas.queries/*/*.read:own" not in perms

    async def test_explicit_denial_overrides_role_grant(self, client, db, key_user):
        user, role = key_user
        resp = await client.post(
            "/api/v1/api-keys",
            json={
                "name": "narrowed",
                "role_ids": [str(role.id)],
                "permissions": {"sinas.agents/*/*.read:own": False},
            },
            headers=auth_headers(user),
        )
        plain = resp.json()["key"]
        _, perms = await validate_api_key(db, plain)
        assert perms.get("sinas.agents/*/*.read:own") is False


class TestUpdate:
    async def test_attach_role_to_existing_key(self, client, db, key_user):
        user, role = key_user
        created = await client.post(
            "/api/v1/api-keys",
            json={"name": "bind-later", "permissions": {"sinas.agents/*/*.read:own": True}},
            headers=auth_headers(user),
        )
        key_id, plain = created.json()["id"], created.json()["key"]

        # No PATCH permission in key_user's role yet — add the update grant
        db.add(
            RolePermission(
                role_id=role.id,
                permission_key="sinas.api_keys/*/*.update:own",
                permission_value=True,
            )
        )
        db.add(
            RolePermission(
                role_id=role.id, permission_key="sinas.api_keys.update:own", permission_value=True
            )
        )
        await db.flush()

        resp = await client.patch(
            f"/api/v1/api-keys/{key_id}",
            json={"role_ids": [str(role.id)]},
            headers=auth_headers(user),
        )
        assert resp.status_code == 200, resp.text
        assert [r["name"] for r in resp.json()["roles"]] == [role.name]
        # Existing explicit grant untouched, role grants now flow — same key value
        _, perms = await validate_api_key(db, plain)
        assert perms.get("sinas.api_keys.create:own") is True  # from role
        assert perms.get("sinas.agents/*/*.read:own") is True  # explicit, kept

        # Clearing roles with [] removes the links
        resp = await client.patch(
            f"/api/v1/api-keys/{key_id}", json={"role_ids": []}, headers=auth_headers(user)
        )
        assert resp.status_code == 200
        assert resp.json()["roles"] == []
        _, perms = await validate_api_key(db, plain)
        assert "sinas.api_keys.create:own" not in perms

    async def test_update_permissions_subset_enforced(self, client, db, key_user):
        user, role = key_user
        db.add(
            RolePermission(
                role_id=role.id, permission_key="sinas.api_keys.update:own", permission_value=True
            )
        )
        await db.flush()
        created = await client.post(
            "/api/v1/api-keys", json={"name": "escalate-later"}, headers=auth_headers(user)
        )
        resp = await client.patch(
            f"/api/v1/api-keys/{created.json()['id']}",
            json={"permissions": {"sinas.*:all": True}},
            headers=auth_headers(user),
        )
        assert resp.status_code == 400
        assert "exceed" in resp.json()["detail"]

    async def test_update_requires_permission(self, client, db, key_user, test_user):
        user, _ = key_user
        created = await client.post(
            "/api/v1/api-keys", json={"name": "not-yours"}, headers=auth_headers(user)
        )
        resp = await client.patch(
            f"/api/v1/api-keys/{created.json()['id']}",
            json={"name": "hijacked"},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 403


class TestRoleCreateInlinePermissions:
    async def test_admin_creates_role_with_permissions_atomically(self, client, db, admin_user):
        name = f"inline-{uuid.uuid4().hex[:8]}"
        resp = await client.post(
            "/api/v1/roles",
            json={
                "name": name,
                "description": "one call",
                "permissions": {"sinas.agents/pkg/*.chat:all": True},
            },
            headers=auth_headers(admin_user),
        )
        assert resp.status_code == 201, resp.text
        role_id = resp.json()["id"]
        rows = (
            await db.execute(select(RolePermission).where(RolePermission.role_id == role_id))
        ).scalars().all()
        assert {r.permission_key: r.permission_value for r in rows} == {
            "sinas.agents/pkg/*.chat:all": True
        }

    async def test_inline_permissions_require_manage_permission(self, client, db, key_user):
        user, role = key_user
        db.add(
            RolePermission(
                role_id=role.id, permission_key="sinas.roles.create:own", permission_value=True
            )
        )
        await db.flush()
        resp = await client.post(
            "/api/v1/roles",
            json={"name": f"sneaky-{uuid.uuid4().hex[:8]}", "permissions": {"sinas.*:all": True}},
            headers=auth_headers(user),
        )
        assert resp.status_code == 403
        assert "manage role permissions" in resp.json()["detail"]
