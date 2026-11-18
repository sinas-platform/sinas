"""Startup diagnostics for the non-root images (app.core.runtime_checks).

Since 0.5.0 the images run as uid 10001. A file-storage volume an earlier
version wrote as root, or the host Docker socket, then fails on first use;
these checks report it at startup and on the System page instead.
"""

import logging
import os

import pytest

from app.core import runtime_checks

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="permission checks need a non-root test process"
)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    path = tmp_path / "files"
    path.mkdir()
    monkeypatch.setenv("FILE_STORAGE_PATH", str(path))
    monkeypatch.delenv("FILE_STORAGE_BACKEND", raising=False)
    yield path
    for p in (path, *path.iterdir()):
        p.chmod(0o755)


def test_writable_storage_is_fine(storage):
    (storage / "ns").mkdir()
    assert runtime_checks.file_storage_problem() is None


def test_unwritable_storage_root_is_reported(storage):
    storage.chmod(0o555)  # e.g. root-owned 0755 from before 0.5.0
    problem = runtime_checks.file_storage_problem()
    assert problem and str(storage) in problem
    assert "chgrp -R 0" in problem and "docker-compose.yml" in problem and "fsGroup" in problem


def test_unwritable_subdirectory_is_reported(storage):
    sub = storage / "ns"
    sub.mkdir()
    sub.chmod(0o555)
    problem = runtime_checks.file_storage_problem()
    assert problem and str(sub) in problem


def test_missing_storage_is_left_to_storage_itself(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_STORAGE_PATH", str(tmp_path / "nope" / "files"))
    assert runtime_checks.file_storage_problem() is None


def test_root_process_is_not_checked(storage, monkeypatch):
    storage.chmod(0o555)
    monkeypatch.setattr(runtime_checks.os, "geteuid", lambda: 0)
    assert runtime_checks.file_storage_problem() is None


@pytest.fixture
def socket_file(tmp_path, monkeypatch):
    from app.core.config import settings

    sock = tmp_path / "docker.sock"
    sock.touch()
    monkeypatch.setenv("DOCKER_HOST", f"unix://{sock}")
    monkeypatch.setattr(settings, "sandbox_executor", "docker_pool")
    monkeypatch.setattr(settings, "trusted_executor", "docker_shared")
    monkeypatch.setattr(settings, "code_execution_enabled", True)
    yield sock
    sock.chmod(0o644)


def test_inaccessible_docker_socket_is_reported(socket_file):
    socket_file.chmod(0o000)  # root:docker 0660 seen by uid 10001
    problem = runtime_checks.docker_socket_problem()
    assert problem and str(socket_file) in problem
    assert "docker_pool" in problem and 'user: "0:0"' in problem


def test_accessible_docker_socket_is_fine(socket_file):
    assert runtime_checks.docker_socket_problem() is None


@pytest.mark.parametrize(
    "setting,value",
    [
        ("sandbox_executor", "k8s_pod"),
        ("code_execution_enabled", False),
    ],
)
def test_socket_not_checked_without_docker_executors(socket_file, monkeypatch, setting, value):
    from app.core.config import settings

    socket_file.chmod(0o000)
    monkeypatch.setattr(settings, setting, value)
    if setting == "sandbox_executor":
        monkeypatch.setattr(settings, "trusted_executor", "k8s_shared")
    assert runtime_checks.docker_socket_problem() is None


def test_tcp_docker_host_is_not_a_file(socket_file, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker:2375")
    assert runtime_checks.docker_socket_problem() is None


def test_problems_are_logged_not_raised(storage, caplog):
    storage.chmod(0o555)
    with caplog.at_level(logging.ERROR, logger="app.core.runtime_checks"):
        problems = runtime_checks.log_runtime_problems("queue-worker")
    assert problems and any("[queue-worker]" in r.getMessage() for r in caplog.records)


def test_problems_surface_as_critical_system_warnings(storage):
    from app.api.v1.endpoints import system as system_endpoint

    storage.chmod(0o555)
    warnings = system_endpoint._generate_warnings([], [{"queue": "functions"}, {"queue": "agents"}], 0, {}, {})
    assert any(w["level"] == "critical" and str(storage) in w["message"] for w in warnings)
