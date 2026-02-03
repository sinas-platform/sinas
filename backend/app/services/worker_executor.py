"""Worker executor - runs inside worker containers to execute functions."""
import json
import time
import traceback
from typing import Any


def execute_function_in_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Execute function inside worker container.

    This runs in a separate worker container process.
    """
    import uuid as uuid_module
    from datetime import datetime

    execution_id = payload["execution_id"]
    function_namespace = payload["function_namespace"]
    function_name = payload["function_name"]
    enabled_namespaces = payload.get("enabled_namespaces", [])
    input_data = payload["input_data"]

    start_time = time.time()

    try:
        # Load function from database
        from app.core.database import sync_session_maker
        from app.models.function import Function

        with sync_session_maker() as db:
            # Get function
            function = (
                db.query(Function)
                .filter(Function.namespace == function_namespace, Function.name == function_name)
                .first()
            )

            if not function:
                raise Exception(f"Function {function_namespace}/{function_name} not found")

            # Build namespace (no tracking in workers for simplicity)
            namespace = {
                "__builtins__": __builtins__,
                "json": json,
                "datetime": datetime,
                "uuid": uuid_module,
            }

            # Compile and execute function code
            compiled_code = compile(
                function.code, f"<function:{function_namespace}/{function_name}>", "exec"
            )
            exec(compiled_code, namespace)

            # Find the function
            func = None
            for key, value in namespace.items():
                if key not in ["__builtins__", "json", "datetime", "uuid"] and callable(value):
                    func = value
                    break

            if not func:
                raise Exception(f"Function {function_name} not found in code")

            # Execute function
            result = func(input_data)

            duration_ms = int((time.time() - start_time) * 1000)

            return {"status": "success", "result": result, "duration_ms": duration_ms}

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return {
            "status": "failed",
            "error": str(e),
            "traceback": traceback.format_exc(),
            "duration_ms": duration_ms,
        }
