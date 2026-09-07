"""Installing one package more than once (package.multiInstance).

A multi-instance package is installed under an install name (`instance`);
${{ install.name }} in its YAML resolves to that name, so every install gets
its own namespaces, roles and permission keys, its own Package row, and its
own managed_by tag. The declared package name is kept in package_name.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connector import Connector
from app.models.package import Package
from app.models.user import Role, RolePermission
from app.services.package_service import PackageService, resolve_install_name

MULTI_YAML = """
apiVersion: sinas.co/v1
kind: SinasPackage
package:
  name: {pkg}
  version: "1.0.0"
  multiInstance: true
spec:
  connectors:
    - namespace: ${{{{ install.name }}}}
      name: api
      baseUrl: https://example.com
  agents:
    - namespace: ${{{{ install.name }}}}
      name: clerk
      description: instance agent
      systemPrompt: You keep records for ${{{{ install.name }}}}.
  manifests:
    - namespace: ${{{{ install.name }}}}
      name: ${{{{ install.name }}}}
      requiredResources:
        - {{ type: agent, namespace: "${{{{ install.name }}}}", name: clerk }}
"""

SINGLE_YAML = """
apiVersion: sinas.co/v1
kind: SinasPackage
package:
  name: {pkg}
  version: "1.0.0"
spec:
  connectors:
    - namespace: {pkg}
      name: api
      baseUrl: https://example.com
"""

MULTI_WITHOUT_REF_YAML = SINGLE_YAML.replace(
    'version: "1.0.0"', 'version: "1.0.0"\n  multiInstance: true'
)

ROLES_YAML = """
apiVersion: sinas.co/v1
kind: SinasPackage
package:
  name: {pkg}
  version: "1.0.0"
  multiInstance: true
spec:
  connectors:
    - namespace: ${{{{ install.name }}}}
      name: api
      baseUrl: https://example.com
  roles:
    - name: ${{{{ install.name }}}}-reader
      description: read this instance
      permissions:
        - {{ key: "${{{{ install.name }}}}.*.read:all", value: true }}
"""


def _pkg():
    return f"pkg-{uuid.uuid4().hex[:8]}"


async def _connectors(db: AsyncSession, namespace: str) -> list[Connector]:
    rows = await db.execute(select(Connector).where(Connector.namespace == namespace))
    return list(rows.scalars().all())


async def _package(db: AsyncSession, name: str) -> Package | None:
    return (await db.execute(select(Package).where(Package.name == name))).scalar_one_or_none()


class TestResolveInstallName:
    def test_defaults_to_the_declared_name_and_substitutes(self):
        name, text, declared, multi = resolve_install_name(MULTI_YAML.format(pkg="records"), None)
        assert (name, declared, multi) == ("records", "records", True)
        assert "${{ install.name }}" not in text
        assert "namespace: records" in text

    def test_single_instance_package_refuses_another_name(self):
        with pytest.raises(ValueError, match="does not support multiple instances"):
            resolve_install_name(SINGLE_YAML.format(pkg="records"), "records-personal")

    def test_multi_instance_must_reference_install_name(self):
        with pytest.raises(ValueError, match="never uses"):
            resolve_install_name(MULTI_WITHOUT_REF_YAML.format(pkg="records"), "records-personal")

    def test_instance_name_is_validated(self):
        with pytest.raises(ValueError, match="invalid"):
            resolve_install_name(MULTI_YAML.format(pkg="records"), "Records Personal")

    def test_same_name_as_declared_is_always_fine(self):
        name, _, _, _ = resolve_install_name(SINGLE_YAML.format(pkg="records"), "records")
        assert name == "records"


class TestPackageInstances:
    async def test_default_install_records_the_declared_name(self, db, admin_user):
        pkg = _pkg()
        package, result = await PackageService(db).install(
            MULTI_YAML.format(pkg=pkg), str(admin_user.id)
        )
        assert result.success, result.errors
        assert package.name == pkg and package.package_name == pkg
        (connector,) = await _connectors(db, pkg)
        assert connector.managed_by == f"pkg:{pkg}"

    async def test_two_instances_live_side_by_side_and_uninstall_separately(self, db, admin_user):
        pkg = _pkg()
        svc = PackageService(db)
        yaml = MULTI_YAML.format(pkg=pkg)
        work, r1 = await svc.install(yaml, str(admin_user.id), instance=f"{pkg}-work")
        personal, r2 = await svc.install(yaml, str(admin_user.id), instance=f"{pkg}-personal")
        assert r1.success and r2.success
        assert (work.name, work.package_name) == (f"{pkg}-work", pkg)
        assert (personal.name, personal.package_name) == (f"{pkg}-personal", pkg)

        (c_work,) = await _connectors(db, f"{pkg}-work")
        (c_personal,) = await _connectors(db, f"{pkg}-personal")
        assert c_work.managed_by == f"pkg:{pkg}-work"
        assert c_personal.managed_by == f"pkg:{pkg}-personal"
        assert await _connectors(db, pkg) == []  # no default instance was created

        deleted = await svc.uninstall(f"{pkg}-work")
        assert deleted.get("connectors") == 1 and deleted.get("manifests") == 1
        assert deleted.get("agents") == 1
        assert await _package(db, f"{pkg}-work") is None
        assert await _package(db, f"{pkg}-personal") is not None
        assert len(await _connectors(db, f"{pkg}-personal")) == 1

    async def test_reinstall_under_the_same_instance_upgrades_in_place(self, db, admin_user):
        pkg = _pkg()
        svc = PackageService(db)
        yaml = MULTI_YAML.format(pkg=pkg)
        first, _ = await svc.install(yaml, str(admin_user.id), instance=f"{pkg}-work")
        again, result = await svc.install(
            yaml.replace('"1.0.0"', '"1.1.0"'), str(admin_user.id), instance=f"{pkg}-work"
        )
        assert result.success
        assert again.id == first.id and again.version == "1.1.0"
        assert len(await _connectors(db, f"{pkg}-work")) == 1

    async def test_instance_name_taken_by_another_package_is_refused(self, db, admin_user):
        pkg_a, pkg_b = _pkg(), _pkg()
        svc = PackageService(db)
        await svc.install(SINGLE_YAML.format(pkg=pkg_a), str(admin_user.id))
        with pytest.raises(ValueError, match="already installed from package"):
            await svc.install(MULTI_YAML.format(pkg=pkg_b), str(admin_user.id), instance=pkg_a)

    async def test_single_instance_package_cannot_be_installed_twice(self, db, admin_user):
        pkg = _pkg()
        with pytest.raises(ValueError, match="does not support multiple instances"):
            await PackageService(db).install(
                SINGLE_YAML.format(pkg=pkg), str(admin_user.id), instance=f"{pkg}-two"
            )

    async def test_preview_reports_install_info(self, db, admin_user):
        pkg = _pkg()
        result, _, _, info = await PackageService(db).preview(
            MULTI_YAML.format(pkg=pkg), str(admin_user.id), instance=f"{pkg}-work"
        )
        assert info == {"instance": f"{pkg}-work", "package_name": pkg, "multi_instance": True}
        assert any(f"{pkg}-work" in c.resourceName for c in result.changes)
        assert await _connectors(db, f"{pkg}-work") == []  # dry run

        _, _, _, default_info = await PackageService(db).preview(
            MULTI_YAML.format(pkg=pkg), str(admin_user.id)
        )
        assert default_info["instance"] == pkg

    async def test_roles_and_permission_keys_follow_the_install_name(self, db, admin_user):
        pkg = _pkg()
        _, result = await PackageService(db).install(
            ROLES_YAML.format(pkg=pkg),
            str(admin_user.id),
            instance=f"{pkg}-work",
            allow_broad_role_permissions=True,
        )
        assert result.success, result.errors
        role = (
            await db.execute(select(Role).where(Role.name == f"{pkg}-work-reader"))
        ).scalar_one()
        assert role.managed_by == f"pkg:{pkg}-work"
        rows = (
            (await db.execute(select(RolePermission).where(RolePermission.role_id == role.id)))
            .scalars()
            .all()
        )
        assert {p.permission_key for p in rows} == {f"{pkg}-work.*.read:all"}
