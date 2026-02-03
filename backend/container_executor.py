"""
Executor script that runs inside user containers.
This script loads functions and executes them on demand.
"""
import json
import sys
import time
import traceback
from typing import Any


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
                    # The actual function name is extracted from the code (usually just 'name')
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

    def run(self):
        """Main loop - wait for execution requests."""
        print("Container executor started", file=sys.stderr)

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
                # Check for execution trigger
                try:
                    with open("/tmp/exec_trigger") as f:
                        f.read()

                    # Read execution request
                    with open("/tmp/exec_request.json") as f:
                        request = json.load(f)

                    action = request.get("action")

                    if action == "execute":
                        # Execute function
                        # Build full function name from namespace + name
                        function_namespace = request.get("function_namespace", "default")
                        function_name = request["function_name"]
                        full_function_name = f"{function_namespace}/{function_name}"

                        result = self.execute_function(
                            full_function_name,
                            request["input_data"],
                            request["execution_id"],
                            request.get("context", {}),
                        )

                        # Write result
                        with open("/tmp/exec_result.json", "w") as f:
                            json.dump(result, f)

                    elif action == "load_functions":
                        # Reload functions
                        self.load_functions(request["functions"])
                        with open("/tmp/exec_result.json", "w") as f:
                            json.dump({"status": "loaded"}, f)

                    elif action == "execute_inline":
                        # Execute function with inline code (no pre-loading required)
                        try:
                            function_code = request["function_code"]
                            function_namespace = request.get("function_namespace", "default")
                            function_name = request["function_name"]
                            input_data = request["input_data"]
                            context = request.get("context", {})
                            execution_id = request["execution_id"]

                            # Create temporary namespace for this execution
                            temp_namespace = {
                                "__builtins__": __builtins__,
                                "json": json,
                            }

                            # Add common modules
                            try:
                                import datetime
                                import uuid

                                temp_namespace["datetime"] = datetime
                                temp_namespace["uuid"] = uuid
                            except ImportError:
                                pass

                            # Compile and execute function code
                            compiled_code = compile(
                                function_code,
                                f"<function:{function_namespace}/{function_name}>",
                                "exec",
                            )
                            exec(compiled_code, temp_namespace)

                            # Find the function (usually same name as function_name)
                            if function_name in temp_namespace:
                                func = temp_namespace[function_name]
                            else:
                                # Try to find any callable that's not a built-in
                                func = None
                                for name, obj in temp_namespace.items():
                                    if callable(obj) and not name.startswith("_"):
                                        func = obj
                                        break

                                if not func:
                                    result = {
                                        "error": f"No callable function found in code for {function_namespace}/{function_name}",
                                        "execution_id": execution_id,
                                        "status": "failed",
                                    }
                                    with open("/tmp/exec_result.json", "w") as f:
                                        json.dump(result, f)
                                    continue

                            # Execute function
                            start_time = time.time()
                            func_result = func(input_data, context)
                            duration_ms = int((time.time() - start_time) * 1000)

                            result = {
                                "result": func_result,
                                "execution_id": execution_id,
                                "duration_ms": duration_ms,
                                "status": "completed",
                            }

                        except Exception as e:
                            result = {
                                "error": str(e),
                                "traceback": traceback.format_exc(),
                                "execution_id": execution_id,
                                "status": "failed",
                            }

                        # Write result
                        with open("/tmp/exec_result.json", "w") as f:
                            json.dump(result, f)

                    # Clear request file
                    import os

                    try:
                        os.remove("/tmp/exec_request.json")
                    except:
                        pass

                except FileNotFoundError:
                    # No execution pending, wait
                    time.sleep(0.1)
                    continue

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
