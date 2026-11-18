"""Startup diagnostics for running as a non-root user.

Since 0.5.0 the Sinas images run as uid 10001 (group 0). Two things an
install from before that can still carry fail later, and confusingly, under
that user: a file-storage volume whose files an earlier version wrote as
root (uploads and file writes fail on first use), and the host Docker socket
(root:docker 0660 on Linux), which the Docker executors need. Both are
fixed by configuration (0.5.0's docker-compose.yml runs the socket-using
services as root; the Helm chart's fsGroup re-owns the volume), so these
checks only REPORT: a loud error at process start and a critical warning on
the System page, never a crash loop.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

_DOCKER_EXECUTORS = {"docker_pool", "docker_ephemeral", "docker_shared"}
# Subdirectories of the file-storage root checked for writability (the
# root alone passes when only it was re-owned); bounded to stay cheap.
_SUBDIR_SAMPLE = 50


def _identity() -> str:
    return f"uid {os.geteuid()}, gid {os.getegid()}"


def file_storage_path() -> str:
    return os.getenv("FILE_STORAGE_PATH", "/var/sinas/files")


def _writable_dir(path: str) -> bool:
    return os.access(path, os.W_OK | os.X_OK)


def file_storage_problem() -> str | None:
    """Why the configured local file storage isn't writable by this
    process, or None. Root can write anywhere, so only non-root checks."""
    if os.getenv("FILE_STORAGE_BACKEND", "local") != "local" or os.geteuid() == 0:
        return None
    path = file_storage_path()
    if not os.path.isdir(path):
        # Not there (yet): nothing an earlier version could have written;
        # storage creates it on first use and reports its own errors.
        return None
    where = None if _writable_dir(path) else path
    if where is None:
        try:
            with os.scandir(path) as it:
                for i, entry in enumerate(it):
                    if i >= _SUBDIR_SAMPLE:
                        break
                    if entry.is_dir(follow_symlinks=False) and not _writable_dir(entry.path):
                        where = entry.path
                        break
        except OSError:
            where = path
    if where is None:
        return None
    return (
        f"File storage {path} is not writable by this process ({_identity()}; "
        f"{where} is not): file uploads and writes will fail. Since 0.5.0 the "
        "Sinas images run as uid 10001, group 0. Docker Compose: use 0.5.0's "
        "docker-compose.yml (it runs the Sinas services as root), or make the "
        "volume group-writable once: chgrp -R 0 <dir> && chmod -R g+rwX <dir>. "
        "Kubernetes: the chart's fsGroup: 0 fixes the ownership on first "
        "mount; on storage that ignores fsGroup, run that chgrp/chmod (see the "
        "0.5.0 upgrade notes)."
    )


def _docker_socket_path() -> str | None:
    host = os.getenv("DOCKER_HOST", "")
    if not host:
        return "/var/run/docker.sock"
    parsed = urlparse(host)
    return parsed.path if parsed.scheme == "unix" else None  # tcp:// etc.: not a file


def docker_socket_problem() -> str | None:
    """Why a configured Docker executor can't reach the Docker socket, or
    None. Only checked when a Docker executor is configured and code
    execution is on; a missing socket is left to the executors' own errors."""
    executors = {settings.sandbox_executor, settings.trusted_executor} & _DOCKER_EXECUTORS
    if not executors or not settings.code_execution_enabled:
        return None
    path = _docker_socket_path()
    if not path or not os.path.exists(path) or os.access(path, os.R_OK | os.W_OK):
        return None
    return (
        f"Docker socket {path} is not accessible to this process ({_identity()}): "
        f"code execution with {', '.join(sorted(executors))} will fail. Since "
        "0.5.0 the Sinas images run as uid 10001; on Linux the socket is "
        "root:docker 0660. Docker Compose: use 0.5.0's docker-compose.yml, "
        'which runs the services that use the socket with user: "0:0".'
    )


def runtime_problems() -> list[str]:
    problems = []
    for check in (file_storage_problem, docker_socket_problem):
        try:
            problem = check()
        except Exception as e:  # a diagnostic must never take a process down
            logger.debug("runtime check %s failed: %s", check.__name__, e)
            continue
        if problem:
            problems.append(problem)
    return problems


def log_runtime_problems(process: str) -> list[str]:
    """Log every problem as an error at process start; returns them."""
    problems = runtime_problems()
    for problem in problems:
        logger.error("[%s] %s", process, problem)
    return problems
