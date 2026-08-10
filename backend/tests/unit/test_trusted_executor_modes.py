"""Trusted executor modes: disabled + k8s_shared (warm credential-free pods)."""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Function
from app.services.executor import factory
from app.services.executor.base import ResultStatus
from app.services.executor.k8s_shared_trusted import K8sSharedTrustedExecutor


@pytest.fixture
def reset_factory():
    factory._reset_cache_for_tests()
    yield
    factory._reset_cache_for_tests()


class TestFactoryModes:
    def test_disabled_returns_none(self, monkeypatch, reset_factory):
        monkeypatch.setattr(settings, "trusted_executor", "disabled")
        assert factory.get_trusted_executor() is None

    def test_k8s_shared_resolves(self, monkeypatch, reset_factory):
        monkeypatch.setattr(settings, "trusted_executor", "k8s_shared")
        assert isinstance(factory.get_trusted_executor(), K8sSharedTrustedExecutor)

    def test_unknown_mode_rejected(self, monkeypatch, reset_factory):
        monkeypatch.setattr(settings, "trusted_executor", "carrier-pigeon")
        with pytest.raises(ValueError, match="carrier-pigeon"):
            factory.get_trusted_executor()


class TestDisabledMode:
    async def test_shared_pool_execution_rejected_with_clear_error(
        self, monkeypatch, reset_factory
    ):
        from app.services.execution_engine import (
            FunctionExecutionError,
            FunctionExecutor,
        )

        monkeypatch.setattr(settings, "trusted_executor", "disabled")
        engine = FunctionExecutor()
        fn = SimpleNamespace(namespace="acme", name="report")

        with pytest.raises(FunctionExecutionError, match="disabled.*acme/report"):
            await engine._execute_in_shared_pool(
                function=fn,
                input_data={},
                execution_id="e1",
                user_id="u1",
                user_email="u@example.com",
                access_token="t",
                trigger_type="api",
                chat_id=None,
                db=None,
            )


@pytest.fixture
async def shared_fn(db: AsyncSession, test_user):
    fn = Function(
        user_id=test_user.id,
        namespace="acme",
        name=f"trusted-{uuid.uuid4().hex[:6]}",
        code="def handler(input, context): return {'ok': True}",
        input_schema={},
        output_schema={},
        is_active=True,
        shared_pool=True,
    )
    db.add(fn)
    await db.flush()
    return fn


@pytest.fixture
def k8s_mocks(monkeypatch):
    """Patch the k8s runtime boundary; capture dispatches."""
    calls = SimpleNamespace(dispatches=[], ready_checks=[], wire=None)

    monkeypatch.setattr(
        "app.services.executor._k8s_runtime.resolve_namespace", lambda: "test-ns"
    )

    def _ready(name, namespace):
        calls.ready_checks.append(name)
        return True

    monkeypatch.setattr(
        "app.services.executor.k8s_shared_trusted._pod_ready_blocking", _ready
    )

    async def _run(name, namespace, payload, timeout):
        calls.dispatches.append({"pod": name, "payload": payload})
        return calls.wire

    monkeypatch.setattr(
        "app.services.executor._k8s_runtime.run_payload_in_pod", _run
    )

    async def _secrets(self, db, user_id=None):
        return {"API_KEY": "decrypted"}

    monkeypatch.setattr(
        "app.services.shared_worker_manager.SharedWorkerManager._load_secrets",
        _secrets,
    )
    monkeypatch.setattr(settings, "k8s_release_name", "acme")
    monkeypatch.setattr(settings, "k8s_trusted_workers", 2)
    return calls


class TestK8sSharedExecute:
    async def _execute(self, db, fn):
        return await K8sSharedTrustedExecutor().execute(
            user_id="u1",
            user_email="u@example.com",
            access_token="tok",
            function_namespace=fn.namespace,
            function_name=fn.name,
            input_data={"x": 1},
            execution_id="e-123",
            trigger_type="api",
            chat_id=None,
            db=db,
            timeout=30,
        )

    async def test_dispatches_shared_payload_to_warm_pod(self, db, shared_fn, k8s_mocks):
        k8s_mocks.wire = {"status": "completed", "result": {"ok": True}}
        res = await self._execute(db, shared_fn)

        assert res.status is ResultStatus.COMPLETED
        d = k8s_mocks.dispatches[0]
        assert d["pod"].startswith("acme-trusted-worker-")
        payload = d["payload"]
        assert payload["action"] == "execute_inline"
        assert payload["function_code"] == shared_fn.code
        ctx = payload["context"]
        assert ctx["access_token"] == "tok"
        # Trusted context carries decrypted secrets, same as docker_shared
        assert ctx["secrets"] == {"API_KEY": "decrypted"}

    async def test_non_shared_pool_function_refused(self, db, shared_fn, k8s_mocks):
        shared_fn.shared_pool = False
        await db.flush()
        k8s_mocks.wire = {"status": "completed"}
        res = await self._execute(db, shared_fn)
        assert res.status is ResultStatus.FAILED
        assert "shared_pool" in res.error
        assert k8s_mocks.dispatches == []  # never reached a pod

    async def test_awaiting_input_gets_pod_handle_stamped(self, db, shared_fn, k8s_mocks):
        # The pod doesn't know its own name — the executor must stamp it so
        # resume can find the same pod.
        k8s_mocks.wire = {"status": "awaiting_input", "prompt": "continue?"}
        res = await self._execute(db, shared_fn)
        assert res.status is ResultStatus.AWAITING_INPUT
        assert res.handle == k8s_mocks.dispatches[0]["pod"]

    async def test_fails_over_to_next_pod_on_infra_error(
        self, db, shared_fn, k8s_mocks, monkeypatch
    ):
        attempts = []

        async def _flaky(name, namespace, payload, timeout):
            attempts.append(name)
            if len(attempts) == 1:
                raise RuntimeError("exec websocket died")
            return {"status": "completed", "result": 1}

        monkeypatch.setattr(
            "app.services.executor._k8s_runtime.run_payload_in_pod", _flaky
        )
        res = await self._execute(db, shared_fn)
        assert res.status is ResultStatus.COMPLETED
        assert len(attempts) == 2
        assert attempts[0] != attempts[1]  # tried a different pod


class TestK8sSharedResume:
    async def test_dead_pod_fails_cleanly(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.executor._k8s_runtime.resolve_namespace", lambda: "ns"
        )
        monkeypatch.setattr(
            "app.services.executor.k8s_shared_trusted._pod_ready_blocking",
            lambda n, ns: False,
        )
        res = await K8sSharedTrustedExecutor().resume(
            handle="acme-trusted-worker-0",
            resume_value="yes",
            execution_id="e1",
            timeout=10,
        )
        assert res.status is ResultStatus.FAILED
        assert "no longer available" in res.error

    async def test_writes_resume_value_and_returns_result(self, monkeypatch):
        import json as _json

        monkeypatch.setattr(
            "app.services.executor._k8s_runtime.resolve_namespace", lambda: "ns"
        )
        monkeypatch.setattr(
            "app.services.executor.k8s_shared_trusted._pod_ready_blocking",
            lambda n, ns: True,
        )
        execs = []

        def _exec(name, namespace, command, *, stdin_payload=None, timeout):
            execs.append({"cmd": command, "stdin": stdin_payload})
            if stdin_payload is not None:
                n = len(stdin_payload.encode())
                return (f"WROTE {n}", "", 0)
            return (_json.dumps({"status": "completed", "result": 42}), "", 0)

        monkeypatch.setattr(
            "app.services.executor._k8s_runtime._exec_blocking", _exec
        )

        res = await K8sSharedTrustedExecutor().resume(
            handle="acme-trusted-worker-1",
            resume_value={"answer": "yes"},
            execution_id="e1",
            timeout=10,
        )
        assert res.status is ResultStatus.COMPLETED
        # First exec wrote the resume value via the length-prefixed protocol
        assert _json.loads(execs[0]["stdin"]) == {"value": {"answer": "yes"}}
