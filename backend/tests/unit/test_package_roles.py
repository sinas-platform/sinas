"""Packages shipping roles: define-only governance, namespace bounds, upgrades.

Packages may DEFINE roles (their own least-privilege permission surface) but
never BIND them: emailDomain (auto-membership) is a hard error, assignment
stays an operator act. Granted permissions outside the package's own
namespaces require explicit operator consent (allowBroadRolePermissions).
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, RolePermission, UserRole
from app.services.package_service import PackageService

BASE_YAML = """
apiVersion: sinas.co/v1
kind: SinasPackage
metadata:
  name: {pkg}
package:
  name: {pkg}
  version: "{version}"
spec:
  connectors:
    - namespace: {ns}
      name: probe
      baseUrl: https://example.com
  roles:
    - name: {role}
      description: service role
      {email_domain}
      permissions:
{perms}
"""


def _yaml(pkg, ns, role, perms, version="1.0.0", email_domain=""):
    perm_lines = "\n".join(
        f"        - key: \"{k}\"\n          value: {str(v).lower()}" for k, v in perms
    )
    return BASE_YAML.format(
        pkg=pkg, ns=ns, role=role, perms=perm_lines, version=version,
        email_domain=email_domain or "# no emailDomain",
    )


async def _role(db: AsyncSession, name: str) -> Role | None:
    return (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()


async def _role_perms(db: AsyncSession, role_id) -> dict[str, bool]:
    rows = (
        await db.execute(select(RolePermission).where(RolePermission.role_id == role_id))
    ).scalars().all()
    return {r.permission_key: r.permission_value for r in rows}


class TestPackageRoles:
    async def test_install_creates_managed_role_with_permissions(self, db, admin_user):
        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        role_name = f"{pkg}-service"
        yaml = _yaml(pkg, "pkgns", role_name, [("sinas.agents/pkgns/*.execute:all", True)])
        package, result = await PackageService(db).install(yaml, str(admin_user.id))
        assert result.success, result.errors

        role = await _role(db, role_name)
        assert role is not None
        assert role.managed_by == f"pkg:{pkg}"
        perms = await _role_perms(db, role.id)
        assert perms == {"sinas.agents/pkgns/*.execute:all": True}

    async def test_email_domain_is_hard_error(self, db, admin_user):
        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        yaml = _yaml(
            pkg, "pkgns", f"{pkg}-service",
            [("sinas.agents/pkgns/*.execute:all", True)],
            email_domain="emailDomain: example.com",
        )
        with pytest.raises(ValueError, match="never bind"):
            await PackageService(db).install(yaml, str(admin_user.id))

    async def test_foreign_namespace_requires_consent(self, db, admin_user):
        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        role_name = f"{pkg}-service"
        yaml = _yaml(pkg, "pkgns", role_name, [("sinas.agents/otherns/*.execute:all", True)])
        with pytest.raises(ValueError, match="allowBroadRolePermissions"):
            await PackageService(db).install(yaml, str(admin_user.id))
        assert await _role(db, role_name) is None

        package, result = await PackageService(db).install(
            yaml, str(admin_user.id), allow_broad_role_permissions=True
        )
        assert result.success
        assert any("operator consent" in w for w in result.warnings)
        assert await _role(db, role_name) is not None

    async def test_non_namespaced_key_counts_as_broad(self, db, admin_user):
        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        yaml = _yaml(pkg, "pkgns", f"{pkg}-service", [("sinas.chats.create:own", True)])
        with pytest.raises(ValueError, match="allowBroadRolePermissions"):
            await PackageService(db).install(yaml, str(admin_user.id))

    async def test_denials_are_never_broad(self, db, admin_user):
        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        yaml = _yaml(pkg, "pkgns", f"{pkg}-service", [
            ("sinas.agents/pkgns/*.execute:all", True),
            ("sinas.chats.create:own", False),
        ])
        package, result = await PackageService(db).install(yaml, str(admin_user.id))
        assert result.success

    async def test_upgrade_applies_permission_only_change(self, db, admin_user):
        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        role_name = f"{pkg}-service"
        svc = PackageService(db)
        v1 = _yaml(pkg, "pkgns", role_name, [("sinas.agents/pkgns/*.execute:all", True)])
        await svc.install(v1, str(admin_user.id))

        # Same name/description — only the permission set changes. Before the
        # checksum included permissions this hit the unchanged-shortcut and
        # the new grant was silently never applied.
        v2 = _yaml(
            pkg, "pkgns", role_name,
            [
                ("sinas.agents/pkgns/*.execute:all", True),
                ("sinas.queries/pkgns/*.read:all", True),
            ],
            version="1.1.0",
        )
        package, result = await svc.install(v2, str(admin_user.id))
        assert result.success
        role = await _role(db, role_name)
        perms = await _role_perms(db, role.id)
        assert perms.get("sinas.queries/pkgns/*.read:all") is True

    async def test_existing_unmanaged_role_not_taken_over(self, db, admin_user):
        role_name = f"operator-role-{uuid.uuid4().hex[:8]}"
        db.add(Role(name=role_name, description="operator-owned"))
        await db.flush()

        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        yaml = _yaml(pkg, "pkgns", role_name, [("sinas.agents/pkgns/*.execute:all", True)])
        package, result = await PackageService(db).install(yaml, str(admin_user.id))
        assert any("not managed by" in w for w in result.warnings)
        role = await _role(db, role_name)
        assert role.managed_by is None  # untouched

    async def test_uninstall_removes_role_and_assignments(self, db, admin_user, test_user):
        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        role_name = f"{pkg}-service"
        svc = PackageService(db)
        yaml = _yaml(pkg, "pkgns", role_name, [("sinas.agents/pkgns/*.execute:all", True)])
        await svc.install(yaml, str(admin_user.id))

        role = await _role(db, role_name)
        db.add(UserRole(role_id=role.id, user_id=test_user.id, active=True))
        await db.flush()

        counts = await svc.uninstall(pkg)
        assert counts.get("roles") == 1
        assert await _role(db, role_name) is None
        assignments = (
            await db.execute(select(UserRole).where(UserRole.role_id == role.id))
        ).scalars().all()
        assert assignments == []

    async def test_preview_surfaces_role_governance(self, db, admin_user):
        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        yaml = _yaml(pkg, "pkgns", f"{pkg}-service", [("sinas.agents/otherns/*.execute:all", True)])
        result, _, _ = await PackageService(db).preview(yaml, str(admin_user.id))
        assert any("allowBroadRolePermissions" in w for w in result.warnings)
        assert await _role(db, f"{pkg}-service") is None


class TestChatWithoutReadAdvisory:
    async def test_chat_only_role_installs_with_advisory(self, db, admin_user):
        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        yaml = _yaml(pkg, "pkgns", f"{pkg}-service", [("sinas.agents/pkgns/*.chat:all", True)])
        package, result = await PackageService(db).install(yaml, str(admin_user.id))
        assert result.success
        assert any("403 on its first call" in w for w in result.warnings)

    async def test_chat_with_read_has_no_advisory(self, db, admin_user):
        pkg = f"pkg-{uuid.uuid4().hex[:8]}"
        yaml = _yaml(pkg, "pkgns", f"{pkg}-service", [
            ("sinas.agents/pkgns/*.chat:all", True),
            ("sinas.agents/pkgns/*.read:all", True),
        ])
        package, result = await PackageService(db).install(yaml, str(admin_user.id))
        assert result.success
        assert not any("403 on its first call" in w for w in result.warnings)
