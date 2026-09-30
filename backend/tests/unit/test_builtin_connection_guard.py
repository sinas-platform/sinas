"""The built-in database connection is not editable into a broken state (#76).

Its host/credentials come from the deployment's own DATABASE_* settings. The
API used to accept a new password, encrypt it, and leave the connection
permanently broken — recoverable only by deleting the row directly in
Postgres or reading the real password off the host, neither of which an
operator with console access alone can do.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import EncryptionService
from app.models.database_connection import DatabaseConnection
from tests.conftest import auth_headers

async def _make_connection(db: AsyncSession, managed_by: str | None) -> DatabaseConnection:
    conn = DatabaseConnection(
        name=f"conn-{uuid.uuid4().hex[:8]}",
        connection_type="postgresql",
        host="postgres",
        port=5432,
        database="sinas_data",
        username="postgres",
        password=EncryptionService().encrypt("original-secret"),
        is_active=True,
        read_only=False,
        managed_by=managed_by,
        config={"pool_size": 5},
    )
    db.add(conn)
    await db.flush()
    await db.refresh(conn)
    return conn


class TestSystemManagedConnection:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("password", "new-secret"),
            ("host", "elsewhere.example.com"),
            ("username", "someone-else"),
            ("database", "not_sinas_data"),
            ("port", 6543),
        ],
    )
    async def test_connection_critical_fields_are_rejected(
        self, client, db: AsyncSession, admin_user, field, value
    ):
        conn = await _make_connection(db, managed_by="system")
        before = conn.password

        response = await client.patch(
            f"/api/v1/database-connections/{conn.id}",
            json={field: value},
            headers=auth_headers(admin_user),
        )

        assert response.status_code == 400, response.text
        assert field in response.json()["detail"]
        await db.refresh(conn)
        assert conn.password == before

    async def test_safe_fields_remain_editable(
        self, client, db: AsyncSession, admin_user
    ):
        conn = await _make_connection(db, managed_by="system")

        response = await client.patch(
            f"/api/v1/database-connections/{conn.id}",
            json={"read_only": True, "is_active": False},
            headers=auth_headers(admin_user),
        )

        assert response.status_code == 200, response.text
        await db.refresh(conn)
        assert conn.read_only is True
        assert conn.is_active is False

    async def test_user_created_connections_are_unaffected(
        self, client, db: AsyncSession, admin_user
    ):
        conn = await _make_connection(db, managed_by=None)
        before = conn.password

        response = await client.patch(
            f"/api/v1/database-connections/{conn.id}",
            json={"password": "rotated-by-the-owner"},
            headers=auth_headers(admin_user),
        )

        assert response.status_code == 200, response.text
        await db.refresh(conn)
        assert conn.password != before
