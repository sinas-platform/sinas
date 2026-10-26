"""The inprocess trusted executor hands a function the same context as the
shared executors: `context["secrets"]` was missing, so a function reading it
worked on the full stack and failed on the lite profile
(TRUSTED_EXECUTOR=inprocess)."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.encryption import encryption_service
from app.models import Function
from app.models.secret import Secret
from app.services.executor.base import ResultStatus
from app.services.executor.inprocess_trusted import InProcessTrustedExecutor
from app.services.executor.k8s_shared_trusted import K8sSharedTrustedExecutor

# Returns the context's shape, and the secret values only as hashes: the test
# compares them without any value appearing in a failure message or log.
CODE = """
import hashlib

def handler(input_data, context):
    return {
        "keys": sorted(context),
        "secrets": {
            name: hashlib.sha256(value.encode()).hexdigest()
            for name, value in context["secrets"].items()
        },
    }
"""


@pytest.fixture
async def secrets(db: AsyncSession, test_user, admin_user):
    """A shared secret, one the user overrides privately, and another user's
    private secret the user must not see."""
    suffix = uuid.uuid4().hex[:6].upper()
    names = {key: f"{key}_{suffix}" for key in ("SHARED", "OVERRIDDEN", "OTHERS")}
    rows = [
        (admin_user.id, names["SHARED"], "shared-value", "shared"),
        (admin_user.id, names["OVERRIDDEN"], "shared-value", "shared"),
        (test_user.id, names["OVERRIDDEN"], "private-value", "private"),
        (admin_user.id, names["OTHERS"], "someone-elses", "private"),
    ]
    db.add_all([
        Secret(user_id=uid, name=name, encrypted_value=encryption_service.encrypt(value), visibility=vis)
        for uid, name, value, vis in rows
    ])
    await db.flush()
    return names


@pytest.fixture
async def trusted_fn(db: AsyncSession, test_user):
    fn = Function(
        user_id=test_user.id, namespace="acme", name=f"ctx-{uuid.uuid4().hex[:6]}",
        code=CODE, input_schema={}, output_schema={}, is_active=True, shared_pool=True,
    )
    db.add(fn)
    await db.flush()
    return fn


def _args(fn, user, db):
    return {
        "user_id": str(user.id), "user_email": user.email, "access_token": "tok",
        "function_namespace": fn.namespace, "function_name": fn.name, "input_data": {},
        "execution_id": f"e-{uuid.uuid4().hex[:6]}", "trigger_type": "api", "chat_id": None,
        "user_custom_fields": {"team": "a"}, "db": db, "timeout": 30,
    }


async def _k8s_context(monkeypatch, fn, user, db) -> dict:
    """The context the k8s shared executor would send its pod."""
    sent = {}

    async def _run(name, namespace, payload, timeout):
        sent.update(payload["context"])
        return {"status": "completed", "result": None}

    monkeypatch.setattr("app.services.executor._k8s_runtime.resolve_namespace", lambda: "test-ns")
    monkeypatch.setattr("app.services.executor._k8s_runtime.run_payload_in_pod", _run)
    monkeypatch.setattr(
        "app.services.executor.k8s_shared_trusted._pod_ready_blocking", lambda name, ns: True
    )
    monkeypatch.setattr(settings, "k8s_release_name", "acme")
    monkeypatch.setattr(settings, "k8s_trusted_workers", 1)
    await K8sSharedTrustedExecutor().execute(**_args(fn, user, db))
    return sent


def _digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


async def test_secrets_are_in_the_context(db, test_user, secrets, trusted_fn):
    res = await InProcessTrustedExecutor().execute(**_args(trusted_fn, test_user, db))
    assert res.status is ResultStatus.COMPLETED, res.error
    got = res.result["secrets"]
    assert got[secrets["SHARED"]] == _digest("shared-value")
    assert got[secrets["OVERRIDDEN"]] == _digest("private-value")  # private wins
    assert secrets["OTHERS"] not in got  # another user's private secret


async def test_the_context_has_the_shared_executors_keys(monkeypatch, db, test_user, trusted_fn):
    res = await InProcessTrustedExecutor().execute(**_args(trusted_fn, test_user, db))
    assert res.status is ResultStatus.COMPLETED, res.error
    shared = await _k8s_context(monkeypatch, trusted_fn, test_user, db)
    assert res.result["keys"] == sorted(shared)


async def test_without_a_user_only_shared_secrets(db, test_user, secrets, trusted_fn):
    res = await InProcessTrustedExecutor().execute(**{**_args(trusted_fn, test_user, db), "user_id": None})
    assert res.status is ResultStatus.COMPLETED, res.error
    got = res.result["secrets"]
    assert got[secrets["SHARED"]] == _digest("shared-value")
    assert got[secrets["OVERRIDDEN"]] == _digest("shared-value")  # no private override
    assert secrets["OTHERS"] not in got


@pytest.mark.parametrize("target", ["load_trusted_secrets", "function"])
async def test_a_lost_connection_while_preparing_is_retryable(monkeypatch, db, test_user, trusted_fn, target):
    """Loading the function or its secrets runs before any code: a dropped
    connection there is ExecutorUnavailable (the queue may retry), as on
    the other executors."""
    from sqlalchemy.exc import OperationalError

    from app.services.executor import inprocess_trusted
    from app.services.executor.base import ExecutorUnavailable

    lost = OperationalError("SELECT 1", {}, ConnectionResetError("connection lost"))

    async def _fail(*args, **kwargs):
        raise lost

    if target == "function":
        monkeypatch.setattr(db, "execute", _fail)
    else:
        monkeypatch.setattr(inprocess_trusted, "load_trusted_secrets", _fail)
    with pytest.raises(ExecutorUnavailable):
        await InProcessTrustedExecutor().execute(**_args(trusted_fn, test_user, db))


async def test_an_unreadable_secret_is_skipped_without_its_value(db, test_user, trusted_fn, caplog):
    name = f"BROKEN_{uuid.uuid4().hex[:6]}"
    db.add(Secret(user_id=test_user.id, name=name, encrypted_value="not-a-fernet-token", visibility="shared"))
    await db.flush()
    res = await InProcessTrustedExecutor().execute(**_args(trusted_fn, test_user, db))
    assert res.status is ResultStatus.COMPLETED, res.error
    assert name not in res.result["secrets"]
    warnings = [r.getMessage() for r in caplog.records if name in r.getMessage()]
    assert warnings and not any("not-a-fernet-token" in w for w in warnings)
