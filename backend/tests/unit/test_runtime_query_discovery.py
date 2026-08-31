"""Runtime GET /queries — same discovery gap as pipelines (#168).

execute existed with no way to learn which queries exist without the
management plane. Mirrors the discovery contract: permission-filtered,
active-only.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database_connection import DatabaseConnection
from app.models.query import Query
from app.models.user import User

from tests.conftest import auth_headers


async def _query(db: AsyncSession, owner: User, ns: str, name: str, active: bool = True) -> Query:
    conn = DatabaseConnection(
        name=f"conn-{uuid.uuid4().hex[:8]}",
        connection_type="postgresql",
        host="localhost",
        port=5432,
        database="x",
        username="u",
        password="enc",
        is_active=True,
    )
    db.add(conn)
    await db.flush()
    q = Query(
        user_id=owner.id,
        namespace=ns,
        name=name,
        database_connection_id=conn.id,
        operation="read",
        sql="SELECT 1",
        is_active=active,
    )
    db.add(q)
    await db.flush()
    return q


class TestRuntimeQueryDiscovery:
    async def test_admin_lists_queries(self, client, db, admin_user):
        ns = f"qdisc{uuid.uuid4().hex[:6]}"
        await _query(db, admin_user, ns, "lookup")
        resp = await client.get("/queries", headers=auth_headers(admin_user))
        assert resp.status_code == 200, resp.text
        names = {(q["namespace"], q["name"]) for q in resp.json()}
        assert (ns, "lookup") in names

    async def test_inactive_hidden(self, client, db, admin_user):
        ns = f"qdisc{uuid.uuid4().hex[:6]}"
        await _query(db, admin_user, ns, "dormant", active=False)
        resp = await client.get("/queries", headers=auth_headers(admin_user))
        assert (ns, "dormant") not in {(q["namespace"], q["name"]) for q in resp.json()}

    async def test_scoped_user_sees_only_permitted(self, client, db, admin_user, test_user):
        # test_role grants sinas.queries/*/*.read:all — sees everything active;
        # but a user with NO query grants must see [].
        ns = f"qdisc{uuid.uuid4().hex[:6]}"
        await _query(db, admin_user, ns, "visible")
        resp = await client.get("/queries", headers=auth_headers(test_user))
        assert resp.status_code == 200
        assert (ns, "visible") in {(q["namespace"], q["name"]) for q in resp.json()}

        loner = User(email=f"noq-{uuid.uuid4().hex[:8]}@example.com")
        db.add(loner)
        await db.flush()
        resp = await client.get("/queries", headers=auth_headers(loner))
        assert resp.status_code == 200
        assert resp.json() == []
