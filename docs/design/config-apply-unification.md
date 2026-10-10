# Config/CRUD Unification — one apply layer per resource

- **Status:** In progress. Foundations + `schedules` pilot + change history implemented (2026-10-01); remaining kinds migrate per §5.
- **Date:** 2026-08-05
- **Authors:** Kjeld Oostra, Claude
- **Related code:** `backend/app/services/config_apply/`, `backend/app/api/v1/endpoints/`, `backend/app/schemas/config.py`, `backend/app/services/resource_serializers.py`, `backend/app/services/config_export.py`, `backend/app/services/package_service.py`
- **Related docs:** `docs/design/gitops-agent-native.md`, issue [#87](https://github.com/sinas-platform/sinas/issues/87) (instance modes, deferred)

## 1. Context

Sinas has two parallel write paths for every configurable resource:

1. **Per-resource CRUD endpoints** (`backend/app/api/v1/endpoints/*.py`) that
   mutate SQLAlchemy models inline, each with its own validation, permission
   checks, and side effects.
2. **Declarative config apply** (`backend/app/services/config_apply/*`) that
   upserts resources from a `SinasConfig` YAML, with its own camelCase schema
   twins (`schemas/config.py`), its own camel→snake field mapping, and its own
   (partial) side-effect handling. Export back to YAML is a third hand-written
   mapping (`resource_serializers.py` + `config_export.py`).

Every resource field therefore exists in **four hand-maintained copies**: the
REST schema (snake_case), the config schema (camelCase), the apply-layer
camel→snake mapping, and the serializer snake→camel mapping. The connector
OAuth work in the 0.3.0 cycle had to touch all four; `CONNECTOR_AUTH_FIELD_MAP`
(`schemas/config.py:399`) deduped exactly one nested object of one resource.
The deeper duplication — create/update semantics, validation, side effects —
has already produced real behavioral drift (§3, §4).

**Goal:** declarative config apply becomes the *primary* write channel. The
per-resource CRUD endpoints become thin translators that build a config
fragment and call the same per-resource apply layer. One place per resource
defines upsert semantics, validation, and side effects; parity is enforced by
tests, not by discipline.

This is a design + migration plan. No big-bang rewrite: the apply layer is
introduced resource-by-resource behind the existing APIs.

## 2. Current state — inventory

### 2.1 Resource kinds and their paths

19 kinds flow through `ConfigApplyService.apply_config` (`config_apply/service.py:119`),
in dependency order: roles, users, llmProviders, databaseConnections, secrets,
dependencies, connectors, functions, skills, components, queries, collections,
templates, stores, manifests, agents, webhooks, schedules, databaseTriggers.
(`spec.variables` is package-install-time only, resolved by
`PackageService._resolve_variables` before parsing.)

| Kind | Config applier | CRUD endpoint | Upsert key (config) | CRUD lookup |
|---|---|---|---|---|
| roles | `identity.py:17` | `roles.py` | `Role.name` | name |
| users | `identity.py:123` | `users.py` | `User.email` | UUID |
| llmProviders | `data_sources.py:20` | `llm_providers.py` | `LLMProvider.name` | UUID |
| databaseConnections | `data_sources.py:134` | `database_connections.py` | `DatabaseConnection.name` | UUID |
| secrets | `resources.py:137` | `secrets.py` | **`name` only** | (name, visibility[, user]) |
| dependencies | `resources.py:865` | `dependencies.py` | `package_name` | UUID |
| connectors | `resources.py:33` | `connectors.py` | (namespace, name) | (namespace, name) |
| functions | `resources.py:318` | `functions.py` | (namespace, name) | (namespace, name) |
| skills | `resources.py:438` | `skills.py` | (namespace, name) | (namespace, name) |
| components | `resources.py:509` | `components.py` | (namespace, name) | (namespace, name) |
| queries | `resources.py:216` | `queries.py` | (namespace, name) | (namespace, name) |
| collections | `resources.py:610` | `collections.py` | (namespace, name) | (namespace, name) |
| templates | `integrations.py:113` | `templates.py` | (namespace, name) | UUID |
| stores | `resources.py:699` | `stores.py` | (namespace, name) | (namespace, name) |
| manifests | `resources.py:779` | `manifests.py` | (namespace, name) | (namespace, name) |
| agents | `agents.py:60` | `agents.py` | (namespace, name) | (namespace, name) |
| webhooks | `integrations.py:22` | `webhooks.py` | `Webhook.path` | path |
| schedules | `integrations.py:190` | `schedules.py` | `ScheduledJob.name` | name |
| databaseTriggers | `integrations.py:280` | `database_triggers.py` | `DatabaseTrigger.name` | name (globally!) |

Non-config resources with CRUD only (out of scope): api_keys, messages, queue,
request_logs, database_schema DDL/DML.

### 2.2 Behavioral drift matrix (the actual pain)

Differences observed between the two paths on `origin/dev` (97dbeb8):

| Concern | CRUD path | Config-apply path |
|---|---|---|
| **Permissions** | per-resource `check_permission` + `get_with_permissions`/ownership compare; admin-only for llmProviders/dbConnections; `shared_pool` requires `sinas.functions.shared_pool:all` | one coarse `sinas.config.apply:all` gate (`config.py:90`), then writes everything as `owner_user_id`; no per-resource check, no shared_pool gate |
| **Validation: referenced resources** | triggers: connection must exist **and be active**, function must exist (create only; PATCH re-binds function unchecked); webhooks/schedules check target on create+update | triggers: connection existence only (ignores `is_active`), **function never checked**; webhooks/schedules rely on `config_parser`, which is skipped with `force=true` |
| **Validation: field constraints** | REST schemas carry `pattern`/`ge`/`le` (auth type enum, cron via `croniter`, AST-parse of function code, operations ⊆ {INSERT,UPDATE}) | config twins carry almost none — YAML can apply `auth.type: "garbage"`, invalid cron, unparseable code |
| **CDC reload (triggers)** | per-trigger `add`/`update`/`remove` publish (`database_triggers.py:101,205,234`) | one global `reload` after commit (`service.py:264`), **skipped entirely when `auto_commit=False` — i.e. on every package install** |
| **Scheduler reload** | `_notify_scheduler` on create/update/delete (`schedules.py:101,243,274`) | **never published** — config-applied schedules invisible until scheduler restart |
| **Component compile** | `background_tasks.add_task(_do_compile, …)` on create/update | sets `compile_status="pending"` only; nothing consumes "pending" → config/package components stay uncompiled |
| **DB pool invalidation** | `DatabasePoolManager.invalidate()` on connection patch/delete | never — running pools keep stale credentials after config-driven change |
| **Function versioning** | new `FunctionVersion` only when `code` changes | new version on **every** update pass (`resources.py:376`) |
| **Execution cache** | `executor.clear_cache()` after function update/delete | never |
| **Secret scoping** | upsert keyed (name, visibility[, user_id]) | keyed `name` alone — can overwrite another user's private secret; config schema has no `visibility` field |
| **Package detach** | `detach_if_package_managed` in 13 update handlers (not in any DELETE; not for secrets/llmProviders/dbConnections/roles/users; not in connector `import-openapi`) | reverse: `should_skip_existing` (`normalizers.py:76`) silently *adopts* `managed_by=NULL` rows; roles/llmProviders/dbConnections use a stricter guard that doesn't adopt |
| **managed_by stamping** | never touched by CRUD (a PATCHed llmProvider stays "config-managed") | set on create; on update only connectors/secrets re-stamp; adopted rows keep `managed_by=NULL` → package uninstall misses them |
| **Rename conflict check** | functions/queries/skills/manifests/templates/components: yes; **connectors/agents: no** | n/a (upsert by natural key) |
| **Transactionality** | per-request flush/commit; triggers/schedules commit explicitly | one commit; per-resource errors collected but `success=True` still returned; partial applies commit |
| **is_default demotion** (agents, llmProviders) | bulk-unset others | same — one of the few behaviors implemented twice *consistently* |

### 2.3 The four-copies casing problem

- `schemas/config.py`: ~35 models with **hardcoded camelCase attribute names**
  (no aliases, no generator). Nearly no constraints. Pydantic v1-style
  `@validator` shims on a v2 install.
- `schemas/<resource>.py`: snake_case REST twins, where all the real
  constraints live. Sub-models duplicated up to three times
  (`EnabledStoreConfig` exists in `agent.py`, `store.py`, and as
  `EnabledStoreConfigYaml` in `config.py`).
- `config_apply/*`: hand-written camel→snake attribute reads per field.
- `resource_serializers.py`: hand-written snake→camel dict literals per field.

There is **no snake↔camel helper anywhere in the backend**; the only factored
mapping is `CONNECTOR_AUTH_FIELD_MAP` (2 importers). Ref-shape differences add
friction: config refs by *name* (`connectionName`, `llmProviderName`,
`functionName: "ns/name"`), REST by *UUID* or split fields.

### 2.4 Callers of the apply path

1. `POST /config/apply` (`endpoints/config.py:70`) — user-facing, dryRun/force.
2. `PackageService.install/preview` — `managed_by="pkg:<name>"`,
   `auto_commit=False`, `skip_resource_types=PACKAGE_SKIP_TYPES`.
3. Startup auto-apply (`scheduler/service.py:166`) when
   `CONFIG_FILE` + `AUTO_APPLY_CONFIG` — currently broken (§4).

## 3. Defects found during investigation (fix independently, Phase 0)

These are pre-existing bugs, listed here because parity tests will trip over
them and several are user-visible:

1. **Startup auto-apply always crashes**: `scheduler/service.py:203` reads
   `validation.valid`; the attribute is `is_valid` (`config_parser.py:56`).
   `AttributeError` → `RuntimeError` on every boot with `AUTO_APPLY_CONFIG=true`.
   Lines 210-211 also do `warning.path`/`warning.message` on plain-`str` warnings.
2. **`POST /config/apply` 500s instead of returning validation errors** when
   the config is invalid *and* has warnings: `config.py:112` formats `str`
   warnings as `w.path`/`w.message`.
3. **Secrets applier ignores visibility/ownership** (`resources.py:153`): keyed
   by `name` alone; can match and overwrite a private secret of another user;
   created rows silently get model-default `visibility="shared"`.
4. **Package install never notifies CDC**: the one-shot reload runs only under
   `auto_commit=True` (`service.py:256-275`); packages pass `auto_commit=False`.
5. **Config-applied schedules never reach the running scheduler** (no
   `sinas:scheduler:jobs` publish anywhere in config_apply).
6. **Config/package components are never compiled** (`compile_status="pending"`
   with no consumer).
7. **Function version churn**: config apply creates a new `FunctionVersion` on
   every non-checksum-equal update, even for description-only changes.
8. **`${ENV_VAR}` interpolation is documented but not implemented**
   (`docs-mint/admin/config-manager.mdx:49`, `schemas/config.py:95` comment) —
   only `${{ vars.* }}` (package install) exists.
9. `config_parser` mis-validates webhook/schedule agent targets ("default/None")
   — **already being fixed** in the uncommitted `config_parser.py` WIP on the
   main checkout (`feature/webhook-agent-targets`). Don't duplicate; that WIP
   lands first.
10. Smaller: `package_service.py:524` renders the literal `{{{{name}}}}`
    (missing f-string interpolation); `strict` param of
    `parse_and_validate` is accepted but unused; dead code
    `config_apply/agents.py:172-174`; connector/agent rename without
    uniqueness re-check; `database_triggers` GET/PATCH/DELETE select by name
    globally without user scoping.

## 4. Target architecture

### 4.1 One canonical spec model per resource

Replace the REST-schema/config-schema twin pair with a single **spec model**
per resource, in `backend/app/schemas/spec/<resource>.py`:

```python
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

class SpecModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,       # accepts snake_case (REST) AND camelCase (YAML)
        extra="forbid",
    )

class ConnectorSpec(SpecModel):
    namespace: str = Field(pattern=NAME_RE)
    name: str = Field(pattern=NAME_RE)
    base_url: str                    # YAML: baseUrl, REST: base_url — same field
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    auth: ConnectorAuthSpec | None = None
    ...
```

Properties this buys, all from one definition:

- **YAML config parsing**: `ConnectorSpec.model_validate(yaml_dict)` accepts
  camelCase via aliases. Existing YAML remains valid byte-for-byte.
- **REST payloads**: same model accepts snake_case via field names, so current
  REST clients keep working. (`ConnectorCreate = ConnectorSpec`; `Update`
  variants derived, see §4.5.)
- **Serialization**: `model_dump(by_alias=True, exclude_none=True)` replaces
  the whole of `resource_serializers.py` for the export/package path;
  `model_dump()` (snake) replaces per-field ORM mapping — spec field names ==
  DB column names by construction.
- **Validation parity**: the REST constraints (patterns, ranges, AST checks,
  croniter) move onto the one model, closing the "YAML can write garbage" gap.
- **JSON Schema**: `model_json_schema()` emits the camelCase schema the GitOps
  plan's `/config/schema` endpoint needs (gitops doc #8a/8b) — the
  kind-discriminated `SinasConfig` union becomes a union of these specs.

`CONNECTOR_AUTH_FIELD_MAP` and the v1 `@validator` shims are retired as each
resource migrates. Reference fields stay **by-name** in the spec
(`connection: "name"`, `llmProvider: "name"`, `function: "ns/name"`); the REST
endpoints translate UUIDs→names at the boundary during the compat window
(§4.5). Name-refs are the declarative-native form and what export already
emits.

### 4.2 Per-resource applier

One module per resource under `backend/app/services/resources/<resource>.py`
(the existing `config_apply/` modules evolve into these):

```python
class ApplyContext:
    db: AsyncSession
    actor_user_id: str            # who is doing this (audit, ownership)
    owner_user_id: str            # who owns created resources
    origin: Literal["api", "config", "package", "startup"]
    managed_by: str | None        # None (manual/API), "config", "pkg:<name>"
    config_name: str | None
    dry_run: bool
    effects: SideEffectBus        # see §4.3

class ResourceApplier(Generic[TSpec]):
    kind: ClassVar[str]                       # "connectors"
    spec_model: ClassVar[type[TSpec]]

    async def plan(self, spec: TSpec, ctx) -> ResourcePlan      # diff: create/update/unchanged + field-level changes
    async def apply(self, spec: TSpec, ctx) -> ApplyResult      # upsert; records side effects on ctx.effects
    async def delete(self, key: ResourceKey, ctx) -> None       # shared delete semantics + effects
    async def serialize(self, obj) -> dict                      # ORM row -> spec dict (export)
```

Semantics, defined **once** here and inherited by both channels:

- **Upsert by natural key** (namespace/name, name, email, path — per §2.1).
- **Checksum short-circuit**: current SHA-256 `config_checksum` behavior,
  computed from the *canonical spec dump* instead of ad-hoc dicts (secret-ish
  fields stay excluded from the hash, as today).
- **Validation**: spec-model constraints + referenced-resource existence checks
  (connection exists *and is active*, target function/agent exists, provider
  resolvable) run in `plan` for both channels. `config_parser`'s cross-resource
  reference validation stays as the batch pre-pass; the applier check is the
  per-resource enforcement that today only some CRUD handlers do.
- **Resource-specific rules live here**: FunctionVersion bump *only on code
  change*; agent/provider `is_default` demotion; secret upsert keyed
  (name, visibility, owner); role-permission replace semantics; encryption via
  one injected `EncryptionService`.
- **Ownership state machine** (§4.4) applied uniformly.

`ConfigApplyService` shrinks to an orchestrator: parse `SinasConfig`, iterate
kinds in dependency order, feed each item to its applier with
`origin="config"`, aggregate the plan/result — its public behavior
(`dryRun`, summary, changes, `skip_resource_types`) is unchanged. `plan()`
gives us real **dry-run/diff output with field-level changes** for free, which
the GitOps `sinas diff` command (gitops doc #7b) can consume.

### 4.3 Side-effect bus (post-commit)

Side effects are the worst drift source, and half the bugs in §3 are "effect
fired on one path only" or "effect fired before commit". Appliers never call
Redis/background tasks directly; they record intents:

```python
ctx.effects.add(CdcTriggerChanged(action="update", trigger_id=...))
ctx.effects.add(SchedulerJobChanged(action="add", name=...))
ctx.effects.add(ComponentCompileRequested(ns, name))
ctx.effects.add(DbPoolInvalidate(connection_id))
ctx.effects.add(ExecutorCacheClear())
```

The **caller that owns the transaction** flushes the bus *after* commit
(dedup-aware: many trigger changes → one reload). This fixes, structurally:
package installs skipping CDC notify (auto_commit=False no longer matters —
whoever commits, flushes), missing scheduler notifies on config apply,
components never compiling, effects published before a commit that might
still fail (today's `database_triggers.py` remove-before-flush), and pool
invalidation missing on the config path.

### 4.4 Ownership / `managed_by` state machine

Make the implicit rules explicit and uniform:

| Row state | API write (`origin="api"`) | Config apply | Package apply |
|---|---|---|---|
| `managed_by=NULL` (manual) | write | **adopt + stamp** `managed_by` (today: adopts silently without stamping) | **warn + skip** (never adopt; see below) |
| `managed_by="config"` | write + **detach** (today: only `pkg:` detaches) | write, re-stamp | conflict → warn + skip |
| `managed_by="pkg:x"` | write + detach (as today) | conflict → warn + skip | write if same pkg, else warn + skip |

Packages never adopt a manual row (decided 2026-10-01, PR #206 review):
once adopted, uninstalling the package — or an upgrade that no longer ships
it (§4.8) — would delete something an operator made by hand. Config apply is
the operator's own declaration, so it still adopts. A restore is a manual
write too: the restored resource is unmanaged, or a package upgrade would
prune it straight away again.

Changes vs today: (a) detach-on-manual-edit applies to *all* managed resources
— including secrets, llmProviders, databaseConnections, roles, users, which
currently keep a stale config linkage after a CRUD edit; (b) adoption always
stamps, so package uninstall and `managed_only` export see adopted rows;
(c) detach also runs on DELETE and on side-door mutations
(connector `import-openapi`). Whether API writes to config-managed rows should
*warn* (or, later, be blocked in "locked" instance mode, issue #87) is a knob
on `ApplyContext` — this layer is exactly where gitops mode enforcement will
plug in.

### 4.5 CRUD endpoints become thin translators

Per endpoint, the write handlers reduce to:

1. **Permission check** — unchanged, stays at the API boundary (per-resource
   perms, ownership compare, admin-only gates, the `shared_pool` gate).
   `POST /config/apply` keeps its coarse `sinas.config.apply:all`.
2. **Translate payload → spec**: mostly identity (same model); UUID-refs
   resolved to name-refs here during the compat window (e.g.
   `database_connection_id` → connection name lookup). Response schemas keep
   their current shape (snake_case + ids) — only the write internals change.
3. **Call applier** with `origin="api"`, `managed_by=None`:
   - `POST` = `apply` with a must-create flag (409 on existing key, preserving
     today's contract; config channel keeps pure upsert).
   - `PUT` = `apply` (full spec).
   - `PATCH` = load existing → `serialize()` → merge patch fields → `apply`.
     This makes PATCH "edit the config fragment", which is the semantic we
     want; `exclude_unset` handling is per-field merge as today.
   - `DELETE` = `applier.delete` (shared cascade/soft-delete rules + effects).
4. Commit + flush effects (via the shared request teardown).

**Stays imperative, untouched** (thin routes calling services, no spec):
execute/test/render/compile/OAuth authorize/token routes, parse-openapi,
password reset, role members, api_keys, queue/system/containers/workers ops,
database_schema DDL. These are *operations on* resources, not resource state.
`import-openapi` is a hybrid: it should build a spec and go through the
applier (it mutates `operations`), gaining detach semantics it lacks today.

Deletes in the *declarative* channel: config apply still never prunes (no
change). A `--prune`/`deletePolicy` flag over `managed_by`+`config_name` scope
becomes trivially implementable later via `applier.delete`, but is explicitly
out of scope here (it's a GitOps-phase decision).

### 4.6 What export becomes

`resource_serializers.py` dissolves into `applier.serialize()` per resource
(`model_dump(by_alias=True)` of a spec built from the row).
`config_export.py` keeps only orchestration — and gains `ORDER BY namespace,
name` per query, which together with canonical dumps yields the
**deterministic export** the GitOps plan needs (its #7a). Package
`create_from_resources` uses the same serializers (it already imports them, so
this is a swap). Export/spec parity holes close as a side effect:
`databaseConnections` and `variables` exported, `lastLoginAt` dropped.

### 4.7 Change history (version control)

Added 2026-10-01. The goal of unifying the write path is not only parity: once
every create, update and delete goes through one applier, **every change can be
recorded in one place**, whichever channel made it — console, REST, config apply,
package install or startup apply. That is the basis for version control of an
instance's configuration, including edits made in the UI.

- **Where history lives: inside Sinas.** Each applier write appends a row to
  `config_revisions` **in the same transaction as the change**, so history can
  never disagree with state: a rolled-back change leaves no revision, and a
  committed change always has one. (It is deliberately *not* a post-commit side
  effect.) An external git remote is a later, optional mirror of this log, not a
  dependency: version control has to work on instances with no egress.
- **What a revision holds:** resource kind, natural key, resource id (so history
  follows a resource across renames), action (create/update/delete), the full
  canonical spec after the change (the spec before it, for a delete), a
  field-level diff against the previous state, origin, actor (id and email, as a
  future git author), and `managed_by`/`config_name`.
- **What it never holds: secret values.** A resource whose spec carries secrets
  must record references, not values (`${{ secrets.X }}` — see the GitOps doc).
  Each applier owns that redaction; `schedules` has no secret fields.
- **No-op writes record nothing:** an apply that changes no field (checksum or
  canonical-spec equal) creates no revision and fires no effect.
- **Ordering:** a global monotonically increasing id, so the log is totally
  ordered without per-key revision counters that concurrent writers could race.
- **Read API:** `GET /config/history` (filter by kind, key or resource id; keyset
  pagination; field values and specs opt-in via `include_details`) and
  `GET /config/history/{id}`, behind `sinas.config.read:all`.
- **Restore:** `POST /config/history/{id}/restore` (`sinas.config.apply:all`)
  brings a resource back to a revision's state through its normal applier. A
  deleted resource is recreated under its original id and original owner
  (revisions record `owner_user_id`); an existing one is reverted, renames
  included. The restore is itself a revision (`restored_from_id`).

Not yet: a console history view and the git mirror. Each builds on this log
without changing it.

### 4.8 All-or-nothing applies

Added 2026-10-01. A config apply or package install with **any** failing
resource now changes nothing (resolving the "Transactionality" row of §2.2):
`success=False`, every error listed, queued effects discarded, and the
transaction rolled back by whoever owns it. A dry run reaches the same verdict.
Deletion remains out of the declarative channel (§4.5); with restore available,
pruning on *package upgrade* (removing what a new version no longer ships,
scoped to that package) becomes safe to add, and is the natural next step.

## 5. Migration sequencing

Principles: one resource kind at a time; REST API contract frozen (responses
byte-compatible, error codes preserved); every migrated resource lands with a
parity test *before* its old code path is deleted; config YAML remains
backward-compatible throughout (aliases guarantee it).

**Pilot: `schedules`.** Worst user-visible drift (config-applied schedules are
invisible to the running scheduler until restart), small surface (~6 fields),
exercises every interesting mechanism: name-ref target (function *or* agent —
coordinate with the in-flight `feature/webhook-agent-targets` branch, which
touches the same schema), croniter validation missing on the config side, a
Redis side effect, detach, and the summary/dry-run plumbing. If the
architecture works here, it works everywhere.

**Then `databaseTriggers` + `webhooks`** (same `integrations.py` family, CDC
effect bus, existence-check parity), **then `connectors`** — the original
motivator: retires `CONNECTOR_AUTH_FIELD_MAP` and the four-place OAuth-field
duplication, and pulls `import-openapi` through the applier.

**Then the long tail in dependency-order batches**, hardest last:
functions/components (versioning + compile effects), skills/queries/
collections/stores/manifests/templates (mechanical), secrets (scoping fix
rides along), agents (biggest model, normalizers become spec validators),
and finally identity/data-sources (roles/users/llmProviders/
databaseConnections — admin-only endpoints, UUID-ref translation, pool
invalidation effect).

### Slice 2: webhooks + databaseTriggers (decisions)

- **Trigger names are unique per owner.** A REST write addresses the
  owner's own trigger, and checks renames and function references against
  the owner (it runs as them). A config or package apply declares *the*
  trigger by that name: the one that source already manages, else the
  applying user's, else the oldest other one (ownership rules then adopt or
  skip). Scoping it to the applying user alone would create a second trigger
  on the same table whenever another admin applied the same config.
- **Connections are referenced by name in the spec** (as config always did);
  REST still takes `database_connection_id` and translates. Async
  `current_spec` / `write_row` hooks on the applier resolve name ↔ id.
- **CDC gets per-trigger `add`/`update`/`remove`** after commit, from every
  channel. Config apply's blanket `reload` is gone: it never restarted a
  running poll loop, so changed settings waited a full interval.
- **The poll bookmark resets** when connection, schema, table or poll column
  changes (it pointed at other data; with a type change, every poll failed).
  `last_poll_value` / `error_message` are runtime state, not spec.
- **Only the target type's own reference is stored**, for webhooks and
  triggers alike. A PATCH that switches type must name the new target: a
  stale reference stored for another type used to be picked up silently, with
  no existence or permission check.
- **A webhook's `is_active` is operator state unless declared.** Config may
  now declare `isActive` (and export keeps disabled webhooks, with
  `isActive: false`); left out, a new webhook is active and an existing one
  keeps its state, so a re-apply never re-arms a webhook someone disabled.
  Generic mechanism: `ResourceApplier.keep_unless_declared`. REST lookups no longer filter on `is_active` — a disabled
  webhook used to be a 404 for GET, PATCH and DELETE, so it could never be
  enabled again.
- **Strictness follows what worked.** Since applies are all-or-nothing (and
  run at boot), the specs refuse only YAML that never worked at runtime:
  poll interval or batch size below 1, dedup TTL below 1, unknown HTTP
  methods, quotes in SQL identifiers, whitespace in webhook paths, and
  targets that don't exist. Things that ran keep working: empty `operations`
  (the poller ignores them), intervals and TTLs above the API's caps,
  lowercase `httpMethod`, any other path characters, any target namespace.
- Webhook permission checks (`chat:all` on an agent target, `run:own` on a
  pipeline) stay at the REST boundary: they are about the caller, not the
  resource.

### Slice 3: connectors (decisions)

- **One nested spec replaces the field maps.** `CONNECTOR_AUTH_FIELD_MAP`,
  `TOKEN_RESPONSE_PATH_FIELD_MAP` and the hand-written operation/retry
  mappings are gone: the spec's camelCase aliases translate YAML, and
  storage is the spec's snake_case dump (without nulls, which REST used to
  store and config didn't).
- **History never shows secret values.** Header values, `auth.token_params`
  values and URL credentials (userinfo and query of `base_url`, the token
  and authorize URLs and operation paths) are redacted with a
  keyed HMAC — stable, so a changed value still shows as a change, but not
  reversible by hashing guesses. The real values are kept encrypted in
  `config_revisions.secret_state` and used only by restore, so a deleted
  connector still comes back exactly. `auth.secret` is a Secret's *name* and
  stays readable. Generic hooks: `history_spec` / `secret_values` /
  `with_secrets`; any later kind with secret-bearing fields uses them.
- **Repointing OAuth drops stored user tokens.** When auth type, token URL,
  client id or authorize URL changes, users' `ConnectorOAuthToken` rows for
  that connector are deleted and they sign in again: otherwise a refresh
  posts the old refresh token, with the client secret, to the new token URL.
- **`is_active` is operator state unless declared** (as for webhooks).
- **Package upgrades now remove connectors** a new version no longer ships,
  like every applier kind; their users' OAuth tokens go with them (a restore
  brings the connector back, users reconnect).
- `import-openapi` is an ordinary edit now: validated, recorded, detaching.
- Strictness follows what worked: refused are unknown auth types (requests
  went out unauthenticated), OAuth grants missing their URLs/client id,
  zero retries or timeout, unknown request-body mappings (parameters were
  dropped). Lowercase methods, HEAD/OPTIONS, unknown backoff/position/client-auth values
  (they always fell back to a default) and duplicate operation names keep
  working.

### Slice 4: skills, templates, queries (decisions)

- **Shared REST plumbing.** `services/resources/rest.py` holds what every
  endpoint does around an applier (parse, lock the authorized row, write,
  commit, publish). These three kinds use it; earlier kinds keep their own
  copies until they are touched again.
- **`is_active` is operator state unless declared**, for all three. Exports
  now include disabled ones (templates used to be left out) with
  `isActive: false`.
- **Queries hold the connection by name** in the spec (config's form); REST
  still sends an id, which the endpoint authorizes (permission + active)
  and turns into the name. The applier checks that the connection exists,
  accepting one the same config declares in a preview.
- **A template PATCH keeps null-clears-the-field** for optional fields;
  nulling `html_content` is a 422 instead of a 500 at flush.
- Strictness follows what worked: refused are a query operation other than
  read/write (it ran through the write path, so a SELECT returned only a
  count and got no LIMIT), a zero timeout or max-rows, and a namespace with
  "/". Operation case is forgiven. Empty skill text stays accepted from
  config (REST still requires it).
- The default-template seed looked `otp_email` up by name alone: a template
  of that name in any other namespace crashed startup. It is scoped to
  `default` now.

### Slice 5: components (decisions)

- **No build step.** A component is an HTML page (`source_code` is its body:
  markup, `<style>`, `<script>`), served as is with a small vanilla `sinas`
  client inlined by the render endpoint (no npm, no CDN). The esbuild builder
  service, the compile pipeline and its statuses, and React/`@sinas/ui` from
  unpkg are gone — so the compile effect this design anticipated isn't
  needed. Migration `h1t2m3l4c5p6` drops the build columns, folds
  `css_overrides` into the page as a `<style>`, and drops the unused
  `version`/`is_published`.
- **One write path** like every applier kind: REST, config and packages
  share validation, ownership and history. A REST delete is a real delete
  (recorded, restorable from history) instead of flagging the row inactive,
  which kept the name taken and could not be undone. `is_active` is operator
  state unless declared. A bare store reference in config still means
  read-write.

### Slice 6: stores, collections, manifests (decisions)

- **Same shape as slice 4.** REST, config apply, package install/upgrade
  and uninstall share one applier per kind, so writes are recorded (and
  restorable), packages never adopt hand-made rows, two config files no
  longer overwrite each other, and an upgrade prunes what a new version
  dropped. Uninstall goes through the applier instead of a bulk delete.
- **Specs are as lenient as config was** (e.g. `defaultVisibility`,
  `exposedNamespaces` keys); the REST schemas keep their stricter checks.
- **REST keeps its old update semantics:** null means "leave as is", and a
  store PUT still ignores namespace/name. A manifest PUT may rename.
- **Manifests:** `storeDependencies` is now applied (config dropped it), and
  `is_active` is operator state unless declared (config switched every
  manifest it touched back on). Export includes `isActive`.
- **Collections:** the upload hooks must name existing functions on every
  channel (only config's parser checked before). The runtime upload that
  creates a missing collection goes through the applier too.
- Store states and collection files are removed by the database cascade
  (`passive_deletes`), not loaded one by one. Collection blobs in storage
  are still not removed on delete — separate issue.

### Slice 7: functions (decisions)

- **One write path:** REST, config, packages and restore share
  FunctionApplier: history, restore, prune on upgrade, recorded uninstall.
- **Versions** snapshot code and schemas: v1 on create, the next one only
  when code or a schema actually changes — on every channel (REST used to add
  one whenever code was sent). Versions are deleted with the function and
  are not part of history; a restore starts again at v1.
- **`is_active` is operator state** ("disabled"), kept unless declared, as
  are `sharedPool` and `requiresApproval` (config already left those as they
  were when unset). Config used to treat a disabled function as reclaimable
  and switch it back on. Export includes disabled ones.
- **REST-only gates stay REST-only:** code execution being off, the
  shared-pool permission, and the syntax/schema checks. Config and packages
  may still declare functions (decided: they just can't run).
- The dead `function_ids` cache (keyed by bare name) is gone.

### Parity test strategy

New `backend/tests/unit/config_parity/` harness (there are currently **zero**
tests on apply/export/serializers):

1. **Two-path equivalence**: for each resource kind, one fixture spec; apply
   via the REST handler and via config-apply into separate transactions;
   snapshot ORM rows (excluding id/timestamps/managed_by); assert identical.
   Run for create, update, no-op re-apply (checksum), and rename where legal.
2. **Round-trip**: spec → apply → `serialize()` → spec dump == original
   (normalized); export → apply → export is a fixed point (determinism).
3. **Effect assertions**: mock bus; assert the same effect set from both
   channels (e.g. schedule create → `SchedulerJobChanged("add")`).
4. Existing live integration suite (`tests/integration/suites/config_apply.py`)
   keeps passing untouched — it pins the external contract.

## 6. GitOps-refactor alignment

This design is the enabling layer for `docs/design/gitops-agent-native.md`;
nothing here conflicts with its decisions:

- **Schema endpoint + kind-discriminated union (#8a/b)**: spec models with
  camelCase aliases *are* the union members; `/config/schema` serves
  `model_json_schema()` of them. This design supersedes the config-twin
  models the union would otherwise have been built from — build the union on
  specs, do it once.
- **Deterministic export (#7a)** falls out of §4.6.
- **Diff (#7b)**: `plan()` provides server-side field-level diffs; the CLI can
  render them.
- **Instance modes (#6, deferred)**: the applier + ownership state machine is
  the single choke point where open/protected/locked enforcement plugs in —
  instead of guarding "every mutating route" as the gitops doc feared.
- **Structured validator errors (#8c)**: the uncommitted `config_parser.py`
  WIP continues independently; spec-model pydantic errors give it per-field
  paths for free once resources migrate.
- **CLI/skills stay in `sinas-skills`** — untouched; they talk to the same
  endpoints.

## 7. Phased issue breakdown (ready to file)

Labels suggested: `epic:config-unification`. Each phase is independently
shippable; 1–2 are prerequisites for 3+.

**Phase 0 — bug fixes (no design dependency, can land during 0.3.0 stabilization):**
1. *Fix startup auto-apply crash (`validation.valid` → `is_valid`; warning formatting)* — `scheduler/service.py:203-211`, plus the same `str`-warning bug in `endpoints/config.py:112`.
2. *Config apply: key secrets by (name, visibility, owner); add `visibility` to secret config schema* — `config_apply/resources.py:137`.
3. *Emit CDC + scheduler notifications after package install / config apply commits* — minimal pre-bus fix: move the notify out of the `auto_commit` branch; add the missing scheduler publish. (Superseded later by Phase 2 bus, but cheap and user-facing now.)
4. *Compile components after config/package apply* (consume `compile_status="pending"` or schedule compile post-commit).
5. *Function versions: bump only when code changes in config apply* — `config_apply/resources.py:376`.
6. *Docs: remove or implement `${ENV_VAR}` interpolation claim* (`config-manager.mdx`, `schemas/config.py:95`).

**Phase 1 — foundations:**
7. *Parity test harness + baseline fixtures for all 19 kinds* (two-path equivalence, round-trip, effect assertions; document known-drift waivers per kind until migrated).
8. *Spec-model base (`SpecModel` with `to_camel` alias generator) + `backend/app/schemas/spec/` layout; migrate `SinasConfig`/`ConfigSpec` root to pydantic-v2 idioms* (retire v1 `@validator` shims at the root; per-resource twins stay until each migrates).
9. *`ResourceApplier` protocol, `ApplyContext`, `SideEffectBus` + post-commit flush wiring in request teardown, config apply, package install, startup apply.*

**Phase 2 — pilot:**
10. *Migrate `schedules` to a `ScheduleApplier`* (spec model, both channels, scheduler-notify via bus, croniter parity, parity tests green; coordinate schema with `feature/webhook-agent-targets`).

**Phase 3 — integrations family:**
11. *Migrate `databaseTriggers`* (per-trigger CDC effects; connection-active + function-existence checks on both channels; fix global-by-name lookup scoping).
12. *Migrate `webhooks`* (target existence parity; agent-target support per in-flight branch).

**Phase 4 — connectors (the motivator):**
13. *Migrate `connectors`; retire `CONNECTOR_AUTH_FIELD_MAP`; route `import-openapi` through the applier; add rename-conflict check.*

**Phase 5 — resources long tail (one issue per batch):**
14. *functions + components* (version/compile semantics unified).
15. *skills, queries, collections, stores, manifests, templates.*
16. *secrets* (rides Phase 0 fix into the applier; REST upsert semantics preserved).
17. *agents* (normalizers → spec validators; UUID→name provider translation at REST boundary; rename-conflict check).

**Phase 6 — identity & data sources:**
18. *roles + users* (permission/identity replace semantics into appliers; guardrails — Admins protection, SUPERADMIN — stay at API layer).
19. *llmProviders + databaseConnections* (pool-invalidation effect; is_default demotion; detach-on-CRUD-edit begins applying to these kinds per §4.4).

**Phase 7 — convergence:**
20. *Ownership state machine everywhere: adopt-with-stamp, detach on DELETE and side-door mutations, detach for all managed kinds.*
21. *Export via applier serializers; deterministic ordering; export `databaseConnections`/`variables`; drop `lastLoginAt`; delete `resource_serializers.py`.*
22. *Delete legacy `config_apply` per-resource code + config-twin models; `schemas/config.py` becomes root/union + response models only.*

## 8. Open questions

1. **POST-on-existing**: keep strict 409 on the REST channel (proposed), or
   adopt upsert everywhere? Secrets' REST POST is already an upsert — proposal
   keeps per-resource compat quirks at the translator layer.
2. **Warn vs. silent on API-writes-to-config-managed rows** (pre-modes): add a
   response warning header/field now, or defer entirely to issue #87?
3. **UUID-ref deprecation**: once name-refs work end-to-end, do we deprecate
   `database_connection_id`/`llm_provider_id` in REST payloads (console
   migration needed), or keep dual-ref support indefinitely?
4. **Where per-resource permission strings live**: endpoints keep them
   (proposed), or move a permission descriptor onto the applier so
   `/config/apply` could optionally enforce per-resource perms for non-admin
   appliers later?
5. **Pruning**: confirm deletes stay out of the declarative channel until the
   GitOps phase defines `deletePolicy`.
