"""Deployment feature toggles: code execution and the built-in database.

Both default on. Off means the capability is *removed* — the tool is not
advertised to the model, execution is refused explicitly, and the heavy
executor startup is skipped — rather than left half-present to fail oddly.
"""

from unittest.mock import patch

import pytest

from app.services.code_execution import execute as execute_code


class TestCodeExecutionToggle:
    def test_enabled_by_default(self):
        from app.core.config import Settings

        assert Settings().code_execution_enabled is True

    async def test_disabled_refuses_with_a_clear_error(self):
        """A model can still emit a call for a tool it saw earlier in the
        conversation, so the executor itself must refuse rather than rely on
        the tool simply being absent."""
        with patch("app.services.code_execution.settings") as s:
            s.code_execution_enabled = False
            s.code_execution_timeout = 120
            result = await execute_code("print('hi')")

        assert result["result"] is None
        assert "disabled" in result["error"].lower()
        # Shaped like a normal result so the agent loop handles it uniformly
        assert set(result) >= {"stdout", "stderr", "result", "duration_ms", "error"}

    async def test_function_execution_is_refused(self):
        """execute_function is the single choke point for every trigger type
        (API, webhook, schedule, agent tool, CDC)."""
        from app.services.execution_engine import FunctionExecutionError, executor

        with patch("app.services.execution_engine.settings") as s:
            s.code_execution_enabled = False
            with pytest.raises(FunctionExecutionError, match="disabled"):
                await executor.execute_function(
                    function_namespace="default",
                    function_name="whatever",
                    input_data={},
                    execution_id="e1",
                    trigger_type="api",
                    trigger_id="t1",
                    user_id="u1",
                )


class TestBuiltinDatabaseToggle:
    def test_enabled_by_default(self):
        from app.core.config import Settings

        assert Settings().builtin_database_enabled is True

    async def test_disabled_skips_creation_without_touching_anything(self):
        """Only creation is skipped: no connection is opened, so an existing
        sinas_data database and its record are left intact and the toggle is
        reversible."""
        from app.scheduler import service as scheduler_service

        with patch.object(scheduler_service, "settings") as s:
            s.builtin_database_enabled = False
            with patch.object(scheduler_service, "asyncpg") as pg:
                await scheduler_service._initialize_builtin_database()
                pg.connect.assert_not_called()
