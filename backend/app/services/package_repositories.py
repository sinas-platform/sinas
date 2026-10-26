"""Package repositories: index refresh, the catalog, fetching packages, update
status (ADR 2026-10-13-package-repositories).

A repository serves one `index.json` (schema `sinas.package-index/v1`) at
its base URL, listing each package's name, version, the path of its
`package.yaml` relative to that base, and the file's SHA-256. Sinas fetches
the index now and then and keeps the last good copy on the repository row;
installing from a repository downloads the package file and refuses it
unless it matches the checksum the index gave.

A GitHub repository URL (https://github.com/<owner>/<repo>, optionally
/tree/<branch>/<subdir>) is read from raw.githubusercontent.com; anything
else is taken as the base URL itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.secret_refs import secret_ref_name
from app.models.package import Package
from app.models.package_repository import PackageRepository

logger = logging.getLogger(__name__)

INDEX_SCHEMA = "sinas.package-index/v1"
INDEX_FILE = "index.json"

# The quick-add in the console.
OFFICIAL_REPOSITORY = {
    "name": "sinas-official",
    "url": "https://github.com/sinas-platform/packages",
    "description": "Packages maintained by the Sinas team",
}

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RepositoryError(Exception):
    """A repository could not be read, or served something unusable. The
    message is safe to show (no credentials)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def base_url(url: str) -> str:
    """The URL packages' paths resolve against, with a trailing slash."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http"):
        raise RepositoryError("A repository URL must start with https://")
    if parts.scheme == "http" and not settings.package_repositories_allow_http:
        raise RepositoryError("A repository URL must use https")
    if parts.username or parts.password:
        raise RepositoryError("Put repository credentials in auth (a Secret), not in the URL")
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in ("github.com", "www.github.com"):
        if len(segments) < 2:
            raise RepositoryError("A GitHub repository URL needs an owner and a repository")
        owner, repo = segments[0], segments[1].removesuffix(".git")
        branch, sub = "main", []
        if len(segments) >= 4 and segments[2] == "tree":
            branch, sub = segments[3], segments[4:]
        path = "/".join([owner, repo, branch, *sub])
        return f"https://raw.githubusercontent.com/{path}/"
    path = parts.path
    if path.endswith("/" + INDEX_FILE) or path == INDEX_FILE:
        path = path[: -len(INDEX_FILE)]
    if not path.endswith("/"):
        path += "/"
    return f"{parts.scheme}://{parts.netloc}{path}"


def file_url(repository_url: str, path: str) -> str:
    """A package file's URL: its index path resolved against the base. The
    path must stay inside the repository."""
    if not _safe_path(path):
        raise RepositoryError(f"Unsafe package path in index: {path!r}")
    return urljoin(base_url(repository_url), path)


def _safe(url: str) -> str:
    """A URL as error messages may show it: no query (tokens sit there)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(malformed URL)"
    return f"{parts.scheme}://{parts.hostname or ''}{parts.path}"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

async def _auth_headers(db: AsyncSession, repository: PackageRepository) -> dict[str, str]:
    """The credential for a private repository. Repositories are
    installation-wide, so it comes from a shared Secret. A configured one that
    is missing refuses the request rather than sending it unauthenticated."""
    auth = repository.auth or {}
    kind = auth.get("type", "none")
    if kind == "none":
        return {}
    from app.services.connector_service import connector_service

    secret = secret_ref_name(auth.get("secret"))
    value = await connector_service._resolve_secret_value(db, secret, None) if secret else None
    if value is None:
        raise RepositoryError(f"Secret '{secret}' for repository '{repository.name}' not found")
    if kind == "bearer":
        return {"Authorization": f"Bearer {value}"}
    return {auth.get("header") or "X-Api-Key": value}


_MAX_REDIRECTS = 5


def _check_scheme(url: str, allow_http: bool) -> None:
    scheme = urlsplit(url).scheme
    if scheme != "https" and not (scheme == "http" and allow_http):
        raise RepositoryError(f"{_safe(url)}: refusing a non-https URL")


async def _get(
    url: str,
    headers: dict[str, str],
    *,
    max_bytes: Optional[int] = None,
    timeout: Optional[float] = None,
    allow_http: Optional[bool] = None,
) -> bytes:
    """GET with redirects followed by hand: every hop must be https, and the
    repository's credentials go only to the host they were configured for —
    a redirect to another host is followed without them. The bounds default
    to the package repository settings; other fetchers (the model price
    ratecard) pass their own."""
    limit = settings.package_repository_max_bytes if max_bytes is None else max_bytes
    if allow_http is None:
        allow_http = settings.package_repositories_allow_http
    origin = (urlsplit(url).hostname or "").lower()
    try:
        async with httpx.AsyncClient(
            timeout=settings.package_repository_timeout_seconds if timeout is None else timeout,
            follow_redirects=False,
        ) as client:
            for _hop in range(_MAX_REDIRECTS + 1):
                _check_scheme(url, allow_http)
                same_host = (urlsplit(url).hostname or "").lower() == origin
                async with client.stream("GET", url, headers=headers if same_host else {}) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise RepositoryError(f"{_safe(url)} redirected without a location")
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise RepositoryError(f"{_safe(url)} answered {response.status_code}")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise RepositoryError(f"{_safe(url)} is larger than {limit} bytes")
                        chunks.append(chunk)
                    return b"".join(chunks)
            raise RepositoryError(f"{_safe(url)}: too many redirects")
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as e:
        # ValueError: a malformed URL, e.g. a redirect to `https://[`.
        raise RepositoryError(f"{_safe(url)} could not be fetched: {type(e).__name__}") from e


def _safe_path(path: str) -> bool:
    return not (
        not path or path.startswith("/") or "\\" in path or "://" in path
        or any(part in ("", ".", "..") for part in path.split("/"))
    )


def parse_index(raw: bytes) -> dict[str, Any]:
    """The index, validated: a list of packages with a name, a version, a
    path and a SHA-256 each, names unique."""
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise RepositoryError(f"index.json is not valid JSON: {e}") from e
    if not isinstance(data, dict) or data.get("schema") != INDEX_SCHEMA:
        raise RepositoryError(f"index.json is not a {INDEX_SCHEMA} index")
    packages = data.get("packages")
    if not isinstance(packages, list):
        raise RepositoryError("index.json has no packages list")
    seen: set[str] = set()
    clean = []
    for entry in packages:
        if not isinstance(entry, dict):
            raise RepositoryError("index.json: every package must be an object")
        name, version, path, sha = entry.get("name"), entry.get("version"), entry.get("path"), entry.get("sha256")
        if not (isinstance(name, str) and _NAME.match(name)):
            raise RepositoryError(f"index.json: invalid package name {name!r}")
        if name in seen:
            raise RepositoryError(f"index.json: package '{name}' is listed twice")
        if not (isinstance(version, str) and version):
            raise RepositoryError(f"index.json: package '{name}' has no version")
        if not (isinstance(path, str) and _safe_path(path)):
            raise RepositoryError(f"index.json: package '{name}' has no usable path ({path!r})")
        if not (isinstance(sha, str) and _SHA256.match(sha)):
            raise RepositoryError(f"index.json: package '{name}' has no valid sha256")
        seen.add(name)
        clean.append({
            "name": name,
            "version": version,
            "description": entry.get("description") if isinstance(entry.get("description"), str) else None,
            "author": entry.get("author") if isinstance(entry.get("author"), str) else None,
            "path": path,
            # A bad readme link is dropped, never a reason to refuse the index.
            "readme": entry.get("readme") if isinstance(entry.get("readme"), str) and _safe_path(entry["readme"]) else None,
            "sha256": sha,
            # Optional: the package may be installed more than once. Only a
            # real boolean counts; anything else is dropped, never fatal.
            "multi_instance": entry.get("multiInstance") is True,
            # Optional: the packages it requires, as its package.requires
            # declares them. Informational (the catalog shows it); installs
            # use the requirements in the checksum-verified file.
            "requires": _index_requires(entry.get("requires")),
        })
    return {"schema": INDEX_SCHEMA, "packages": clean}


def _index_requires(value: Any) -> list[dict[str, Any]]:
    """An index entry's requires, validated one by one: a malformed
    requirement is dropped, never a reason to refuse the index."""
    from app.schemas.config import PackageRequirement

    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        try:
            req = PackageRequirement.model_validate(item)
        except Exception:
            continue
        out.append(req.model_dump(exclude_none=True))
    return out


async def refresh(db: AsyncSession, repository: PackageRepository) -> PackageRepository:
    """Fetch the repository's index now. A failure is recorded on the row and
    the last good index kept, so the catalog keeps working while a
    repository is briefly unreachable. Nothing is locked while fetching;
    afterwards the row is locked and re-read, and the result is dropped if
    the repository was pointed somewhere else meanwhile (its index belongs
    to the new source). Does not commit."""
    source = (repository.url, dict(repository.auth or {}))
    index, error = None, None
    try:
        url = urljoin(base_url(source[0]), INDEX_FILE)
        index = parse_index(await _get(url, await _auth_headers(db, repository)))
    except RepositoryError as e:
        error = str(e)
        logger.warning("Package repository '%s': %s", repository.name, e)
    await db.refresh(repository, with_for_update=True)
    if (repository.url, dict(repository.auth or {})) != source:
        return repository  # re-pointed while fetching: this result is stale
    repository.last_refreshed_at = _now()
    if index is not None:
        repository.index = index
        repository.last_error = None
    else:
        repository.last_error = error
    return repository


async def refresh_named(name: str) -> None:
    """Refresh one repository in a session of its own (post-commit effect)."""
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        repository = (
            await db.execute(select(PackageRepository).where(PackageRepository.name == name))
        ).scalar_one_or_none()
        if repository is None or not repository.is_active:
            return
        await refresh(db, repository)
        await db.commit()


async def refresh_due(now: Optional[datetime] = None) -> int:
    """Refresh every active repository whose index is older than the refresh
    interval (or was never fetched). From the agent worker's cron; returns
    how many were refreshed."""
    from app.core.database import AsyncSessionLocal

    now = now or _now()
    stale_before = now - timedelta(hours=settings.package_repository_refresh_hours)
    async with AsyncSessionLocal() as db:
        names = (
            await db.execute(
                select(PackageRepository.name).where(
                    PackageRepository.is_active.is_(True),
                    (PackageRepository.last_refreshed_at.is_(None))
                    | (PackageRepository.last_refreshed_at < stale_before),
                )
            )
        ).scalars().all()
    for name in names:
        try:
            await refresh_named(name)
        except Exception:
            # One repository going wrong must not hold up the others.
            logger.exception("Refreshing package repository '%s' failed", name)
    return len(names)


async def fetch_package(db: AsyncSession, repository: PackageRepository, package: str) -> tuple[str, dict[str, Any], str]:
    """A package's YAML from the repository, checked against the index's
    SHA-256: (yaml, index entry, file URL). A mismatch refreshes the index
    once (the repository may have published since) before refusing."""
    if not repository.is_active:
        raise RepositoryError(f"Repository '{repository.name}' is disabled")
    for attempt in range(2):
        if repository.index is None or attempt == 1:
            await refresh(db, repository)
            if repository.index is None:
                raise RepositoryError(repository.last_error or f"Repository '{repository.name}' has no index")
        entry = next((p for p in repository.index.get("packages", []) if p["name"] == package), None)
        if entry is None:
            if attempt == 0:
                continue
            raise RepositoryError(f"Repository '{repository.name}' has no package '{package}'")
        url = file_url(repository.url, entry["path"])
        raw = await _get(url, await _auth_headers(db, repository))
        if hashlib.sha256(raw).hexdigest() == entry["sha256"]:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as e:
                raise RepositoryError(f"Package '{package}' is not UTF-8 text") from e
            _check_declared(text, entry)
            return text, entry, url
    raise RepositoryError(
        f"Package '{package}' from '{repository.name}' does not match the checksum in its index"
    )


def _check_declared(text: str, entry: dict[str, Any]) -> None:
    """The file must be the package the index entry describes: the install
    target (and later update status) come from what the file declares."""
    import yaml

    try:
        doc = yaml.safe_load(text)
    except Exception as e:
        raise RepositoryError(f"Package '{entry['name']}' is not valid YAML: {e}") from e
    meta = doc.get("package") if isinstance(doc, dict) else None
    if not isinstance(meta, dict):
        raise RepositoryError(f"The file for '{entry['name']}' has no package metadata; refusing it")
    declared = (meta.get("name"), str(meta.get("version")) if meta.get("version") is not None else None)
    if declared != (entry["name"], entry["version"]):
        raise RepositoryError(
            f"The file for '{entry['name']}' {entry['version']} declares "
            f"'{declared[0]}' {declared[1]}; refusing it"
        )


# ---------------------------------------------------------------------------
# Versions, the catalog, update status
# ---------------------------------------------------------------------------

_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)(?:[-.]?([0-9A-Za-z.-]+))?$")


def _version_key(version: str) -> Optional[tuple]:
    """A sortable key for a semver-style version ("1.2.0", "1.2.0-rc.1"), or
    None when it isn't one. A pre-release sorts before its release."""
    version = version.strip().split("+", 1)[0]  # build metadata doesn't order versions
    match = _VERSION.match(version)
    if not match:
        return None
    release = tuple(int(part) for part in match.group(1).split("."))
    release = release + (0,) * (4 - len(release)) if len(release) < 4 else release
    pre = match.group(2)
    if pre is None:
        return (release, (1,))
    return (release, (0, tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split("."))))  # a hyphen stays inside a label


def is_newer(candidate: str, installed: str) -> bool:
    """Whether `candidate` is a later version than `installed`. Versions that
    aren't semver-style make no claim either way."""
    a, b = _version_key(candidate), _version_key(installed)
    if a is None or b is None:
        return False
    return a > b


def _declared_name(package: Package) -> str:
    return package.package_name or package.name


async def _repositories(db: AsyncSession) -> list[PackageRepository]:
    return list(
        (
            await db.execute(
                select(PackageRepository)
                .where(PackageRepository.is_active.is_(True))
                .order_by(PackageRepository.name)
            )
        ).scalars()
    )


def _source_for(package: Package, repositories: list[PackageRepository]) -> Optional[tuple[PackageRepository, dict]]:
    """The repository entry an installed package follows: the repository it
    was installed from; for one installed some other way (pasted YAML), the
    first repository offering a package by that name."""
    name = _declared_name(package)
    candidates = (
        [r for r in repositories if r.name == package.source_repository]
        if package.source_repository else repositories
    )
    for repository in candidates:
        for entry in (repository.index or {}).get("packages", []):
            if entry["name"] == name:
                return repository, entry
    return None


async def package_status(db: AsyncSession, packages: list[Package]) -> dict[Any, dict[str, Any]]:
    """Per installed package: the repository it follows, the version
    published there, and whether that is an update."""
    repositories = await _repositories(db)
    status: dict[Any, dict[str, Any]] = {}
    for package in packages:
        found = _source_for(package, repositories)
        if found is None:
            status[package.id] = {
                "repository": package.source_repository, "latest_version": None, "update_available": False,
            }
            continue
        repository, entry = found
        status[package.id] = {
            "repository": repository.name,
            "latest_version": entry["version"],
            "update_available": is_newer(entry["version"], package.version),
        }
    return status


def _readme_url(repository: PackageRepository, entry: dict[str, Any]) -> Optional[str]:
    try:
        return file_url(repository.url, entry["readme"]) if entry.get("readme") else None
    except RepositoryError:
        return None


async def catalog(db: AsyncSession) -> list[dict[str, Any]]:
    """Every package every active repository offers, with what is installed
    of it and whether an update is available."""
    from app.services.package_dependencies import requirement_status

    repositories = await _repositories(db)
    installed = list((await db.execute(select(Package))).scalars())
    by_name = {p.name: p for p in installed}
    out = []
    for repository in repositories:
        for entry in (repository.index or {}).get("packages", []):
            # Installs that follow this repository's entry (see _source_for).
            mine = [
                p for p in installed
                if _declared_name(p) == entry["name"]
                and (_source_for(p, repositories) or (None,))[0] is repository
            ]
            out.append({
                "repository": repository.name,
                "name": entry["name"],
                "version": entry["version"],
                "description": entry.get("description"),
                "author": entry.get("author"),
                "readme_url": _readme_url(repository, entry),
                # Indexes stored before the field existed have no key: false.
                "multi_instance": entry.get("multi_instance") is True,
                # Each with whether what is installed meets it now (the same
                # check GET /packages makes), for the catalog to show.
                "requires": [requirement_status(r, by_name) for r in entry.get("requires") or []],
                "installed": [
                    {"name": p.name, "version": p.version, "update_available": is_newer(entry["version"], p.version)}
                    for p in mine
                ],
                "update_available": any(is_newer(entry["version"], p.version) for p in mine),
            })
    return out
