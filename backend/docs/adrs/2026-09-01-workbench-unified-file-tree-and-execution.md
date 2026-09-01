# ADR: Workbench — one mutable file tree under both file tools and code execution

- **Status:** Accepted (2026-09-01; `kind` discriminator confirmed, one workbench per chat — cross-chat sharing goes through collections via checkout/promote)
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

The workbench is deliberately multi-purpose: it is the agent's scratchpad
(intermediate results, working notes — cheap to write, versioned anyway)
**and** the artifact surface — a deliverable (report, chart, generated
document) is just a workbench file the chat UI renders or offers for
download, and promotes to a collection when it should outlive the chat.
No separate artifact concept is needed.

### Naming

**Decided: Workbench** (2026-09-01). The shortlist below is kept for the
record. "Workspace" is out: it already informally means the whole tenant in this
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

A workbench **is** a collection row — auto-created on first use, owned by
the chat's user — but discriminated by a **`kind` column**
(`'collection' | 'workbench'`), not by a reserved-namespace convention.
Reusing the table buys `File`/`FileVersion` versioning, the four file
tools' internals, storage backends, and uniqueness machinery for free.
The `kind` discriminator is what keeps the reuse from leaking: workbench
rows have no user-facing namespace/name identity (internal key only, e.g.
the chat id), are **not addressable through the collections API at all**
— every collections-API query and the collection permission resolver
filter on `kind='collection'` at the query level, so there is no
string-matched namespace exclusion anywhere — and are reached exclusively
via `/chats/{id}/workbench`, authorized by chat access. Lifecycle follows
the chat (archived/deleted with it). New alongside `kind`: relative-path
filenames (`src/app.py`) — `File.name` is a 255-char string today, so
nested paths fit; we validate against traversal.

**Backward compatibility:** `kind` gets a `server_default='collection'`
in the migration; existing rows, API request/response shapes, and config
round-trips are untouched — the field is additive and clients never need
to send it.

**Alternative considered (open for review): no `kind`, plain collections
plus careful permissioning.** Workbenches could be ordinary collections
gated purely by grants. What tips it toward `kind` is not reachability
but **grant compatibility**: existing deployments hold `collections:*`
wildcard grants today, and without a discriminator those grants would
silently widen to cover every chat's working files the day workbenches
ship — the permission-level analogue of an API break. Secondary: without
`kind`, each workbench needs a synthetic namespace/name (the reserved-
namespace convention returns through the back door), and listings/config
export need to filter them — that filter *is* a kind column. The audit
argument does not require plain collections either: auditing follows the
chat, which runtime access control already treats as the unit (strict
owner check at `chats.py`), so a management/audit-level chat-read
permission naturally covers `/chats/{id}/workbench` — the conversation
and its files audit together, which is better audit semantics than
reading working files detached from their transcript. What plain
collections *would* buy is ACL-based workbench sharing for free; if
sharing a workbench with a colleague ever becomes a requirement, revisit
then.

Scope is **per-chat** in v1. A per-task/per-delegation scope can layer on
later by keying the backing collection differently; don't design for it now.

### Metadata and visibility

Collections today gate access in two layers: a namespaced collection
permission (`collection_tools.py:339-348`), then per-file `visibility`
(`private` = owner-only for reads, hidden from others' search;
`shared` = readable by anyone holding the collection permission —
`collection_tools.py:378,490`). A workbench collapses this: the tree
belongs to the chat, and the chat belongs to one user, so there is nothing
for `shared` to mean inside it.

- **Every workbench file is `visibility="private"`, `user_id` = the chat's
  owner.** The workbench-bound tool path sets this unconditionally — note
  the plain tool path defaults writes to `"shared"` today
  (`collection_tools.py:619`), so this is an explicit override, not an
  inherited default. With everything private, the existing per-file checks
  already deny reads and hide search results for any other user who
  reaches the backing collection through generic code paths.
- **Authorization derives from chat access, not collection grants.**
  Because workbench rows are typed `kind='workbench'` and filtered out of
  the collections API and permission resolver at the query level (see
  Storage above), a wildcard `collections:*` grant structurally cannot
  reach them — no namespace string-matching involved. The rule is: you can
  reach a workbench iff you can reach its chat. Operator/support access to
  workbench contents, if wanted, is its own explicit permission — never a
  side effect of a broad collection grant. Test this adversarially anyway.
- **`metadata_schema` / `file_metadata`:** backing collections carry no
  metadata schema (free-form); `file_metadata` stays available and is where
  workbench bookkeeping lives (e.g. `origin: upload | tool | execution`,
  originating execution id).
- **Nothing is inherited implicitly at promotion.** Promotion re-decides
  visibility (default: private to the promoting user), validates
  `file_metadata` against the target collection's `metadata_schema` — a
  promotion can legitimately fail validation — and runs the target's hooks.
- **Sync safety follows from single ownership:** the sandbox receives the
  whole tree, which is fine only because every file in it is the chat
  owner's. If multi-participant chats ever arrive, workbench visibility
  semantics must be redesigned *before* those chats get workbench sync.

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
  Cap eager materialization by per-file and total-tree size from settings.

**Oversized files: lazy fetch over the pause channel.** Files above the
eager cap (and, by the same mechanism, any file when the tree as a whole is
too big) are materialized as **stubs** — the path exists, a manifest marks
it lazy. "The file is needed" is signaled by the sandbox trying to read it:

- The wrapper patches Python-level reads (`builtins.open` / `Path.open` /
  `os.open`) for paths under the workbench root; a read of a lazy path
  triggers a fetch instead of returning stub bytes.
- The fetch transport is the **pause/resume channel that already exists**:
  the executor protocol's `AWAITING_INPUT` generalizes to typed pause
  requests (`kind: "fetch_file"`) that the backend auto-completes with the
  version's content instead of waiting on a human — the exact "pluggable
  completers" generalization the deferred-tools design already calls for,
  so this is convergent work, not a side quest. On resume the wrapper
  writes the real bytes over the stub and the read proceeds; `docker_pool`
  containers keep fetched blobs by content hash, so each is paid for once.
- The Python-level hook can't see raw reads from C extensions (memmap,
  DB engines opening files directly). For those the sandbox context gets an
  explicit `workbench.fetch(path)` helper, and the lazy manifest is visible
  to the agent, so the model knows which paths to fetch before handing them
  to native code. Good enough for v1; a FUSE layer that makes laziness
  fully transparent is a later option behind the same interface.

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

### Collections and the workbench: checkout, not mount

A chat's user typically holds permissions on several collections. Those
collections are **not** mounted into the workbench or the sandbox. Three
reasons: the sandbox tree is all-or-nothing (a mounted collection would
expose `shared` files wholesale and bypass per-file `private` visibility
the moment code, not the tool layer, does the reading); auto-mounting
makes every sandbox write a potential mutation of curated shared data
(the two-way-sync problem in a different coat); and eagerly syncing
everything a user can read is exactly the transfer-cost explosion the
lazy design avoids.

Instead, the git mental model — the workbench is the working copy,
collections are remotes:

- **Read via existing tools, unchanged:** the collection file tools keep
  working in workbench-enabled chats, with their existing permission and
  visibility checks. Browsing a collection doesn't require pulling it in.
- **Checkout (v1), single file or bulk:** an explicit
  `workbench_checkout(collection, path_or_glob)` tool copies a file, a
  prefix, a glob, or the whole collection into the workbench. Bulk is
  permission-aware by construction: the collection permission gates the
  call, and the file query applies the same visibility filter as search —
  the caller's own private files plus `shared` ones; other users' private
  files are never candidates. Bulk should also be **cheap**: `FileVersion`
  stores a content hash, so checkout creates new `File`/`FileVersion` rows
  that reference the existing stored blob instead of copying bytes —
  copy-on-write, since any edit writes a new version anyway. (This needs
  the `storage_path` unique constraint relaxed into a shared-blob
  arrangement — a deliberate part of the checkout PR, with delete-time
  refcounting to match.) Eager-sync caps still apply: a huge checked-out
  dataset lands as lazy stubs in the sandbox and fetches on read.
  Every checkout records provenance in `file_metadata` (source collection,
  file id, version).
- **Promote is the reverse of checkout** — the same round trip, outbound:
  copy a workbench file into a target collection, running the target's
  hooks and metadata validation. Where checkout provenance exists,
  promote offers *update the source file* (and detects that the source
  moved on since checkout, surfacing a conflict instead of clobbering);
  without provenance — a file born in the workbench — promote creates a
  new file in the target.
- **Read-only lazy mount (v1.5):** selected collections can appear in the
  sandbox under a read-only root (e.g. `/collections/<name>/`) using the
  same stub + `fetch_file` pause channel as oversized workbench files —
  the manifest is built per-user (private files of others never listed),
  and the backend re-checks permission on every fetch. Read-only means no
  write-back semantics to design; modifying still goes through checkout.
  This lands after the fetch channel exists and only if checkout proves
  too much friction.

### Chat uploads land in the workbench

Chat uploads currently target a collection directly, which conflates "hand
the agent a file to work on" with "commit a document to the curated
library". With workbenches, uploads default to the chat's workbench, and
**promotion** to a real collection is an explicit action (agent tool +
UI affordance) that copies the current version out.

The safety property collections provide today — pre/post upload hooks, used
e.g. to filter sensitive images so they are never stored — carries over for
free, because a workbench **is** a collection: workbench backing collections
carry a deployment-level (overridable per-agent) default hook chain, so an
upload to the workbench passes the same pre-upload filter before any bytes
persist. Promotion then runs the *target* collection's own hooks at
promotion time, since its policy may be stricter.

Explicitly rejected: two-way sync between a workbench and a collection.
It turns every sandbox write into a potential mutation of a curated,
shared collection, needs conflict resolution in both directions, and
double-writes version history. One-way promotion keeps the mental model
(bench = working copy, shelf = published) and the audit trail clean.
Front-ends that want today's behavior can still upload straight to a
collection — the runtime upload endpoint keeps accepting an explicit
collection target.

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
| `models/file.py` + migration | `kind` discriminator on collections; workbench rows carry no public namespace/name; chat-lifecycle cascade; collections-API queries and permission resolver filter `kind='collection'` |
| `collection_tools.py` (new tool) | `workbench_checkout` — copy collection files into the workbench with provenance metadata |
| `collection_tools.py` | resolve the implicit target collection to the chat's workbench when the agent flag is on; accept nested paths |
| `code_execution.py` | build sync manifest from `chat_id`; write back changed files post-execution |
| `executor/` wrapper + `container_executor.py` | materialize tree pre-run, hash-walk and return changes post-run |
| `models/agent.py`, `config_apply/agents.py`, schemas | one enablement field |
| Console / Studio | workbench file browser on the chat view (reuses collection file UI); out of v1's backend scope |

## Open questions

1. Concurrency: what happens when two executions in one chat overlap? The
   per-chat lock from the steering triad is the clean answer; until it
   lands, last-write-wins per file (each write is a version, so nothing is
   lost, merely shadowed).
2. The eager/lazy size thresholds and the shape of the default workbench
   hook chain (deployment-level only, or per-agent overridable from day
   one?).
3. Whether promotion preserves version history in the target collection
   (copy current version only, or replay the workbench's versions?). v1
   leans current-version-only.
4. `kind` column vs. plain collections with permission discipline — see
   the alternative in Storage. Current lean: keep `kind` (grant
   compatibility, no synthetic naming); revisit if workbench *sharing*
   becomes a requirement.

## What we'd NOT do

- No new storage system, no new file tables — Collections are the substrate.
- No RWX mounts into sandboxes in v1.
- No session-pinned warm sandboxes in v1 (pool affinity at most).
- No per-task workbench scope in v1.
- No two-way workbench↔collection sync — promotion is an explicit one-way
  copy.
- No config-apply resource for workbenches — runtime state only.

## Next steps

1. Settle the name.
2. Backing-collection mechanics + migration (small, self-contained PR).
3. Sync in `docker_ephemeral` (simplest runtime) end-to-end; then
   `docker_pool` deltas and `k8s_pod`.
4. Agent flag + tool binding.
5. E2e: agent writes file → executes it → edits → re-executes across two
   turns, asserting `FileVersion` history matches.
