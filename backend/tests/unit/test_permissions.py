"""Tests for the SINAS permission system."""

from app.core.permissions import check_permission
from tests.conftest import auth_headers


# ---------------------------------------------------------------------------
# 1. Unit tests for check_permission
# ---------------------------------------------------------------------------


class TestCheckPermissionExactMatch:
    async def test_exact_match(self):
        perms = {"sinas.chats.read:own": True}
        assert check_permission(perms, "sinas.chats.read:own") is True

    async def test_exact_match_false_value(self):
        perms = {"sinas.chats.read:own": False}
        assert check_permission(perms, "sinas.chats.read:own") is False


class TestCheckPermissionWildcard:
    async def test_service_level_wildcard_matches_everything(self):
        perms = {"sinas.*:all": True}
        assert check_permission(perms, "sinas.chats.read:own") is True
        assert check_permission(perms, "sinas.agents.create:all") is True
        assert check_permission(perms, "sinas.functions.delete:own") is True

    async def test_service_wildcard_matches_namespaced_resources(self):
        perms = {"sinas.*:all": True}
        assert check_permission(perms, "sinas.functions/marketing/send.execute:own") is True

    async def test_resource_action_wildcard(self):
        perms = {"sinas.functions.*:all": True}
        assert check_permission(perms, "sinas.functions.read:all") is True
        assert check_permission(perms, "sinas.functions.create:all") is True
        assert check_permission(perms, "sinas.functions.delete:own") is True

    async def test_resource_action_wildcard_does_not_match_other_resource(self):
        perms = {"sinas.functions.*:all": True}
        assert check_permission(perms, "sinas.agents.read:all") is False

    async def test_path_wildcard_both_segments(self):
        perms = {"sinas.functions/*/*.execute:own": True}
        assert check_permission(perms, "sinas.functions/marketing/send_email.execute:own") is True

    async def test_path_wildcard_namespace_only(self):
        perms = {"sinas.functions/marketing/*.execute:own": True}
        assert check_permission(perms, "sinas.functions/marketing/send_email.execute:own") is True

    async def test_path_wildcard_wrong_namespace(self):
        perms = {"sinas.functions/marketing/*.execute:own": True}
        assert check_permission(perms, "sinas.functions/sales/send_email.execute:own") is False


class TestCheckPermissionScopeHierarchy:
    async def test_all_satisfies_own(self):
        perms = {"sinas.chats.read:all": True}
        assert check_permission(perms, "sinas.chats.read:own") is True

    async def test_own_does_not_satisfy_all(self):
        perms = {"sinas.chats.read:own": True}
        assert check_permission(perms, "sinas.chats.read:all") is False

    async def test_all_satisfies_all(self):
        perms = {"sinas.chats.read:all": True}
        assert check_permission(perms, "sinas.chats.read:all") is True

    async def test_own_satisfies_own(self):
        perms = {"sinas.chats.read:own": True}
        assert check_permission(perms, "sinas.chats.read:own") is True


class TestCheckPermissionEdgeCases:
    async def test_non_matching_permission(self):
        perms = {"sinas.chats.read:own": True}
        assert check_permission(perms, "sinas.agents.create:own") is False

    async def test_empty_permissions_dict(self):
        assert check_permission({}, "sinas.chats.read:own") is False

    async def test_custom_service_namespace(self):
        perms = {"titan.*:all": True}
        assert check_permission(perms, "titan.student_profile.read:own") is True

    async def test_custom_service_does_not_match_sinas(self):
        perms = {"titan.*:all": True}
        assert check_permission(perms, "sinas.chats.read:own") is False

    async def test_multiple_permissions_first_match(self):
        perms = {
            "sinas.agents.read:own": True,
            "sinas.functions.read:all": True,
        }
        assert check_permission(perms, "sinas.functions.read:own") is True
        assert check_permission(perms, "sinas.agents.read:own") is True
        assert check_permission(perms, "sinas.agents.create:own") is False


# ---------------------------------------------------------------------------
# 2. API-level permission checks via POST /auth/check-permissions
# ---------------------------------------------------------------------------


class TestCheckPermissionsAPI:
    async def test_admin_passes_all_checks(self, client, admin_user):
        resp = await client.post(
            "/auth/check-permissions",
            json={
                "permissions": [
                    "sinas.agents.create:all",
                    "sinas.roles.manage_members:all",
                    "sinas.system.read:all",
                ],
                "logic": "AND",
            },
            headers=auth_headers(admin_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"] is True
        assert all(c["has_permission"] for c in body["checks"])

    async def test_regular_user_passes_granted_permissions(self, client, test_user):
        resp = await client.post(
            "/auth/check-permissions",
            json={
                "permissions": ["sinas.agents.read:all", "sinas.users.read:own"],
                "logic": "AND",
            },
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"] is True

    async def test_regular_user_fails_ungranted_permissions(self, client, test_user):
        resp = await client.post(
            "/auth/check-permissions",
            json={
                "permissions": ["sinas.roles.manage_members:all"],
                "logic": "AND",
            },
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"] is False
        assert body["checks"][0]["has_permission"] is False

    async def test_and_logic_all_must_match(self, client, test_user):
        resp = await client.post(
            "/auth/check-permissions",
            json={
                "permissions": [
                    "sinas.agents.read:all",  # granted
                    "sinas.roles.delete:all",  # not granted
                ],
                "logic": "AND",
            },
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"] is False

    async def test_or_logic_any_must_match(self, client, test_user):
        resp = await client.post(
            "/auth/check-permissions",
            json={
                "permissions": [
                    "sinas.agents.read:all",  # granted
                    "sinas.roles.delete:all",  # not granted
                ],
                "logic": "OR",
            },
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"] is True


# ---------------------------------------------------------------------------
# 3. Endpoint-level enforcement
# ---------------------------------------------------------------------------


class TestEndpointEnforcement:
    async def test_user_without_create_permission_cannot_post_agents(
        self, client, db, test_user, test_role
    ):
        """User whose role lacks sinas.agents.create:own gets 403."""
        from sqlalchemy import delete

        from app.models.user import RolePermission

        # Remove the create permission from the test role
        await db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == test_role.id,
                RolePermission.permission_key == "sinas.agents.create:own",
            )
        )
        await db.flush()

        resp = await client.post(
            "/api/v1/agents",
            json={
                "name": "forbidden-agent",
                "namespace": "test",
                "system_prompt": "test",
            },
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 403

    async def test_admin_can_post_agents(self, client, admin_user):
        """Admin with sinas.*:all can create agents."""
        resp = await client.post(
            "/api/v1/agents",
            json={
                "name": "admin-agent",
                "namespace": "test",
                "system_prompt": "test",
            },
            headers=auth_headers(admin_user),
        )
        # 201 Created or 409 if name collision — either way, not 403
        assert resp.status_code != 403
