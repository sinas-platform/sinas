"""Shared pieces of the sandbox wire protocol.

The request/trigger/result-file exchange is identical across the Docker and
k8s sandbox runtimes. This module owns the in-container wait script so both
runtimes speak the same dialect — including the lazy-fetch extension: while
an execution runs, the sandbox wrapper may drop a fetch-request file
(`/tmp/wb_fetch_req_{eid}.json`) asking the backend for a workbench file;
the wait script exits early with that request, the backend services it by
writing `/tmp/wb_fetch_resp_{eid}.json` into the container, and re-enters
the wait. The wrapper's blocked read picks the response up and continues.
"""
import json
from typing import Any, Optional


def fetch_request_path(execution_id: str) -> str:
    return f"/tmp/wb_fetch_req_{execution_id}.json"


def fetch_response_path(execution_id: str) -> str:
    return f"/tmp/wb_fetch_resp_{execution_id}.json"


def build_wait_script(
    execution_id: str,
    timeout: float,
    *,
    write_trigger: bool,
) -> str:
    """The in-container poll loop, printing one JSON envelope and exiting.

    Envelopes: {"kind": "result", "data": <wire result>} when the result
    file appears, {"kind": "fetch", "req": {...}} when the wrapper requests
    a workbench file. write_trigger must be True only for the FIRST wait of
    an execution — re-writing the trigger would make the executor daemon
    re-run the request.
    """
    trigger_file = f"/tmp/exec_trigger_{execution_id}"
    result_file = f"/tmp/exec_result_{execution_id}.json"
    req_file = fetch_request_path(execution_id)
    return f"""
import sys, json, time, os
if {write_trigger!r}:
    with open("{trigger_file}", "w") as f:
        f.write("1")
max_wait = {timeout}
start = time.time()
while time.time() - start < max_wait:
    if os.path.exists("{result_file}"):
        try:
            with open("{result_file}", "r") as f:
                data = json.load(f)
        except Exception:
            time.sleep(0.05)
            continue
        os.remove("{result_file}")
        print(json.dumps({{"kind": "result", "data": data}}))
        sys.exit(0)
    if os.path.exists("{req_file}"):
        try:
            with open("{req_file}", "r") as f:
                req = json.load(f)
        except Exception:
            time.sleep(0.05)
            continue
        os.remove("{req_file}")
        print(json.dumps({{"kind": "fetch", "req": req}}))
        sys.exit(0)
    time.sleep(0.1)
print(json.dumps({{"kind": "result", "data": {{"status": "failed", "error": "Execution timed out"}}}}))
sys.exit(1)
"""


def parse_wait_output(raw_output: str) -> Optional[dict[str, Any]]:
    """Parse the wait script's stdout into its envelope, or None if it isn't
    one (older executors / stray prints fall back to legacy handling)."""
    try:
        data = json.loads(raw_output.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("kind") in ("result", "fetch"):
        return data
    # A bare wire result (no envelope) still counts as a result.
    if isinstance(data, dict) and "status" in data:
        return {"kind": "result", "data": data}
    return None
