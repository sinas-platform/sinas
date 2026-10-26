"""Package dependencies: a package that needs another package
(docs/design/package-dependencies.md).

A package declares `package.requires` — other packages by name, with an
optional version range, repository hint and install name. This module holds
the pure parts (version ranges) and the resolver that turns a package's
requirements into a plan: what is already installed and fits, what an active
package repository can supply, and what can't be met. PackageService acts on
the plan (preview, install, uninstall, upgrade checks).

Not the `dependencies` resource kind, which approves Python packages for
function containers.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.package import Package

# ---------------------------------------------------------------------------
# Version ranges
# ---------------------------------------------------------------------------

_CLAUSE = re.compile(r"^\s*(~=|==|!=|>=|<=|>|<)\s*(v?[0-9][0-9A-Za-z.+-]*?)(\.\*)?\s*$")


@dataclass(frozen=True)
class Clause:
    op: str
    version: str
    wildcard: bool = False  # "==1.2.*" / "!=1.2.*"


def _key(version: str):
    from app.services.package_repositories import _version_key

    return _version_key(version)


def _release(version: str) -> tuple[int, ...]:
    """The release numbers as written (not padded): "1.2" -> (1, 2)."""
    version = version.strip().lstrip("v").split("+", 1)[0]
    parts = []
    for part in version.split("."):
        digits = re.match(r"^\d+", part)
        if not digits:
            break
        parts.append(int(digits.group(0)))
        if digits.group(0) != part:
            break  # "0-rc" ends the release part
    return tuple(parts)


def parse_range(text: str) -> list[Clause]:
    """A pyproject-style range: comma-separated clauses, each an operator
    (>=, >, <=, <, ==, !=, ~=) and a version; == and != also take a trailing
    .* ("==1.2.*"). Raises ValueError saying what's wrong."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a version range can't be empty")
    clauses = []
    for raw in text.split(","):
        match = _CLAUSE.match(raw)
        if not match:
            raise ValueError(
                f"'{raw.strip()}' in version range '{text}' is not a clause like '>=1.2' "
                "(operators: >=, >, <=, <, ==, !=, ~=)"
            )
        op, version, star = match.group(1), match.group(2), bool(match.group(3))
        if star and op not in ("==", "!="):
            raise ValueError(f"'{raw.strip()}': only == and != take a .* wildcard")
        if _key(version) is None:
            raise ValueError(f"'{version}' in version range '{text}' is not a version")
        if op == "~=" and len(_release(version)) < 2:
            raise ValueError(f"'{raw.strip()}': ~= needs at least two version parts (~=1.2)")
        clauses.append(Clause(op, version, star))
    return clauses


def _prefix_match(version: str, prefix: str) -> bool:
    want = _release(prefix)
    have = _release(version) + (0,) * len(want)
    return have[: len(want)] == want


def _clause_holds(version: str, clause: Clause) -> bool:
    have, want = _key(version), _key(clause.version)
    if clause.op == "==":
        return _prefix_match(version, clause.version) if clause.wildcard else have == want
    if clause.op == "!=":
        return not (_prefix_match(version, clause.version) if clause.wildcard else have == want)
    if clause.op == ">=":
        return have >= want
    if clause.op == ">":
        return have > want
    if clause.op == "<=":
        return have <= want
    if clause.op == "<":
        return have < want
    # ~=1.4.2 is >=1.4.2 and ==1.4.*
    release = _release(clause.version)
    return have >= want and _prefix_match(version, ".".join(map(str, release[:-1])))


def satisfies(version: str | None, range_text: str | None) -> bool:
    """Whether `version` is in `range_text`. No range: any version. A version
    that isn't semver-style never satisfies a range. A pre-release satisfies
    a range only when one of its clauses names a pre-release (as in PEP 440,
    so "<2" doesn't admit 2.0.0-rc.1)."""
    if not range_text:
        return True
    if version is None or _key(version) is None:
        return False
    clauses = parse_range(range_text)
    is_pre = _key(version)[1] != (1,)
    if is_pre and not any(_key(c.version)[1] != (1,) for c in clauses):
        return False
    return all(_clause_holds(version, c) for c in clauses)


# ---------------------------------------------------------------------------
# Requirements as stored on a package record
# ---------------------------------------------------------------------------


def stored_requires(requirements: list[Any]) -> list[dict[str, Any]]:
    """PackageRequirement models as Package.requires keeps them."""
    return [
        {
            "name": r.name,
            "version": r.version,
            "repository": r.repository,
            "instance": r.instance,
            "install_name": r.install_name,
        }
        for r in requirements
    ]


def _target(entry: dict[str, Any]) -> str:
    return entry.get("install_name") or entry.get("instance") or entry.get("name")


def requirement_status(entry: dict[str, Any], installed: dict[str, Package]) -> dict[str, Any]:
    """A requirement (as stored, or as an index lists it) against what is
    installed now: status satisfied, missing or incompatible, with the
    installed version and package, and for incompatible the reason:
    "version" (out of range) or "other_package" (the install name holds a
    different package)."""
    target = _target(entry)
    package = installed.get(target)
    out = {
        **entry, "install_name": target, "installed_version": None,
        "installed_package": None, "reason": None,
    }
    if package is None:
        return {**out, "status": "missing"}
    out["installed_version"] = package.version
    out["installed_package"] = package.package_name or package.name
    if out["installed_package"] != entry.get("name"):
        return {**out, "status": "incompatible", "reason": "other_package"}
    try:
        in_range = satisfies(package.version, entry.get("version"))
    except ValueError:
        in_range = False  # an unparseable range (an index is read leniently)
    if not in_range:
        return {**out, "status": "incompatible", "reason": "version"}
    return {**out, "status": "satisfied"}


def dependents_of(install_name: str, packages: list[Package]) -> list[Package]:
    """Installed packages with a requirement targeting `install_name`."""
    return [
        p for p in packages
        if p.name != install_name and any(_target(e) == install_name for e in (p.requires or []))
    ]


def dependency_status(shown: list[Package], installed: list[Package]) -> dict[Any, dict[str, Any]]:
    """Per shown package: its requirements with their status now, the
    installs requiring it, and whether it is an orphan (installed as a
    dependency, required by nothing any more)."""
    by_name = {p.name: p for p in installed}
    out = {}
    for package in shown:
        required_by = sorted(d.name for d in dependents_of(package.name, installed))
        out[package.id] = {
            "requires": [requirement_status(e, by_name) for e in (package.requires or [])],
            "required_by": required_by,
            "installed_as_dependency": bool(package.installed_as_dependency),
            "orphaned": bool(package.installed_as_dependency) and not required_by,
        }
    return out


def broken_dependents(
    install_name: str, package_name: str, version: str, packages: list[Package]
) -> list[dict[str, Any]]:
    """The installed packages whose requirement on `install_name` the given
    package and version would no longer meet (an upgrade, or another package
    put under that name)."""
    breaks = []
    for dependent in dependents_of(install_name, packages):
        for entry in dependent.requires or []:
            if _target(entry) != install_name:
                continue
            if entry.get("name") != package_name or not satisfies(version, entry.get("version")):
                breaks.append({
                    "dependent": dependent.name,
                    "requires": entry.get("name"),
                    "version": entry.get("version"),
                })
    return breaks


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

SATISFIED = "satisfied"
INSTALL = "install"
MISSING = "missing"
INCOMPATIBLE = "incompatible"
CYCLE = "cycle"
INVALID = "invalid"
BLOCKING = (MISSING, INCOMPATIBLE, CYCLE, INVALID)

MAX_DEPTH = 10
# Rounds of the constraint fixpoint (see resolve_plan); a graph that hasn't
# settled by then is refused rather than resolved forever.
MAX_ROUNDS = 50


@dataclass
class PlanEntry:
    """One required install: what it is, where it comes from, and whether it
    can be had."""

    install_name: str
    package_name: str
    status: str
    constraints: list[dict[str, Any]] = field(default_factory=list)  # [{required_by, version}]
    version: str | None = None            # installed, or the one that will be
    installed_version: str | None = None
    repository: str | None = None         # where an INSTALL entry comes from
    message: str | None = None
    # For INSTALL entries: the fetched (checksum-verified) YAML and its URL.
    yaml: str | None = field(default=None, repr=False)
    file_url: str | None = None
    requires: list[str] = field(default_factory=list)  # install names it requires
    # An INSTALL entry whose own requirements depend on its variables, which
    # haven't been given yet: resolved on a preview with its values.
    needs_input: bool = False

    @property
    def required_by(self) -> list[str]:
        return sorted({c["required_by"] for c in self.constraints})

    @property
    def instance(self) -> str | None:
        """The `instance` to install it under (None: its own name)."""
        return None if self.install_name == self.package_name else self.install_name

    def as_dict(self) -> dict[str, Any]:
        return {
            "install_name": self.install_name,
            "package_name": self.package_name,
            "status": self.status,
            "version": self.version,
            "installed_version": self.installed_version,
            "repository": self.repository,
            "constraints": self.constraints,
            "required_by": self.required_by,
            "requires": self.requires,
            "needs_input": self.needs_input,
            "message": self.message,
        }


@dataclass
class Plan:
    """Dependencies first, in install order (a dependency before whatever
    requires it). The root package itself is not in it."""

    entries: list[PlanEntry] = field(default_factory=list)
    # Problems not tied to one entry (the fixpoint didn't settle).
    problems: list[str] = field(default_factory=list)

    @property
    def to_install(self) -> list[PlanEntry]:
        return [e for e in self.entries if e.status == INSTALL]

    @property
    def blocking(self) -> list[PlanEntry]:
        """What refuses an install: an unmet requirement, or a package to
        install whose own requirements still wait for its values."""
        return [e for e in self.entries if e.status in BLOCKING or e.needs_input]

    def errors(self) -> list[str]:
        return self.problems + [f"Requirement '{e.install_name}': {e.message}" for e in self.blocking]


def _requirements_of(yaml_content: str, values: dict[str, Any] | None = None) -> list[Any]:
    """package.requires of a package YAML, validated. Variable values given
    (and declared defaults) are substituted first, so a requirement may name
    its instance through a variable. YAML that doesn't parse, or has no
    package metadata, requires nothing here: the package's own validation
    reports it."""
    requirements, _missing = _nested_requirements(yaml_content, values)
    return requirements


def _nested_requirements(yaml_content: str, values: dict[str, Any] | None = None) -> tuple[list[Any], list[str]]:
    """(package.requires, variables it still waits for). When a requirement
    names a variable that has no value yet (given, or a declared default),
    the requirements can't be read until it has one: ([], those names), not
    an error. Raises ValueError (one `path: message` per problem, as the
    package validation words them) only for requirements that are invalid
    with every value they use filled in."""
    from pydantic import ValidationError

    from app.schemas.config import PackageMetadataConfig
    from app.services.config_schema_errors import schema_errors

    text = _substitute_known(yaml_content, values or {})
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return [], []
    meta = doc.get("package") if isinstance(doc, dict) else None
    if not isinstance(meta, dict) or not meta.get("requires"):
        return [], []
    waiting = sorted(set(_VAR.findall(json.dumps(meta.get("requires"), default=str))))
    if waiting:
        return [], waiting
    # Only what planning needs: the requirements, and the name for the
    # can't-require-itself check. Anything else in `package` (an unknown
    # key, `version: 1.0` read as a number) is the package validation's to
    # report, in its own words, once the plan is made.
    name = meta.get("name")
    planning = {"name": name if isinstance(name, str) else "", "requires": meta["requires"]}
    try:
        return PackageMetadataConfig.model_validate(planning).requires, []
    except ValidationError as e:
        problems = schema_errors(e, {"package": planning}, prefix=("package",))
        raise ValueError("; ".join(f"{path}: {message}" for path, message in problems)) from None


_VAR = re.compile(r"\$\{\{\s*vars\.([A-Z][A-Z0-9_]*)\s*\}\}")


def _substitute_known(yaml_content: str, values: dict[str, Any]) -> str:
    try:
        doc = yaml.safe_load(yaml_content)
    except Exception:
        return yaml_content
    declared = {}
    spec = doc.get("spec") if isinstance(doc, dict) else None
    declarations = spec.get("variables") if isinstance(spec, dict) else None
    for decl in declarations if isinstance(declarations, list) else []:
        if isinstance(decl, dict) and isinstance(decl.get("name"), str):
            declared[decl["name"]] = decl.get("default")

    def sub(m):
        name = m.group(1)
        value = values.get(name, declared.get(name))
        return m.group(0) if value is None else str(value)

    return _VAR.sub(sub, yaml_content)


async def installed_packages(db: AsyncSession) -> dict[str, Package]:
    return {p.name: p for p in (await db.execute(select(Package))).scalars()}


@dataclass(frozen=True)
class _Constraint:
    """One package's requirement on an install name."""

    required_by: str
    name: str
    version: str | None
    repository: str | None       # the requirement's hint
    dependent_repo: str | None   # where the requiring package comes from


def _describe(constraints: list[_Constraint]) -> str:
    return ", ".join(
        f"'{c.required_by}' requires {c.name}{' ' + c.version if c.version else ''}" for c in constraints
    )


def _version_order(version: str):
    # Versions that aren't semver-style sort below every one that is.
    return _key(version) or ((), ())


def _fits(version: str, constraints: list[_Constraint]) -> bool:
    return all(satisfies(version, c.version) for c in constraints)


async def resolve_plan(
    db: AsyncSession,
    *,
    root_name: str,
    root_install_name: str,
    requirements: list[Any],
    root_repository: str | None = None,
    dependency_variables: dict[str, dict[str, Any]] | None = None,
) -> Plan:
    """Resolve the root package's requirements against what is installed and
    what the active package repositories offer, recursively.

    Every constraint on an install name, from anywhere in the graph, is
    collected before a version is chosen for it (a fixpoint: choosing a
    package to install adds its own requirements, which may constrain others,
    which are then chosen again), so the result doesn't depend on the order
    requirements are listed in or met:

    - An installed package must meet all of them, or it is `incompatible`
      (never upgraded automatically), listing the ones it doesn't meet.
    - A package to install comes from the most preferred active repository
      whose version meets all of them. Preference: a repository some
      requirement names as its hint, then one a requiring package comes from,
      then the rest; ties by repository name. Each repository's index lists
      one version per package, so this picks among repositories, not among
      versions of one repository.
    - Limit: the choice is made per install name. There is no backtracking
      over alternative versions that would change which packages are
      required at all (a different version of A requiring a different C);
      the first consistent choice per name is kept.

    A satisfied (installed) requirement is not resolved further, but its
    stored requirements are followed to find cycles through installed
    packages. Cycles, unmet requirements and packages that can't be had are
    entries with a blocking status, never raised: the preview shows them all.
    """
    from app.services import package_repositories as repos
    from app.services.package_service import resolve_install_name

    installed = await installed_packages(db)
    repositories = await repos._repositories(db)
    dependency_variables = dependency_variables or {}

    constraints: dict[str, dict[str, _Constraint]] = {}
    imposed: dict[str, list[str]] = {}   # what a package to install requires, by install name
    decided: dict[str, PlanEntry] = {}
    signatures: dict[str, tuple] = {}
    fetched: dict[tuple[str, str], Any] = {}

    def add(required_by: str, reqs: list[Any], dependent_repo: str | None) -> None:
        for r in reqs:
            constraints.setdefault(r.install_name, {})[required_by] = _Constraint(
                required_by, r.name, r.version, r.repository, dependent_repo
            )
        imposed[required_by] = [r.install_name for r in reqs]

    def withdraw(by: str) -> None:
        for target in imposed.pop(by, []):
            constraints.get(target, {}).pop(by, None)

    async def fetch(repository, name: str):
        key = (repository.name, name)
        if key not in fetched:
            try:
                fetched[key] = await repos.fetch_package(db, repository, name)
            except repos.RepositoryError as e:
                fetched[key] = e
        return fetched[key]

    async def decide(target: str, cons: list[_Constraint]) -> tuple[PlanEntry, list[Any]]:
        listed = [{"required_by": c.required_by, "version": c.version} for c in cons]
        names = sorted({c.name for c in cons})
        if len(names) > 1:
            return PlanEntry(
                install_name=target, package_name=names[0], status=INCOMPATIBLE, constraints=listed,
                message=f"different packages are required as '{target}': {_describe(cons)}",
            ), []
        name = names[0]

        package = installed.get(target)
        if package is not None:
            declared = package.package_name or package.name
            entry = PlanEntry(
                install_name=target, package_name=declared, status=SATISFIED, constraints=listed,
                version=package.version, installed_version=package.version,
            )
            if declared != name:
                entry.status = INCOMPATIBLE
                entry.message = f"'{target}' is installed, but it is package '{declared}', not '{name}'"
            else:
                unmet = [c for c in cons if not satisfies(package.version, c.version)]
                if unmet:
                    entry.status = INCOMPATIBLE
                    entry.message = (
                        f"{name} {package.version} is installed, but {_describe(unmet)}; update it first"
                    )
            return entry, []

        entry = PlanEntry(install_name=target, package_name=name, status=MISSING, constraints=listed)
        hints = {c.repository for c in cons if c.repository}
        dependents = {c.dependent_repo for c in cons if c.dependent_repo}
        offering = []
        for repository in repositories:
            index_entry = next(
                (p for p in (repository.index or {}).get("packages", []) if p["name"] == name), None
            )
            if index_entry is not None:
                rank = 0 if repository.name in hints else 1 if repository.name in dependents else 2
                offering.append((rank, repository.name, repository, index_entry))
        offering.sort(key=lambda o: (o[0], o[1]))
        chosen = next((o for o in offering if _fits(o[3]["version"], cons)), None)
        if chosen is None:
            if offering:
                offered = ", ".join(f"{o[3]['version']} in {o[1]}" for o in offering)
                entry.message = (
                    f"not installed, and no active repository offers a version that meets all of: "
                    f"{_describe(cons)} (offered: {offered})"
                )
            else:
                entry.message = f"not installed, and no active package repository offers '{name}'"
            return entry, []

        repository = chosen[2]
        result = await fetch(repository, name)
        if isinstance(result, Exception):
            entry.message = f"could not be fetched from '{repository.name}': {result}"
            return entry, []
        text, index_entry, url = result
        if not _fits(index_entry["version"], cons):
            # The index moved on while fetching (a refresh).
            entry.message = (
                f"{repository.name} now offers {name} {index_entry['version']}, which doesn't meet "
                f"all of: {_describe(cons)}"
            )
            return entry, []

        entry.version, entry.repository, entry.file_url = index_entry["version"], repository.name, url
        try:
            # It must be installable under that name (multiInstance rule).
            _n, substituted, _d, _m = resolve_install_name(text, entry.instance)
            nested, waiting = _nested_requirements(substituted, dependency_variables.get(target))
        except Exception as e:  # ValueError, yaml / pydantic errors
            entry.status = INVALID
            entry.message = (
                f"{name} {entry.version} from {repository.name} can't be installed as '{target}': {e}"
            )
            # Kept (never installed): the preview still lists its variables,
            # so a value that made it invalid can be corrected.
            entry.yaml = text
            return entry, []
        entry.status, entry.yaml = INSTALL, text
        if waiting:
            entry.needs_input = True
            entry.message = (
                f"its own requirements use its variables ({', '.join(waiting)}): give their values "
                "and preview again"
            )
        entry.requires = [r.install_name for r in nested]
        return entry, nested

    add(root_install_name, list(requirements), root_repository)
    settled = False
    for _round in range(MAX_ROUNDS):
        changed = False
        for target in sorted(constraints):
            if target == root_install_name:
                continue  # a requirement back to the root: a cycle, found below
            cons = sorted(constraints[target].values(), key=lambda c: c.required_by)
            if not cons:
                if target in decided:
                    # Nothing requires it any more (a requirer chose otherwise).
                    del decided[target]
                    signatures.pop(target, None)
                    withdraw(target)
                    changed = True
                continue
            signature = tuple(cons)
            if signatures.get(target) == signature:
                continue
            signatures[target] = signature
            entry, nested = await decide(target, cons)
            decided[target] = entry
            withdraw(target)
            add(target, nested, entry.repository)
            changed = True
        if not changed:
            settled = True
            break

    plan = Plan()
    if not settled:
        plan.problems.append(
            f"the requirements didn't settle after {MAX_ROUNDS} rounds of resolution; refusing"
        )

    # Cycles: through the plan, and through what is installed already (an
    # installed package's stored requirements), from the root.
    def edges(node: str) -> list[str]:
        if node == root_install_name:
            return [r.install_name for r in requirements]
        entry = decided.get(node)
        if entry is not None and entry.status == INSTALL:
            return list(entry.requires)
        package = installed.get(node)
        if package is not None:
            return [_target(e) for e in package.requires or []]
        return []

    finished: set[str] = set()

    def find_cycles(node: str, path: list[str]) -> None:
        for nxt in edges(node):
            if nxt in path:
                cycle = path[path.index(nxt):] + [nxt]
                # Reported on the first package of the path the plan shows.
                for name in path[1:] + [nxt]:
                    if name in decided:
                        decided[name].status = CYCLE
                        decided[name].message = f"circular requirement: {' → '.join(cycle)}"
                        break
                continue
            if nxt not in finished:
                find_cycles(nxt, path + [nxt])
        finished.add(node)

    find_cycles(root_install_name, [root_install_name])

    # The plan: what the root reaches, dependencies before dependents.
    order: list[PlanEntry] = []
    placed: set[str] = set()

    def plan_edges(node: str) -> list[str]:
        # Only the root and packages to install add entries to the plan.
        entry = decided.get(node)
        if node == root_install_name or (entry is not None and entry.status == INSTALL):
            return edges(node)
        return []

    def place(node: str, depth: int) -> None:
        for nxt in plan_edges(node):
            if nxt in placed or nxt == root_install_name or nxt not in decided:
                continue
            placed.add(nxt)
            if depth + 1 > MAX_DEPTH:
                decided[nxt].status = MISSING
                decided[nxt].message = f"requirements nest deeper than {MAX_DEPTH} levels"
            else:
                place(nxt, depth + 1)
            order.append(decided[nxt])

    place(root_install_name, 0)
    plan.entries = order
    return plan


async def lock_packages(db: AsyncSession) -> None:
    """Serialize package installs and uninstalls until the transaction ends,
    so an uninstall can't remove a package between a dependent's plan seeing
    it installed and the dependent's commit. Taken before any row lock an
    install takes (lock order: before lock_singleton)."""
    import zlib

    from sqlalchemy import text

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": zlib.crc32(b"sinas:packages")}
    )
