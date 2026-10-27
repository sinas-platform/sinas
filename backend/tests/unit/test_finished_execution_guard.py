"""A function execution that already finished never runs again.

Whatever re-delivers its queue job (a finish arq could not record, Redis
being full at that moment; a requeue; a duplicate enqueue), an execution
that is COMPLETED, FAILED or CANCELLED is not run a second time: a function
may have side effects. (Agent and pipeline jobs have max_tries=1, so arq
itself refuses to run them twice.)
"""

import pytest
from sqlalchemy import select

from app.models.execution import Execution, ExecutionStatus
from app.services.execution_engine import FunctionExecutionError, executor
from tests.unit.test_function_queue_retry import (  # noqa: F401 — fixtures
    _FakeRedis,
    _function,
    _kwargs,
    _pending_row,
    sandbox,
    use_test_db,
)


@pytest.mark.usefixtures("use_test_db")
class TestFinishedExecutionsDoNotRunAgain:
    async def _finish(self, db, row, status, **fields):
        row.status = status
        for k, v in fields.items():
            setattr(row, k, v)
        await db.commit()

    async def test_completed_returns_its_output(self, db, test_user, sandbox):
        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        await self._finish(db, row, ExecutionStatus.COMPLETED, output_data={"n": 1})
        fake = sandbox(RuntimeError("must not run"))

        result = await executor.execute_function(
            function_namespace=fn.namespace, function_name=fn.name, input_data={},
            execution_id=row.execution_id, trigger_type="API", trigger_id="t",
            user_id=str(test_user.id),
        )
        assert result == {"n": 1}
        assert fake.calls == 0

    @pytest.mark.parametrize("status", [ExecutionStatus.FAILED, ExecutionStatus.CANCELLED])
    async def test_failed_or_cancelled_is_not_rerun(self, db, test_user, sandbox, status):
        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        await self._finish(db, row, status, error="it broke")
        fake = sandbox(RuntimeError("must not run"))

        with pytest.raises(FunctionExecutionError) as exc:
            await executor.execute_function(
                function_namespace=fn.namespace, function_name=fn.name, input_data={},
                execution_id=row.execution_id, trigger_type="API", trigger_id="t",
                user_id=str(test_user.id),
            )
        assert exc.value.recorded and not exc.value.dead_letter
        assert fake.calls == 0
        await db.refresh(row)
        assert row.status == status and row.error == "it broke"

    async def test_a_redelivered_queue_job_does_not_run_twice(self, db, test_user, sandbox):
        """The worker path: the job of a COMPLETED execution comes again
        (its finish wasn't recorded): no second run, no dead letter."""
        from app.queue import worker

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        await self._finish(db, row, ExecutionStatus.COMPLETED, output_data={"ok": True})
        fake = sandbox(RuntimeError("must not run"))
        redis = _FakeRedis()

        result = await worker.execute_function_job(
            {"redis": redis, "job_try": 2}, **_kwargs(fn, row, test_user)
        )
        assert result == {"ok": True}
        assert fake.calls == 0
        assert redis.lists == {}  # nothing dead-lettered
        row = (await db.execute(select(Execution).where(Execution.id == row.id))).scalar_one()
        assert row.status == ExecutionStatus.COMPLETED

