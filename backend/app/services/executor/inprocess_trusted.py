"""TrustedExecutor that runs admin-approved code in the calling process.

Step 4 of the executor refactor: with `trusted_executor="inprocess"` (and
`sandbox_executor` set to a non-Docker backend or "disabled"), no process in
the deployment needs a Docker socket — this is what enables single-container
and k8s deployments.

Contract parity with the in-container executor's shared mode
(`container_executor._execute_inline_shared`): code is compiled and exec'd
into a bare namespace (json/datetime/uuid pre-imported), the entry point is
`handler(input_data, context)` (legacy fallback: a callable named after the
function), and the timeout is enforced by raising an async exception into the
worker thread.

Deliberate differences:
- `input()` raises immediately — pause/resume HITL is not available on this
  executor (durable HITL is issue #79); `resume()` always fails.
- The code shares the worker process's environment and credentials. Routing
  here carries the same trust decision an admin makes for `shared_pool=True`
  today, minus the container boundary — deployments that require credential
  isolation from trusted code (e.g. metered managed hosts) must keep a
  container/pod-backed trusted executor.
"""

from __future__ import annotations

import asyncio
import ctypes
import datetime
import json
import logging
import threading
import time
import traceback as tb_module
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.executor.base import ExecutionResult, before_run
from app.services.executor.trusted_secrets import load_trusted_secrets

logger = logging.getLogger(__name__)


class _FunctionTimeout(Exception):
    pass


def _no_input(prompt: str = "") -> str:
    raise RuntimeError(
        "input() is not available on the inprocess trusted executor "
        "(pause/resume requires a container-backed executor; see issue #79)."
    )


def _raise_in_thread(thread_id: int, exc_type: type) -> None:
    ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(thread_id), ctypes.py_object(exc_type)
    )


def _run_handler(
    *,
    function_code: str,
    function_namespace: str,
    function_name: str,
    input_data: dict[str, Any],
    context: dict[str, Any],
    timeout: int,
) -> ExecutionResult:
    """Blocking: compile, locate the entry point, and run it in a watched thread.

    Runs inside `asyncio.to_thread`, so blocking on join() here holds one
    executor-pool thread for the duration — the same occupancy the docker
    exec round-trip had.
    """
    namespace: dict[str, Any] = {
        "__builtins__": __builtins__,
        "json": json,
        "datetime": datetime,
        "uuid": uuid,
        "input": _no_input,
    }

    try:
        compiled = compile(
            function_code,
            f"<function:{function_namespace}/{function_name}>",
            "exec",
        )
        exec(compiled, namespace)
    except Exception as e:
        return ExecutionResult.failed(
            f"Failed to load function code: {e}", tb_module.format_exc()
        )

    if "handler" in namespace and callable(namespace["handler"]):
        func = namespace["handler"]
    elif function_name in namespace and callable(namespace[function_name]):
        func = namespace[function_name]
        logger.warning(
            "DEPRECATION: function %s/%s uses '%s' instead of 'handler'",
            function_namespace, function_name, function_name,
        )
    else:
        return ExecutionResult.failed(
            f"No 'handler' function found in code for {function_namespace}/{function_name}"
        )

    outcome: dict[str, Any] = {}

    def _target() -> None:
        try:
            outcome["result"] = func(input_data, context)
        except _FunctionTimeout:
            outcome["timeout"] = True
        except Exception as e:
            outcome["error"] = str(e)
            outcome["traceback"] = tb_module.format_exc()

    thread = threading.Thread(target=_target, daemon=True)
    start = time.time()
    thread.start()
    timer = threading.Timer(
        timeout, lambda: _raise_in_thread(thread.ident, _FunctionTimeout)
    )
    timer.daemon = True
    timer.start()
    thread.join(timeout=timeout + 5)
    timer.cancel()
    duration_ms = int((time.time() - start) * 1000)

    if outcome.get("timeout") or thread.is_alive():
        return ExecutionResult.failed(f"Function timed out after {timeout}s")
    if "error" in outcome:
        return ExecutionResult.failed(outcome["error"], outcome.get("traceback"))

    result = outcome.get("result")
    # The container path implicitly enforced JSON-serializable results (the
    # result crossed a JSON wire); keep that contract.
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        return ExecutionResult.failed(
            f"Function returned a non-JSON-serializable result: {type(result).__name__}"
        )
    from app.services.executor.base import ResultStatus

    return ExecutionResult(
        ResultStatus.COMPLETED, result=result, duration_ms=duration_ms
    )


class InProcessTrustedExecutor:
    """Runs `Function.shared_pool=True` code in this process. Implements TrustedExecutor."""

    async def execute(
        self,
        *,
        user_id: str,
        user_email: str,
        access_token: str,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        chat_id: str | None,
        user_custom_fields: dict[str, Any] | None = None,
        db: AsyncSession,
        timeout: int,
    ) -> ExecutionResult:
        from app.models.function import Function

        async with before_run("Loading the function"):
            result = await db.execute(
                select(Function).where(
                    Function.namespace == function_namespace,
                    Function.name == function_name,
                    Function.is_active == True,
                )
            )
            function = result.scalar_one_or_none()
        if not function:
            return ExecutionResult.failed(
                f"Function {function_namespace}/{function_name} not found"
            )
        async with before_run("Loading secrets"):
            secrets = await load_trusted_secrets(db, user_id)

        # The same context the shared Docker workers and k8s pods build
        # (shared_worker_manager.execute_function, K8sSharedTrustedExecutor).
        context = {
            "user_id": user_id,
            "user_email": user_email,
            "user_custom_fields": user_custom_fields or {},
            "access_token": access_token,
            "execution_id": execution_id,
            "trigger_type": trigger_type,
            "chat_id": chat_id,
            "secrets": secrets,
        }
        return await asyncio.to_thread(
            _run_handler,
            function_code=function.code,
            function_namespace=function_namespace,
            function_name=function_name,
            input_data=input_data,
            context=context,
            timeout=timeout or settings.function_timeout,
        )

    async def resume(
        self,
        *,
        handle: str,
        resume_value: Any,
        execution_id: str,
        timeout: int,
    ) -> ExecutionResult:
        return ExecutionResult.failed(
            "The inprocess trusted executor does not support resume "
            "(input()/HITL is unavailable; see issue #79)."
        )
