# ADR: Pipelines — unified triggers + linear typed steps

- **Status:** Proposed
- **Date:** 2026-07-28
- **Authors:** Kjeld Oostra (with Claude)
- **Related code:**
  - `backend/app/cdc/service.py` — reference implementation for the runner's ops model (dedicated process, per-resource loops, pub/sub config reload)
  - `backend/app/queue/worker.py`, `backend/app/services/queue_service.py` — arq queues the pipeline job type plugs into
  - `backend/app/services/scheduler.py`, `backend/app/models/schedule.py` — schedule trigger (already has generic `schedule_type`/target columns)
  - `backend/app/models/webhook.py`, `backend/app/api/runtime/endpoints/webhooks.py` — webhook trigger (parallel branch adds `target_type` "agent" + raw mode; we add the third value)
  - `backend/app/models/database_trigger.py` — CDC trigger
  - `backend/app/services/connector_service.py`, `execution_engine.py`, `database_pool.py`, `message_service.py` — step executors
  - `backend/app/services/tool_execution.py`, `tool_discovery.py` — pipelines-as-tools
  - `backend/app/schemas/config.py`, `config_apply/`, `config_export.py`, `resource_serializers.py` — config round-trip

## Context

A recurring shape shows up across every planned integration (Gmail triage, Jira
worklog sync, webhook→agent, CDC→function, airbyte-lite ELT):

> poll or receive from a source → transform → optionally invoke an agent →
> deterministically act on its output (upsert to a DB, call a connector op)

Today each instance is hand-built: a function re-implements HTTP+auth, a schedule
fires an agent that is *prompted* to call the right tools in the right order, cursor
state is kept in ad-hoc store keys. Prompting an agent to chain tool calls is the
least reliable link — taking the agent's **structured output** (agents already have
`output_schema` + enforced JSON replies) and executing the follow-ups
deterministically is strictly more dependable.

This ADR introduces **`pipelines`**: a named, **linear** sequence of typed steps,
fired by the existing trigger resources, with platform-managed cursor state and
optional exposure as an agent tool.

**Explicitly not a DAG.** No branching, fan-out, or joins in v1. A `function` step
is the escape hatch for anything conditional or computational; the most we
anticipate later is a per-step `when:` condition. Pipelines calling pipelines is
also excluded in v1 (no recursion source beyond agents).

This design **supersedes** the parked "declarative input/output transforms on
connector operations" + "runtime connector-execute API" brief (2026-07-27); see
Future work for what was parked and the narrow forms in which those could return.

## Resource schema

YAML/API is camelCase; DB/Python snake_case, per repo convention.

```yaml
pipelines:
  - namespace: gmail
    name: inbox-triage
    description: Poll Gmail history and triage new messages
    inputSchema:            # validates run input (trigger payload / tool args / manual run)
      type: object
      properties: {}
    perUser:                # optional: fan out one run per user connected to this
      connector: google/gmail        # connector (oauth2_authorization_code)
      disableAfterFailures: 5        # per-user skip-until-reconnect (optional)
    steps:
      - name: fetch
        type: connector
        connector: google/gmail
        operation: list-history
        input:
          userId: me
          startHistoryId.$: cursor      # JMESPath — see Mapping
        cursor:
          param: startHistoryId          # where the cursor value is injected
          path: body.historyId           # where the new high-water mark is read
        retry: { maxAttempts: 3, backoff: exponential }

      - name: extract
        type: function
        function: gmail/extract-messages
        input.$: steps.fetch.output.body

      - name: triage
        type: agent
        agent: gmail/triage
        input.$: steps.extract.output    # mapped to the agent's inputSchema
        # message: optional; defaults to the JSON-serialized step input

      - name: act
        type: connector
        connector: google/gmail
        operation: modify-message
        input:
          id.$: steps.triage.output.messageId
          addLabelIds.$: steps.triage.output.labels

    # Optional: expose as ONE semantic agent tool
    asTool: false
    toolDescription: null      # defaults to description
    syncTimeoutSeconds: 120    # budget for sync (tool / manual sync) runs
    concurrency: single        # single | parallel; default single when any cursor
                               # config present, else parallel
    output.$: steps.act.output # optional final-output shaping; default = last step's output
```

### Step types

| type | executes via | output |
|---|---|---|
| `connector` | `connector_service.execute_operation` in-process (auth incl. per-user OAuth, retries reused) | `{statusCode, body, elapsedMs}`; non-2xx = step failure by default (`allowStatuses: [404]` opts specific codes out) |
| `function` | `queue_service.enqueue_and_wait` (existing runtime, depth-propagated) | function return value |
| `agent` | fresh chat + agent queue (queued runs) / delegation-depth-checked enqueue (sync runs) — see Execution | parsed JSON reply validated against the agent's `outputSchema` (step failure if invalid); raw text when no schema |
| `query` | `DatabasePoolManager.execute_query` in-process | `{rows, rowCount, affectedRows}` |
| `load` | upsert sink, see below | `{upserted: n, table}` |

Step names are unique per pipeline (`^[a-zA-Z_][a-zA-Z0-9_-]*$`); step count capped
(32) as a footgun guard.

### `load` — the upsert sink

```yaml
- name: land
  type: load
  connection: reporting          # DatabaseConnection name
  table: jira_worklogs           # optionally schema-qualified
  primaryKey.$: item.id          # evaluated per item
  items.$: steps.extract.output.worklogs    # array (or single object)
```

v1 lands **raw JSONB** — no schema evolution, no column mapping. The table is
auto-created on first run if missing. Shared pipelines:

```sql
CREATE TABLE IF NOT EXISTS "<table>" (
  pk         text PRIMARY KEY,
  payload    jsonb NOT NULL,
  synced_at  timestamptz NOT NULL DEFAULT now()
);
INSERT ... ON CONFLICT (pk) DO UPDATE SET payload = EXCLUDED.payload, synced_at = now();
```

`perUser` pipelines (see Per-user runs) write many users' data into one table, and
source ids (Gmail message ids, Slack ts) are only unique *per account* — so rows
carry the user they were synced for and the key is composite:

```sql
CREATE TABLE IF NOT EXISTS "<table>" (
  user_id    uuid NOT NULL,
  pk         text NOT NULL,
  payload    jsonb NOT NULL,
  synced_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, pk)
);
```

with `user_id` = the run's user, injected implicitly (not mappable — attribution
must not be spoofable from payload data). The conflict target matches the shape.
Switching a pipeline between shared and perUser after the table exists is a
validation error (shape mismatch), not a silent migration.

All items in one run are upserted in a single transaction (all-or-nothing), so a
failed run holds the cursor and safely replays. `primaryKey` is evaluated per item
with the item bound as `item`; a null/missing pk fails the step (never silently
drop rows). Typed columns / schema evolution is explicitly future work.

## Data mapping — JMESPath, AWS States-style keys

One mapping convention everywhere (step `input`, `cursor.path`, `primaryKey`,
`items`, pipeline `output`). We adopt **JMESPath** (battle-tested, safe — no code
execution, handles projections and simple aggregations) with the AWS States
Language key convention, rather than inventing an expression language:

- `key: value` — literal, passed through (recursively for objects/arrays).
- `key.$: "<jmespath>"` — evaluated against the run context; result bound to `key`.
- A step's whole input may be an expression: `input.$: steps.fetch.output.body`.

The run context document:

```json
{
  "input":  { },                  // pipeline run input (trigger payload / tool args / run body)
  "steps":  { "<name>": { "output": ... } },   // completed steps only
  "cursor": "...",                // committed cursor for this run's scope (see Per-user), or null
  "run":    { "id": "...", "triggerType": "SCHEDULE", "firedAt": "...", "userId": "..." }
}
```

An expression that references a missing path yields `null` (JMESPath semantics); a
syntactically invalid expression is rejected at save/config-apply time (compiled
once at validation). Anything beyond projection/selection belongs in a `function`
step (embeddings, MIME building, format conversion, heavy aggregation) — the
mapping layer is deliberately not a computation layer.

New dependency: `jmespath` (pure-Python, no transitive deps).

## Triggers stay separate resources

No trigger config inside the pipeline. The three trigger resources each gain
`pipeline` as a target, which buys: one pipeline reusable across triggers, a manual
run API for testing/backfills, and a uniform "trigger payload = pipeline input"
rule.

- **Webhooks** — composes with the in-flight "agent targets + raw response mode"
  branch (`claude/sharp-poincare-b5bb8a`): `target_type` becomes
  `Literal["function", "agent", "pipeline"]` (column is already `String(10)`;
  "pipeline" is 8 chars). New nullable `pipeline_namespace`/`pipeline_name`
  columns, mirroring that branch's agent columns exactly, with the analogous
  `model_validator` arm (`pipeline_name` required; `message_template` n/a; `raw`
  response mode stays function-only). Request body+headers+query defaults become
  the pipeline input. `sync` waits for the run result; `async` returns
  `202 {run_id}`. Our migration lands **after** and `Revises:` that branch's
  `w2a3b4t5g6t7` — coordinate merge order, do not fork the enum.
- **Schedules** — `schedule_type` (String(20)) gains `"pipeline"`;
  `target_namespace`/`target_name`/`input_data` are already generic. No migration
  needed beyond validation. "Poll Gmail every 2 min" = schedule → pipeline whose
  first step declares cursor config.
- **Database triggers (CDC)** — `target_type` column (default `"function"`) +
  nullable `pipeline_namespace`/`pipeline_name`, same pattern. The CDC service
  enqueues a pipeline run with the rows payload as input instead of a function
  execution. Note the semantic difference: CDC advances its *own* bookmark at
  enqueue time (at-most-once w.r.t. downstream success, unchanged); a pipeline's
  *own* cursor commits only on run success (at-least-once). Both are documented;
  CDC-triggered pipelines usually don't also declare cursor config.

Trigger-side permission checks follow the webhook-branch precedent: the pipeline
target requires `sinas.pipelines/{ns}/{name}.run:own|:all` for the webhook's owner
model, and schedules/CDC run as the resource owner as today.

## Execution model — one runner, two entry points

The highest-risk requirement is that tool-invoked pipelines sit in the interactive
chat path while trigger-fired runs go through workers. We do **not** build two
execution engines. There is one async runner:

```python
async def run_pipeline(pipeline, input, *, trigger_type, trigger_id,
                       user_id, user_token, depth) -> PipelineRunResult
```

in a new `backend/app/services/pipeline_runner.py`, and two entry points:

1. **Queued** — a new arq job `execute_pipeline_job` on its own queue
   (`sinas:queue:pipelines`, new `PipelineWorkerSettings`, its own process in
   docker-compose like the CDC service). Runs are await-heavy (they mostly wait on
   child executions and HTTP), so this worker gets high concurrency (default 50)
   and long job timeout. A dedicated queue means long pipeline runs can never
   starve function workers, and vice versa.
2. **Inline (sync)** — the runner is awaited directly with
   `asyncio.wait_for(..., timeout=pipeline.sync_timeout_seconds)`:
   - from the agent tool path (`pipeline_tools.py` converter, `_metadata.type ==
     "pipeline"`, dispatched from `tool_execution.execute_single_tool` like the
     other converters), and
   - from `POST /pipelines/{ns}/{name}/run` with `mode: sync`, and
   - from sync webhook responses.

### Step execution details

- **connector / query / load** — direct in-process calls; milliseconds of overhead.
  No new HTTP surface needed (this is what removes the need for the parked
  connector-execute API in the common case). Connector steps reuse retry config
  from the step (falling back to the connector's own `retry`).
- **function** — `enqueue_and_wait` with `depth` threaded through, so a pipeline
  triggered from inside a function execution (or whose function step nests
  further) is bounded by `MAX_EXECUTION_DEPTH` and admission control (PR #81
  guarantees hold).
- **agent** — a fresh chat per run (scheduler pattern; `title="pipeline:{ns}/{name} — {ts}"`,
  no session continuity in v1), then `enqueue_agent_message` + wait on the
  stream-relay channel — the same machinery as `execute_agent_tool`. For
  tool-invoked (sync) pipelines the enqueue is preceded by
  `delegation.child_depth_or_error()` so agent→pipeline→agent chains count against
  the existing delegation-depth limit and land on the sub-agent queue (issue #90
  starvation protections apply unchanged). The reply must parse as JSON valid
  against the agent's `output_schema` when one is set — parse/validation failure is
  a step failure (no silent passthrough); without a schema the raw text is the
  step output.

### Sync-mode risk assessment (scrutinized as required)

| Concern | Position |
|---|---|
| Slow agent step inside a tool-invoked pipeline | Allowed but bounded: the whole run is under `syncTimeoutSeconds` (default 120s, max 600). On timeout the tool call returns a clear error including the run id and which step was in flight. Docs + console will state plainly: agent steps in `asTool` pipelines cost seconds-to-minutes; prefer connector/query/function steps there. |
| Timeout ≠ rollback | Steps already executed have had side effects. The run record marks the timed-out step; the error message says "steps up to N completed". Same honesty rule as everywhere else — never mask partial execution. |
| Worker-slot pressure | A sync pipeline holds its caller's slot (agent worker for tool calls, request handler for API/webhook) exactly as a chained sequence of individual tool calls would today — pipelines replace those chains roughly 1:1, so net pressure does not increase. Child agent steps go through the sub-agent queue; child function steps through depth-checked admission. |
| Recursion | No pipeline step type "pipeline"; agents are the only re-entry, and they are depth-bounded via the existing delegation counter. |
| Latency floor | connector/query/load steps are in-process (ms). A function step adds one queue round-trip (tens of ms warm, `shared_pool` recommended for mapping-adjacent functions). An `asTool` pipeline of connector+query steps is comfortably interactive. |

## Per-user runs (fan-out)

The single-global-cursor model is wrong for any pipeline whose source connector
authenticates per user: each connected user is an independent source with their
own position in the stream. Motivating case: a personal cross-platform inbox —
per-user ingestion of Gmail/Slack messages into one table, agents sort
downstream.

Per-user auth exists in two forms today, and both make a source per-user:

1. `oauth2_authorization_code` — per-user tokens in `connector_oauth_tokens`
   (PR #85).
2. **Private secret overrides** — for `bearer`/`basic`/`api_key`/
   `oauth2_client_credentials`, `_resolve_secret_value` resolves a private Secret
   (visibility `private`, owned by the user) over the shared one of the same
   name; the client-credentials token cache is keyed by `user_id` for exactly
   this reason. Users who each store their own API key under the connector's
   secret name are independent sources too.

`perUser: {connector: <ns/name>}` declares this. Semantics:

- **Fan-out**: a trigger firing the pipeline enqueues one *fire* job; the fire job
  expands to one queued run per **connected user**, enumerated by the declared
  connector's auth type:
  - `oauth2_authorization_code` → users with a `connector_oauth_tokens` row (the
    set the OAuth status endpoint reports);
  - secret-based auth (`bearer`/`basic`/`api_key`/`oauth2_client_credentials`) →
    users holding a **private** Secret named by the connector's `auth.secret`.
    Users without a private override are *not* fanned out — they'd silently run
    against the shared credential, which is never what a per-user pipeline means.
  - `none`/`sinas_token` → no user registry exists; `perUser` on such a
    connector is a validation error.

  Triggers stay dumb; expansion lives in the pipeline job handler.
- **Identity**: each run executes *as* that user — `user_id` is theirs, a JWT is
  minted for them (scheduler pattern), so connector steps resolve their OAuth
  token exactly as `execute_operation(user_id=...)` already does, and function/
  agent/query steps run under their permissions. The run context exposes
  `run.userId`.
- **Isolation**: cursor, single-flight lock, coalesce flag, hold-on-failure, and
  consecutive-failure counting are all keyed per `(pipeline, user)`. One user's
  revoked token or failing mailbox must never block or fail other users' runs.
  `perUser.disableAfterFailures: N` skips a user after N consecutive failures
  *until they re-credential* — the OAuth callback storing a fresh token, or a
  create/update of their private Secret, resets the counter (the natural
  recovery points) — rather than deactivating the pipeline.
- **Validation**: `perUser` requires an auth type with a user registry (the
  enumeration rules above); `none`/`sinas_token` is a validation error. Global
  cursor (`perUser` absent) remains correct for shared-credential sources (a
  shared secret with no private overrides in play) and CDC-fed pipelines.
- **Manual runs / tools**: `POST .../run` on a perUser pipeline runs for the
  *calling* user only (natural for testing and for `asTool` invocation, which
  always executes as the chat's user); `?allUsers=true` requires `run:all` and
  fans out like a trigger.
- **Concurrency across users**: runs for different users may execute in parallel
  (bounded by the pipeline queue's worker concurrency); a per-pipeline
  `maxConcurrentUsers` throttle is future work if fan-out sizes demand it.
- **Consent note**: fan-out is gated on the OAuth connection itself — connecting
  a connector is the user's consent for pipelines sourced from it to run on their
  behalf (admins control which pipelines exist). A per-user opt-in/opt-out flag
  per pipeline is future work.

Source specifics that shaped this (and belong in the docs examples):

- *Gmail* `history.list` — a true since-cursor (`startHistoryId` → response
  `historyId`), per user. The schema example above.
- *Slack* `search.messages` with a **user** token (scope `search:read`; Slack's
  OAuth v2 nests it under `authed_user.access_token` — the configurable
  token-response path for connector OAuth is being added separately in the 0.3.0
  work, and this design assumes it). Slack's `after:` filter is day-granular, so
  the cursor is the last-synced *day* re-polled with overlap; the duplicates this
  produces are exactly what the at-least-once + idempotent-upsert contract
  absorbs (conflict target `(user_id, pk)` with pk = `channel:ts`).

## Cursor state ("reversed CDC")

Any step may declare `cursor: {param, path, initial?}`; in practice it's step 1.
Semantics:

- Before the step runs, the committed cursor value is available as `cursor` in the
  context and injected as `input[param]` (unless the mapping already set it). On
  the very first run, `initial` (literal or `.$` expression over `{input}`) is
  used; absent that, the param is omitted (APIs like Gmail then return
  "everything", and bootstrap functions can handle it).
- The step's *candidate* new cursor is read from its output at `path` immediately
  after the step succeeds, but it is **committed only when the whole run
  completes successfully** — one `UPDATE pipelines SET cursor_value = ...` in the
  run-finalization transaction alongside the run record.
- Failed/timed-out runs hold the cursor → the next firing re-reads from the same
  high-water mark → **at-least-once** delivery. This is safe because `load` steps
  are idempotent upserts, and agent steps are documented as at-least-once
  (duplicate triage of the same email on retry is acceptable; exactly-once via
  dedup keys is future work).
- If `path` yields null on a successful run (e.g. empty poll), the cursor is left
  unchanged — never regress or clear a bookmark on "no data".

Cursor storage:

- Shared pipelines: `cursor_value TEXT` on the `pipelines` row (exactly like
  `DatabaseTrigger.last_poll_value` — one bookmark per pipeline).
- `perUser` pipelines: a `pipeline_cursors` table —
  `(pipeline_id FK, user_id FK, cursor_value TEXT, consecutive_failures INT,
  last_error TEXT, updated_at)`, unique `(pipeline_id, user_id)`, CASCADE on both
  FKs. Keeping the shared bookmark on the pipeline row and per-user bookmarks in
  their own table avoids NULL-in-unique-constraint contortions and gives per-user
  failure state a natural home.

Multiple cursor steps per pipeline are rejected at validation in v1.

Optional pagination on a cursor-bearing (or any connector/function) step is
deferred to v1.1 unless trivially cheap during implementation:
`paginate: {param, tokenPath.$, itemsPath.$, maxPages: 10}` — loop the step feeding
`tokenPath` back into `param`, output `{items: [...concatenated], pages: n}`. The
Gmail and Jira consumers can ship without it (batch sizes cover the poll interval).

## Single-flight & overlap

A slow run and the next trigger firing must not overlap (CDC gets this for free
from its one-loop-per-trigger process model; queued pipeline runs do not). Design:

- A Redis lock per run scope — `sinas:pipeline:lock:{id}` for shared pipelines,
  `sinas:pipeline:lock:{id}:{user_id}` for perUser runs (the coalesce flag is
  keyed identically) — acquired with `SET NX EX <syncTimeout+margin>` at run
  start, held for the run, refreshed by the runner on long runs, released in a
  `finally`. Different users' runs of the same perUser pipeline never contend.
- `concurrency: single` (the default whenever cursor config is present): if the
  lock is held, a queued firing sets a *coalesce flag*
  (`sinas:pipeline:pending:{id}`) and exits; the lock holder checks the flag on
  completion and immediately runs once more (with fresh trigger metadata). N
  fires during a run collapse to one follow-up — correct for polling (the
  follow-up reads from the committed cursor).
- Sync/manual runs against a held lock return `409 {active_run_id}` rather than
  queueing — an interactive caller should not silently wait behind a poll run.
- `concurrency: parallel` (default for cursor-less pipelines): no lock; concurrent
  tool invocations of e.g. an embed-search pipeline are naturally fine.
- Stale locks self-heal via the EX TTL (a crashed worker's lock expires; the run
  record is finalized as failed by the arq job's exception path, mirroring how the
  shared-pool admission set self-heals).

## Failure semantics

- **Per-step retry**: `retry: {maxAttempts (1–10), backoff: none|linear|exponential}`,
  same vocabulary as `ConnectorRetry`. Applies to connector/query/load/function
  steps. Agent steps are **not** retried by the runner in v1 (side effects +
  cost); their one-shot failure fails the run.
- **Run failure**: first step to exhaust retries fails the run: status `failed`,
  error + failing step recorded, cursor held (for the run's scope — one user's
  failure holds only that user's cursor), remaining steps skipped. There is no
  `continueOnError` in v1 — a linear pipeline whose step N failed has nothing
  sound to feed step N+1 (`when:` conditions are the future home for "optional"
  steps).
- **Dead-letter**: the `pipeline_runs` row *is* the dead letter — it stores the
  full run input, per-step summaries, and the error, and is queryable/rerunnable
  (`POST /pipelines/runs/{run_id}/replay` re-enqueues with the stored input;
  replay of a cursor run is safe because the cursor never advanced).
- **Persistent failure**: optional `disableAfterFailures: N` — after N
  *consecutive* failed runs the pipeline is deactivated (`is_active = false`) with
  `error_message` set (the `DatabaseTrigger.error_message` pattern), surfacing in
  console/config export rather than silently burning quota forever. Default: off
  (halt-and-hold-cursor only, cadence bounded by the trigger). For perUser
  pipelines the analogous knob lives under `perUser.disableAfterFailures` and
  applies per user (skip-until-reconnect, counter on `pipeline_cursors`) — a
  single user's dead token never deactivates the pipeline for everyone.

## Observability

New `pipeline_runs` table:

```
id, pipeline_id (FK), run_id (unique str), trigger_type (enum), trigger_id,
status (running|succeeded|failed|timed_out), input (JSON),
steps (JSON: [{name, type, status, startedAt, durationMs, executionId?, chatId?, error?}]),
error, cursor_before, cursor_after, started_at, completed_at, duration_ms, user_id
```

Child executions link through the existing machinery: function/agent steps enqueue
with `trigger_type=TriggerType.PIPELINE` (new enum value + migration) and
`trigger_id=<run_id>`, so the existing executions UI/API shows them grouped per
run; the run's `steps` array carries the `execution_id`/`chat_id` back-references.
Connector/query/load steps (in-process, no Execution row) record their outcome in
the `steps` summary only — acceptable for v1 and consistent with how connector
tool calls are (not) recorded today. Runs API:

```
POST /pipelines/{ns}/{name}/run          {input?, mode: sync|async}   (?allUsers=true for perUser fan-out, requires run:all)
GET  /pipelines/{ns}/{name}/runs         ?status=&user=&limit=
GET  /pipelines/runs/{run_id}
POST /pipelines/runs/{run_id}/replay
```

`pipeline_runs.user_id` is the user the run executed as — for perUser fan-out,
the connected user, not the trigger's owner.

Retention: `pipeline_runs` rows pruned by the same janitor cadence as executions
(config `PIPELINE_RUN_RETENTION_DAYS`, default 30).

## Pipelines as agent tools

`asTool: true` requires a non-empty `inputSchema` and `description` (or
`toolDescription`). Agents opt in via a new `enabledPipelines: ["ns/name", ...]`
list (plain refs, wildcards supported via `resource_resolver`, same as
`enabledQueries`). Tool name `pipeline_<ns>__<name>`; tool parameters = the
pipeline's `inputSchema` verbatim (no derivation magic — that complexity died with
the parked transforms design); `_metadata.type = "pipeline"`. Execution enforces
`sinas.pipelines/{ns}/{name}.run:own` per the query-tool precedent.

This absorbs the parked transforms use cases as whole semantic tools:
- *semantic search*: embed(query_text) via function step → tsvector/vector query
  step → shape rows via `output.$` — one tool call, small token surface.
- *token-cheap reads*: get-issue connector step → flatten via `output.$`
  (JMESPath projection alone often suffices — no function needed).

## Resource plumbing (mechanical, follows existing patterns)

- `pipelines` table: id, user_id, namespace, name (unique together), description,
  input_schema, steps (JSON), per_user (JSON, nullable), as_tool,
  tool_description, sync_timeout_seconds, concurrency, disable_after_failures,
  cursor_value, error_message, output (JSON), is_active,
  managed_by/config_name/config_checksum, timestamps. Plus the `pipeline_cursors`
  table (see Cursor state) for perUser bookmarks and per-user failure counters.
  `PermissionMixin`; permission base `sinas.pipelines/{ns}/{name}` with actions
  `create/read/update/delete/run`; default Users role: `read:own` + `run:own`
  (creation/update stay admin-granted, matching queries' conservative default
  rather than connectors' permissive one).
- CRUD endpoints `backend/app/api/v1/endpoints/pipelines.py` (standard shape);
  runtime endpoints for run/runs as above; config: `PipelineConfig` in
  `schemas/config.py` (camelCase incl. `steps` passed through with `.$` keys
  intact — they're data), `apply_pipelines` in config_apply (ordered after
  connectors/functions/queries/agents so references usually exist; missing
  references are a save-time warning, run-time error, same lazy philosophy as the
  rest of config apply), `serialize_pipeline` for export (cursor_value and
  error_message are runtime state — **not** exported), docs page
  `docs-mint/build-resources/pipelines.mdx` + trigger-page updates.
- Steps validation at save/apply: known `type`, unique names, JMESPath
  compilation, one cursor max, `asTool ⇒ inputSchema`, cap 32 steps, per-type
  required fields. Cross-resource reference existence is warned, not enforced
  (install order).
- Config reload for triggers: webhook/schedule paths already reload on CRUD; the
  CDC process's pub/sub reload channel handles database-trigger changes; the
  pipeline worker itself is stateless per job (config read at run start), so no
  reload machinery is needed for pipelines themselves. Only cursor bootstrap
  mutates the pipeline row outside CRUD.

## Future work (recorded, deliberately not built)

Parked from the superseded 2026-07-27 brief:

1. **Connector-execute HTTP API for functions**
   (`POST /connectors/{ns}/{name}/execute/{operation}` + SDK
   `client.connectors.execute`) — still useful someday as the escape hatch for
   custom-shaped connector interaction *inside one function*: pagination loops,
   multipart upload, binary download (Drive uploads, Gmail attachments,
   `gmail/send_email` convenience functions). Small and non-blocking. Sketch, for
   when it's picked up: runtime-API placement (root router, next to
   `/functions/.../execute`), permission spelling
   `sinas.connectors/{ns}/{name}/{op}.execute:own` OR the 2-segment
   connector-level fallback (the matcher compares path segments 1:1, so both
   spellings must be checked), depth threaded via `_resolve_child_depth`, dispatch
   reusing `connector_service.execute_operation` unchanged.
2. **Per-operation transforms on connectors/queries** — superseded by
   pipelines-as-tools. If per-op response bloat remains painful for ops agents
   call *directly*, the revival is a lightweight declarative JMESPath
   `outputFilter` on the operation — pure projection, no function execution — not
   function transforms.
3. **Embeddings API decision** (carried open question): embedding steps/functions
   need an embeddings API; LLM providers are infrastructure-only today. Either a
   platform embeddings endpoint for the function runtime (preferred eventually)
   or per-function API-key secrets (works today). Pipelines are agnostic.

Also future: `when:` per-step conditions, typed-column `load` with schema
evolution, pagination config (if not landed in v1), session-continuity agent
steps, dedup keys for effectively-once agent steps, pipeline-level `env`/constants
block, per-user opt-in/opt-out flags for perUser pipelines, `maxConcurrentUsers`
fan-out throttle.

## Open questions

1. **Webhook branch coordination** — our webhook migration must `Revises:`
   `w2a3b4t5g6t7` and the `target_type` Literal must be extended in one place;
   if that branch's shape changes before merge, this ADR's webhook section
   follows it, not vice versa.
2. **Agent-step message** — default is the JSON-serialized mapped input; is an
   optional `message` template (Jinja, matching webhook `message_template`)
   wanted in v1, or is structured input + system prompt enough? Leaning: include
   `message` as an optional literal/`.$` string, default JSON input.
3. **Sync webhook + slow pipeline** — sync webhook responses inherit
   `syncTimeoutSeconds`; providers with short webhook timeouts (Slack: 3s)
   should use `async`. Document, or auto-force async when a pipeline contains an
   agent step? Leaning: document only.

## Verification plan

- Unit: mapping resolution (literal/`.$`/whole-input/missing-path→null), step
  input assembly, cursor inject/read/hold-on-failure/no-regress-on-null, retry
  policy, single-flight lock + coalesce flag (shared and per-user keying), run
  finalization (status, cursor commit atomicity), agent-reply schema validation,
  `load` pk extraction + transactional upsert (both table shapes, implicit
  user_id not spoofable via mapping), fan-out expansion (connected-user
  enumeration for both OAuth-token and private-secret registries, per-user
  failure isolation, skip-until-re-credential + counter reset on token store /
  private-secret update), steps validation rules, config round-trip
  (YAML→DB→export) with `.$` keys intact.
- Integration (dev stack): schedule→pipeline Gmail-shaped poll with a stub
  connector (cursor advances only on success), a perUser fan-out with two
  connected users where one token is revoked (the other user's runs proceed,
  cursors stay independent), webhook→pipeline sync+async, tool-invoked pipeline
  from a chat, replay of a failed run.
- Latency: measure inline runner overhead for a 3-step connector/query pipeline
  (target: <100ms added over the raw calls) and function-step queue round-trip;
  record numbers in the PR.
