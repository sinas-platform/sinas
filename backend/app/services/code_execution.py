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
    Execute Python code in a sandbox container.

    Uses the container_pool (untrusted execution) — LLM-generated code
    must run in isolated, capability-dropped containers.

    Returns:
        {"stdout": str, "stderr": str, "result": any, "duration_ms": int}
    """
    from app.services.container_pool import container_pool

    effective_timeout = timeout or settings.code_execution_timeout
    execution_id = str(uuid.uuid4())
    start_time = time.time()

    # Wrap the user code so we capture the result of the last expression
    # We use a wrapper that exec's the code and captures the last expression
    wrapper_code = _build_wrapper(code)

    # Acquire a container from the pool
    pc = await container_pool.acquire()
    logger.info(f"Acquired sandbox container {pc.name} for code execution (chat={chat_id})")

    tainted = False
    try:
        container = await asyncio.to_thread(container_pool.client.containers.get, pc.name)

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

        # Per-execution file paths
        eid = execution_id
        request_filename = f"exec_request_{eid}.json"
        trigger_file = f"/tmp/exec_trigger_{eid}"
        result_file = f"/tmp/exec_result_{eid}.json"

        # Write payload into the container via stdin pipe
        payload_bytes = json.dumps(payload).encode("utf-8")
        request_path = f"/tmp/{request_filename}"
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
        import socket as _sock_mod
        sock._sock.shutdown(_sock_mod.SHUT_WR)
        sock.read()
        sock.close()

        # Trigger execution and wait for result
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
            tainted = True
            stderr_output = exec_result.output.decode("utf-8", errors="replace") if exec_result.output else ""
            return {
                "stdout": "",
                "stderr": stderr_output,
                "result": None,
                "duration_ms": duration_ms,
                "error": "Code execution failed",
            }

        # Parse result
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

        if result_data.get("status") == "failed":
            tainted = True
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

    except Exception as e:
        tainted = True
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Code execution error: {e}")
        return {
            "stdout": "",
            "stderr": str(e),
            "result": None,
            "duration_ms": duration_ms,
            "error": str(e),
        }

    finally:
        await asyncio.to_thread(container_pool.release, pc.name, tainted=tainted)


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
