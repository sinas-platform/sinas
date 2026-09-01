# ADR: Workbench — one mutable file tree under both file tools and code execution

- **Status:** Proposed
- **Date:** 2026-09-01
- **Authors:** Kjeld Oostra (with Claude)
- **Related code:**
  - `backend/app/services/collection_tools.py` — the file tools (search / get_file / write_file / edit_file with exact-match + `replace_all` semantics) the workbench reuses
  - `backend/app/models/file.py` — `Collection` / `File` / `FileVersion`, the storage + versioning substrate
  - `backend/app/services/file_storage.py` — `LocalFileStorage` at `/var/sinas/files` (mounted into backend and workers, **not** into sandboxes)
  - `backend/app/services/code_execution.py` — sandbox entry point; today has zero awareness of collections or file storage
  - `backend/app/services/executor/` — the three sandbox runtimes (`docker_pool`, `docker_ephemeral`, `k8s_pod`) and the `execute_inline` wire payload the sync rides on
  - `backend/app/services/execution_engine.py` — `AWAITING_INPUT` pause/resume; changed on `release/0.4.0`, rebase before implementing
  - `backend/app/services/config_apply/agents.py` — where the one per-agent enablement field lands

## Context

Reducing Claude Code / Cowork to primitives, Sinas already has most of them:
skills (first-class, preloadable), subagents (delegation with depth bound and
suspend mode), hooks, versioned file tools whose `edit_file` is genuine
Read/Write/Edit semantics (`collection_tools.py:246`), and two working
pause/resume mechanisms (`PendingToolApproval`, and execution-level
`AWAITING_INPUT` where function code can call `input()` and resume with a
human value — `execution_engine.py:411,521`).

The concentrated architectural gap is that Sinas has **two disjoint file
universes**:

- The agent's file tools operate on **Collections** — Postgres metadata
  (`File`/`FileVersion`) plus bytes under `/var/sinas/files`, a volume
  mounted into the backend and workers only.
- **Code execution** runs in sandboxes (`docker_pool` / `docker_ephemeral` /
  `k8s_pod`) that deliberately see none of it: `code_execution.py` builds an
  `execute_inline` payload with the code string and nothing else.

So an agent can write a file it can never execute, and execute code whose
outputs evaporate at the end of the call. Claude Code's core loop — Edit and
`pytest` touching the same tree, results persisting to the next turn — is
impossible today. This is the single missing primitive; the rest of the gap
(steering, permission modes, MCP, compaction) is behavioral and orthogonal.

## Decision

Introduce the **Workbench**: a per-chat mutable file tree that

1. the existing file tools operate on,
2. is synchronized into that chat's sandbox executions (both directions), and
3. persists across turns with the versioning Collections already have.

### Naming

"Workspace" is out: it already informally means the whole tenant in this
codebase (`agent.py:29` — "NULL = workspace-wide") and collides with
claude.ai/Slack workspace vocabulary. Candidates considered:

| Name | For | Against |
|---|---|---|
| **Workbench** | The place an agent works with tools on materials; pairs naturally with Collections-as-shelf; reads well in UI ("Open workbench") and API (`/chats/{id}/workbench`) | none found |
| Worktree | Precise ("mutable tree"), git flavor | invites confusion with git worktrees; developer-only vocabulary in a product growing a BA surface (Studio) |
| Desk | Cowork-friendly, short | cute over clear; weak as an API noun |
| Scratchpad | familiar | implies disposable — this is persistent and versioned, the opposite |
| Drive / Vault / Locker | storage nouns | describe only half the feature (miss the execution unification); Drive collides with the Google Drive connector, Vault with secrets |

**Recommendation: Workbench** (working name throughout this ADR; s/Workbench/X/
is cheap until code lands).

### Storage: a backing collection, not new tables

A workbench **is** a collection with a reserved flavor — auto-created on first
use, e.g. namespace `_workbench`, name = the chat's `session_key`, owned by
the chat's user. This buys, for free: `File`/`FileVersion` versioning, the
existing four file tools, storage backends, and the existing uniqueness/
visibility machinery. What's new is a `kind` (or reserved-namespace
convention) so workbenches are excluded from normal collection listings and
lifecycle (deleted/archived with the chat), and relative-path filenames
(`src/app.py`) — `File.name` is a 255-char string today, so nested paths fit;
we validate against traversal.

Scope is **per-chat** in v1. A per-task/per-delegation scope can layer on
later by keying the backing collection differently; don't design for it now.

### Execution: sync first, mount later

The design decision with teeth is mount vs sync. **Decide: copy-in/copy-out
sync in v1**, because it is the only option that works across all three
sandbox runtimes and changes nothing about the sandbox trust boundary:

- **Copy-in:** `code_execution.execute()` (which already receives `chat_id`)
  loads the workbench manifest, and ships current-version file contents into
  the sandbox inside the existing `execute_inline` payload; the wrapper
  materializes them under a fixed root (e.g. `/workbench`, the process cwd).
- **Copy-out:** the wrapper walks the tree after execution, returns
  `{path → content}` for files whose hash changed (plus created/deleted
  lists) in the result envelope; the backend writes them back through the
  same code path as `write_file`, so every execution's effects are versioned
  `FileVersion` rows — turn-level diffability free of charge.
- **Deltas, not full trees:** `FileVersion` already stores a content hash;
  send only what the sandbox hasn't seen (matters for `docker_pool`, where a
  warm container can keep its tree and receive deltas keyed on chat_id).
  Cap tree size and per-file size from settings; oversized files stay
  tool-accessible but are listed, not materialized, in the sandbox.

A k8s RWX subpath mount is a **later optimization behind the same
interface**, not the v1: it requires RWX in prod, punches the file-storage
volume into untrusted pods (subPath scoping becomes security-critical), and
does nothing for the two docker runtimes. If sync transfer costs ever hurt,
that's the moment to build it.

Same call on warm session-scoped sandboxes ("the agent's machine"): stay
ephemeral-with-sync in v1. `docker_pool` already gives warm-container
mechanics; binding a pooled container to a chat for its lifetime is a cost/
lifecycle problem to take on only after sync proves the experience. Sync
makes warmth an optimization instead of a correctness requirement — pool
affinity by `chat_id` gets most of the feel with none of the lifecycle risk.

### Enablement

Per-agent flag (alongside the existing tool enablement fields): workbench
off by default, on = the agent's file tools bind to the chat's workbench
(collection tools on explicit collections unchanged) and code execution
gains the sync behavior. This flag is the **only** config-apply touchpoint.

## Sequencing

- **Vs. config-apply unification** (folding every management endpoint into
  config-apply): **parallel is correct**. A workbench is runtime state —
  chat-scoped, user-owned, like messages and files — and never appears in
  declarative config. The single shared edge is the per-agent flag, one
  field in a resource `config_apply/agents.py` already serializes.
- **Vs. the steering triad** (per-chat lock, cooperative interrupt
  [#142](https://github.com/sinas-platform/sinas/issues/142), mid-turn
  message injection): independent code paths, but the triad is what makes
  long workbench-powered turns safe to ship to users. Build in parallel;
  gate any "long autonomous turn" positioning on both landing.
- **Branches:** `dev` is currently a strict ancestor of `release/0.4.0`
  (39 commits behind, including changes to `execution_engine.py` and
  `config_apply/agents.py`). This ADR branches off `dev` and merges forward
  cleanly; the implementation should start from whichever line is the
  integration target when it begins, and expect a rebase over 0.4.0.

## Impact

| Component | Change |
|---|---|
| `models/file.py` + migration | collection `kind` (or reserved namespace) for workbench backing collections; chat-lifecycle cascade |
| `collection_tools.py` | resolve the implicit target collection to the chat's workbench when the agent flag is on; accept nested paths |
| `code_execution.py` | build sync manifest from `chat_id`; write back changed files post-execution |
| `executor/` wrapper + `container_executor.py` | materialize tree pre-run, hash-walk and return changes post-run |
| `models/agent.py`, `config_apply/agents.py`, schemas | one enablement field |
| Console / Studio | workbench file browser on the chat view (reuses collection file UI); out of v1's backend scope |

## Open questions

1. Final name — Workbench is the recommendation; decide before code lands.
2. Concurrency: what happens when two executions in one chat overlap? The
   per-chat lock from the steering triad is the clean answer; until it
   lands, last-write-wins per file (each write is a version, so nothing is
   lost, merely shadowed).
3. Binary/large files: v1 caps materialization size — is "listed but not
   materialized" acceptable, or do we need lazy fetch from inside the
   sandbox (implies a sandbox→backend channel we currently don't have and
   may not want)?
4. Does the workbench also become the default target for user file uploads
   in chat? (Probably yes; out of scope here.)

## What we'd NOT do

- No new storage system, no new file tables — Collections are the substrate.
- No RWX mounts into sandboxes in v1.
- No session-pinned warm sandboxes in v1 (pool affinity at most).
- No per-task workbench scope in v1.
- No config-apply resource for workbenches — runtime state only.

## Next steps

1. Settle the name.
2. Backing-collection mechanics + migration (small, self-contained PR).
3. Sync in `docker_ephemeral` (simplest runtime) end-to-end; then
   `docker_pool` deltas and `k8s_pod`.
4. Agent flag + tool binding.
5. E2e: agent writes file → executes it → edits → re-executes across two
   turns, asserting `FileVersion` history matches.
