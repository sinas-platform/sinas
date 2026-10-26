"""
Package service for installing, uninstalling, and managing integration packages.
"""
import logging
import re
from typing import Any, Optional

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.connector import Connector
from app.models.mcp_server import McpServer
from app.models.manifest import Manifest
from app.models.component import Component
from app.models.database_trigger import DatabaseTrigger
from app.models.file import Collection
from app.models.function import Function
from app.models.package import Package
from app.models.query import Query
from app.models.schedule import ScheduledJob
from app.models.skill import Skill
from app.models.store import Store
from app.models.template import Template
from app.models.webhook import Webhook
from app.schemas.config import ConfigApplyResponse, ConfigApplySummary, OwnershipSkip, SinasConfig
from app.services.config_apply import ConfigApplyService
from app.services.config_export import ConfigExportService
from app.services.config_parser import ConfigParser
from app.services.resource_serializers import (
    _remove_none_values,
    serialize_agent,
    serialize_collection,
    serialize_component,
    serialize_connector,
    serialize_mcp_server,
    serialize_database_trigger,
    serialize_function,
    serialize_manifest,
    serialize_query,
    serialize_schedule,
    serialize_skill,
    serialize_store,
    serialize_template,
    serialize_webhook,
)

logger = logging.getLogger(__name__)

# Resource types that packages cannot include (environment-specific).
# Roles are deliberately NOT here: a package may DEFINE roles (its own
# least-privilege permission surface) but never BIND them — assignment to
# users stays an operator act, and emailDomain (auto-membership) is rejected.
PACKAGE_SKIP_TYPES = {"users", "llmProviders", "databaseConnections", "packageRepositories", "identityProviders"}

# Permission keys shaped sinas.<resource>/<namespace>/... — anything else
# (non-namespaced or non-sinas keys) counts as broad for package roles.
NAMESPACED_PERM_RE = re.compile(r"^sinas\.[a-z_]+/(?P<ns>[^/]+)/")

# Models that support managed_by
MANAGED_MODELS = [Agent, Connector, McpServer, Manifest, Component, Collection, DatabaseTrigger, Function, Query, ScheduledJob, Skill, Store, Template, Webhook]


def _package_namespaces(config: SinasConfig) -> set[str]:
    """Namespaces the package installs resources into."""
    namespaces: set[str] = set()
    for field in type(config.spec).model_fields:
        value = getattr(config.spec, field, None)
        if isinstance(value, list):
            for item in value:
                ns = getattr(item, "namespace", None)
                if isinstance(ns, str) and ns:
                    namespaces.add(ns)
    return namespaces


def package_role_violations(config: SinasConfig) -> tuple[list[str], list[str], list[str]]:
    """
    Governance checks for roles shipped by a package.

    Returns (hard_errors, broad_permissions, advisories):
    - hard_errors: things a package may never do — bind users (emailDomain is
      auto-membership). Always rejected.
    - broad_permissions: granted keys reaching outside the namespaces the
      package itself installs into (non-namespaced keys, foreign or wildcard
      namespaces). Rejected unless the operator explicitly consents.
    - advisories: known potholes surfaced as warnings — e.g. agents .chat
      without .read (the chats endpoint checks read on the agent BEFORE the
      chat permission, so a chat-only role 403s on the very first call).
    """
    hard: list[str] = []
    broad: list[str] = []
    advisories: list[str] = []
    roles = getattr(config.spec, "roles", None) or []
    if not roles:
        return hard, broad, advisories

    namespaces = _package_namespaces(config)
    for role in roles:
        if role.emailDomain:
            hard.append(
                f"role '{role.name}' sets emailDomain — packages define roles, "
                "never bind them to users"
            )
        granted = [p.key for p in role.permissions if p.value]
        for key in granted:
            m = NAMESPACED_PERM_RE.match(key)
            if not m or m.group("ns") == "*" or m.group("ns") not in namespaces:
                broad.append(f"role '{role.name}': {key}")

        for key in granted:
            m = re.match(r"^sinas\.agents/([^/]+)/.+\.chat:", key)
            if m and not any(
                g.startswith(f"sinas.agents/{m.group(1)}/") and ".read:" in g
                for g in granted
            ):
                advisories.append(
                    f"role '{role.name}' grants {key} without a matching "
                    f"sinas.agents/{m.group(1)}/*.read permission — chatting with an "
                    "agent also requires reading it, so this role will 403 on its "
                    "first call"
                )
    return hard, broad, advisories


def detach_if_package_managed(resource) -> bool:
    """
    If a resource is managed by a package, clear its managed_by to detach it.
    Call this in update endpoints before modifying fields.

    Returns True if the resource was detached, False otherwise.
    """
    if hasattr(resource, "managed_by") and resource.managed_by and resource.managed_by.startswith("pkg:"):
        resource.managed_by = None
        resource.config_name = None
        resource.config_checksum = None
        return True
    return False


_INSTALL_NAME_PATTERN = re.compile(r"\$\{\{\s*install\.name\s*\}\}")
_INSTANCE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _declared_package(yaml_content: str) -> tuple[Optional[str], bool]:
    """(package.name, package.multiInstance) from the raw YAML, before any
    substitution. Malformed YAML yields (None, False); full validation
    happens later in parse_and_validate with a proper error."""
    try:
        doc = yaml.safe_load(yaml_content)
    except Exception:
        return None, False
    if not isinstance(doc, dict) or not isinstance(doc.get("package"), dict):
        return None, False
    meta = doc["package"]
    name = meta.get("name")
    return (name if isinstance(name, str) else None), bool(meta.get("multiInstance", False))


def resolve_install_name(yaml_content: str, instance: Optional[str]) -> tuple[str, str, Optional[str], bool]:
    """Decide the install name and substitute ${{ install.name }}.

    Returns (install_name, substituted_yaml, declared_name, multi_instance).

    The install name is the declared package name unless `instance` is given.
    A different name is only allowed when the package declares
    package.multiInstance: true AND references ${{ install.name }} at least
    once — without that, two installs would create the same namespaced
    resources and each would take over the other's.
    """
    declared, multi = _declared_package(yaml_content)
    install_name = instance or declared or ""
    if instance and not _INSTANCE_NAME_RE.match(instance):
        raise ValueError(
            f"Instance name '{instance}' is invalid: lowercase letters, digits and dashes only"
        )
    if instance and declared and instance != declared:
        if not multi:
            raise ValueError(
                f"Package '{declared}' does not support multiple instances; "
                "it must declare package.multiInstance: true to be installed as "
                f"'{instance}'"
            )
        if not _INSTALL_NAME_PATTERN.search(yaml_content):
            raise ValueError(
                f"Package '{declared}' declares multiInstance but never uses "
                "${{ install.name }}, so a second install would share resources "
                "with the first. Use ${{ install.name }} in namespaces, role "
                "names and permission keys."
            )
    substituted = _INSTALL_NAME_PATTERN.sub(install_name, yaml_content) if install_name else yaml_content
    return install_name, substituted, declared, multi


def _instance_conflicts(warnings: list[str]) -> list[str]:
    """Resources a multi-instance install left alone because another owner
    has them. Each instance must own everything it declares."""
    return [str(w) for w in warnings if isinstance(w, OwnershipSkip)]


def _describe_breaks(breaks: list[dict]) -> str:
    return ", ".join(
        f"'{b['dependent']}' requires {b['requires']}{' ' + b['version'] if b.get('version') else ''}"
        for b in breaks
    )


def _breaks(install_name: str, declared_name: Optional[str], version: Optional[str], installed: list) -> list[dict]:
    """The installed packages a package of this name and version under this
    install name would leave outside their stated range. One check for
    preview and install, on the version as it will be installed (variables
    substituted). A version still waiting for a variable can't be judged
    yet: the preview asks for the value, and the install refuses without it."""
    from app.services.package_dependencies import broken_dependents

    # An empty version is judged like any other: it satisfies no range.
    if not declared_name or version is None or "${{" in version:
        return []
    return broken_dependents(install_name, declared_name, version, installed)


def _declared_meta(yaml_content: str) -> tuple[Optional[str], Optional[str]]:
    """(package.name, package.version) from raw YAML, best effort."""
    try:
        doc = yaml.safe_load(yaml_content)
    except Exception:
        return None, None
    meta = doc.get("package") if isinstance(doc, dict) else None
    if not isinstance(meta, dict):
        return None, None
    name, version = meta.get("name"), meta.get("version", "1.0.0")
    return (name if isinstance(name, str) else None), (str(version) if version is not None else None)


# Kinds a package's resources are referred to under, as pending references
# hold them (see ConfigApplyService.apply_config).
_REFERABLE_KINDS = (
    "functions", "agents", "pipelines", "skills", "queries", "stores", "collections",
    "components", "connectors", "mcpServers",
)


def _declared_resources(config: SinasConfig) -> dict[str, dict[str, bool]]:
    """What a package declares, by kind and "namespace/name" (roles by name),
    with whether it is declared active: what a dependent's dry run may count
    as about to exist."""
    out: dict[str, dict[str, bool]] = {}
    for kind in _REFERABLE_KINDS:
        out[kind] = {
            f"{item.namespace}/{item.name}": getattr(item, "isActive", True) is not False
            for item in getattr(config.spec, kind, None) or []
        }
    out["roles"] = {role.name: True for role in config.spec.roles}
    return out


def _declared_resources_raw(yaml_content: str) -> dict[str, dict[str, bool]]:
    """_declared_resources from the YAML as written, for a package whose
    preview didn't get as far as parsing (it still needs values): its
    dependents' previews then report what is really wrong with them, not
    references to resources it is about to provide."""
    try:
        doc = yaml.safe_load(yaml_content)
    except Exception:
        return {}
    spec = doc.get("spec") if isinstance(doc, dict) else None
    if not isinstance(spec, dict):
        return {}
    out: dict[str, dict[str, bool]] = {}
    for kind in _REFERABLE_KINDS:
        items = spec.get(kind)
        out[kind] = {
            f"{item.get('namespace', 'default')}/{item['name']}": item.get("isActive") is not False
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
    roles = spec.get("roles")
    out["roles"] = {
        r["name"]: True for r in (roles if isinstance(roles, list) else [])
        if isinstance(r, dict) and isinstance(r.get("name"), str)
    }
    return out


class PackageInUse(ValueError):
    """An uninstall refused: other installed packages require this one."""

    def __init__(self, name: str, dependents: list[str]):
        self.dependents = dependents
        super().__init__(
            f"Package '{name}' is required by {', '.join(repr(d) for d in dependents)}; "
            "uninstall those first"
        )


class PackageService:
    """Service for managing installable integration packages."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def install(
        self,
        yaml_content: str,
        user_id: str,
        variables: Optional[dict[str, Any]] = None,
        allow_broad_role_permissions: bool = False,
        instance: Optional[str] = None,
        source: Optional[tuple[str, str]] = None,
        adopt_unmanaged: bool = False,
        dependency_variables: Optional[dict[str, dict[str, Any]]] = None,
        allow_breaking_dependents: bool = False,
    ) -> tuple[Package, ConfigApplyResponse]:
        """
        Install a package from YAML content, with the packages it requires
        (docs/design/package-dependencies.md).

        Args:
            yaml_content: YAML string with kind: SinasPackage
            user_id: ID of the user installing the package
            variables: Install-time variable values (keyed by variable name)
            allow_broad_role_permissions: accept package roles whose granted
                permissions reach outside the package's own namespaces (for
                every package the install installs)
            instance: install name for a multi-instance package (defaults to
                the package name); substituted for ${{ install.name }}
            source: (repository name, file URL) when installed from a package
                repository; recorded in the same commit as the install
            adopt_unmanaged: take over declared resources made or edited by
                hand instead of skipping them (the operator's opt-in; an
                uninstall then deletes them). This package only, not its
                dependencies.
            dependency_variables: variable values for the packages the
                install installs as dependencies, by their install name
            allow_breaking_dependents: upgrade even when an installed package
                requiring this one doesn't accept the new version

        The required packages that aren't installed are installed first, from
        the active package repositories, all in this one transaction: if any
        of them (or this package) fails, nothing is committed. Those are in
        self.installed_dependencies afterwards, as (Package, result) pairs.

        Returns:
            Tuple of (Package record, ConfigApplyResponse)
        """
        from app.services import package_dependencies as deps

        self.installed_dependencies: list[tuple[Package, ConfigApplyResponse]] = []
        dependency_variables = dependency_variables or {}
        # Installs and uninstalls one at a time: the plan below reads what is
        # installed, and must still be true at the commit.
        await deps.lock_packages(self.db)

        install_name, substituted, declared_name, _multi = resolve_install_name(yaml_content, instance)
        # What it requires, from the YAML with its variables. Only that: the
        # package as a whole is validated once what it requires is installed
        # (its references may name their resources).
        try:
            requirements = deps._requirements_of(substituted, variables)
        except Exception as e:
            raise ValueError(f"Package validation failed: package: {e}") from e
        # Name and version as installed: with its variables substituted
        # (version: ${{ vars.VERSION }}), as the preview checks them.
        declared_name, version = _declared_meta(deps._substitute_known(substituted, variables or {}))
        installed = await deps.installed_packages(self.db)
        existing = installed.get(install_name)

        breaks = _breaks(install_name, declared_name, version, list(installed.values()))
        if breaks and not allow_breaking_dependents:
            raise ValueError(
                f"Installing {declared_name} {version} as '{install_name}' "
                f"would break packages that require it: {_describe_breaks(breaks)}. Update those "
                "first, or install with allowBreakingDependents=true."
            )

        plan = await deps.resolve_plan(
            self.db,
            root_name=declared_name or install_name,
            root_install_name=install_name,
            requirements=requirements,
            root_repository=source[0] if source else (existing.source_repository if existing else None),
            dependency_variables=dependency_variables,
        )
        # errors(), not just blocking entries: a resolve that didn't settle
        # reports a problem without any one entry being blocked.
        if plan.errors():
            raise ValueError(
                "Package not installed: its requirements can't be met. " + "; ".join(plan.errors())
            )

        services: list[ConfigApplyService] = []
        for entry in plan.to_install:
            try:
                dep_package, dep_result, dep_service = await self._install_one(
                    entry.yaml,
                    user_id,
                    variables=dependency_variables.get(entry.install_name),
                    allow_broad_role_permissions=allow_broad_role_permissions,
                    instance=entry.instance,
                    source=(entry.repository, entry.file_url),
                    as_dependency=True,
                )
            except ValueError as e:
                raise ValueError(
                    f"Required package '{entry.install_name}' ({entry.package_name} {entry.version} "
                    f"from {entry.repository}) failed to install, so nothing was installed: {e}"
                ) from e
            services.append(dep_service)
            self.installed_dependencies.append((dep_package, dep_result))

        package, result, service = await self._install_one(
            yaml_content,
            user_id,
            variables=variables,
            allow_broad_role_permissions=allow_broad_role_permissions,
            instance=instance,
            source=source,
            adopt_unmanaged=adopt_unmanaged,
        )
        services.append(service)
        if breaks:
            result.warnings.append(
                "Installed although packages requiring it don't accept this version "
                f"(allowBreakingDependents): {_describe_breaks(breaks)}"
            )

        # Single commit for everything: the dependencies and the package.
        await self.db.commit()
        # auto_commit=False means each apply service left its notifications
        # queued for us; without this a package's schedules never reach the
        # running scheduler and its CDC triggers aren't picked up until restart.
        for applied in services:
            await applied.flush_notifications()
        await self.db.refresh(package)
        for dep_package, _ in self.installed_dependencies:
            await self.db.refresh(dep_package)

        return package, result

    async def _install_one(
        self,
        yaml_content: str,
        user_id: str,
        variables: Optional[dict[str, Any]] = None,
        allow_broad_role_permissions: bool = False,
        instance: Optional[str] = None,
        source: Optional[tuple[str, str]] = None,
        adopt_unmanaged: bool = False,
        as_dependency: bool = False,
    ) -> tuple[Package, ConfigApplyResponse, ConfigApplyService]:
        """One package of an install: applied and recorded, not committed."""
        # The install name first: it may appear inside variable defaults too.
        install_name, yaml_content, declared_name, multi = resolve_install_name(yaml_content, instance)

        # Substitute variables before parsing if provided
        yaml_content, resolved_values = await self._resolve_variables(
            yaml_content, variables or {}, user_id
        )

        config, validation = await ConfigParser.parse_and_validate(yaml_content, db=self.db)

        if not validation.is_valid:
            errors = [str(e) for e in validation.errors]
            raise ValueError(f"Package validation failed: {'; '.join(errors)}")

        if config.kind != "SinasPackage":
            raise ValueError("Expected kind: SinasPackage")

        if not config.package:
            raise ValueError("Package metadata is required for SinasPackage")

        declared_name = config.package.name
        pkg_name = install_name or declared_name   # resources and the record carry the install name
        managed_by = f"pkg:{pkg_name}"

        hard, broad, role_advisories = package_role_violations(config)
        if hard:
            raise ValueError("Package roles rejected: " + "; ".join(hard))
        if broad and not allow_broad_role_permissions:
            raise ValueError(
                "Package role permissions reach outside the package's own namespaces: "
                + "; ".join(broad)
                + ". Review them, then re-install with allowBroadRolePermissions=true to accept."
            )

        # Check if already installed — upgrade in place if so
        existing_result = await self.db.execute(
            select(Package).where(Package.name == pkg_name)
        )
        existing_package = existing_result.scalar_one_or_none()
        if existing_package and (existing_package.package_name or existing_package.name) != declared_name:
            raise ValueError(
                f"'{pkg_name}' is already installed from package "
                f"'{existing_package.package_name or existing_package.name}', not '{declared_name}'; "
                "pick another instance name or uninstall it first"
            )

        # Apply config with package managed_by, skip environment-specific types, no auto-commit
        apply_service = ConfigApplyService(
            db=self.db,
            config_name=pkg_name,
            owner_user_id=user_id,
            managed_by=managed_by,
            auto_commit=False,
            skip_resource_types=PACKAGE_SKIP_TYPES,
            # An upgrade removes what the new version no longer ships.
            prune_missing=True,
            adopt_unmanaged=adopt_unmanaged,
        )

        result = await apply_service.apply_config(config, dry_run=False)

        conflicts = _instance_conflicts(apply_service.warnings) if multi else []
        if conflicts:
            # A skipped resource would leave this instance pointing at another
            # owner's copy, so instances share it after all. Refuse instead.
            raise ValueError(
                f"Package not installed as '{pkg_name}': it would share resources "
                f"owned by someone else. {'; '.join(conflicts)}"
            )

        if not result.success:
            # All or nothing: nothing from this package is committed. The
            # caller's transaction rolls back (the API returns 400; the agent
            # tool's session closes without committing).
            raise ValueError(
                f"Package not installed: {len(result.errors)} resource(s) failed, so "
                f"nothing was applied. {'; '.join(result.errors)}"
            )

        # Add validation warnings to result
        result.warnings.extend(validation.warnings)
        if broad:
            result.warnings.append(
                "Accepted broad role permissions (operator consent): " + "; ".join(broad)
            )
        result.warnings.extend(role_advisories)

        from app.services.package_dependencies import stored_requires

        # Create or update package record
        if existing_package:
            existing_package.package_name = declared_name
            existing_package.version = config.package.version
            existing_package.description = config.package.description
            existing_package.author = config.package.author
            existing_package.source_url = config.package.url
            existing_package.source_yaml = yaml_content
            existing_package.values = resolved_values
            existing_package.requires = stored_requires(config.package.requires)
            package = existing_package
        else:
            package = Package(
                name=pkg_name,
                package_name=declared_name,
                version=config.package.version,
                description=config.package.description,
                author=config.package.author,
                source_url=config.package.url,
                source_yaml=yaml_content,
                values=resolved_values,
                requires=stored_requires(config.package.requires),
                installed_as_dependency=as_dependency,
                installed_by=user_id,
            )
            self.db.add(package)
        if source is not None:
            # Where its updates are looked for from now on.
            package.source_repository, package.source_url = source
        # Rows (and the record) visible to what applies next in this
        # transaction: a dependent's references, its plan.
        await self.db.flush()

        return package, result, apply_service

    async def preview(
        self,
        yaml_content: str,
        user_id: str,
        variables: Optional[dict[str, Any]] = None,
        instance: Optional[str] = None,
        adopt_unmanaged: bool = False,
        dependency_variables: Optional[dict[str, dict[str, Any]]] = None,
        allow_breaking_dependents: bool = False,
        repository: Optional[str] = None,
    ) -> tuple[ConfigApplyResponse, list[dict], bool, dict[str, Any]]:
        """
        Preview a package install (dry run), with the packages it requires.

        `repository` is the package repository the package comes from, if
        any: required packages are looked for there after their own hint.

        Returns:
            Tuple of (ConfigApplyResponse, variable_declarations, requires_input, install_info)
            where install_info is {"instance", "package_name", "multi_instance",
            "dependencies", "breaks"}: the dependency plan (each entry to
            install with its own variables and preview) and the installed
            packages an upgrade would break.
        """
        from app.services import package_dependencies as deps

        dependency_variables = dependency_variables or {}
        install_name, substituted, _declared, _multi = resolve_install_name(yaml_content, instance)

        # The plan first: the root's dry run needs what its dependencies
        # would create. Read from the YAML with the values given, best
        # effort: an invalid package reports itself in its own preview.
        try:
            requirements = deps._requirements_of(substituted, variables)
        except Exception:
            requirements = []
        installed = await deps.installed_packages(self.db)
        existing = installed.get(install_name)
        # Name and version with its variables substituted, as the install
        # reads them (version: ${{ vars.VERSION }}).
        root_meta = _declared_meta(deps._substitute_known(substituted, variables or {}))
        plan = await deps.resolve_plan(
            self.db,
            root_name=root_meta[0] or install_name,
            root_install_name=install_name,
            requirements=requirements,
            root_repository=repository or (existing.source_repository if existing else None),
            dependency_variables=dependency_variables,
        )

        provided: dict[str, dict[str, bool]] = {}
        planned: list[dict[str, Any]] = []
        failed_dependencies: list[str] = []
        for entry in plan.entries:
            item = entry.as_dict()
            if entry.status == deps.INSTALL:
                dep_result, dep_vars, dep_needs, _info, dep_config = await self._preview_one(
                    entry.yaml, user_id, variables=dependency_variables.get(entry.install_name),
                    instance=entry.instance, extra_pending=provided,
                )
                declared = (
                    _declared_resources(dep_config) if dep_config is not None
                    else _declared_resources_raw(resolve_install_name(entry.yaml, entry.instance)[1])
                )
                for kind, keys in declared.items():
                    provided.setdefault(kind, {}).update(keys)
                if not dep_result.success or dep_result.errors:
                    failed_dependencies.append(entry.install_name)
                item.update({
                    "variables": dep_vars,
                    "requires_input": dep_needs,
                    "preview": dep_result.model_dump(mode="json"),
                })
            elif entry.status == deps.INVALID and entry.yaml:
                # Its variables may be what made it invalid (a value that
                # isn't a usable instance name): list them, so the values
                # can be corrected and previewed again.
                declarations = self._extract_variable_declarations(entry.yaml)
                item.update({"variables": declarations, "requires_input": bool(declarations)})
            planned.append(item)

        result, variable_declarations, requires_input, install_info, config = await self._preview_one(
            yaml_content, user_id, variables=variables, instance=instance,
            adopt_unmanaged=adopt_unmanaged, extra_pending=provided,
        )

        version = config.package.version if config is not None else root_meta[1]
        breaks = _breaks(install_name, root_meta[0], version, list(installed.values()))
        install_info["dependencies"] = planned
        install_info["breaks"] = breaks

        for message in plan.errors():
            result.errors.append(message)
        for name in failed_dependencies:
            result.errors.append(
                f"Required package '{name}' can't be installed as previewed: see its preview "
                "(it may need values for its variables)"
            )
        if breaks:
            message = (
                f"Would break packages that require '{install_name}': {_describe_breaks(breaks)}"
            )
            if allow_breaking_dependents:
                result.warnings.append(message + " (allowBreakingDependents)")
            else:
                result.errors.append(message + ". Update those first, or install with allowBreakingDependents=true.")
        if result.errors:
            result.success = False
        return result, variable_declarations, requires_input, install_info

    async def _preview_one(
        self,
        yaml_content: str,
        user_id: str,
        variables: Optional[dict[str, Any]] = None,
        instance: Optional[str] = None,
        adopt_unmanaged: bool = False,
        extra_pending: Optional[dict[str, dict[str, bool]]] = None,
    ) -> tuple[ConfigApplyResponse, list[dict], bool, dict[str, Any], Optional[SinasConfig]]:
        """One package's dry run. `extra_pending`: what the packages planned
        before it declare (see ConfigApplyService.extra_pending_references).
        The last element is the parsed package, or None when it didn't get
        that far (it needs values, or doesn't validate)."""
        install_name, yaml_content, declared_name, multi = resolve_install_name(yaml_content, instance)
        install_info = {"instance": install_name, "package_name": declared_name, "multi_instance": multi}

        # Parse first to extract variable declarations (before substitution)
        variable_declarations = self._extract_variable_declarations(yaml_content)
        requires_input = any(v.get("required", True) and v.get("default") is None for v in variable_declarations)

        def needs_input(message: str):
            """A package with variables that doesn't preview with the values
            given (none yet, a missing one, a value that doesn't validate):
            the declarations and the error, so the caller can ask for values
            and preview again, instead of a bare failure."""
            failed = ConfigApplyResponse(
                success=False, summary=ConfigApplySummary(), changes=[], errors=[message]
            )
            return failed, variable_declarations, True, install_info, None

        # Resolve variables whenever the package declares any, with the values
        # given or none: the same rules as the install (defaults, an existing
        # secret reused, a required value missing), so a successful preview
        # means the install with these values gets past them too.
        if variables or variable_declarations:
            # Preview is a dry run: validate + substitute, persist nothing.
            try:
                yaml_content, _ = await self._resolve_variables(
                    yaml_content, variables or {}, user_id, persist_secrets=False
                )
            except ValueError as e:
                if not variable_declarations:
                    raise
                return needs_input(str(e))

        # A reference (or manifest requirement) may name what the packages
        # planned before this one declare: the dry run doesn't create them.
        config, validation = await ConfigParser.parse_and_validate(
            yaml_content, db=self.db, extra_names=extra_pending
        )

        if not validation.is_valid:
            errors = [str(e) for e in validation.errors]
            message = f"Package validation failed: {'; '.join(errors)}"
            if variable_declarations:
                return needs_input(message)
            raise ValueError(message)

        if config.kind != "SinasPackage":
            raise ValueError("Expected kind: SinasPackage")

        if not config.package:
            raise ValueError("Package metadata is required for SinasPackage")

        pkg_name = install_name or config.package.name
        managed_by = f"pkg:{pkg_name}"

        apply_service = ConfigApplyService(
            db=self.db,
            config_name=pkg_name,
            owner_user_id=user_id,
            managed_by=managed_by,
            auto_commit=False,
            skip_resource_types=PACKAGE_SKIP_TYPES,
            # An upgrade removes what the new version no longer ships.
            prune_missing=True,
            # The real install saves these before applying; the preview doesn't.
            supplied_secrets={
                v["name"] for v in variable_declarations
                if v.get("type") == "secret" and (variables or {}).get(v["name"]) is not None
            },
            adopt_unmanaged=adopt_unmanaged,
            extra_pending_references=extra_pending,
        )

        result = await apply_service.apply_config(config, dry_run=True)
        if multi:
            conflicts = _instance_conflicts(apply_service.warnings)
            if conflicts:
                result.errors.extend(conflicts)
                result.warnings = [w for w in result.warnings if w not in conflicts]
                result.success = False
        result.warnings.extend(validation.warnings)
        if multi and install_name == config.package.name:
            result.warnings.append(
                "This package supports multiple instances: pass `instance` to install "
                "it under another name next to this one."
            )

        hard, broad, role_advisories = package_role_violations(config)
        for violation in hard:
            result.errors.append(f"Package roles rejected: {violation}")
        for perm in broad:
            result.warnings.append(
                "Broad role permission (install requires allowBroadRolePermissions=true): "
                + perm
            )
        result.warnings.extend(role_advisories)

        return result, variable_declarations, requires_input, install_info, config

    async def uninstall(self, package_name: str, actor_user_id: Optional[str] = None) -> dict:
        """
        Uninstall a package: delete all resources with matching managed_by and the package record.

        Returns dict with deleted counts per resource type.
        """
        managed_by = f"pkg:{package_name}"

        # Check package exists
        result = await self.db.execute(
            select(Package).where(Package.name == package_name)
        )
        package = result.scalar_one_or_none()
        if not package:
            raise ValueError(f"Package '{package_name}' is not installed")

        # Not while installed packages require it: their references to its
        # resources would dangle. Locked against an install planning on it.
        from app.services import package_dependencies as deps

        await deps.lock_packages(self.db)
        dependents = deps.dependents_of(
            package_name, list((await deps.installed_packages(self.db)).values())
        )
        if dependents:
            raise PackageInUse(package_name, sorted(d.name for d in dependents))

        deleted_counts = {}

        # Kinds with an applier go through it rather than a bulk delete: each
        # deletion is recorded in the change history (so it can be restored),
        # and running workers are told — a bulk delete left the scheduler
        # firing jobs, and CDC polling tables, for rows already gone.
        from app.services.resources import ApplyContext
        from app.services.resources.registry import all_appliers

        applier_ctx = ApplyContext(
            db=self.db,
            origin="package",
            actor_user_id=actor_user_id,
            managed_by=managed_by,
            config_name=package_name,
        )
        for applier in all_appliers():
            if not applier.deleted_with_package:
                continue
            # Locked, and the ownership filter re-checked on the locked row: a
            # concurrent manual edit detaches a row (managed_by = NULL), and
            # must not be deleted after its save succeeded.
            rows = (
                await self.db.execute(
                    select(applier.model)
                    .where(applier.model.managed_by == managed_by)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalars().all()
            for row in rows:
                await applier.delete(row, applier_ctx)
            if rows:
                deleted_counts[applier.kind] = len(rows)

        # Package-managed roles went with the appliers above, user and API-key
        # assignments with them — deliberate: an uninstalled package's
        # authority should not linger anywhere.

        # Delete package record
        await self.db.delete(package)
        await self.db.commit()
        await applier_ctx.effects.flush()

        return deleted_counts

    async def list_packages(self) -> list[Package]:
        """List all installed packages."""
        result = await self.db.execute(
            select(Package).order_by(Package.installed_at.desc())
        )
        return list(result.scalars().all())

    async def get_package(self, name: str) -> Optional[Package]:
        """Get a package by name."""
        result = await self.db.execute(
            select(Package).where(Package.name == name)
        )
        return result.scalar_one_or_none()

    async def export_package(self, name: str) -> str:
        """Export a package's original YAML."""
        package = await self.get_package(name)
        if not package:
            raise ValueError(f"Package '{name}' not found")
        return package.source_yaml

    async def create_from_resources(
        self,
        name: str,
        version: str,
        resources: list[dict],
        description: Optional[str] = None,
        author: Optional[str] = None,
        url: Optional[str] = None,
    ) -> str:
        """
        Create a SinasPackage YAML from selected resources.

        Args:
            name: Package name
            version: Package version
            resources: List of {"type": str, "namespace": str, "name": str}
            description: Package description
            author: Package author
            url: Source URL

        Returns:
            YAML string for the SinasPackage
        """
        spec = {}

        # Type -> (model, export_fn)
        type_handlers = {
            "agent": (Agent, self._export_agent),
            "connector": (Connector, self._export_connector),
            "mcp_server": (McpServer, self._export_mcp_server),
            "function": (Function, self._export_function),
            "skill": (Skill, self._export_skill),
            "manifest": (Manifest, self._export_manifest),
            "component": (Component, self._export_component),
            "query": (Query, self._export_query),
            "collection": (Collection, self._export_collection),
            "template": (Template, self._export_template),
            "webhook": (Webhook, self._export_webhook),
            "schedule": (ScheduledJob, self._export_schedule),
            "store": (Store, self._export_store),
            "database_trigger": (DatabaseTrigger, self._export_database_trigger),
        }

        for ref in resources:
            res_type = ref["type"]
            res_namespace = ref.get("namespace", "default")
            res_name = ref["name"]

            handler = type_handlers.get(res_type)
            if not handler:
                continue

            model_cls, export_fn = handler
            resource = await self._get_resource(model_cls, res_namespace, res_name)
            if not resource:
                continue

            spec_key = self._type_to_spec_key(res_type)
            if spec_key not in spec:
                spec[spec_key] = []

            exported = await export_fn(resource)
            spec[spec_key].append(exported)

        config_dict = {
            "apiVersion": "sinas.co/v1",
            "kind": "SinasPackage",
            "package": _remove_none_values({
                "name": name,
                "version": version,
                "description": description,
                "author": author,
                "url": url,
            }),
            "spec": spec,
        }

        return yaml.dump(config_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def _type_to_spec_key(self, res_type: str) -> str:
        mapping = {
            "agent": "agents",
            "connector": "connectors",
            "mcp_server": "mcpServers",
            "function": "functions",
            "skill": "skills",
            "manifest": "manifests",
            "component": "components",
            "query": "queries",
            "collection": "collections",
            "template": "templates",
            "webhook": "webhooks",
            "schedule": "schedules",
            "store": "stores",
            "database_trigger": "databaseTriggers",
        }
        return mapping.get(res_type, res_type + "s")

    async def _get_resource(self, model_cls, namespace: str, name: str):
        """Get a resource by namespace/name or just name."""
        if hasattr(model_cls, "namespace"):
            stmt = select(model_cls).where(
                model_cls.namespace == namespace,
                model_cls.name == name,
            )
        elif hasattr(model_cls, "path"):
            # Webhook uses path instead of name
            stmt = select(model_cls).where(model_cls.path == name)
        else:
            stmt = select(model_cls).where(model_cls.name == name)

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _export_agent(self, agent: Agent) -> dict:
        from app.models.llm_provider import LLMProvider
        provider_name = None
        if agent.llm_provider_id:
            result = await self.db.execute(
                select(LLMProvider).where(LLMProvider.id == agent.llm_provider_id)
            )
            provider = result.scalar_one_or_none()
            if provider:
                provider_name = provider.name
        return serialize_agent(agent, provider_name)

    async def _export_function(self, func: Function) -> dict:
        return serialize_function(func)

    async def _export_skill(self, skill: Skill) -> dict:
        return serialize_skill(skill)

    async def _export_manifest(self, manifest: Manifest) -> dict:
        return serialize_manifest(manifest)

    async def _export_component(self, component: Component) -> dict:
        return serialize_component(component)

    async def _export_query(self, query: Query) -> dict:
        from app.services.resources.queries import connection_name

        return serialize_query(query, await connection_name(self.db, query.database_connection_id))

    async def _export_collection(self, collection: Collection) -> dict:
        return serialize_collection(collection)

    async def _export_store(self, store: Store) -> dict:
        return serialize_store(store)

    async def _export_template(self, template: Template) -> dict:
        return serialize_template(template)

    async def _export_webhook(self, webhook: Webhook) -> dict:
        return serialize_webhook(webhook)

    async def _export_schedule(self, schedule: ScheduledJob) -> dict:
        return serialize_schedule(schedule)

    async def _export_database_trigger(self, trigger: DatabaseTrigger) -> dict:
        from app.models.database_connection import DatabaseConnection
        conn_name = None
        if trigger.database_connection_id:
            result = await self.db.execute(
                select(DatabaseConnection).where(
                    DatabaseConnection.id == trigger.database_connection_id
                )
            )
            conn = result.scalar_one_or_none()
            if conn:
                conn_name = conn.name
        return serialize_database_trigger(trigger, conn_name)

    async def _export_connector(self, conn: Connector) -> dict:
        return serialize_connector(conn)

    async def _export_mcp_server(self, server: McpServer) -> dict:
        return serialize_mcp_server(server)

    # ── Variable substitution ──────────────────────────────

    _VAR_PATTERN = r'\$\{\{\s*vars\.([A-Z][A-Z0-9_]*)\s*\}\}'

    def _extract_variable_declarations(self, yaml_content: str) -> list[dict]:
        """Extract spec.variables from raw YAML without full parsing."""
        import yaml as pyyaml
        try:
            doc = pyyaml.safe_load(yaml_content)
        except Exception:
            return []
        if not isinstance(doc, dict):
            return []
        spec = doc.get("spec", {})
        if not isinstance(spec, dict):
            return []
        variables = spec.get("variables", [])
        if not isinstance(variables, list):
            return []
        return variables

    async def _resolve_variables(
        self,
        yaml_content: str,
        provided: dict[str, Any],
        user_id: str,
        persist_secrets: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        """Validate and substitute install-time variables.

        persist_secrets=False (preview) validates secret variables but writes
        nothing — a dry run must never persist secrets.

        Returns (substituted_yaml, stored_values).
        stored_values is what gets persisted in Package.values
        (secret values are masked).
        """
        import re

        declarations = self._extract_variable_declarations(yaml_content)
        if not declarations:
            return yaml_content, {}

        decl_by_name = {v["name"]: v for v in declarations}
        stored_values: dict[str, Any] = {}
        resolved: dict[str, str] = {}  # name -> substitution string

        # Check for existing package values (for upgrade pre-fill)
        for decl in declarations:
            name = decl["name"]
            var_type = decl.get("type", "text")
            required = decl.get("required", True)
            default = decl.get("default")

            # Get value: provided > default
            value = provided.get(name)
            if value is None:
                value = default

            # For secret type: check if secret already exists in DB
            if var_type == "secret" and value is None:
                from app.models.secret import Secret
                # The shared one: that is what the package fills in, and
                # what its resources resolve. (A name-only lookup matched
                # other users' private secrets too, and raised once two of
                # them shared the name.)
                existing_secret = await self.db.execute(
                    select(Secret.id).where(Secret.name == name, Secret.visibility == "shared")
                )
                if existing_secret.first() is not None:
                    # Secret already exists, skip — don't require re-entry.
                    # Same reference as a freshly written one (this produced
                    # the literal `{{{{name}}}}`, naming no secret at all).
                    resolved[name] = f"{{{{{name}}}}}"
                    stored_values[name] = "***"
                    continue

            if value is None and required:
                raise ValueError(f"Required variable '{name}' not provided")
            if value is None:
                continue

            # Type validation
            if var_type == "text":
                value = str(value)
                pattern = decl.get("pattern")
                if pattern and not re.match(pattern, value):
                    raise ValueError(f"Variable '{name}' does not match pattern '{pattern}'")

            elif var_type == "boolean":
                if isinstance(value, str):
                    value = value.lower() in ("true", "1", "yes")
                value = bool(value)

            elif var_type == "enum":
                choices = decl.get("choices", [])
                if str(value) not in choices:
                    raise ValueError(f"Variable '{name}' must be one of: {choices}")
                value = str(value)

            elif var_type == "resource_ref":
                resource_type = decl.get("resource")
                if resource_type:
                    exists = await self._validate_resource_ref(resource_type, str(value))
                    if not exists:
                        raise ValueError(
                            f"Variable '{name}': {resource_type} '{value}' not found"
                        )
                value = str(value)

            elif var_type == "secret":
                if persist_secrets:
                    # Upsert the SHARED secret. Scoped to visibility="shared":
                    # a name-only lookup could silently overwrite another
                    # user's PRIVATE secret of the same name (same bug class
                    # the config-apply secrets path fixed).
                    # Through the applier, as the installing user's own entry
                    # (recorded, value redacted). It stays theirs: the
                    # package may fill it in but never deletes it.
                    from app.services.resources import ApplyContext
                    from app.services.resources.secrets import SecretApplier

                    applier = SecretApplier()
                    ctx = ApplyContext(
                        db=self.db, origin="api",
                        actor_user_id=str(user_id), owner_user_id=str(user_id),
                    )
                    secret = await applier.find(ctx, name)
                    if secret is None:
                        spec = applier.spec_model.model_validate({
                            "name": name, "value": str(value),
                            "description": decl.get("description"),
                        })
                    else:
                        spec = applier.spec_from_row(secret).model_copy(update={"value": str(value)})
                    await applier.apply(spec, ctx, existing=secret)
                # The substitution value is the secret reference syntax
                resolved[name] = f"{{{{{name}}}}}"
                stored_values[name] = "***"
                continue

            resolved[name] = str(value)
            stored_values[name] = value if var_type != "secret" else "***"

        # Perform substitution on raw YAML
        def replace_var(m):
            var_name = m.group(1)
            if var_name in resolved:
                return resolved[var_name]
            return m.group(0)  # leave unresolved vars as-is

        substituted = re.sub(self._VAR_PATTERN, replace_var, yaml_content)
        return substituted, stored_values

    def _is_uuid(self, value: str) -> bool:
        """Check if a string is a valid UUID."""
        import uuid as uuid_mod
        try:
            uuid_mod.UUID(value)
            return True
        except ValueError:
            return False

    async def _validate_resource_ref(self, resource_type: str, value: str) -> bool:
        """Check that a resource_ref variable points to an existing resource."""
        if resource_type == "llm_providers":
            from app.models.llm_provider import LLMProvider
            query = select(LLMProvider).where(LLMProvider.is_active == True)
            if self._is_uuid(value):
                query = query.where((LLMProvider.name == value) | (LLMProvider.id == value))
            else:
                query = query.where(LLMProvider.name == value)
            result = await self.db.execute(query)
            return result.scalar_one_or_none() is not None

        elif resource_type == "database_connections":
            from app.models.database_connection import DatabaseConnection
            query = select(DatabaseConnection).where(DatabaseConnection.is_active == True)
            if self._is_uuid(value):
                query = query.where((DatabaseConnection.name == value) | (DatabaseConnection.id == value))
            else:
                query = query.where(DatabaseConnection.name == value)
            result = await self.db.execute(query)
            return result.scalar_one_or_none() is not None

        elif resource_type == "collections":
            if "/" in value:
                ns, name = value.split("/", 1)
                coll = await Collection.get_by_name(self.db, ns, name)
                return coll is not None
            return False

        elif resource_type == "secrets":
            from app.core.secret_refs import secret_ref_name
            from app.models.secret import Secret
            # The value is a secret reference (`NAME` or `{{ NAME }}`). Any
            # secret by that name counts, as before; several (a shared one and
            # private ones) must not raise MultipleResultsFound.
            result = await self.db.execute(
                select(Secret.id).where(Secret.name == secret_ref_name(value)).limit(1)
            )
            return result.first() is not None

        elif resource_type == "roles":
            from app.models.user import Role
            result = await self.db.execute(
                select(Role).where(Role.name == value)
            )
            return result.scalar_one_or_none() is not None

        return False
