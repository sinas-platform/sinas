"""Code execution service for agent sandbox code execution.

Allows agents with 'codeExecution' in their system_tools list to generate
and run Python code in pooled sandbox containers (untrusted execution).
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Tool definition exposed to LLMs
_BASE_DESCRIPTION = (
    "Execute Python code in a sandboxed environment. Use this to run calculations, "
    "process data, test algorithms, or perform any computational task. The code runs "
    "in an isolated container with internet access. "
    "To access user-uploaded files, fetch them by URL using urllib.request or requests. "
    "The last expression in your code will be captured as the result."
)

_CODE_EXECUTION_TOOL_TEMPLATE = {
    "type": "function",
    "function": {
        "name": "execute_code",
        "description": _BASE_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute. The last expression's value is captured as the result.",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what this code does (for logging/display).",
                },
            },
            "required": ["code"],
        },
    },
}


async def get_tool_definition(db) -> dict[str, Any]:
    """Return the OpenAI-format tool definition for code execution.

    Dynamically injects the list of approved packages so the LLM knows
    exactly what is available.
    """
    from sqlalchemy import select
    from app.models.dependency import Dependency

    import copy
    tool = copy.deepcopy(_CODE_EXECUTION_TOOL_TEMPLATE)

    try:
        result = await db.execute(select(Dependency.package_name))
        package_names = [row[0] for row in result.all()]
    except Exception:
        package_names = []

    if package_names:
        pkg_list = ", ".join(sorted(package_names))
        tool["function"]["description"] = (
            _BASE_DESCRIPTION
            + f" Installed packages: {pkg_list}."
        )
    else:
        tool["function"]["description"] = (
            _BASE_DESCRIPTION
            + " Only the Python standard library is available."
        )

    return tool


async def execute(
    code: str,
    timeout: Optional[int] = None,
    user_id: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Execute Python code in a sandbox container (untrusted execution).

    Routes to the configured sandbox executor:
    - docker_pool: a reusable container from the warm pool.
    - docker_ephemeral: a fresh single-use container from the baked image.
    - k8s_pod: a fresh single-use Kubernetes pod.

    Returns:
        {"stdout": str, "stderr": str, "result": any, "duration_ms": int}
    """
    effective_timeout = timeout or settings.code_execution_timeout
    execution_id = str(uuid.uuid4())

    # Metering leaf: one op per codeExecution tool call
    from app.services import metering

    await metering.record(metering.OperationKind.CODE)

    if not settings.code_execution_enabled:
        # Belt and braces: the tool isn't advertised when disabled, but a model
        # can still emit a call for a tool it saw earlier in the conversation.
        return {
            "stdout": "",
            "stderr": "Code execution is disabled on this deployment.",
            "result": None,
            "duration_ms": 0,
            "error": "Code execution is disabled on this deployment.",
        }
    start_time = time.time()

    # Workbench sync (copy-in): when the chat's agent opted into the
    # workbench, materialize its tree into this execution and persist
    # changes back afterwards (see _finalize_workbench below).
    workbench_input: dict[str, Any] = {}
    workbench_skipped_in: list[dict[str, Any]] = []
    workbench_chat_id: Optional[str] = None
    if chat_id:
        from sqlalchemy import select as _select

        from app.core.database import AsyncSessionLocal as _SessionLocal
        from app.models.chat import Chat as _Chat
        from app.services import workbench as workbench_service

        try:
            async with _SessionLocal() as _db:
                _chat = (
                    await _db.execute(_select(_Chat).where(_Chat.id == chat_id))
                ).scalar_one_or_none()
                if _chat and await workbench_service.chat_has_workbench_enabled(_db, _chat):
                    manifest = await workbench_service.build_sync_manifest(_db, _chat)
                    await _db.commit()  # get_or_create may have inserted the workbench row
                    workbench_chat_id = str(_chat.id)
                    workbench_skipped_in = manifest["skipped"]
                    workbench_input = {
                        "workbench_files": manifest["files"],
                        "workbench_limits": {
                            "max_file_bytes": settings.workbench_sync_max_file_bytes,
                            "max_total_bytes": settings.workbench_sync_max_total_bytes,
                        },
                    }
        except Exception as e:
            logger.error(f"Workbench copy-in failed for chat {chat_id}: {e}")

    async def _finalize_workbench(res: dict[str, Any]) -> dict[str, Any]:
        """Copy-out: persist created/changed files, strip the blob payload."""
        if workbench_chat_id is None:
            return res
        inner = res.get("result")
        if not isinstance(inner, dict):
            return res
        changes = inner.pop("workbench_changes", None)
        return_skipped = inner.pop("workbench_return_skipped", None)
        info: dict[str, Any] = {}
        if changes:
            try:
                async with _SessionLocal() as _db:
                    _chat = (
                        await _db.execute(_select(_Chat).where(_Chat.id == workbench_chat_id))
                    ).scalar_one_or_none()
                    if _chat:
                        sync = await workbench_service.apply_sync_changes(
                            _db, _chat, str(_chat.user_id), changes
                        )
                        await _db.commit()
                        info["synced"] = sync["synced"]
                        if sync["rejected"]:
                            info["rejected"] = sync["rejected"]
            except Exception as e:
                logger.error(f"Workbench copy-out failed for chat {workbench_chat_id}: {e}")
                info["error"] = f"Failed to persist workbench changes: {e}"
        if workbench_skipped_in:
            info["not_materialized"] = workbench_skipped_in
        if return_skipped:
            info["not_synced_back"] = return_skipped
        if info:
            res["workbench"] = info
        return res

    # Wrap the user code so we capture stdout and the last expression result.
    wrapper_code = _build_wrapper(code, workbench=workbench_chat_id is not None)

    payload = {
        "action": "execute_inline",
        "function_code": wrapper_code,
        "execution_id": execution_id,
        "function_namespace": "_code_execution",
        "function_name": "handler",
        "timeout": effective_timeout,
        "input_data": workbench_input,
        "context": {
            "user_id": user_id or "",
            "user_email": "",
            "access_token": "",
            "execution_id": execution_id,
            "trigger_type": "code_execution",
            "chat_id": chat_id or "",
        },
    }

    def _error(exc: Exception) -> dict[str, Any]:
        logger.error(f"Code execution error: {exc}")
        return {
            "stdout": "",
            "stderr": str(exc),
            "result": None,
            "duration_ms": int((time.time() - start_time) * 1000),
            "error": str(exc),
        }

    if settings.sandbox_executor == "disabled":
        return _error(
            RuntimeError(
                "Code execution is disabled on this deployment (SANDBOX_EXECUTOR=disabled)."
            )
        )

    # k8s: a fresh single-use pod per execution (no pool).
    if settings.sandbox_executor == "k8s_pod":
        from app.core.database import AsyncSessionLocal
        from app.services.executor._k8s_runtime import (
            create_sandbox_pod,
            delete_sandbox_pod,
            run_payload_in_pod,
        )

        try:
            async with AsyncSessionLocal() as db:
                name, namespace = await create_sandbox_pod(
                    db, execution_id=execution_id
                )
            try:
                wire = await run_payload_in_pod(
                    name, namespace, payload, effective_timeout
                )
                return await _finalize_workbench(
                    _shape_code_result(wire, int((time.time() - start_time) * 1000))
                )
            finally:
                await delete_sandbox_pod(name, namespace)
        except Exception as e:
            return _error(e)

    # Ephemeral: a fresh single-use container from the baked image (no pool).
    if settings.sandbox_executor == "docker_ephemeral":
        from app.core.database import AsyncSessionLocal
        from app.services.executor._ephemeral_runtime import (
            create_ephemeral_container,
            remove_ephemeral_container,
        )

        try:
            async with AsyncSessionLocal() as db:
                _client, container, name = await create_ephemeral_container(
                    db, execution_id=execution_id
                )
            try:
                return await _finalize_workbench(
                    await _run_code_payload(
                        container, payload, execution_id, effective_timeout, start_time
                    )
                )
            finally:
                await remove_ephemeral_container(container, name)
        except Exception as e:
            return _error(e)

    # Default: a reusable container from the warm pool.
    from app.services.container_pool import container_pool

    pc = await container_pool.acquire()
    logger.info(f"Acquired sandbox container {pc.name} for code execution (chat={chat_id})")
    tainted = False
    try:
        container = await asyncio.to_thread(container_pool.client.containers.get, pc.name)
        result = await _run_code_payload(
            container, payload, execution_id, effective_timeout, start_time
        )
        # Discard the container if the run errored (avoid handing leaked state on).
        if result.get("error") is not None:
            tainted = True
        return await _finalize_workbench(result)
    except Exception as e:
        tainted = True
        return _error(e)
    finally:
        await asyncio.to_thread(container_pool.release, pc.name, tainted=tainted)


async def _run_code_payload(
    container,
    payload: dict[str, Any],
    execution_id: str,
    effective_timeout: int,
    start_time: float,
) -> dict[str, Any]:
    """Run a code-execution payload in `container` and parse the result.

    Shared by the pooled and ephemeral code-execution paths; only the container
    lifecycle around this call differs. Raises on Docker/infra errors (callers
    convert those to an error result).
    """
    import socket as _sock_mod

    eid = execution_id
    request_path = f"/tmp/exec_request_{eid}.json"
    trigger_file = f"/tmp/exec_trigger_{eid}"
    result_file = f"/tmp/exec_result_{eid}.json"

    # Write the request into the container via a stdin pipe.
    payload_bytes = json.dumps(payload).encode("utf-8")
    api = container.client.api
    exec_id = api.exec_create(
        container.id,
        [
            "python3", "-c",
            f'import sys; open("{request_path}","wb").write(sys.stdin.buffer.read())',
        ],
        stdin=True,
        stdout=True,
        stderr=True,
    )["Id"]
    sock = api.exec_start(exec_id, socket=True)
    sock._sock.sendall(payload_bytes)
    sock._sock.shutdown(_sock_mod.SHUT_WR)
    sock.read()
    sock.close()

    exec_result = await asyncio.to_thread(
        container.exec_run,
        cmd=[
            "python3",
            "-c",
            f"""
import sys, json, time, os
with open("{trigger_file}", "w") as f:
    f.write("1")
max_wait = {effective_timeout}
start = time.time()
while time.time() - start < max_wait:
    if os.path.exists("{result_file}"):
        with open("{result_file}", "r") as f:
            data = json.load(f)
        os.remove("{result_file}")
        print(json.dumps(data))
        sys.exit(0)
    time.sleep(0.1)
print(json.dumps({{"status": "failed", "error": "Execution timed out"}}))
sys.exit(1)
""",
        ],
        stdout=True,
        stderr=True,
    )

    duration_ms = int((time.time() - start_time) * 1000)

    if exec_result.exit_code != 0:
        stderr_output = exec_result.output.decode("utf-8", errors="replace") if exec_result.output else ""
        return {
            "stdout": "",
            "stderr": stderr_output,
            "result": None,
            "duration_ms": duration_ms,
            "error": "Code execution failed",
        }

    raw_output = exec_result.output.decode("utf-8", errors="replace") if exec_result.output else "{}"
    try:
        result_data = json.loads(raw_output.strip())
    except json.JSONDecodeError:
        return {
            "stdout": raw_output,
            "stderr": "",
            "result": None,
            "duration_ms": duration_ms,
        }

    return _shape_code_result(result_data, duration_ms)


def _shape_code_result(
    result_data: dict[str, Any], duration_ms: int
) -> dict[str, Any]:
    """Map a wire result dict to the code-execution tool's result shape."""
    if result_data.get("status") == "failed":
        return {
            "stdout": result_data.get("stdout", ""),
            "stderr": result_data.get("stderr", result_data.get("error", "")),
            "result": None,
            "duration_ms": duration_ms,
            "error": result_data.get("error", "Execution failed"),
        }

    return {
        "stdout": result_data.get("stdout", ""),
        "stderr": result_data.get("stderr", ""),
        "result": result_data.get("result"),
        "duration_ms": duration_ms,
    }


def _build_wrapper(user_code: str, workbench: bool = False) -> str:
    """Wrap user code to capture stdout and the last expression result.

    With workbench=True the wrapper materializes the chat's workbench files
    (passed via input_data) into a fresh temp directory, chdirs into it for
    the user code, and afterwards reports created/changed files back (by
    sha256 diff) as output["workbench_changes"] for the backend to persist.
    The temp tree is always removed — pooled containers must not leak one
    chat's files into the next execution.
    """
    # The wrapper captures stdout and evaluates the code, trying to capture
    # the last expression as a result value.
    user_code_repr = repr(user_code)
    workbench_setup = ""
    workbench_teardown = ""
    if workbench:
        workbench_setup = '''
    import os as _os, base64 as _b64, hashlib as _hashlib, shutil as _shutil, tempfile as _tempfile
    _wb_files = (input_data or {}).get("workbench_files") or []
    _wb_limits = (input_data or {}).get("workbench_limits") or {}
    _wb_hashes = {}
    _wb_root = _tempfile.mkdtemp(prefix="workbench_")
    _wb_old_cwd = _os.getcwd()
    for _f in _wb_files:
        _p = _os.path.join(_wb_root, _f["path"])
        _os.makedirs(_os.path.dirname(_p), exist_ok=True)
        with open(_p, "wb") as _fh:
            _fh.write(_b64.b64decode(_f["content_b64"]))
        _wb_hashes[_f["path"]] = _f["sha256"]
    _os.chdir(_wb_root)
'''
        workbench_teardown = '''
    _wb_changes = []
    _wb_return_skipped = []
    try:
        _max_file = int(_wb_limits.get("max_file_bytes") or 2 * 1024 * 1024)
        _max_total = int(_wb_limits.get("max_total_bytes") or 32 * 1024 * 1024)
        _total = 0
        for _dirpath, _dirnames, _filenames in _os.walk(_wb_root):
            _dirnames[:] = [_d for _d in _dirnames if not _d.startswith(".")]
            for _fn in _filenames:
                _full = _os.path.join(_dirpath, _fn)
                _rel = _os.path.relpath(_full, _wb_root).replace(_os.sep, "/")
                if _os.path.islink(_full):
                    continue
                _size = _os.path.getsize(_full)
                if _size > _max_file or _total + _size > _max_total:
                    _wb_return_skipped.append({"path": _rel, "size_bytes": _size})
                    continue
                with open(_full, "rb") as _fh:
                    _data = _fh.read()
                _digest = _hashlib.sha256(_data).hexdigest()
                if _wb_hashes.get(_rel) == _digest:
                    continue
                _total += _size
                _wb_changes.append({"path": _rel, "content_b64": _b64.b64encode(_data).decode()})
    except Exception as _wb_exc:
        _wb_return_skipped.append({"path": "<walk>", "error": str(_wb_exc)})
    finally:
        try:
            _os.chdir(_wb_old_cwd)
        except Exception:
            _os.chdir("/tmp")
        _shutil.rmtree(_wb_root, ignore_errors=True)
    output["workbench_changes"] = _wb_changes
    if _wb_return_skipped:
        output["workbench_return_skipped"] = _wb_return_skipped
'''
    return f'''
import sys, io, json, traceback

def handler(input_data, context):
    """Wrapper that executes user code and captures output."""
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    sys.stdout = captured_stdout
    sys.stderr = captured_stderr

    result = None
    error = None
{workbench_setup}
    try:
        user_code = {user_code_repr}
        # Try to split into statements and eval the last one for a result
        import ast
        try:
            tree = ast.parse(user_code)
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                # Last statement is an expression — eval it separately
                last_expr = ast.Expression(tree.body[-1].value)
                module = ast.Module(body=tree.body[:-1], type_ignores=[])
                exec(compile(module, "<code>", "exec"))
                result = eval(compile(last_expr, "<code>", "eval"))
            else:
                exec(compile(tree, "<code>", "exec"))
        except SyntaxError:
            exec(user_code)
    except Exception as e:
        error = traceback.format_exc()

    sys.stdout = old_stdout
    sys.stderr = old_stderr

    output = {{
        "stdout": captured_stdout.getvalue(),
        "stderr": captured_stderr.getvalue(),
    }}
{workbench_teardown}
    if error:
        output["error"] = error
        output["status"] = "failed"
    else:
        output["status"] = "completed"
        # Try to serialize result
        try:
            json.dumps(result)
            output["result"] = result
        except (TypeError, ValueError):
            output["result"] = repr(result) if result is not None else None

    return output
'''
