"""Users stay on their own config path (identity, not config), with fixes:
config didn't normalize emails (so "Ann@X.com" created a second user next to
ann@x.com), wiped a user's whole membership history on every apply, and the
export listed ended memberships as roles held plus a lastLoginAt the config
schema doesn't read.
"""

import uuid

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, User, UserRole
from app.schemas.config import SinasConfig
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService


async def _apply(db, owner, **spec):
    config = SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "people"},
        "spec": spec,
    })
    svc = ConfigApplyService(db, "people", owner_user_id=str(owner.id), managed_by="config", auto_commit=False)
    return await svc.apply_config(config)


async def _role(db, name):
    role = Role(name=name)
    db.add(role)
    await db.flush()
    return role


async def test_emails_are_normalized(db: AsyncSession, admin_user):
    local = f"ann-{uuid.uuid4().hex[:6]}"
    assert (await _apply(db, admin_user, users=[{"email": f"  {local.upper()}@Example.COM "}])).success
    rows = (await db.execute(select(User).where(User.email.ilike(f"%{local}%")))).scalars().all()
    assert [u.email for u in rows] == [f"{local}@example.com"]
    again = await _apply(db, admin_user, users=[{"email": f"{local}@example.com"}])
    assert again.success
    rows = (await db.execute(select(User).where(User.email.ilike(f"%{local}%")))).scalars().all()
    assert len(rows) == 1


async def test_dropped_memberships_are_ended_not_deleted(db: AsyncSession, admin_user):
    a, b = await _role(db, f"ra-{uuid.uuid4().hex[:6]}"), await _role(db, f"rb-{uuid.uuid4().hex[:6]}")
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    assert (await _apply(db, admin_user, users=[{"email": email, "roles": [a.name, b.name]}])).success
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    assert (await _apply(db, admin_user, users=[{"email": email, "roles": [a.name], "customFields": {"x": 1}}])).success
    rows = (await db.execute(select(UserRole).where(UserRole.user_id == user.id))).scalars().all()
    state = {r.role_id: (r.active, r.removed_at is not None) for r in rows}
    assert state == {a.id: (True, False), b.id: (False, True)}  # b ended, kept as history
    # Declared again: the ended row comes back, no duplicate.
    assert (await _apply(db, admin_user, users=[{"email": email, "roles": [a.name, b.name]}])).success
    rows = (await db.execute(select(UserRole).where(UserRole.user_id == user.id))).scalars().all()
    assert len(rows) == 2 and all(r.active for r in rows)


async def test_export_lists_only_current_memberships(db: AsyncSession, admin_user):
    a, b = await _role(db, f"ra-{uuid.uuid4().hex[:6]}"), await _role(db, f"rb-{uuid.uuid4().hex[:6]}")
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    user = User(email=email)
    db.add(user)
    await db.flush()
    db.add_all([
        UserRole(user_id=user.id, role_id=a.id, active=True),
        UserRole(user_id=user.id, role_id=b.id, active=False),
    ])
    await db.flush()
    doc = yaml.safe_load(await ConfigExportService(db).export_config())["spec"]
    exported = next(u for u in doc["users"] if u["email"] == email)
    assert exported["roles"] == [a.name] and "lastLoginAt" not in exported


async def test_a_user_stored_un_normalized_is_found_and_fixed(db: AsyncSession, admin_user):
    local = f"bob-{uuid.uuid4().hex[:6]}"
    db.add(User(email=f"\u00a0\t{local.upper()}@Example.com \n", managed_by="config", config_name="people"))
    await db.flush()
    assert (await _apply(db, admin_user, users=[{"email": f"{local}@example.com", "customFields": {"a": 1}}])).success
    rows = (await db.execute(select(User).where(User.email.ilike(f"%{local}%")))).scalars().all()
    assert [u.email for u in rows] == [f"{local}@example.com"]


async def test_removing_the_last_role_ends_it_and_leaving_roles_out_doesnt(db: AsyncSession, admin_user):
    a = await _role(db, f"ra-{uuid.uuid4().hex[:6]}")
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    assert (await _apply(db, admin_user, users=[{"email": email, "roles": [a.name]}])).success
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    # No roles key: memberships aren't this config's business.
    assert (await _apply(db, admin_user, users=[{"email": email, "customFields": {"x": 1}}])).success
    row = (await db.execute(select(UserRole).where(UserRole.user_id == user.id))).scalar_one()
    assert row.active is True
    # An explicit empty list: the user holds none.
    assert (await _apply(db, admin_user, users=[{"email": email, "roles": []}])).success
    await db.refresh(row)
    assert row.active is False and row.removed_at is not None
