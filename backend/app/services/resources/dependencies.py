"""Dependencies applier.

A dependency approves a Python package for function containers; nothing
points at it, and installing happens when containers (re)build. Packages may
add dependencies but never remove them (agreed: another package or function
may rely on one, and removing triggers an image rebuild). A package can't add
one the deployment doesn't allow (ALLOW_PACKAGE_INSTALLATION, ALLOWED_PACKAGES)
— the API refuses those too; an operator's own config may.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.models.dependency import Dependency
from app.schemas.spec.dependency import DependencySpec
from app.services.resources.base import ApplierError, ApplyContext, ResourceApplier


class InstallNotAllowed(ApplierError):
    status_code = 403


def installation_problem(package_name: str) -> str | None:
    """Why this deployment doesn't allow approving the package, if it doesn't."""
    from app.core.config import settings

    if not settings.allow_package_installation:
        return "Package installation is disabled"
    if settings.allowed_packages:
        whitelist = {pkg.strip() for pkg in settings.allowed_packages.split(",")}
        if package_name not in whitelist:
            return (
                f"Package '{package_name}' not in whitelist. Allowed packages: "
                f"{', '.join(sorted(whitelist))}"
            )
    return None


class DependencyApplier(ResourceApplier[DependencySpec]):
    kind = "dependencies"
    label = "Dependency"
    noun = "dependency"
    config_section = "dependencies"
    spec_model = DependencySpec
    model = Dependency
    keep_unless_declared = ("version",)
    deleted_with_package = False

    def key_of(self, spec: DependencySpec) -> str:
        return spec.key

    def key_of_row(self, row: Dependency) -> str:
        return row.package_name

    def config_key(self, item: Any) -> str:
        return item.packageName.split("==", 1)[0]

    async def find(self, ctx: ApplyContext, key: str) -> Dependency | None:
        return (
            await ctx.db.execute(
                select(Dependency)
                .where(Dependency.package_name == key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Dependency) -> DependencySpec:
        return DependencySpec.model_construct(package_name=row.package_name, version=row.version or None)

    def new_row(self, spec: DependencySpec, ctx: ApplyContext) -> Dependency:
        installer = ctx.actor_user_id or ctx.owner_user_id
        return Dependency(installed_by=uuid.UUID(str(installer)) if installer else None)

    async def write_row(self, row: Dependency, spec: DependencySpec, ctx: ApplyContext, current=None) -> None:
        row.package_name = spec.package_name
        row.version = spec.version
        row.installed_at = datetime.now(UTC)

    async def check_references(self, spec: DependencySpec, ctx: ApplyContext) -> None:
        # On create: a package can't approve what the deployment doesn't allow.
        if ctx.origin == "package":
            problem = installation_problem(spec.package_name)
            if problem:
                raise InstallNotAllowed(problem)
