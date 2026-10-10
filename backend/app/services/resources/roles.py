"""Roles applier: a role's definition and its permission map.

Memberships and API-key bindings are not part of it ("define, never bind").

The default roles (Admins, Users, GuestUsers) are the platform's: startup
creates and maintains them, auth flows and settings find them by name.
Config and packages leave them alone, as they always did; the API may edit
them without detaching them, but never renames or deletes one, and never
changes the Admins permissions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, select

from app.models.user import Role, RolePermission
from app.schemas.spec.role import RoleSpec
from app.services.resources.base import (
    ApplierError,
    ApplyContext,
    OwnershipDecision,
    ResourceApplier,
    ownership_decision,
)

ADMINS = "Admins"


def default_role_names() -> set[str]:
    from app.core.permissions import DEFAULT_ROLE_PERMISSIONS

    return set(DEFAULT_ROLE_PERMISSIONS)


class ProtectedRole(ApplierError):
    status_code = 403


class RoleApplier(ResourceApplier[RoleSpec]):
    kind = "roles"
    label = "Role"
    noun = "role"
    config_section = "roles"
    spec_model = RoleSpec
    model = Role

    def key_of(self, spec: RoleSpec) -> str:
        return spec.key

    def key_of_row(self, row: Role) -> str:
        return row.name

    def config_key(self, item: Any) -> str:
        return item.name

    async def find(self, ctx: ApplyContext, key: str) -> Role | None:
        return (
            await ctx.db.execute(
                select(Role)
                .where(Role.name == key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def current_spec(self, ctx: ApplyContext, row: Role) -> RoleSpec:
        permissions = {}
        if row.id is not None:
            permissions = {
                key: value
                for key, value in (
                    await ctx.db.execute(
                        select(RolePermission.permission_key, RolePermission.permission_value)
                        .where(RolePermission.role_id == row.id)
                    )
                ).all()
            }
        return self.spec_from_row(row, permissions)

    def spec_from_row(self, row: Role, permissions: dict[str, bool] | None = None) -> RoleSpec:
        return RoleSpec.model_construct(
            name=row.name,
            description=row.description,
            email_domain=row.email_domain,
            permissions=dict(permissions or {}),
        )

    def new_row(self, spec: RoleSpec, ctx: ApplyContext) -> Role:
        return Role()

    async def write_row(self, row: Role, spec: RoleSpec, ctx: ApplyContext, current=None) -> None:
        row.name = spec.name
        row.description = spec.description
        row.email_domain = spec.email_domain
        if current is not None and current.permissions == spec.permissions:
            return
        if not inspect(row).persistent:
            # New (or restored under its old id): insert it first, the
            # permissions below point at it.
            ctx.db.add(row)
            await ctx.db.flush()
        # Upsert in place (unique per role and key): keys that stay keep their
        # rows, removed ones go.
        existing = {
            p.permission_key: p
            for p in (
                await ctx.db.execute(select(RolePermission).where(RolePermission.role_id == row.id))
            ).scalars().all()
        }
        for key, permission in existing.items():
            if key not in spec.permissions:
                await ctx.db.delete(permission)
        for key, value in spec.permissions.items():
            if key in existing:
                existing[key].permission_value = value
            else:
                ctx.db.add(RolePermission(role_id=row.id, permission_key=key, permission_value=value))

    def pinned_fields(self, row: Role) -> tuple[str, ...]:
        # Kept on every write, restores included (the API refuses changing
        # them with a clear message).
        if row.name == ADMINS:
            return ("name", "permissions")
        if row.name in default_role_names():
            return ("name",)
        return ()

    def ownership(self, row: Role, ctx: ApplyContext) -> OwnershipDecision:
        if row.name in default_role_names() and row.managed_by is None:
            return "write" if ctx.origin == "api" else "skip"
        return ownership_decision(row.managed_by, ctx, row.config_name)

    async def delete(self, row: Role, ctx: ApplyContext) -> None:
        if row.name in default_role_names():
            raise ProtectedRole(f"Role '{row.name}' is a default role and can't be deleted")
        # Its permissions and memberships go with it (ORM cascade; API-key
        # bindings by the database's).
        await super().delete(row, ctx)
