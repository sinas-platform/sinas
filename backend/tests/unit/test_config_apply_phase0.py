"""Phase-0 defects in the config-apply path.

These are pre-existing bugs found while designing the config/CRUD unification;
each test pins one so the eventual refactor can't silently reintroduce it.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encryption_service
from app.models.secret import Secret
from app.services.config_parser import ConfigValidation


# --------------------------------------------------------------------------
# 1 + 2. The validation result contract (crashes on boot / on POST)
# --------------------------------------------------------------------------

def test_validation_exposes_is_valid_not_valid():
    """scheduler/service.py read `validation.valid`, which does not exist —
    AUTO_APPLY_CONFIG=true raised AttributeError on every boot."""
    v = ConfigValidation()
    assert v.is_valid is True
    assert not hasattr(v, "valid")


def test_validation_warnings_are_plain_strings():
    """Both the scheduler and POST /config/apply formatted warnings as objects
    (`w.path`/`w.message`); they are strings, so an invalid config WITH warnings
    500'd instead of returning its validation errors."""
    v = ConfigValidation()
    v.warnings.append("SinasPackage should not define environment-specific resources")
    assert all(isinstance(w, str) for w in v.warnings)
    # the formatting the code now uses must work on a str
    assert f"  - {v.warnings[0]}".endswith("resources")


def test_startup_autoapply_formatting_smoke():
    """Guards the exact expressions the startup path uses."""
    v = ConfigValidation()
    v.warnings.append("w1")
    assert v.is_valid  # not v.valid
    assert [f"  - {w}" for w in v.warnings] == ["  - w1"]


# --------------------------------------------------------------------------
# 3. Secrets applier could overwrite another user's PRIVATE secret
# --------------------------------------------------------------------------

class TestSecretsApplierScoping:
    async def _apply(self, db: AsyncSession, owner_id, name, value):
        from app.services.config_apply.resources import apply_secrets

        class _Cfg:
            def __init__(self, name, value):
                self.name = name
                self.value = value
                self.description = None

        changes: list = []
        await apply_secrets(
            db=db,
            secrets=[_Cfg(name, value)],
            dry_run=False,
            managed_by="config",
            config_name="test",
            owner_user_id=owner_id,
            calculate_hash=lambda d: "hash-" + str(sorted(d.items())),
            track_change=lambda *a: changes.append(a),
            errors=[],
            warnings=[],
        )
        return changes

    async def test_private_secret_of_another_user_is_not_overwritten(
        self, db: AsyncSession, test_user, admin_user
    ):
        """A private secret is a per-user override. Config declares platform
        (shared) secrets, so applying one must not reach into someone's private
        row — which the name-only lookup did, replacing its value."""
        private = Secret(
            user_id=test_user.id,
            name=f"API_KEY_{uuid.uuid4().hex[:6]}",
            encrypted_value=encryption_service.encrypt("user-private-value"),
            visibility="private",
        )
        db.add(private)
        await db.flush()

        await self._apply(db, str(admin_user.id), private.name, "config-value")
        await db.flush()

        await db.refresh(private)
        assert encryption_service.decrypt(private.encrypted_value) == "user-private-value"

    async def test_config_created_secret_is_explicitly_shared(
        self, db: AsyncSession, admin_user
    ):
        name = f"SHARED_{uuid.uuid4().hex[:6]}"
        await self._apply(db, str(admin_user.id), name, "v")
        await db.flush()

        row = (
            await db.execute(select(Secret).where(Secret.name == name))
        ).scalar_one()
        assert row.visibility == "shared"

    async def test_existing_shared_secret_is_updated(self, db: AsyncSession, admin_user):
        """Regression guard: scoping to shared must not break the normal path."""
        name = f"SHARED_{uuid.uuid4().hex[:6]}"
        await self._apply(db, str(admin_user.id), name, "first")
        await db.flush()
        await self._apply(db, str(admin_user.id), name, "second")
        await db.flush()

        rows = (await db.execute(select(Secret).where(Secret.name == name))).scalars().all()
        assert len(rows) == 1
        assert encryption_service.decrypt(rows[0].encrypted_value) == "second"


# --------------------------------------------------------------------------
# 5 + 4. Post-commit notifications (schedules / CDC)
# --------------------------------------------------------------------------

class TestApplyNotifications:
    async def test_flush_publishes_queued_events(self, db: AsyncSession, monkeypatch):
        """Config-applied schedules never reached the running scheduler, and the
        CDC reload was skipped entirely for callers using auto_commit=False."""
        from app.services.config_apply.service import ConfigApplyService

        published: list[tuple[str, str]] = []

        class _FakeRedis:
            async def publish(self, channel, payload):
                published.append((channel, payload))

        async def fake_get_redis():
            return _FakeRedis()

        monkeypatch.setattr("app.core.redis.get_redis", fake_get_redis)

        svc = ConfigApplyService(db, "test-config", owner_user_id=None, auto_commit=False)
        svc._pending_scheduler.append(("create", "job-1"))
        svc._pending_cdc_reload = True

        await svc.flush_notifications()

        channels = [c for c, _ in published]
        assert "sinas:scheduler:jobs" in channels
        assert "sinas:cdc:triggers" in channels

    async def test_flush_is_idempotent_and_clears_queue(self, db: AsyncSession, monkeypatch):
        from app.services.config_apply.service import ConfigApplyService

        calls: list = []

        class _FakeRedis:
            async def publish(self, channel, payload):
                calls.append(channel)

        async def fake_get_redis():
            return _FakeRedis()

        monkeypatch.setattr("app.core.redis.get_redis", fake_get_redis)

        svc = ConfigApplyService(db, "c", owner_user_id=None, auto_commit=False)
        svc._pending_scheduler.append(("create", "j"))
        await svc.flush_notifications()
        # assert the first flush really published — otherwise a missed
        # monkeypatch would make the idempotency check below vacuously true
        assert calls == ["sinas:scheduler:jobs"]

        await svc.flush_notifications()  # queue drained; must be a no-op
        assert calls == ["sinas:scheduler:jobs"]

    async def test_notify_failure_does_not_break_apply(self, db: AsyncSession, monkeypatch):
        """The transaction already committed; a notification problem must not
        turn a successful apply into an error."""
        from app.services.config_apply.service import ConfigApplyService

        async def boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr("app.core.redis.get_redis", boom)

        svc = ConfigApplyService(db, "c", owner_user_id=None, auto_commit=False)
        svc._pending_cdc_reload = True
        await svc.flush_notifications()  # must not raise


# --------------------------------------------------------------------------
# 6. Config-applied components must reach the compiler
# --------------------------------------------------------------------------

class TestComponentCompileNotification:
    async def _apply(self, db, owner_id, comp_config, notify):
        from app.services.config_apply.resources import apply_components

        await apply_components(
            db=db,
            components=[comp_config],
            dry_run=False,
            managed_by="config",
            config_name="test",
            owner_user_id=owner_id,
            calculate_hash=lambda d: "hash-" + str(sorted(str(d))),
            track_change=lambda *a: None,
            errors=[],
            warnings=[],
            notify_compile=notify,
        )

    def _config(self, name, source="export default () => null"):
        from app.schemas.config import ComponentConfig

        return ComponentConfig(namespace="default", name=name, sourceCode=source)

    async def test_created_component_is_queued_for_compile(self, db, admin_user):
        """Config/package components sat at compile_status="pending" forever —
        only the REST path ever invoked the builder."""
        import uuid as _uuid

        queued = []
        name = f"comp_{_uuid.uuid4().hex[:6]}"
        await self._apply(db, str(admin_user.id), self._config(name), queued.append)
        assert len(queued) == 1  # the new component's id

    async def test_source_change_requeues_but_metadata_change_does_not(
        self, db, admin_user
    ):
        import uuid as _uuid

        queued = []
        name = f"comp_{_uuid.uuid4().hex[:6]}"
        await self._apply(db, str(admin_user.id), self._config(name), queued.append)
        await db.flush()

        # Same source, new title → no recompile
        cfg = self._config(name)
        cfg.title = "New title"
        await self._apply(db, str(admin_user.id), cfg, queued.append)
        assert len(queued) == 1

        # Changed source → recompile
        cfg2 = self._config(name, source="export default () => 42")
        await self._apply(db, str(admin_user.id), cfg2, queued.append)
        assert len(queued) == 2


# --------------------------------------------------------------------------
# 7. FunctionVersion churn: metadata-only updates must not mint versions
# --------------------------------------------------------------------------

class TestFunctionVersionChurn:
    async def _apply(self, db, owner_id, func_config):
        from app.services.config_apply.resources import apply_functions

        return await apply_functions(
            db=db,
            functions=[func_config],
            dry_run=False,
            managed_by="config",
            config_name="test",
            owner_user_id=owner_id,
            calculate_hash=lambda d: "hash-" + str(sorted(str(d))),
            track_change=lambda *a: None,
            errors=[],
            warnings=[],
            function_ids={},
        )

    async def _versions(self, db, name):
        from sqlalchemy import select

        from app.models import Function, FunctionVersion

        fn = (
            await db.execute(select(Function).where(Function.name == name))
        ).scalar_one()
        rows = (
            await db.execute(
                select(FunctionVersion).where(FunctionVersion.function_id == fn.id)
            )
        ).scalars().all()
        return fn, rows

    async def test_metadata_only_update_creates_no_version(self, db, admin_user):
        import uuid as _uuid

        from app.schemas.config import FunctionConfig

        name = f"fn_{_uuid.uuid4().hex[:6]}"
        code = "def handler(input, context): return 1"
        await self._apply(
            db, str(admin_user.id), FunctionConfig(name=name, code=code)
        )
        await db.flush()
        _, versions = await self._versions(db, name)
        assert len(versions) == 1  # initial

        # Description-only change: previously minted version 2
        await self._apply(
            db,
            str(admin_user.id),
            FunctionConfig(name=name, code=code, description="now documented"),
        )
        await db.flush()
        fn, versions = await self._versions(db, name)
        assert fn.description == "now documented"
        assert len(versions) == 1  # unchanged

    async def test_code_change_still_creates_version(self, db, admin_user):
        import uuid as _uuid

        from app.schemas.config import FunctionConfig

        name = f"fn_{_uuid.uuid4().hex[:6]}"
        await self._apply(
            db, str(admin_user.id),
            FunctionConfig(name=name, code="def handler(input, context): return 1"),
        )
        await db.flush()
        await self._apply(
            db, str(admin_user.id),
            FunctionConfig(name=name, code="def handler(input, context): return 2"),
        )
        await db.flush()
        _, versions = await self._versions(db, name)
        assert sorted(v.version for v in versions) == [1, 2]
        assert any("return 2" in v.code for v in versions)

    async def test_schema_change_creates_version(self, db, admin_user):
        import uuid as _uuid

        from app.schemas.config import FunctionConfig

        name = f"fn_{_uuid.uuid4().hex[:6]}"
        code = "def handler(input, context): return 1"
        await self._apply(db, str(admin_user.id), FunctionConfig(name=name, code=code))
        await db.flush()
        await self._apply(
            db, str(admin_user.id),
            FunctionConfig(
                name=name, code=code,
                inputSchema={"type": "object", "properties": {"x": {}}},
            ),
        )
        await db.flush()
        _, versions = await self._versions(db, name)
        assert len(versions) == 2
