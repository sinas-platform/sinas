"""Runtime GET /pipelines — the discovery endpoint the runtime surface lacked.

run/replay/runs existed without a way to learn which pipelines exist, forcing
runtime consumers onto the management plane for discovery. Mirrors the
agents/functions discovery contract: permission-filtered, active-only.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import Pipeline
from app.models.user import Role, RolePermission, User, UserRole

from tests.conftest import auth_headers


async def _pipeline(db: AsyncSession, owner: User, ns: str, name: str, active: bool = True) -> Pipeline:
    p = Pipeline(
        user_id=owner.id,
        namespace=ns,
        name=name,
        steps=[{"name": "s1", "type": "function", "function": f"{ns}/noop"}],
        is_active=active,
    )
    db.add(p)
    await db.flush()
    return p


class TestRuntimePipelineDiscovery:
    async def test_admin_lists_pipelines(self, client, db, admin_user):
        ns = f"disc{uuid.uuid4().hex[:6]}"
        await _pipeline(db, admin_user, ns, "flow-a")
        resp = await client.get("/pipelines", headers=auth_headers(admin_user))
        assert resp.status_code == 200, resp.text
        names = {(p["namespace"], p["name"]) for p in resp.json()}
        assert (ns, "flow-a") in names

    async def test_inactive_pipelines_hidden(self, client, db, admin_user):
        ns = f"disc{uuid.uuid4().hex[:6]}"
        await _pipeline(db, admin_user, ns, "dormant", active=False)
        resp = await client.get("/pipelines", headers=auth_headers(admin_user))
        assert resp.status_code == 200
        assert (ns, "dormant") not in {(p["namespace"], p["name"]) for p in resp.json()}

    async def test_no_pipeline_permission_sees_empty(self, client, db, admin_user, test_user):
        # test_role grants agents/functions/queries but nothing on pipelines
        ns = f"disc{uuid.uuid4().hex[:6]}"
        await _pipeline(db, admin_user, ns, "hidden-flow")
        resp = await client.get("/pipelines", headers=auth_headers(test_user))
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_namespace_scoped_read(self, client, db, admin_user):
        ns_ok = f"disc{uuid.uuid4().hex[:6]}"
        ns_no = f"disc{uuid.uuid4().hex[:6]}"
        await _pipeline(db, admin_user, ns_ok, "visible")
        await _pipeline(db, admin_user, ns_no, "invisible")

        role = Role(name=f"pipe-reader-{uuid.uuid4().hex[:8]}")
        db.add(role)
        await db.flush()
        db.add(RolePermission(
            role_id=role.id,
            permission_key=f"sinas.pipelines/{ns_ok}/*.read:all",
            permission_value=True,
        ))
        user = User(email=f"reader-{uuid.uuid4().hex[:8]}@example.com")
        db.add(user)
        await db.flush()
        db.add(UserRole(role_id=role.id, user_id=user.id, active=True))
        await db.flush()

        resp = await client.get("/pipelines", headers=auth_headers(user))
        assert resp.status_code == 200
        names = {(p["namespace"], p["name"]) for p in resp.json()}
        assert (ns_ok, "visible") in names
        assert (ns_no, "invisible") not in names
