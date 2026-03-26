"""
Executor script that runs inside user containers.
This script loads functions and executes them on demand.

Supports two container modes (set via SINAS_CONTAINER_MODE env var):
  - shared: Threaded execution with input() support for pause/resume
  - sandbox: Single-threaded SIGALRM timeout, input() raises RuntimeError
"""
import ctypes
import glob
import json
import os
import signal
import socket
import sys
import tempfile
import threading
import time
import traceback
from typing import Any


# Detect container mode from environment
CONTAINER_MODE = os.environ.get("SINAS_CONTAINER_MODE", "sandbox")
IS_SHARED = CONTAINER_MODE == "shared"

# How long input() waits for a resume file before timing out (seconds).
# This is a safety net — the function-level timeout should fire first.
INPUT_POLL_TIMEOUT = 600


class FunctionTimeoutError(Exception):
    """Raised when a function exceeds its execution timeout."""
    pass


def _timeout_handler(signum, frame):
    raise FunctionTimeoutError("Function execution timed out")


def _raise_in_thread(thread_id: int, exc_type: type) -> None:
    """Asynchronously raise an exception in a thread (CPython only)."""
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(thread_id), ctypes.py_object(exc_type)
    )
    if res == 0:
        pass  # Thread already dead
    elif res > 1:
        # Revert — something went wrong
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), None)


def _atomic_write_json(path: str, data: Any) -> None:
    """Write JSON atomically via rename to prevent partial reads."""
    dir_name = os.path.dirname(path) or "/tmp"
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, default=str)
        os.rename(tmp_path, path)
    except Exception as write_err:
        print(f"[exec] Failed to write result JSON: {write_err}", file=sys.stderr)
        # Write a minimal error result so the polling script finds something
        try:
            with open(path, "w") as f:
                json.dump({"status": "failed", "error": f"Result serialization failed: {write_err}"}, f)
        except Exception:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _make_custom_input(execution_id: str, result_path: str):
    """
    Create a custom input() replacement for shared containers.

    When called:
    1. Deletes the result file (clears any previous state)
    2. Writes {"status": "awaiting_input", "prompt": prompt} to result file
    3. Polls for /tmp/exec_resume_{eid}.json
    4. Returns the resume value as a string
    """
    def custom_input(prompt=""):
        resume_file = f"/tmp/exec_resume_{execution_id}.json"

        # Clear any previous result so the backend polls for a fresh one
        try:
            os.remove(result_path)
        except OSError:
            pass

        # Signal that we're waiting for input
        _atomic_write_json(result_path, {
            "status": "awaiting_input",
            "prompt": str(prompt),
            "execution_id": execution_id,
        })

        print(f"[exec] Awaiting input: {prompt}", file=sys.stderr)

        # Poll for resume file
        deadline = time.time() + INPUT_POLL_TIMEOUT
        while time.time() < deadline:
            if os.path.exists(resume_file):
                try:
                    with open(resume_file, "r") as f:
                        resume_data = json.load(f)
                    os.remove(resume_file)
                    value = resume_data.get("value", "")
                    print(f"[exec] Resumed with input value", file=sys.stderr)
                    return str(value)
                except (json.JSONDecodeError, OSError):
                    # File might be partially written, retry
                    time.sleep(0.1)
                    continue
            time.sleep(0.2)

        raise TimeoutError(f"input() timed out after {INPUT_POLL_TIMEOUT}s waiting for resume")

    return custom_input


def _make_sandbox_input():
    """Create an input() replacement that always raises for sandbox containers."""
    def sandbox_input(prompt=""):
        raise RuntimeError("input() is not available in sandbox mode")
    return sandbox_input


class ContainerExecutor:
    def __init__(self):
        self.namespace = {
            "__builtins__": __builtins__,
            "json": json,
        }
        # Map from "namespace/name" to actual function name in code
        self.function_map = {}
        # Import common modules
        try:
            import datetime
            import uuid

            self.namespace["datetime"] = datetime
            self.namespace["uuid"] = uuid
        except ImportError:
            pass

    def load_functions(self, functions_data: dict[str, dict[str, Any]]):
        """Load functions into namespace, organized by namespace."""
        for namespace, functions in functions_data.items():
            for name, func_data in functions.items():
                try:
                    code = func_data["code"]
                    full_name = f"{namespace}/{name}"

                    # Compile and execute function in namespace
                    compiled_code = compile(code, f"<function:{full_name}>", "exec")
                    exec(compiled_code, self.namespace)

                    # Store mapping from "namespace/name" to actual function name
                    self.function_map[full_name] = name

                    print(f"Loaded function: {full_name} -> {name}", file=sys.stderr)
                except Exception as e:
                    print(f"Error loading function {namespace}/{name}: {e}", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)

    def execute_function(
        self,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        context: dict[str, Any] = None,
    ) -> dict[str, Any]:
        """Execute a function from the namespace."""
        try:
            # Map from "namespace/name" to actual function name in code
            actual_name = self.function_map.get(function_name, function_name)

            if actual_name not in self.namespace:
                return {
                    "error": f"Function '{function_name}' not found in namespace (mapped to '{actual_name}')",
                    "execution_id": execution_id,
                }

            func = self.namespace[actual_name]

            # Execute function with input and context
            start_time = time.time()
            result = func(input_data, context or {})
            duration_ms = int((time.time() - start_time) * 1000)

            return {
                "result": result,
                "execution_id": execution_id,
                "duration_ms": duration_ms,
                "status": "completed",
            }

        except Exception as e:
            return {
                "error": str(e),
                "traceback": traceback.format_exc(),
                "execution_id": execution_id,
                "status": "failed",
            }

    def _execute_inline_shared(self, request: dict, result_path: str):
        """Execute inline function in a thread (shared container mode)."""
        function_timeout = request.get("timeout", 290)
        function_code = request["function_code"]
        function_namespace = request.get("function_namespace", "default")
        function_name = request["function_name"]
        input_data = request["input_data"]
        context = request.get("context", {})
        execution_id = request["execution_id"]

        print(f"[exec] Starting {function_namespace}/{function_name} (shared, timeout={function_timeout}s)", file=sys.stderr)

        socket.setdefaulttimeout(min(function_timeout, 30))

        # Build namespace with custom input()
        temp_namespace = {
            "__builtins__": __builtins__,
            "json": json,
            "input": _make_custom_input(execution_id, result_path),
        }
        try:
            import datetime
            import uuid
            temp_namespace["datetime"] = datetime
            temp_namespace["uuid"] = uuid
        except ImportError:
            pass

        # Compile and execute
        compiled_code = compile(
            function_code,
            f"<function:{function_namespace}/{function_name}>",
            "exec",
        )
        exec(compiled_code, temp_namespace)

        # Find entry point
        if "handler" in temp_namespace and callable(temp_namespace["handler"]):
            func = temp_namespace["handler"]
        elif function_name in temp_namespace and callable(temp_namespace[function_name]):
            func = temp_namespace[function_name]
            print(f"[exec] DEPRECATION: function uses '{function_name}' instead of 'handler'", file=sys.stderr)
        else:
            _atomic_write_json(result_path, {
                "error": f"No 'handler' function found in code for {function_namespace}/{function_name}",
                "execution_id": execution_id,
                "status": "failed",
            })
            return

        # Run in thread with timeout via threading.Timer
        thread_result = {}
        thread_error = {}

        def _run():
            try:
                r = func(input_data, context)
                thread_result["value"] = r
            except FunctionTimeoutError:
                thread_error["timeout"] = True
            except Exception as e:
                thread_error["error"] = str(e)
                thread_error["traceback"] = traceback.format_exc()

        t = threading.Thread(target=_run, daemon=True)
        start_time = time.time()
        t.start()

        # Timeout enforcement via async exception
        timer = threading.Timer(function_timeout, lambda: _raise_in_thread(t.ident, FunctionTimeoutError))
        timer.daemon = True
        timer.start()

        t.join(timeout=function_timeout + 5)
        timer.cancel()

        duration_ms = int((time.time() - start_time) * 1000)

        # Check if thread wrote an awaiting_input result (input() was called)
        # In that case, the result file already has the awaiting_input status
        # and we should NOT overwrite it.
        try:
            with open(result_path, "r") as f:
                existing = json.load(f)
            if existing.get("status") == "awaiting_input":
                # Function is paused — don't write final result yet
                print(f"[exec] Paused {function_namespace}/{function_name} (awaiting input) at {duration_ms}ms", file=sys.stderr)
                return
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        if thread_error.get("timeout") or (t.is_alive()):
            print(f"[exec] TIMEOUT {function_namespace}/{function_name} after {duration_ms}ms", file=sys.stderr)
            _atomic_write_json(result_path, {
                "error": f"Function timed out after {function_timeout}s",
                "execution_id": execution_id,
                "duration_ms": duration_ms,
                "status": "failed",
            })
        elif "error" in thread_error:
            print(f"[exec] FAILED {function_namespace}/{function_name}: {thread_error['error']}", file=sys.stderr)
            _atomic_write_json(result_path, {
                "error": thread_error["error"],
                "traceback": thread_error.get("traceback", ""),
                "execution_id": execution_id,
                "duration_ms": duration_ms,
                "status": "failed",
            })
        else:
            print(f"[exec] Completed {function_namespace}/{function_name} in {duration_ms}ms", file=sys.stderr)
            _atomic_write_json(result_path, {
                "result": thread_result.get("value"),
                "execution_id": execution_id,
                "duration_ms": duration_ms,
                "status": "completed",
            })

    def _execute_inline_sandbox(self, request: dict, result_path: str):
        """Execute inline function with SIGALRM timeout (sandbox container mode)."""
        function_timeout = request.get("timeout", 290)
        function_code = request["function_code"]
        function_namespace = request.get("function_namespace", "default")
        function_name = request["function_name"]
        input_data = request["input_data"]
        context = request.get("context", {})
        execution_id = request["execution_id"]

        print(f"[exec] Starting {function_namespace}/{function_name} (sandbox, timeout={function_timeout}s)", file=sys.stderr)

        socket.setdefaulttimeout(min(function_timeout, 30))

        # Build namespace with blocking input()
        temp_namespace = {
            "__builtins__": __builtins__,
            "json": json,
            "input": _make_sandbox_input(),
        }
        try:
            import datetime
            import uuid
            temp_namespace["datetime"] = datetime
            temp_namespace["uuid"] = uuid
        except ImportError:
            pass

        compiled_code = compile(
            function_code,
            f"<function:{function_namespace}/{function_name}>",
            "exec",
        )
        exec(compiled_code, temp_namespace)

        # Find entry point
        if "handler" in temp_namespace and callable(temp_namespace["handler"]):
            func = temp_namespace["handler"]
        elif function_name in temp_namespace and callable(temp_namespace[function_name]):
            func = temp_namespace[function_name]
            print(f"[exec] DEPRECATION: function uses '{function_name}' instead of 'handler'", file=sys.stderr)
        else:
            _atomic_write_json(result_path, {
                "error": f"No 'handler' function found in code for {function_namespace}/{function_name}",
                "execution_id": execution_id,
                "status": "failed",
            })
            return

        # Execute with SIGALRM timeout
        start_time = time.time()
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(function_timeout)
        try:
            func_result = func(input_data, context)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
        duration_ms = int((time.time() - start_time) * 1000)

        print(f"[exec] Completed {function_namespace}/{function_name} in {duration_ms}ms", file=sys.stderr)
        _atomic_write_json(result_path, {
            "result": func_result,
            "execution_id": execution_id,
            "duration_ms": duration_ms,
            "status": "completed",
        })

    def run(self):
        """Main loop - wait for execution requests."""
        print(f"Container executor started (mode={CONTAINER_MODE})", file=sys.stderr)

        # Load initial functions if available
        try:
            with open("/tmp/functions.json") as f:
                payload = json.load(f)
                if payload.get("action") == "load_functions":
                    self.load_functions(payload["functions"])
        except FileNotFoundError:
            print("No initial functions to load", file=sys.stderr)
        except Exception as e:
            print(f"Error loading initial functions: {e}", file=sys.stderr)

        # Main execution loop
        while True:
            try:
                # Scan for trigger files: /tmp/exec_trigger_{eid}
                # Also support legacy /tmp/exec_trigger for backward compat
                triggers = glob.glob("/tmp/exec_trigger_*") + glob.glob("/tmp/exec_trigger")

                if not triggers:
                    time.sleep(0.1)
                    continue

                trigger_path = triggers[0]
                trigger_name = os.path.basename(trigger_path)

                # Extract execution ID suffix (exec_trigger_{eid} -> eid, or "" for legacy)
                if trigger_name == "exec_trigger":
                    suffix = ""
                    request_path = "/tmp/exec_request.json"
                    result_path = "/tmp/exec_result.json"
                else:
                    suffix = trigger_name.replace("exec_trigger_", "")
                    request_path = f"/tmp/exec_request_{suffix}.json"
                    result_path = f"/tmp/exec_result_{suffix}.json"

                try:
                    with open(request_path) as f:
                        request = json.load(f)
                except FileNotFoundError:
                    # Trigger without request — stale, clean up
                    try:
                        os.remove(trigger_path)
                    except OSError:
                        pass
                    continue

                action = request.get("action")

                if action == "execute":
                    function_namespace = request.get("function_namespace", "default")
                    function_name = request["function_name"]
                    full_function_name = f"{function_namespace}/{function_name}"

                    result = self.execute_function(
                        full_function_name,
                        request["input_data"],
                        request["execution_id"],
                        request.get("context", {}),
                    )

                    with open(result_path, "w") as f:
                        json.dump(result, f)

                elif action == "load_functions":
                    self.load_functions(request["functions"])
                    with open(result_path, "w") as f:
                        json.dump({"status": "loaded"}, f)

                elif action == "execute_inline":
                    try:
                        if IS_SHARED:
                            self._execute_inline_shared(request, result_path)
                        else:
                            self._execute_inline_sandbox(request, result_path)

                    except FunctionTimeoutError:
                        execution_id = request.get("execution_id", "")
                        function_timeout = request.get("timeout", 290)
                        start_time_fallback = time.time()
                        print(f"[exec] TIMEOUT (outer) after {function_timeout}s", file=sys.stderr)
                        _atomic_write_json(result_path, {
                            "error": f"Function timed out after {function_timeout}s",
                            "execution_id": execution_id,
                            "status": "failed",
                        })

                    except Exception as e:
                        execution_id = request.get("execution_id", "")
                        print(f"[exec] FAILED (outer): {e}", file=sys.stderr)
                        _atomic_write_json(result_path, {
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                            "execution_id": execution_id,
                            "status": "failed",
                        })

                # Clean up request and trigger files
                try:
                    os.remove(request_path)
                except OSError:
                    pass
                try:
                    os.remove(trigger_path)
                except OSError:
                    pass

            except KeyboardInterrupt:
                print("Executor shutting down", file=sys.stderr)
                break
            except Exception as e:
                print(f"Error in executor loop: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                time.sleep(0.1)


if __name__ == "__main__":
    executor = ContainerExecutor()
    executor.run()
