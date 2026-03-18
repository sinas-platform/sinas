"""CRUD tests for core SINAS resources: Functions, Agents, Queries."""
import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database_connection import DatabaseConnection
from tests.conftest import auth_headers


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_connection(db: AsyncSession, test_user):
    """Create a minimal DatabaseConnection row for query tests."""
    conn = DatabaseConnection(
        name=f"test-conn-{uuid.uuid4().hex[:8]}",
        connection_type="postgresql",
        host="localhost",
        port=5432,
        database="testdb",
        username="testuser",
        password="testpass",
    )
    db.add(conn)
    await db.flush()
    await db.refresh(conn)
    return conn


# =========================================================================
# Functions CRUD
# =========================================================================

FUNCTION_PAYLOAD = {
    "namespace": "test",
    "name": "hello",
    "code": "def main(input): return {'result': 'hello'}",
    "description": "test fn",
}


class TestFunctionsCRUD:
    """Tests for /api/v1/functions endpoints."""

    async def test_create_function(self, client, test_user):
        resp = await client.post(
            "/api/v1/functions",
            json=FUNCTION_PAYLOAD,
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["namespace"] == "test"
        assert data["name"] == "hello"
        assert data["description"] == "test fn"

    async def test_list_functions_includes_created(self, client, test_user):
        await client.post(
            "/api/v1/functions", json=FUNCTION_PAYLOAD, headers=auth_headers(test_user)
        )
        resp = await client.get("/api/v1/functions", headers=auth_headers(test_user))
        assert resp.status_code == 200
        names = [f["name"] for f in resp.json()]
        assert "hello" in names

    async def test_get_function_by_namespace_name(self, client, test_user):
        await client.post(
            "/api/v1/functions", json=FUNCTION_PAYLOAD, headers=auth_headers(test_user)
        )
        resp = await client.get(
            "/api/v1/functions/test/hello", headers=auth_headers(test_user)
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "hello"

    async def test_update_function(self, client, test_user):
        await client.post(
            "/api/v1/functions", json=FUNCTION_PAYLOAD, headers=auth_headers(test_user)
        )
        resp = await client.put(
            "/api/v1/functions/test/hello",
            json={"description": "updated"},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated"

    async def test_delete_function(self, client, test_user):
        await client.post(
            "/api/v1/functions", json=FUNCTION_PAYLOAD, headers=auth_headers(test_user)
        )
        resp = await client.delete(
            "/api/v1/functions/test/hello", headers=auth_headers(test_user)
        )
        assert resp.status_code == 200

        resp = await client.get(
            "/api/v1/functions/test/hello", headers=auth_headers(test_user)
        )
        assert resp.status_code == 404

    async def test_duplicate_function_returns_400(self, client, test_user):
        await client.post(
            "/api/v1/functions", json=FUNCTION_PAYLOAD, headers=auth_headers(test_user)
        )
        resp = await client.post(
            "/api/v1/functions", json=FUNCTION_PAYLOAD, headers=auth_headers(test_user)
        )
        assert resp.status_code == 400

    async def test_unauthenticated_request_rejected(self, client):
        resp = await client.get("/api/v1/functions")
        assert resp.status_code in (401, 403)

    async def test_create_without_permission(self, client, db, test_user, test_role):
        """A user whose role lacks create permission gets 403."""
        from app.models.user import Role, RolePermission, User, UserRole

        # Create a role with only read permission
        role = Role(
            name=f"readonly-{uuid.uuid4().hex[:8]}", description="Read-only role"
        )
        db.add(role)
        await db.flush()
        await db.refresh(role)
        db.add(
            RolePermission(
                role_id=role.id,
                permission_key="sinas.functions.read:all",
                permission_value=True,
            )
        )
        await db.flush()

        user = User(email=f"ro-{uuid.uuid4().hex[:8]}@example.com", is_active=True)
        db.add(user)
        await db.flush()
        await db.refresh(user)
        db.add(UserRole(role_id=role.id, user_id=user.id, active=True))
        await db.flush()

        resp = await client.post(
            "/api/v1/functions",
            json=FUNCTION_PAYLOAD,
            headers=auth_headers(user),
        )
        assert resp.status_code == 403


# =========================================================================
# Agents CRUD
# =========================================================================

AGENT_PAYLOAD = {
    "namespace": "test",
    "name": "helper",
    "description": "test agent",
    "system_prompt": "You are a helper.",
}


class TestAgentsCRUD:
    """Tests for /api/v1/agents endpoints."""

    async def test_create_agent(self, client, test_user):
        resp = await client.post(
            "/api/v1/agents", json=AGENT_PAYLOAD, headers=auth_headers(test_user)
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["namespace"] == "test"
        assert data["name"] == "helper"

    async def test_list_agents_includes_created(self, client, test_user):
        await client.post(
            "/api/v1/agents", json=AGENT_PAYLOAD, headers=auth_headers(test_user)
        )
        resp = await client.get("/api/v1/agents", headers=auth_headers(test_user))
        assert resp.status_code == 200
        names = [a["name"] for a in resp.json()]
        assert "helper" in names

    async def test_get_agent_by_namespace_name(self, client, test_user):
        await client.post(
            "/api/v1/agents", json=AGENT_PAYLOAD, headers=auth_headers(test_user)
        )
        resp = await client.get(
            "/api/v1/agents/test/helper", headers=auth_headers(test_user)
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "helper"

    async def test_update_agent(self, client, test_user):
        await client.post(
            "/api/v1/agents", json=AGENT_PAYLOAD, headers=auth_headers(test_user)
        )
        resp = await client.put(
            "/api/v1/agents/test/helper",
            json={"description": "updated agent"},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated agent"

    async def test_delete_agent(self, client, test_user):
        await client.post(
            "/api/v1/agents", json=AGENT_PAYLOAD, headers=auth_headers(test_user)
        )
        resp = await client.delete(
            "/api/v1/agents/test/helper", headers=auth_headers(test_user)
        )
        assert resp.status_code == 204

        # Soft-deleted agents are filtered out by is_active in list
        resp = await client.get(
            "/api/v1/agents/test/helper", headers=auth_headers(test_user)
        )
        assert resp.status_code == 404

    async def test_duplicate_agent_returns_400(self, client, test_user):
        await client.post(
            "/api/v1/agents", json=AGENT_PAYLOAD, headers=auth_headers(test_user)
        )
        resp = await client.post(
            "/api/v1/agents", json=AGENT_PAYLOAD, headers=auth_headers(test_user)
        )
        assert resp.status_code == 400

    async def test_unauthenticated_request_rejected(self, client):
        resp = await client.get("/api/v1/agents")
        assert resp.status_code in (401, 403)

    async def test_create_without_permission(self, client, db):
        """A user with only read permissions cannot create agents."""
        from app.models.user import Role, RolePermission, User, UserRole

        role = Role(
            name=f"readonly-{uuid.uuid4().hex[:8]}", description="Read-only role"
        )
        db.add(role)
        await db.flush()
        await db.refresh(role)
        db.add(
            RolePermission(
                role_id=role.id,
                permission_key="sinas.agents.read:all",
                permission_value=True,
            )
        )
        await db.flush()

        user = User(email=f"ro-{uuid.uuid4().hex[:8]}@example.com", is_active=True)
        db.add(user)
        await db.flush()
        await db.refresh(user)
        db.add(UserRole(role_id=role.id, user_id=user.id, active=True))
        await db.flush()

        resp = await client.post(
            "/api/v1/agents", json=AGENT_PAYLOAD, headers=auth_headers(user)
        )
        assert resp.status_code == 403


# =========================================================================
# Queries CRUD
# =========================================================================


class TestQueriesCRUD:
    """Tests for /api/v1/queries endpoints."""

    def _query_payload(self, db_connection):
        return {
            "namespace": "test",
            "name": "get-users",
            "description": "test query",
            "database_connection_id": str(db_connection.id),
            "operation": "read",
            "sql": "SELECT 1",
        }

    async def test_create_query(self, client, test_user, db_connection):
        resp = await client.post(
            "/api/v1/queries",
            json=self._query_payload(db_connection),
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["namespace"] == "test"
        assert data["name"] == "get-users"

    async def test_list_queries_includes_created(self, client, test_user, db_connection):
        await client.post(
            "/api/v1/queries",
            json=self._query_payload(db_connection),
            headers=auth_headers(test_user),
        )
        resp = await client.get("/api/v1/queries", headers=auth_headers(test_user))
        assert resp.status_code == 200
        names = [q["name"] for q in resp.json()]
        assert "get-users" in names

    async def test_get_query_by_namespace_name(self, client, test_user, db_connection):
        await client.post(
            "/api/v1/queries",
            json=self._query_payload(db_connection),
            headers=auth_headers(test_user),
        )
        resp = await client.get(
            "/api/v1/queries/test/get-users", headers=auth_headers(test_user)
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "get-users"

    async def test_update_query(self, client, test_user, db_connection):
        await client.post(
            "/api/v1/queries",
            json=self._query_payload(db_connection),
            headers=auth_headers(test_user),
        )
        resp = await client.put(
            "/api/v1/queries/test/get-users",
            json={"description": "updated query"},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated query"

    async def test_delete_query(self, client, test_user, db_connection):
        await client.post(
            "/api/v1/queries",
            json=self._query_payload(db_connection),
            headers=auth_headers(test_user),
        )
        resp = await client.delete(
            "/api/v1/queries/test/get-users", headers=auth_headers(test_user)
        )
        assert resp.status_code == 204

        resp = await client.get(
            "/api/v1/queries/test/get-users", headers=auth_headers(test_user)
        )
        assert resp.status_code == 404

    async def test_duplicate_query_returns_400(self, client, test_user, db_connection):
        payload = self._query_payload(db_connection)
        await client.post(
            "/api/v1/queries", json=payload, headers=auth_headers(test_user)
        )
        resp = await client.post(
            "/api/v1/queries", json=payload, headers=auth_headers(test_user)
        )
        assert resp.status_code == 400

    async def test_unauthenticated_request_rejected(self, client):
        resp = await client.get("/api/v1/queries")
        assert resp.status_code in (401, 403)
