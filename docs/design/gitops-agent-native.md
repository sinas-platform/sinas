# Sinas GitOps & Enterprise Agentic Coding — Implementation Investigation

Status: investigation (pre-implementation). Branch `worktree-gitops-agent-native`.
Maps the design brief onto the current codebase and proposes the smallest
change per intent. No code written yet.

> **Repo topology.** This work spans two repos under
> `/Users/kjeld/Code/sinas-platform/`: the platform (`sinas/`, this worktree —
> Python/FastAPI backend + React `console/`) and **`sinas-skills/`** — a
> separate TS monorepo holding the **CLI** (`packages/cli`), **create-app**
> scaffolder (`packages/create-app`), and the two agent **skills**
> (`skills/sinas-app`, `skills/sinas-package-author`). The CLI is the CI
> backbone the brief refers to; it already exists but is minimal.

## TL;DR — what the codebase already gives us

The apply/export core is in very good shape for GitOps; the gaps are almost
entirely at the *edges* (a real CLI, file references, diff, dependency
ordering, instance modes, a single knowledge source). Concretely:

- **Idempotent, ownership-aware reconcile already exists.** Every managed
  resource carries `managed_by` / `config_name` / `config_checksum`
  (`config_apply/service.py`). Re-apply computes SHA-256 hashes and reports
  `create/update/unchanged/delete`. This is the reconcile engine a GitOps
  loop needs — we mostly need to *drive* it well, not rebuild it.
- **The package/config split the brief wants is already half-built.**
  `PACKAGE_SKIP_TYPES = {roles, users, llmProviders, databaseConnections}`
  (`package_service.py:49`) — packages already cannot create identity/infra;
  those are stripped at install and only `config_parser.py:97-114` warns.
  Intent #1 is mostly about *formalizing and enforcing* this existing seam.
- **Secret definition vs value is already separable.** `SecretConfig.value`
  is optional and excluded from the resource hash
  (`config_apply/resources.py:164`), so names/definitions can live in git and
  values can be supplied per-env without causing drift. Intent #2's secret
  requirement is essentially already satisfied at the model layer.
- **Export exists** (`config_export.py`, `GET /config/export`) but is not
  deterministic and always emits `metadata.name: exported-config`.
- **The `sinas` CLI exists but is minimal.** `@sinas/cli`
  (`sinas-skills/packages/cli`, TypeScript/commander, thin fetch wrapper over
  the Management API). Commands: `init` (delegates to create-app), `login`,
  `validate`, `preview`, `install`, `status`, `add`. It is the right home for
  the CI-backbone work but is missing almost everything GitOps needs — see #7.

The big net-new pieces: (a) **extend the existing CLI** with CI-auth
(env vars), `--json`, deterministic export, diff, file-reference resolution,
and workspace/multi-env orchestration — none of which exist today; (b) instance
**modes** (open/protected/locked) — nothing like this exists; (c) a single
generated source for the package-authoring knowledge + JSON Schema (four
hand-maintained copies exist today, no schema artifact); (d) `requires`/
topo-ordering across packages.

---

## Current-state reference (where everything lives)

| Concern | Location |
|---|---|
| Config/package schema (Pydantic, authoritative) | `backend/app/schemas/config.py` — `ConfigSpec:433`, `SinasConfig:471` |
| Apply orchestrator (order, managed_by, checksums) | `backend/app/services/config_apply/service.py:119` |
| Appliers | `config_apply/{identity,data_sources,resources,agents,integrations}.py` |
| Reference/semantic validation | `backend/app/services/config_parser.py` (`parse_and_validate:63`, `_validate_references:121`) |
| Export (DB→YAML) | `backend/app/services/config_export.py:61` |
| Package install + `${{ vars.* }}` engine | `backend/app/services/package_service.py` (install:76, `_resolve_variables:482`, `_VAR_PATTERN:463`) |
| Package skip-types (the split) | `package_service.py:49` |
| Config API | `backend/app/api/v1/endpoints/config.py` (validate:29, apply:70, export:129) |
| Package API | `backend/app/api/v1/endpoints/packages.py` (install:22, preview:49, create:78, export:172, delete:148) |
| Agent-facing tools | `services/package_tools.py`, `services/config_tools.py` |
| Auth / API keys / scopes | `backend/app/core/auth.py` (`verify_jwt_or_api_key:600`, `create_api_key:492`); `core/permissions.py` |
| Roles/users/permissions | `backend/app/models/user.py` (`Role:67`, `UserRole:97`, `RolePermission:116`) |
| Secrets | `backend/app/models/secret.py`; apply `config_apply/resources.py:144` |
| Console API client | `console/src/lib/api.ts` |

**CLI + skills (separate repo `sinas-skills/`):**

| Concern | Location |
|---|---|
| CLI package | `sinas-skills/packages/cli` — `@sinas/cli` (commander); entry `src/index.ts`, HTTP `src/api.ts`, auth `src/config.ts` (`.sinas/config.json`), file discovery `src/files.ts` |
| CLI commands | `src/commands/{init,login,validate,preview,install,status,add}.ts` |
| create-app scaffolder | `sinas-skills/packages/create-app` — React+Vite template `templates/react-vite/` (scaffolds a single flat `sinas-package.yaml`, no config, no envs) |
| Knowledge copies (4) | `sinas-skills/skills/sinas-package-author/SKILL.md` (~437 lines) + `skills/sinas-app/SKILL.md`; **copied verbatim** into create-app template `.claude/skills/` via `sync-skills`; in-platform `config_examples/sinas-package-author.yaml` (skill+agent); `docs-mint/admin/packages.mdx` |

Notable absences (confirmed): no git plumbing, no instance mode/lock, no JSON
Schema artifact anywhere, no `requires`/topo-sort, no client-side diff, no
file references (`codeFrom:`/`sqlFrom:`), no `instanceSettings` block in
`ConfigSpec` (instance settings are env-only). CLI absences: no env-var/CI
auth (only interactive `login` → `.sinas/config.json`), no `--json`, no
`export`/`diff` command, no `--dry-run` on `install` (only the separate
`preview`), no multi-env/workspace/values, no file-ref inlining, no
`requires` ordering.

---

## Intent-by-intent implementation approach

### #1 Packages carry functionality; config carries the rest (the contract)

Already 80% there via `PACKAGE_SKIP_TYPES`. To formalize:

- **Make the config profile explicit, not a warn-and-strip.** Today a package
  with `roles:` gets a warning and silent strip (`config_parser.py:97-114`).
  Promote this to a first-class distinction: keep one `SinasConfig` root but
  add a **validation mode / profile** that (a) for `kind: SinasPackage`
  *rejects* the config-only kinds (roles, users, llmProviders,
  databaseConnections, secrets-with-values, instance settings) instead of
  warning, and (b) for the instance config, restricts to exactly those kinds
  plus manifests. This is the brief's "your call: new kind, subset profile, or
  validation mode" — recommend **validation mode over the existing schema**
  (smallest change; no new model duplication).
- **Manifests are the contract.** `ManifestConfig` already exists
  (`ConfigSpec.manifests`) and `_validate_references:227` checks manifest
  resource refs. Extend manifests to be a package's *requires* declaration:
  a package declares required roles/permissions/secrets/providers/connections
  by name; `status` (new) verifies the instance config provides/grants them.
  This is the join between the two files. Needs: a manifest "requirement" shape
  (require a permission grant, a secret name w/ value present, a provider) and
  a status checker that reads live DB state.
- **Instance settings gap:** there is no `instanceSettings` in `ConfigSpec`.
  If settings must be version-controlled, add a bounded `settings:` block
  (allow-list of safe keys only) — but this is deferrable; flag it.

### #2 Users, roles, permissions, secret definitions

- Roles + grants already round-trip through config (`RoleConfig`,
  `RolePermissionConfig`; apply is declarative delete-recreate at
  `identity.py:109`). These belong in the (access-restricted) config repo. No
  model change needed.
- **User→role bindings by email already exist** (`UserConfig.email` +
  `roles: list[str]`, `identity.py` `apply_user_roles`). The binding key is
  email→role-names, applied when the user exists. Gap vs brief: when the user
  does *not* exist, apply currently just proceeds/warns — we need a **"pending
  binding" report** surfaced in `status` rather than a silent skip.
- **SSO-forward binding subject:** the brief wants a binding whose *subject*
  can be an email now and an IdP group later. Today the subject is implicitly
  an email on `UserConfig`. Recommend introducing a small **binding model**
  with a typed subject (`{type: email|group, value}`) so the later SSO swap is
  additive. This can be a new `roleBindings:` list in the config profile that
  supersedes the inline `UserConfig.roles` for git-managed instances (keep
  inline working for back-comp).
- Secrets: definition-vs-value is already handled (optional `value`, excluded
  from hash). Need: `status`/`diff` to **compare secrets by existence only**
  and report missing values per-env (the info is already there — value is
  write-only and hash-excluded; just surface "declared but no value" per env).

### #3 Repo layout: dirs per env, one branch, packages defined once

Pure CLI/convention layer — **no backend change**. Proposed layout:

```
repo/
  sinas.workspace.yaml        # declares envs + packages (the pin lives here)
  packages/
    <pkg>/package.yaml        # single definition; may use codeFrom: etc.
    <pkg>/functions/*.py
    <pkg>/queries/*.sql
  envs/
    dev/values.yaml           # ${{ vars.* }} values, secret names, provider choices
    dev/config.yaml           # roles/grants/bindings/secret-defs (config profile)
    acc/...
    prod/...
```

- `sinas.workspace.yaml`: per-env `{url, tokenSource (env var), valuesFile,
  configFile, mode, packages[], pin?}`. `dev` tracks `main`; `acc`/`prod`
  carry a `pin` (commit/tag) → promotion is a pin bump in a PR, not a merge.
- The CLI resolves, per env: workspace pin + that commit's package/config
  content + env values → apply. State stays derivable from the repo. No
  overlays/patches (explicitly rejected by brief; values cover the deltas).
- `${{ vars.* }}` engine already exists server-side. Decide: keep resolution
  server-side (values passed as `variables` on install — already supported) vs
  resolve client-side in the CLI. Recommend **client-side resolution for file
  refs, server-side for vars** (vars already work; don't move them).

### #4 Code as files — inline stays first-class

- Add `codeFrom:` / `sqlFrom:` / `contentFrom:` (and `sourceCodeFrom:` for
  components, `htmlContentFrom:` for templates) as optional siblings of
  `code:`/`sql:`/`content:`.
- **Resolution happens server-side, not in the CLI** (decision — supersedes the
  brief's "server needs no change" note). Rationale: if only the CLI resolves,
  the **console** and the **in-platform package-author agent** gain nothing —
  they'd stay stuck with giant inline YAML. Server-side resolution means all
  three clients get file-refs for one implementation.
- **Transport: one endpoint, two content types.** Keep today's raw-YAML body
  (`PackageInstallRequest.source`, `packages.py:23`) unchanged for
  back-compat; add a `multipart` **zip** upload = entry YAML + referenced files.
  Server unzips to a temp dir (with **zip-slip / path-traversal guards + size
  limits**), locates the entry doc, resolves each `*From:` against the bundle
  root, then feeds the **existing** parse/validate/apply untouched.
- **Resolve-on-ingest → DB model and reconcile don't change at all.** Files are
  inlined at ingest; storage/checksums/`managed_by` stay exactly as today.
- The **CLI** then just zips-and-uploads instead of resolving — *simpler* CLI.
  The **console** gains a "upload bundle (.zip)" path (`api.ts` + Packages/
  ConfigManager UI). A plain-YAML upload with a dangling `*From:` → a clear
  validation error ("bundle required for file references").
- Validation: specifying both inline and `*From:` for the same field is an
  error — enforce in the Pydantic model (a `@model_validator`) so it holds for
  every client and shows up in the generated schema (#8).
- **Export must round-trip to files.** `config_export.py` currently inlines
  everything. For drift diffs to stay meaningful, export needs a "files mode"
  that writes bulky fields to `.py/.sql/.md` and emits `*From:` refs. This
  pairs with deterministic export (#7).
- The `package.yaml`-per-resource-type split (e.g. `connectors.yaml`) is
  flagged/deferrable — file references likely relieve most of the pressure
  (a 100-op connector's bulk moves to referenced files). Defer.

### #5 Shared resources via base packages + `requires`

- **Net-new: a `requires:` field** on package metadata (`PackageMetadataConfig`,
  `config.py:461`). `install` today has a fixed apply order and **no
  cross-package graph** (`config_apply/service.py` uses a hardcoded per-kind
  sequence). The CLI (or a new install-planner) must topo-sort packages by
  `requires`, install bases first, and **refuse uninstall when a dependent
  requires the target**. There is a `dependencies` concept in the spec but it
  is *pip* dependencies (`api/v1/endpoints/dependencies.py`) — different thing,
  don't conflate.
- Simplest home: CLI-side ordering over the workspace's `packages[]`, plus a
  server-side guard on `DELETE /packages/{name}` that checks no installed
  package lists it in `requires` (needs `requires` persisted on the `Package`
  row).

### #6 Environments and modes (open / protected / locked) — DEFERRED

> **Status: deferred, tracked in [issue #87](https://github.com/sinas-platform/sinas/issues/87)**
> (not in the first implementation pass). Dev-only GitOps works without modes — RBAC-scoped CI
> tokens (#7) cover the near-term. Modes become necessary when we lock prod
> against console/agent mutation for enterprise customers. Design retained below
> so the later pass has a starting point.

**Nothing exists today** — this is the key enterprise-security piece and fully
net-new. Mutation is RBAC-only; console and CI hit identical endpoints.

- Add an instance **mode** setting (a single row / setting): `open` (console
  edits + agent installs allowed), `protected` (mutations only via CI token),
  `locked` (mutations only from the linked repo identity; console + agents
  read-only for config).
- Enforcement point: a dependency/guard on the mutating endpoints
  (`/config/apply`, `/packages/install`, `/packages/*` mutations, and the
  per-resource CRUD routes the console uses). Gate = `f(mode, caller-identity)`.
  Needs a way to distinguish caller class — extend API-key metadata with a
  `kind`/`source` (e.g. `ci`, `repo`, `console`, `agent`) so `protected`/
  `locked` can allow the right ones. API keys already carry a `permissions`
  dict (`auth.py:492`) — add a scope/label there.
- This one setting is what makes "agents that install packages" acceptable:
  agents run against `open` dev instances; prod is `locked`.

### #7 CI-grade CLI

The headline deliverable — but it's an **extension of the existing `@sinas/cli`
(TypeScript)**, not a new tool. Keep TypeScript: the CLI is already a thin HTTP
client and the server stays the source of truth for schema/`${{vars}}`
validation (both already server-side via `/config/validate` + install-time
substitution). A Python rewrite would duplicate that for no gain. What each
command needs:

- `validate` — exists (`POST /config/validate`); add file-ref resolution first,
  `--json`, and glob/workspace file discovery (today only `./sinas-package.yaml`
  + `./sinas-config.yaml`, `files.ts`).
- `install` / `apply` — exists (config-then-package, `install.ts`); add
  topo-order (#5), file-ref resolution (#4), `--env`, `--dry-run`, and wire the
  `variables`/values path (accepted by the API layer but never exposed as a
  flag today).
- **`export` (deterministic)** — refactor `config_export.py` for **stable
  ordering** (sort every list by name/kind, canonical key order, stable YAML
  dump) and a files mode (#4). Prerequisite for diff. Also fix the hardcoded
  `metadata.name: exported-config`.
- **`diff` (net-new)** — live instance (export) vs repo (resolved). Secrets
  compared by **existence only**. Server already has a dry-run plan
  (`apply` with `dryRun:true` → `ResourceChange[]`); a true file-vs-file diff
  should be **client-side** over deterministic export for reviewable output.
- **Non-interactive auth is the biggest CLI gap.** Today auth is only
  interactive `login` → `.sinas/config.json` (`config.ts`); no env-var path.
  Add `SINAS_URL` / `SINAS_TOKEN` (and per-env token source resolved from the
  workspace file) so CI never writes a config file. Machine-readable output
  (`--json`) and meaningful exit codes (a `die(msg, code)` helper exists but is
  unused; codes are ad-hoc `1`/`130` today).
- **Scoped CI tokens** — stop recommending `sinas.*:all`. Define minimal
  scope sets per env: dev CI = `config.apply`+`packages.install`; prod CI =
  same but only usable while mode allows. Tighten the docs/skill that currently
  say "make an all key."
- Ship a reference **GitHub Actions workflow**: PR → validate + `diff` posted
  as a comment; merge → per-env install, prod behind environment approvals.

### #8 One source for agent-facing knowledge

- The schema is authoritative *only* as Pydantic (`config.py`). It is then
  hand-restated in **four** places that can drift: `skills/sinas-package-author/
  SKILL.md` (~437 lines, and copied verbatim into the create-app template via
  `sync-skills`), `config_examples/sinas-package-author.yaml` (the in-platform
  skill+agent), and `docs-mint/admin/packages.mdx`. Concrete drift risks the
  skill restates by hand: "no `pipPackages` → use `spec.dependencies`",
  "references are `namespace/name`", the CAN/CANNOT-create resource lists,
  "unknown keys rejected". **No JSON Schema exists anywhere.**
- Plan: **generate a JSON Schema from the Pydantic models** and **serve it from
  the instance** — no committed copies anywhere (a schema is version-bound to
  the server; any vendored copy drifts by construction). One endpoint,
  parameterized by kind:
  - `GET /config/schema` → full union (`oneOf` package/config)
  - `GET /config/schema?kind=SinasPackage` → package branch only
  - `GET /config/schema?kind=SinasConfig` → config branch only

  Implementation: add the route to `config.py` returning
  `SinasConfig.model_json_schema()` (cache in a module global; models are
  static). Public, no auth — it's the contract. `extra="forbid"` already emits
  `additionalProperties:false`, so the schema rejects `pipPackages` exactly like
  the server.
- **Refactor `SinasConfig` into a `kind`-discriminated union**
  (`SinasConfigDoc | SinasPackageDoc`, `Field(discriminator="kind")`). This (a)
  makes the `metadata`-vs-`package` conditional-requires express *in* the schema
  (a `oneOf`), which today are imperative `@validator`s (`config.py:483-497`)
  invisible to JSON Schema; (b) lets the package branch **forbid the config-only
  kinds** (`roles/users/llmProviders/databaseConnections`) at the schema level —
  turning intent #1's split from a server-side warn-and-strip into a
  machine-readable contract editors/CLI enforce offline; (c) retires the v1
  `@validator` shims. **What the schema still can't cover:** reference/existence
  checks (`config_parser._validate_references`) need DB state — server stays
  authoritative for those forever. Schema = shape (~90%); server = references.
- Make validator errors **carry the fix** (structured `{path, message, hint,
  code}` instead of today's `path: message` string, `config_parser.py:37-45`)
  so the skill can shrink to pointers instead of restating rules that then drift.
- Consumers (all fetch-from-instance, zero copies): CLI `validate` does a local
  ajv shape-check against `/config/schema` before the server round-trip (and
  feature-detects: 404 → skip); create-app scaffolds a
  `# yaml-language-server: $schema=<instance>/api/v1/config/schema?kind=...`
  header per file; skills point at the schema instead of restating field rules.
- Regenerate the copies from the canonical source. **create-app exists**
  (`sinas-skills/packages/create-app`) and currently scaffolds only a single
  flat `sinas-package.yaml` with no config/envs/workspace — update it to emit
  the new repo layout (#3: workspace file + `envs/` + `packages/`), and update
  the skills/docs to match. The `sync-skills` build step already gives us one
  place to regenerate the bundled copy.

---

## Suggested sequencing (contract-first, smallest steps)

0. **Schema endpoint + `kind`-discriminated union + structured errors** (#8a/b/c).
   Smallest, highest-leverage: `/config/schema`, retires v1 validators, and
   makes the package/config split a machine-readable contract that unlocks #1.
1. **Deterministic export + server-side file resolution (yaml-or-zip)** (#7/#4).
   Unblocks diff and drift; gives console + agent file-refs too. Server-local.
2. **CLI: workspace file + env resolution + zip upload** (#3/#7): `validate`,
   `export`, `diff`, `apply`, CI-auth, `--json` — wrapping existing endpoints.
3. **Config profile + manifests-as-requires + `status`** (#1/#2): enforce the
   package/config split (rides on the schema union); pending-binding +
   missing-secret reporting.
4. **`requires` + topo-order + uninstall guard** (#5).
5. **Knowledge consolidation + GH Actions reference** (#8 remainder): regenerate
   skills/docs/create-app against the schema; ship the reference CI workflow.
6. **Scoped CI tokens** (#7): retire `sinas.*:all`, define per-env scope sets.

Deferred (tracked): **instance modes** (#6, [issue #87](https://github.com/sinas-platform/sinas/issues/87)).

Steps 1–2 are pure additive tooling (no behavior change to existing single-file
workflow — brief's hard constraint). Step 3 introduces the split enforcement;
4–6 are ergonomics + consolidation.

## Effort sizing

What's genuinely reused vs net-new. The *reconcile engine* (the hardest part —
idempotent apply, `managed_by`/checksums, export) exists; the GitOps *surface*
is mostly net-new but built on it. Sizes are relative (S≈days, M≈~1wk,
L≈multi-wk), assuming one engineer.

| # | Intent | Reused | Net-new | Size |
|---|---|---|---|---|
| 8a | Schema endpoint (`/config/schema`) | `model_json_schema()` | ~5-line route + cache | **S** |
| 8b | `kind`-discriminated union refactor | models exist | split root model, drop v1 validators, keep parse working | **S–M** |
| 8c | Structured validator errors | `config_parser` structure | `{path,message,code,hint}` + update all raise sites + `/validate` response | **M** |
| 8d | Knowledge consolidation (skills/docs/create-app to new layout) | 4 copies exist | rewrite against schema; new-layout scaffold | **M** |
| 4 | Code-as-files (`codeFrom`/…), **server-side zip** | parse/apply unchanged | Pydantic fields+validator (S), server resolver+zip endpoint (M), console bundle upload (M), export round-trip to files (M) | **M–L** |
| 7a | Deterministic export | `config_export.py` | stable ordering, files mode, fix hardcoded name | **M** |
| 7b | `diff` command | dry-run plan exists | client-side file-vs-repo diff over export | **M** |
| 7c | CLI CI-auth + `--json` + exit codes | commander skeleton | env-var auth, JSON output, code map | **S–M** |
| 7d | Reference GitHub Actions workflow | — | validate/diff-comment/promote pipeline | **S** |
| 3 | Workspace file + multi-env + values | single-file CLI | workspace parse, per-env resolution, token source | **M–L** |
| 1 | Config profile + manifests-as-requires + `status` | `PACKAGE_SKIP_TYPES`, `ManifestConfig`, manifest-status route | promote split (rides on 8b), require perms/secrets/providers, CLI status aggregation | **M** |
| 2 | Bindings (pending report, typed subject) | email→role binding exists | typed `{email\|group}` subject, pending-binding report | **S–M** |
| 5 | `requires` + topo-sort + uninstall guard | fixed apply order | `requires` field persisted, CLI topo-sort, backend delete guard | **M** |
| 6 | Instance modes (open/protected/locked) — **DEFERRED, tracked in issue** | RBAC + API keys | mode setting + guard on **every** mutating route (incl. console CRUD) + API-key caller-kind | **M–L** |

**Bottom line:** "most of it already there" is true only for the reconcile
*engine* — which is the part that's usually the hard, risky infrastructure, so
that's a real head start. But *everything in the brief* is a multi-week program
(order of ~8–12 focused weeks solo, less in parallel), because the GitOps
surface — multi-env CLI, modes, diff, schema pipeline, knowledge — is largely
net-new. The two biggest rocks are **#3 (multi-env CLI)** and **#6 (instance
modes)**; the smallest, highest-leverage win is **#8a/b (schema endpoint +
union)**, which also unlocks #1's split-as-contract.

## Open questions to settle before implementing

- **Cross-repo coordination:** CLI/skills/create-app work lands in
  `sinas-skills/`; modes, JSON-Schema generation, structured errors, config
  profile land in `sinas/`. Are these shipped/versioned together? The CLI must
  tolerate a server that predates the new endpoints (feature-detect).
- Vars resolution home: keep server-side (already works) — confirm. (Only
  file-refs move client-side.)
- Config profile: validation-mode over `SinasConfig` (recommended) vs a new
  `kind`. Brief leaves it to us.
- Mode enforcement caller-identity: how to label API keys (`kind` on the key)
  and whether the console uses a distinct identity from agents.
- Where the canonical knowledge source lives and how `create-app` (external?)
  consumes the generated schema.
