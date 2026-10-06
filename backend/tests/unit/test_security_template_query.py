"""Two privilege boundaries.

- Templates render in the API process. A plain jinja2 Environment let a
  template author traverse attributes (__class__/__globals__) to execute
  code there; it is sandboxed now, like template_renderer.
- A query runs its SQL with the connection's credentials. Holding
  queries.create alone let anyone bind any connection by UUID.
"""

import uuid

import pytest
import pytest_asyncio
from jinja2.exceptions import SecurityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database_connection import DatabaseConnection
from app.models.user import Role, RolePermission, User, UserRole
from app.services.template_service import template_service
from tests.conftest import auth_headers


@pytest_asyncio.fixture
async def db_connection(db: AsyncSession) -> DatabaseConnection:
    conn = DatabaseConnection(
        name=f"test-conn-{uuid.uuid4().hex[:8]}", connection_type="postgresql",
        host="localhost", port=5432, database="test", username="test",
    )
    db.add(conn)
    await db.flush()
    return conn


class TestTemplateSandbox:
    def test_attribute_traversal_is_blocked(self):
        payload = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        with pytest.raises(SecurityError):
            template_service.jinja_env.from_string(payload).render()

    def test_ordinary_templates_still_render(self):
        rendered = template_service.jinja_env.from_string("Hi {{ name }}!").render(name="<b>Ann</b>")
        assert rendered == "Hi &lt;b&gt;Ann&lt;/b&gt;!"


async def _user_with(db: AsyncSession, *permissions: str) -> User:
    role = Role(name=f"r-{uuid.uuid4().hex[:8]}", description="test")
    db.add(role)
    await db.flush()
    for key in permissions:
        db.add(RolePermission(role_id=role.id, permission_key=key, permission_value=True))
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    await db.flush()
    db.add(UserRole(role_id=role.id, user_id=user.id, active=True))
    await db.flush()
    return user


def _query(connection_id, **extra) -> dict:
    return {
        "namespace": "default", "name": f"q-{uuid.uuid4().hex[:8]}",
        "database_connection_id": str(connection_id), "operation": "read",
        "sql": "select 1", **extra,
    }


class TestQueryConnections:
    async def test_creating_a_query_needs_connection_access(self, client, db: AsyncSession, db_connection):
        user = await _user_with(db, "sinas.queries.create:own")
        response = await client.post(
            "/api/v1/queries", json=_query(db_connection.id), headers=auth_headers(user)
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Not authorized to use database connections"

    async def test_an_unknown_connection_is_a_404_not_a_500(self, client, admin_user):
        response = await client.post(
            "/api/v1/queries", json=_query(uuid.uuid4()), headers=auth_headers(admin_user)
        )
        assert response.status_code == 404

    async def test_moving_a_query_to_another_connection_is_checked_too(
        self, client, db: AsyncSession, admin_user, db_connection
    ):
        created = await client.post(
            "/api/v1/queries", json=_query(db_connection.id), headers=auth_headers(admin_user)
        )
        assert created.status_code == 201, created.text
        query = created.json()
        moved = await client.put(
            f"/api/v1/queries/{query['namespace']}/{query['name']}",
            json={"database_connection_id": str(uuid.uuid4())},
            headers=auth_headers(admin_user),
        )
        assert moved.status_code == 404
