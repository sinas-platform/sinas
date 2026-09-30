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

    # Wrap the user code so we capture stdout and the last expression result.
    wrapper_code = _build_wrapper(code)

    payload = {
        "action": "execute_inline",
        "function_code": wrapper_code,
        "execution_id": execution_id,
        "function_namespace": "_code_execution",
        "function_name": "handler",
        "timeout": effective_timeout,
        "input_data": {},
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
                return _shape_code_result(
                    wire, int((time.time() - start_time) * 1000)
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
                return await _run_code_payload(
                    container, payload, execution_id, effective_timeout, start_time
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
        return result
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


def _build_wrapper(user_code: str) -> str:
    """Wrap user code to capture stdout and the last expression result."""
    # The wrapper captures stdout and evaluates the code, trying to capture
    # the last expression as a result value.
    user_code_repr = repr(user_code)
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
