"""End-to-end validation of the Claude Code-style loop, driven deterministically.

No LLM provider key is needed: a scripted provider plays the model and emits
pre-planned tool calls round by round, reacting to real tool results where
the loop demands it (the spill pointer, the user's answer). Everything else
is the real stack — the shipped way-of-working package installed through the
package service, `MessageService` streaming a turn under the chat lock, tool
discovery, approval rules, the workbench, `$workbench` references, result
spill, the deferred ask_user round, and code execution through the real
sandbox wrapper (run in-process; only the container transport is faked).

The scenario is the one the package is for: the user uploads a messy CSV,
asks for a cleaned version plus a summary, and the agent

  looks (workbench_list) → checks a conventions file out of a collection →
  reads the input → cleans it with code (outputs persist back) → reads the
  spilled full tool result → edits the summary → surfaces the HTML summary
  as an artifact by reference → asks where to publish (round suspends) →
  on the answer, promotes the cleaned file (approval gate, ask rule) →
  on approval, finishes with a state-of-the-workbench summary.

Every step's effect is asserted on the workbench, the collection and the
transcript, and the observed tool sequence is pinned so a change in the
loop's plumbing shows up here.
"""
import asyncio
import base64
import csv
import io
import json
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, async_engine
from app.models.agent import Agent
from app.models.chat import Chat, Message
from app.models.component import Component
from app.models.file import Collection, File
from app.models.pending_approval import PendingToolApproval
from app.models.pending_completion import PendingCompletion
from app.models.tool_call_result import ToolCallResult
from app.models.user import Role, RolePermission, User, UserRole
from app.providers.base import BaseLLMProvider
from app.services import chat_steering, deferred_completions
from app.services.delegation import current_channel_id
from app.services.message_service import MessageService
from app.services.package_service import PackageService
from app.services.queue_service import queue_service
from app.services.workbench import PROVENANCE_KEY, get_or_create_workbench

from tests.conftest import auth_headers

PACKAGE_PATH = (
    Path(__file__).resolve().parents[3] / "packages" / "way-of-working.yaml"
)
PACKAGE_NAME = "way-of-working"
SKILL_MARKER = "# Skill: way-of-working/workbench-loop"
COLLECTION = "demo/sales"

# ---------------------------------------------------------------------------
# The scenario data
# ---------------------------------------------------------------------------

MESSY_CSV = (
    "Region , Product, Amount (EUR), Date\n"
    "north , Widget , \"1,200.50\", 2026-01-05\n"
    "North, gadget, 300, 2026-01-06\n"
    "\n"
    "south, Widget, 1200.50 , 2026-01-07\n"
    "north , Widget , \"1,200.50\", 2026-01-05\n"
    "EAST, gizmo, n/a, 2026-01-08\n"
    "West, Widget, 450.00, 2026/01/09\n"
)
CONVENTIONS_MD = (
    "# Sales data conventions\n\n"
    "Regions: North, South, East, West\n"
    "Amounts are EUR with two decimals. Dates are ISO 8601.\n"
)
# What a correct cleaning produces — derived by hand from MESSY_CSV, so the
# code the "model" writes is checked against an independent expectation.
EXPECTED_CLEAN_ROWS = [
    {"region": "North", "product": "Widget", "amount_eur": "1200.50", "date": "2026-01-05"},
    {"region": "North", "product": "Gadget", "amount_eur": "300.00", "date": "2026-01-06"},
    {"region": "South", "product": "Widget", "amount_eur": "1200.50", "date": "2026-01-07"},
    {"region": "West", "product": "Widget", "amount_eur": "450.00", "date": "2026-01-09"},
]
EXPECTED_TOTAL = "3151.00"
EXPECTED_DROPPED = {"blank": 1, "duplicate": 1, "invalid_amount": 1, "unknown_region": 0}

# The script the scripted model "writes". Plain loops on purpose: it is what
# a model produces for this job, and it reads the checked-out conventions
# file from the materialized workbench instead of hard-coding the regions.
# __READER_OPTS__ is the one thing the second attempt changes: the messy
# file has a space before its quoted amounts ( "1,200.50"), which the csv
# module only parses as a quoted field with skipinitialspace=True. The first
# run's printed checks expose that (3 invalid amounts, wrong total) and the
# model fixes and reruns — the iterate-on-failure half of the loop.
CLEAN_CODE_TEMPLATE = '''
import csv, json

regions = []
for line in open("conventions.md"):
    if line.startswith("Regions:"):
        regions = [r.strip() for r in line.split(":", 1)[1].split(",")]
canon = {}
for r in regions:
    canon[r.lower()] = r

rows = list(csv.reader(open("sales_raw.csv", newline="")__READER_OPTS__))
kept = []
seen = set()
dropped = {"blank": 0, "duplicate": 0, "invalid_amount": 0, "unknown_region": 0}
for i, raw in enumerate(rows[1:], start=2):
    cells = [c.strip() for c in raw]
    print("row", i, "raw:", cells)
    if not any(cells):
        dropped["blank"] += 1
        continue
    region, product, amount, date = cells[:4]
    region = canon.get(region.lower())
    if region is None:
        dropped["unknown_region"] += 1
        continue
    try:
        amount = round(float(amount.replace(",", "")), 2)
    except ValueError:
        dropped["invalid_amount"] += 1
        continue
    date = date.replace("/", "-")
    key = (region, product.lower(), amount, date)
    if key in seen:
        dropped["duplicate"] += 1
        continue
    seen.add(key)
    kept.append({"region": region, "product": product.title(),
                 "amount_eur": "%.2f" % amount, "date": date})

with open("sales_clean.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["region", "product", "amount_eur", "date"])
    w.writeheader()
    w.writerows(kept)

total = 0.0
by_region = {}
for r in kept:
    total += float(r["amount_eur"])
    by_region[r["region"]] = by_region.get(r["region"], 0.0) + float(r["amount_eur"])

lines = ["# Sales summary", "",
         "- Rows in: %d" % (len(rows) - 1),
         "- Rows kept: %d" % len(kept),
         "- Dropped: " + json.dumps(dropped),
         "- Total EUR: %.2f" % total,
         "", "## By region", ""]
for k in sorted(by_region):
    lines.append("- %s: %.2f" % (k, by_region[k]))
lines += ["", "## Method", "", "TODO"]
with open("summary.md", "w") as f:
    f.write("\\n".join(lines) + "\\n")

items = "".join("<li>%s: %.2f</li>" % (k, by_region[k]) for k in sorted(by_region))
with open("summary.html", "w") as f:
    f.write("<h1>Sales summary</h1><ul>" + items + "</ul><p>Total: %.2f</p>" % total)

print("CHECK rows_in", len(rows) - 1)
print("CHECK rows_kept", len(kept))
print("CHECK total", "%.2f" % total)
print("CHECK dropped", json.dumps(dropped))
'''
CLEAN_CODE_NAIVE = CLEAN_CODE_TEMPLATE.replace("__READER_OPTS__", "")
CLEAN_CODE_FIXED = CLEAN_CODE_TEMPLATE.replace("__READER_OPTS__", ", skipinitialspace=True")

METHOD_NOTE = (
    "Cleaned by a script run with execute_code: regions mapped to the names "
    "in conventions.md, amounts parsed as EUR (quoted thousands separators "
    "included — the first run split them, the rerun reads them with "
    "skipinitialspace), dates normalized to ISO, blank and duplicate rows "
    "dropped. Counts and totals above are the script's printed checks."
)

USER_REQUEST = (
    "I uploaded sales_raw.csv — it's messy. Clean it up following the "
    "conventions in the demo/sales collection, write me a summary, and "
    "publish the cleaned file when you're done."
)

FINAL_REPLY = (
    "Done. Files in the workbench this turn:\n"
    "- sales_clean.csv — 4 cleaned rows (3 dropped: 1 blank, 1 duplicate, 1 invalid amount), "
    "total EUR 3151.00; published to demo/sales\n"
    "- summary.md — counts, totals by region and the method\n"
    "- summary.html — the same summary, shown as an artifact\n"
    "- conventions.md — checked out from demo/sales, unchanged\n"
    "The first cleaning run mis-parsed the quoted amounts (3 invalid, total 1950.50); "
    "the rerun with skipinitialspace reads them correctly. Verified by the script's "
    "printed checks (rows in 7, rows kept 4, total 3151.00)."
)


# ---------------------------------------------------------------------------
# The scripted provider
# ---------------------------------------------------------------------------

Step = dict[str, Any] | Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]


def call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"tool_calls": [{"id": call_id, "name": name, "arguments": arguments}]}


def say(text: str) -> dict[str, Any]:
    return {"text": text}


class ScriptedProvider(BaseLLMProvider):
    """Plays the model: one pre-planned step per request. A step is either a
    dict (tool calls or final text) or a callable deciding from the messages
    it was sent — the way a real model reads the previous tool result."""

    def __init__(self, steps: list[Step]):
        super().__init__()
        self.steps = list(steps)
        self.requests: list[dict[str, Any]] = []

    async def complete(self, messages, model, tools=None, temperature=0.7, max_tokens=None, **kw):
        raise AssertionError("the chat loop streams; complete() must not be used")

    async def stream(
        self, messages, model, tools=None, temperature=0.7, max_tokens=None, **kw
    ) -> AsyncIterator[dict[str, Any]]:
        self.requests.append({"messages": messages, "tools": tools or []})
        assert self.steps, "the scripted model was asked for more rounds than planned"
        step = self.steps.pop(0)
        if callable(step):
            step = step(messages, tools or [])
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        if "tool_calls" in step:
            for index, tc in enumerate(step["tool_calls"]):
                yield {
                    "content": None,
                    "tool_calls": [
                        {
                            "index": index,
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                        }
                    ],
                    "finish_reason": None,
                }
            yield {"content": None, "tool_calls": None, "finish_reason": "tool_calls", "usage": usage}
        else:
            yield {"content": step["text"], "tool_calls": None, "finish_reason": "stop", "usage": usage}

    def format_tool_calls(self, tool_calls):
        return tool_calls

    def extract_usage(self, response):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _last_tool_result(messages: list[dict[str, Any]]) -> dict[str, Any]:
    last = messages[-1]
    assert last["role"] == "tool", last
    return json.loads(last["content"])


def read_spilled_result(messages, tools):
    """Round 5: follow the pointer the truncated execute_code result carries."""
    result = _last_tool_result(messages)
    assert "_full_result" in result, result
    path = result["_full_result"]["workbench_file"]
    return call("call_5", "workbench_read", {"filename": path, "limit": 1})


def fix_and_rerun(messages, tools):
    """Round 6: the full first-run result shows the quoting problem (a split
    amount and three "invalid" amounts) — fix the parse and run again."""
    read = _last_tool_result(messages)
    assert "CHECK rows_kept 3" in read["content"], read["content"]
    assert "200.50" in read["content"] and "invalid_amount" in read["content"]
    return call("call_6", "execute_code", {"code": CLEAN_CODE_FIXED})


def promote_where_the_user_said(messages, tools):
    """Round 10 (after resume): the ask_user answer is the tool result, in
    the exact shape the answer endpoint stores it — {"answer": ...}."""
    answer = messages[-1]
    assert answer["role"] == "tool" and answer["name"] == "ask_user", answer
    collection = json.loads(answer["content"])["answer"]
    return call(
        "call_10",
        "workbench_promote",
        {"filename": "sales_clean.csv", "collection": collection, "visibility": "shared"},
    )


SCRIPT: list[Step] = [
    call("call_1", "workbench_list", {}),
    call("call_2", "workbench_checkout", {"collection": COLLECTION, "path": "conventions.md"}),
    call("call_3", "workbench_read", {"filename": "sales_raw.csv", "limit": 4}),
    call("call_4", "execute_code", {"code": CLEAN_CODE_NAIVE}),
    read_spilled_result,
    fix_and_rerun,
    call("call_7", "workbench_edit", {"filename": "summary.md", "old_string": "TODO", "new_string": METHOD_NOTE}),
    call("call_8", "create_artifact", {"title": "Sales summary", "html": {"$workbench": "summary.html"}}),
    call(
        "call_9",
        "ask_user",
        {"question": "Where should sales_clean.csv be published?", "options": [COLLECTION, "keep it in the workbench"]},
    ),
    promote_where_the_user_said,
    say(FINAL_REPLY),
]


# ---------------------------------------------------------------------------
# Fixtures: the real stack, with the container transport and the LLM faked
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _tmp_file_storage(tmp_path, monkeypatch):
    import app.services.file_storage as fs

    monkeypatch.setenv("FILE_STORAGE_PATH", str(tmp_path / "files"))
    fs._storage = None
    yield
    fs._storage = None


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_per_test():
    yield
    await async_engine.dispose()


@pytest.fixture
def inprocess_sandbox(monkeypatch):
    """Route execute_code through the docker_ephemeral path with the container
    replaced by an in-process run of the real wrapper. Copy-in (manifest),
    copy-out (apply_sync_changes) and result shaping are the real code."""
    from app.services import code_execution
    from app.services.executor import _ephemeral_runtime

    async def fake_create(db, *, execution_id, name_prefix="sinas-sbx"):
        return None, None, f"inproc-{execution_id}"

    async def fake_remove(container, name):
        return None

    async def run_inprocess(container, payload, execution_id, effective_timeout, start_time, fetch_handler=None):
        ns: dict[str, Any] = {}
        exec(payload["function_code"], ns)
        output = await asyncio.to_thread(ns["handler"], payload["input_data"], payload["context"])
        # The in-container executor reports the handler's return value under
        # "result" (container_executor._execute_inline_sandbox); mirror it.
        wire = {"result": output, "execution_id": execution_id, "status": "completed"}
        return code_execution._shape_code_result(wire, int((time.time() - start_time) * 1000))

    monkeypatch.setattr(settings, "sandbox_executor", "docker_ephemeral")
    monkeypatch.setattr(settings, "code_execution_enabled", True)
    monkeypatch.setattr(_ephemeral_runtime, "create_ephemeral_container", fake_create)
    monkeypatch.setattr(_ephemeral_runtime, "remove_ephemeral_container", fake_remove)
    monkeypatch.setattr(code_execution, "_run_code_payload", run_inprocess)


@pytest.fixture
def scripted_model(monkeypatch):
    """Install the scripted provider wherever the chat loop creates one."""
    from app.services import message_service as ms

    provider = ScriptedProvider(SCRIPT)

    async def fake_create_provider(*args, **kwargs):
        return provider

    monkeypatch.setattr(ms, "create_provider", fake_create_provider)
    return provider


@pytest.fixture
def spill_code_results(monkeypatch):
    """Make the execute_code result oversized relative to its cap, so the
    full result spills to tool_results/ and the inline copy carries a pointer."""
    from app.services import tool_execution

    monkeypatch.setitem(tool_execution.TOOL_RESULT_SIZE_OVERRIDES, "execute_code", 600)


@pytest.fixture
def captured_resume(monkeypatch):
    """The deferred round's resume is a queue job; capture its kwargs so the
    test can run the same continuation in-process."""
    captured: dict[str, Any] = {}

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        return "job-inproc"

    monkeypatch.setattr(queue_service, "enqueue_agent_delegate_resume", fake_enqueue)
    return captured


@pytest_asyncio.fixture
async def world():
    """Committed state (tool execution opens its own sessions): a user with
    wildcard permissions, the installed package, a chat with its exemplar
    agent, and the demo/sales collection holding conventions.md."""
    from app.services.file_storage import get_storage
    from app.services.workbench import _write_bytes
    from tests.conftest import _test_db_url

    # This test needs COMMITTED state because tool execution opens its own
    # sessions, so it goes through the application's session factory. Only
    # safe when that factory targets the test database (as CI does); never
    # install/uninstall packages in some other application database.
    from sqlalchemy.engine import make_url

    app_url, test_url = async_engine.url, make_url(_test_db_url)
    if (app_url.host, app_url.port, app_url.database) != (test_url.host, test_url.port, test_url.database):
        pytest.skip(
            "application DB differs from TEST_DATABASE_URL; the way-of-working loop test "
            "commits through the application session factory and would touch the wrong database"
        )

    async with AsyncSessionLocal() as s:
        user = User(email=f"wow-{uuid.uuid4().hex[:8]}@example.com")
        s.add(user)
        await s.flush()
        role = Role(name=f"wow-role-{uuid.uuid4().hex[:8]}", description="way-of-working e2e")
        s.add(role)
        await s.flush()
        s.add(RolePermission(role_id=role.id, permission_key="sinas.*:all", permission_value=True))
        s.add(UserRole(role_id=role.id, user_id=user.id, active=True))
        await s.commit()
        await s.refresh(user)

        _, result = await PackageService(s).install(PACKAGE_PATH.read_text(), str(user.id))
        assert result.success, result.errors
        await s.commit()
        agent = (
            await s.execute(
                select(Agent).where(Agent.namespace == "way-of-working", Agent.name == "assistant")
            )
        ).scalar_one()

        chat = await MessageService(s).create_chat_with_agent(str(agent.id), str(user.id), {})

        ns, name = COLLECTION.split("/")
        coll = Collection(namespace=ns, name=name, user_id=user.id)
        s.add(coll)
        await s.flush()
        written = await _write_bytes(
            s, get_storage(), coll, filename="conventions.md", content=CONVENTIONS_MD.encode(),
            content_type="text/markdown", user_id=str(user.id), visibility="shared",
        )
        assert "error" not in written, written
        await s.commit()
        env = {"user": user, "role_id": role.id, "agent": agent, "chat": chat, "collection_id": coll.id}

    yield env

    # Let fire-and-forget tasks (tool-result store writes) finish first.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    async with AsyncSessionLocal() as s:
        chat_id, user_id = env["chat"].id, env["user"].id
        await s.execute(delete(PendingToolApproval).where(PendingToolApproval.chat_id == chat_id))
        await s.execute(delete(PendingCompletion).where(PendingCompletion.chat_id == chat_id))
        await s.execute(delete(ToolCallResult).where(ToolCallResult.chat_id == chat_id))
        await s.execute(delete(Message).where(Message.chat_id == chat_id))
        await s.execute(delete(Chat).where(Chat.id == chat_id))
        await s.execute(delete(Component).where(Component.user_id == user_id))
        # Files and versions cascade from their collection at the DB level.
        await s.execute(delete(Collection).where(Collection.user_id == user_id))
        await s.commit()
        await PackageService(s).uninstall(PACKAGE_NAME, actor_user_id=str(user_id))
        await s.commit()
        await s.execute(delete(UserRole).where(UserRole.user_id == user_id))
        await s.execute(delete(RolePermission).where(RolePermission.role_id == env["role_id"]))
        await s.execute(delete(Role).where(Role.id == env["role_id"]))
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect(agen) -> list[dict[str, Any]]:
    return [chunk async for chunk in agen]


def _tool_sequence(events: list[dict[str, Any]]) -> list[str]:
    return [e["name"] for e in events if e.get("type") == "tool_start"]


async def _workbench_files(chat: Chat) -> dict[str, File]:
    async with AsyncSessionLocal() as s:
        chat = await s.get(Chat, chat.id)
        wb = await get_or_create_workbench(s, chat)
        rows = (await s.execute(select(File).where(File.collection_id == wb.id))).scalars().all()
        return {f.name: f for f in rows}


async def _read_current(f: File) -> bytes:
    from app.models.file import FileVersion
    from app.services.file_storage import get_storage

    async with AsyncSessionLocal() as s:
        version = (
            await s.execute(
                select(FileVersion).where(
                    FileVersion.file_id == f.id, FileVersion.version_number == f.current_version
                )
            )
        ).scalar_one()
        return await get_storage().read(version.storage_path)


async def _transcript(chat: Chat) -> list[Message]:
    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at)
            )
        ).scalars().all()
        return list(rows)


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exemplar_agent_runs_the_full_loop(
    world, scripted_model, inprocess_sandbox, spill_code_results, captured_resume
):
    user, chat = world["user"], world["chat"]
    user_id, chat_id = str(user.id), str(chat.id)
    token = "tok"

    # The user uploads the messy CSV to the chat's workbench through the API.
    from app.main import app as _app

    async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as client:
        resp = await client.post(
            f"/chats/{chat_id}/workbench/files",
            json={"name": "sales_raw.csv", "content_base64": base64.b64encode(MESSY_CSV.encode()).decode()},
            headers=auth_headers(user),
        )
        assert resp.status_code == 201, resp.text

    # ── Turn 1: streams until the agent asks the user a question ──────────
    current_channel_id.set("chan-e2e")
    async with AsyncSessionLocal() as s:
        events = await _collect(
            MessageService(s).send_message_stream(
                chat_id=chat_id, user_id=user_id, user_token=token, content=USER_REQUEST
            )
        )

    assert _tool_sequence(events) == [
        "workbench_list",
        "workbench_checkout",
        "workbench_read",
        "execute_code",
        "workbench_read",
        "execute_code",
        "workbench_edit",
        "create_artifact",
        "ask_user",
    ]
    assert not [e for e in events if e.get("type") == "error"], events
    suspended = [e for e in events if e.get("type") == deferred_completions.ROUND_SUSPENDED]
    assert len(suspended) == 1 and suspended[0]["tool_call_ids"] == ["call_9"]
    question = next(e for e in events if e.get("type") == "input_required")
    assert question["options"] == [COLLECTION, "keep it in the workbench"]
    pending_completion_id = suspended[0]["pending_completion_id"]

    # Every request the model saw carried the preloaded skill in the system
    # prompt, after the agent's own prompt.
    for request in scripted_model.requests:
        system = request["messages"][0]
        assert system["role"] == "system"
        assert SKILL_MARKER in system["content"]
        assert system["content"].index("hands-on assistant") < system["content"].index(SKILL_MARKER)
    # …and the full loop's tools were offered.
    offered = {t["function"]["name"] for t in scripted_model.requests[0]["tools"]}
    assert {"workbench_write", "workbench_checkout", "workbench_promote", "execute_code",
            "create_artifact", "ask_user"} <= offered

    # Checkout recorded provenance; each code run persisted its outputs as a
    # new version (the rerun overwrote the first run's); the edit made a
    # third version of summary.md. The input was never overwritten.
    files = await _workbench_files(chat)
    assert files["conventions.md"].file_metadata[PROVENANCE_KEY]["collection"] == COLLECTION
    assert files["sales_raw.csv"].current_version == 1
    assert files["sales_clean.csv"].current_version == 2
    assert files["summary.html"].current_version == 2
    assert files["summary.md"].current_version == 3
    for name in ("sales_clean.csv", "summary.md", "summary.html"):
        assert files[name].file_metadata.get("origin") == "execution", (name, files[name].file_metadata)

    clean_rows = list(csv.DictReader(io.StringIO((await _read_current(files["sales_clean.csv"])).decode())))
    assert clean_rows == EXPECTED_CLEAN_ROWS
    summary = (await _read_current(files["summary.md"])).decode()
    assert f"- Total EUR: {EXPECTED_TOTAL}" in summary
    assert "- Rows kept: 4" in summary
    assert "TODO" not in summary and METHOD_NOTE in summary

    # Both execute_code results were oversized: spilled in full with
    # provenance, pointer inline. The first run's full result is what the
    # model diagnosed the quoting problem from (not the truncated copy); the
    # rerun's shows the verified numbers and the files it persisted.
    first = files["tool_results/execute_code_call_4.json"]
    rerun = files["tool_results/execute_code_call_6.json"]
    assert first.file_metadata == {"origin": "tool", "tool_name": "execute_code", "tool_call_id": "call_4"}
    first_result = json.loads((await _read_current(first)).decode())
    assert "CHECK total 1950.50" in first_result["result"]["stdout"]
    rerun_result = json.loads((await _read_current(rerun)).decode())
    assert f"CHECK total {EXPECTED_TOTAL}" in rerun_result["result"]["stdout"]
    assert f"CHECK dropped {json.dumps(EXPECTED_DROPPED)}" in rerun_result["result"]["stdout"]
    assert set(rerun_result["workbench"]["synced"]) == {"sales_clean.csv", "summary.md", "summary.html"}
    inline = json.loads(next(e for e in events if e.get("tool_call_id") == "call_4" and e["type"] == "tool_end")["result"])
    assert inline["_full_result"]["workbench_file"] == "tool_results/execute_code_call_4.json"
    read_back = json.loads(next(e for e in events if e.get("tool_call_id") == "call_5" and e["type"] == "tool_end")["result"])
    assert "CHECK rows_kept 3" in read_back["content"]

    # The artifact got the file's content through the reference — the model
    # never pasted HTML into the call.
    async with AsyncSessionLocal() as s:
        artifact = (
            await s.execute(select(Component).where(Component.user_id == user.id))
        ).scalar_one()
    assert artifact.namespace == "artifacts" and artifact.title == "Sales summary"
    assert "<li>North: 1500.50</li>" in artifact.source_code
    assert f"Total: {EXPECTED_TOTAL}" in artifact.source_code

    # ── The user answers; the round resumes (what the resume job does) ────
    # Same payload the HTTP answer endpoint stores (json.dumps({"answer": …})).
    answered = await deferred_completions.complete(
        pending_completion_id, "call_9", json.dumps({"answer": COLLECTION}),
        user_token=token, resume_channel_id="chan-e2e-2",
    )
    assert answered["resumed"] is True
    ctx = captured_resume["conversation_context"]
    assert captured_resume["chat_id"] == chat_id

    current_channel_id.set("chan-e2e-2")
    lock = await chat_steering.acquire_chat_lock_wait(chat_id)
    assert lock
    try:
        async with AsyncSessionLocal() as s:
            events2 = await _collect(
                MessageService(s)._stream_followup_after_tools(
                    chat_id=chat_id, user_id=user_id, user_token=token,
                    provider=ctx.get("provider"), model=ctx.get("model"),
                    temperature=ctx.get("temperature", 0.7), max_tokens=ctx.get("max_tokens"),
                    tools=ctx.get("tools", []), status_templates=ctx.get("status_templates", {}),
                    depth=ctx.get("tool_iteration_depth", 0), agent_label=ctx.get("agent_label"),
                )
            )
    finally:
        await chat_steering.release_chat_lock(chat_id, lock)

    # Promote is gated by the package's approval rules: the round stops with
    # an approval request instead of publishing.
    approvals = [e for e in events2 if e.get("type") == "approval_required"]
    assert [a["function_name"] for a in approvals] == ["workbench_promote"]
    assert approvals[0]["arguments"]["collection"] == COLLECTION
    assert _tool_sequence(events2) == []  # nothing ran
    async with AsyncSessionLocal() as s:
        coll_files = (
            await s.execute(select(File).where(File.collection_id == world["collection_id"]))
        ).scalars().all()
    assert {f.name for f in coll_files} == {"conventions.md"}  # not published yet

    # ── The user approves; the approved round runs (what the resume job does)
    async with AsyncSessionLocal() as s:
        row = (
            await s.execute(select(PendingToolApproval).where(PendingToolApproval.tool_call_id == "call_10"))
        ).scalar_one()
        assert row.function_namespace == "tool" and row.approved is None
        row.approved = True
        await s.commit()
        approval_ctx, all_calls = row.conversation_context, row.all_tool_calls

    lock = await chat_steering.acquire_chat_lock_wait(chat_id)
    assert lock
    try:
        async with AsyncSessionLocal() as s:
            events3 = await _collect(
                MessageService(s)._handle_tool_calls(
                    chat_id=chat_id, user_id=user_id, user_token=token,
                    messages=approval_ctx["messages"], tool_calls=all_calls,
                    provider=approval_ctx.get("provider"), model=approval_ctx.get("model"),
                    temperature=approval_ctx.get("temperature", 0.7),
                    max_tokens=approval_ctx.get("max_tokens"), tools=approval_ctx.get("tools", []),
                )
            )
    finally:
        await chat_steering.release_chat_lock(chat_id, lock)

    assert _tool_sequence(events3) == ["workbench_promote"]
    promote_result = json.loads(next(e for e in events3 if e.get("type") == "tool_end")["result"])
    assert promote_result.get("updated_source") is False and promote_result["collection"] == COLLECTION
    assert "".join(e.get("content") or "" for e in events3 if e.get("type") not in ("tool_start", "tool_end")) == FINAL_REPLY
    assert not scripted_model.steps  # the whole script played out

    # Published: the cleaned file is now in the collection, shared, with the
    # verified content; the workbench copy and the input are untouched.
    async with AsyncSessionLocal() as s:
        published = (
            await s.execute(
                select(File).where(File.collection_id == world["collection_id"], File.name == "sales_clean.csv")
            )
        ).scalar_one()
    assert published.visibility == "shared"
    assert list(csv.DictReader(io.StringIO((await _read_current(published)).decode()))) == EXPECTED_CLEAN_ROWS

    # ── Transcript: a provider-valid record of the whole loop ─────────────
    transcript = await _transcript(chat)
    assert transcript[0].role == "user" and transcript[0].content == USER_REQUEST
    tool_rows = {m.tool_call_id: m for m in transcript if m.role == "tool"}
    assert set(tool_rows) == {f"call_{i}" for i in range(1, 11)}
    assert tool_rows["call_9"].name == "ask_user"
    assert json.loads(tool_rows["call_9"].content) == {"answer": COLLECTION}
    # Each tool_calls message is followed by its result before the next step.
    for i, m in enumerate(transcript):
        if m.role == "assistant" and m.tool_calls:
            assert transcript[i + 1].role == "tool"
            assert transcript[i + 1].tool_call_id == m.tool_calls[0]["id"]
    assert transcript[-1].role == "assistant" and transcript[-1].content == FINAL_REPLY
