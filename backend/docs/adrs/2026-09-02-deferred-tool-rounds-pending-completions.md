# ADR: Deferred tool rounds — one checkpoint, pluggable completers

- **Status:** Accepted
- **Date:** 2026-09-02
- **Authors:** Kjeld Oostra (with Claude)
- **Related code:**
  - `backend/app/services/deferred_completions.py` — the unified suspend/complete/expire core
  - `backend/app/models/pending_completion.py` — the checkpoint row (née `PendingDelegation`)
  - `backend/app/services/delegation.py` — suspend-on-delegate, now a completer + thin wrappers
  - `backend/app/services/ask_user_tools.py`, `POST /chats/{id}/pending-input/{tool_call_id}` — the new human-input completer
  - `backend/app/services/message_service.py` `_handle_tool_calls` — where a round splits off deferred calls and suspends
  - `backend/app/queue/agent_jobs.py`, `backend/app/queue/worker.py` — suspension handoff + the expiry cron sweep
  - `backend/app/models/pending_approval.py` — adapted (expiry), deliberately not migrated

## Context

Three pause/resume mechanisms grew independently, each bespoke:

1. **PendingToolApproval** — a tool call needing human approval stops the
   round *before execution*; the approve/reject API re-enqueues a resume
   job that re-enters `_handle_tool_calls` from a stored conversation
   snapshot. One row per asked call, tri-state `approved`.
2. **PendingDelegation** (issue #90, suspend mode) — delegated
   `call_agent_*` calls don't block a worker; the parent job ends, each
   finishing child reports back, the last one enqueues a resume job that
   re-enters `_stream_followup_after_tools`. One row per suspended round,
   remaining-count bookkeeping.
3. **Execution-level AWAITING_INPUT** — sandboxed function code calls
   `input()`; the execution parks with a resume handle. The sandbox wire
   protocol recently gained typed pause requests (`kind: fetch_file`) that
   the backend completes *automatically* — the same shape, machine-completed.

All three are the same idea: *this result will arrive later; park cheaply,
resume when it lands*. Each had its own table-or-status, its own resume
job wiring, its own event vocabulary — and the next case in the queue
(letting an agent ask the user a question mid-turn, Cowork-style) would
have become a fourth copy. None of the suspended states had a deadline:
a child that died without reporting, or a question nobody answered, hung
its round forever.

## Decision

Introduce the **deferred tool round**: a tool round may suspend with one
or more **pending completions**, each owned by a pluggable **completer**;
the round resumes when the last completion lands. Concretely:

- **One checkpoint row per suspended round** — `pending_completions`,
  which is `pending_delegations` renamed and extended (not a new table:
  rows suspended mid-deploy keep working; their entries lack a
  `completer` key and default to `sub_agent`). Entries are
  `{tool_call_id: {completer, ...payload, expires_at?}}` plus the
  remaining-count and the conversation context the resume needs.
- **A completer** owns: who supplies the result, the tool-message `name`
  the result is recorded under, and the timeout result when the entry's
  deadline passes. The core (`complete()`) is completer-agnostic: persist
  the tool result **in the same transaction** that decrements the count
  (an assistant `tool_calls` message must never be left without matching
  results when the round resumes), and on the last completion enqueue the
  resume job. The resume job is the existing
  `execute_agent_delegate_resume_job` — unchanged queue contract, now
  understood as the generic *round resume*: it re-enters
  `_stream_followup_after_tools`, holds the per-chat lock
  (`acquire_chat_lock_wait`), and hits the cooperative-interrupt check at
  the round boundary like any inline round.
- **Completers shipped:**
  - `sub_agent` — the existing suspend-on-delegate flow.
    `delegation.suspend_delegations` / `on_child_complete` are now thin
    wrappers over the generic service; job kwargs
    (`pending_delegation_id`) keep their names for in-flight queue
    compatibility.
  - `human_input` — NEW: the `ask_user` system tool (opt-in via
    `system_tools: ["askUser"]`). The round suspends; the question
    surfaces as an `input_required` stream event and via
    `GET /chats/{id}/pending-input` (also on the chat detail response);
    `POST /chats/{id}/pending-input/{tool_call_id}` supplies the answer,
    which becomes the tool result. The answer endpoint returns a fresh
    stream `channel_id` when it triggers the resume — the same reconnect
    contract as tool approval.
  - Human **approval** remains its own pre-execution gate (see below) but
    gains the same expiry treatment.
- **Mixed rounds work by construction**: a round containing both
  `call_agent_*` and `ask_user` calls produces one checkpoint with both
  completer kinds; whichever completion lands last resumes.
- **Expiry**: every entry may carry a deadline; the row indexes the
  earliest one. A minutely arq cron sweep (`sweep_deferred_expiry_job`,
  agent worker) resolves overdue entries with their completer's timeout
  content *through the same `complete()` path* — so a fully-expired round
  resumes with timeout errors as tool results rather than hanging.
  Defaults: `ask_user` 24h (`ASK_USER_TIMEOUT_SECONDS`), delegation and
  approval 0 = disabled (`AGENT_DELEGATE_SUSPEND_TIMEOUT_SECONDS`,
  `TOOL_APPROVAL_TIMEOUT_SECONDS`) — existing behavior untouched until a
  deployment opts in. Expired approvals are auto-rejected (same terminal
  state as a user "no").

### API / schema / interface sketch

```python
# pending_completions (renamed from pending_delegations)
pending  JSON  # {tool_call_id: {"completer": "sub_agent"|"human_input",
               #                 ...payload, "expires_at": iso?}}
expires_at  timestamptz NULL  # min(entry deadlines); the sweep's index

# the completer plugin surface
@dataclass(frozen=True)
class Completer:
    kind: str
    tool_message_name: Callable[[entry], str | None]
    timeout_content:   Callable[[tool_call_id, entry], str]

await complete(pending_id, tool_call_id, content,
               user_token=..., resume_channel_id=None)
# -> {"status": "completed"|"not_found"|"unknown_tool_call",
#     "resumed": bool, "channel_id": ...}
```

```
GET  /chats/{id}/pending-input                     → [{tool_call_id, question, options, expires_at}]
POST /chats/{id}/pending-input/{tool_call_id}      {"answer": "..."}
     → 202 {"status": "answered", "resumed": true, "channel_id": "..."}
```

Stream events: `input_required` (per question), `round_suspended`
(generic, always emitted on suspension), `delegation_pending` (kept for
consumers that predate the unification). Agent jobs treat any
`SUSPENSION_EVENT_TYPES` member as "a resume job owns the rest" — no
"done" event, no terminal bookkeeping.

## Impact

| Component | Change |
|---|---|
| `pending_delegations` | Renamed to `pending_completions`; +`expires_at`. Rows in flight during deploy keep working (entry `completer` defaults to `sub_agent`). |
| `pending_tool_approvals` | +`expires_at` (NULL = ask forever, all existing rows). |
| `delegation.py` | `suspend_delegations`/`on_child_complete` become wrappers over `deferred_completions`; signatures unchanged. |
| `message_service._handle_tool_calls` | The delegate split generalizes to a deferred-call split (delegates in suspend mode; `ask_user` whenever a job channel exists); one combined checkpoint per round. |
| `agent_jobs` | Suspension detection via `SUSPENSION_EVENT_TYPES`; the approval-resume job now also handles a round that suspends again (previously it would have published "done" over a suspended round). |
| `tool_discovery` / `tool_execution` | `askUser` system-tool binding; synchronous paths (no channel) get an explanatory error result instead of a suspension. |
| Approval API | Contract untouched (`approve-tool`, `pending_approvals`, always-allow grants). |
| `AgentWorkerSettings` | +minutely cron sweep (`unique=True`, one worker class owns it). |

## Open questions

- **Resume-without-token.** Expiry has no user token, so the timed-out
  round's follow-up turn runs with an empty one; tools that need auth in
  *subsequent* rounds fail with auth errors the model can report. Storing
  tokens in the checkpoint would be worse (long-lived credentials at
  rest, and likely expired by the time a 24h deadline fires). A
  service-token or token-refresh story would fix this properly.
- **User messages during suspension.** The chat lock is free while a
  round is suspended, so a new user turn can start; the rebuilt history
  repairs the dangling `tool_calls` in-memory
  (`conversation_history` orphan repair), but a completion landing
  *after* that turn appends its tool result out of adjacency order.
  Pre-existing behavior (delegation had it too), now easier to fix in one
  place. A natural follow-up: let a plain user message answer the oldest
  pending `ask_user` question instead of starting a new turn.
- **Console UX.** `input_required` / `pending_inputs` are wired
  end-to-end on the backend; the console needs a question card + answer
  box to make this visible (follow-up to the workbench-panel work).

## What we'd NOT do

- **Migrate `PendingToolApproval` onto the checkpoint table.** Approval
  is a *pre-execution* gate: nothing has run yet, per-call rows are an
  API contract (tri-state `approved`, `always_allow` grants,
  `pending_approvals` on the chat), and resume re-*executes* the round
  rather than continuing after results. Forcing it into the
  results-arrive-later shape would have meant rewriting a working API
  contract for symmetry's sake. It adopts the unified vocabulary
  (a human-approval completer, conceptually) and the expiry sweep; the
  row shape stays.
- **Fold the execution-level `AWAITING_INPUT` in.** That mechanism lives
  a layer down (function executions, not chat tool rounds) and already
  has its own resume handle + `continue_execution` tool. The typed
  sandbox pause requests (`fetch_file`) show the same completer shape
  emerging there; unifying across layers is a separate decision.
- **Per-call timeout arguments on `ask_user`.** A model choosing its own
  deadlines adds surface without a use case; deployment-level settings
  suffice for the first cut.
- **A push channel for expiry.** The sweep publishes to the suspended
  round's stored stream channel; if nobody is connected, the resumed
  conversation is simply in the chat on next load.

## Next steps

1. Console: render `input_required` / `pending_inputs`, post answers.
2. Decide the resume-token story (service tokens vs. short-lived
   re-mint on resume) — also benefits delegation resume.
3. Consider treating a plain user message during an `ask_user`
   suspension as the answer to the oldest open question.
4. Revisit the execution-level pause machinery against the completer
   registry once a second machine completer (beyond `fetch_file`) shows up.
