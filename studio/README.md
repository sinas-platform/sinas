# Sinas Studio

Studio is a standalone web app for **business analysts** to build and operate
AI assistants and automations on a Sinas workspace — without touching Python,
SQL, YAML, or the admin console. It connects to an existing Sinas instance
over the public API and works entirely within the permissions of a "Studio"
role.

This README is the founding design contract. It records the decisions made
before the first line of code so that every screen and feature can be checked
against them. Change the contract deliberately, not by drift.

---

## 1. Who does what: the BA / admin boundary

**Principle:** if it can be done by *choosing, naming, parameterizing,
scheduling, or writing natural language* → BA. If it requires *code, SQL,
schemas, shared credentials, or granting authority* → admin. The BA composes
capabilities; the admin authors them.

| Resource | Admin | BA (Studio) |
|---|---|---|
| Functions | Authors all Python; ships adapter functions | Never creates/edits function rows. "Creating" a router/fan-out/webhook step = creating a *binding* to an admin function with parameters (`default_values`, `inputData`, `functionParameters`) |
| Connectors | Defines (base URL, auth scheme, operations) | Attaches to agents, selects operations, supplies own key (private secret overrides shared) |
| Secrets | Creates/rotates shared credentials | Sets values (never reads any) |
| Agents | Everything | Full create/edit: instructions, tools, knowledge, memory, inputs. Model from an admin-curated list; sampling params hidden |
| Skills | Everything | Authors freely (markdown guidance, zero blast radius) |
| Schedules | Everything | Create/edit/toggle |
| Webhooks | Any | Create against adapter functions with parameters. Public (`requiresAuth: false`) endpoints allowed **with a prominent UI warning** and a permanent "public" badge |
| CDC triggers | Defines DB connections & exposed tables | Creates triggers on exposed tables → adapter functions |
| Queries (SQL) | Authors | Attaches/parameterizes |
| Collections / documents | Any | Creates collections, uploads documents |
| Projects (manifests) | Any | Creates/manages |
| Packages, LLM providers, users/roles, DB connections, system | Yes | **No** — note: packages contain code, so BAs never install packages; admins install integrations, BAs compose them |

Posture: BAs are **trusted members of the org**, not untrusted users. No
per-project permission scoping — BAs see the whole workspace. The boundary is
enforced by a single **"Studio" role** in existing Sinas RBAC (broad read;
write on agents, skills, schedules, webhooks, collections, stores, private
secret values, manifests). No backend changes required to enforce it.

## 2. Concepts, translated

Studio never shows platform nouns. The mapping is fixed:

| Sinas | Studio | Notes |
|---|---|---|
| Agent | **Assistant** | |
| System prompt | **Instructions** | plain text field |
| Skill (`enabledSkills` + `preload`) | **Guidance** | chips under Instructions: "always" (preload) / "when needed" |
| Connector / composable function / query | **Tool** | capability cards with per-action toggles |
| Collection | **Files** | shared library (read-only) or the assistant's **own file space** (auto-created, read & write) |
| Store | **Memory** | predefined type: "Conversation memory — remembers between conversations" (auto-created private store); shared stores attachable |
| `input_schema` + templating | **Inputs** | `@tokens` (e.g. `@customer_tier`), typed rows (text/choice/…), insertable into instructions by typing `@`. Never shows JSON schema or Jinja |
| Webhook / schedule / CDC trigger | **Workflow** | trigger → action strip |
| Manifest (+ future `layout` column) | **Project** | a board/grouping; membership = `required_resources` |
| Execution | **Run** | |

## 3. Screens (v1 — exactly five)

1. **Connect** — workspace URL + sign-in; detects the companion package and
   degrades gracefully (see §6).
2. **Projects** — list of manifests-as-projects; create = create manifest.
3. **Project home** — deliberately boring: lists the project's assistants and
   workflows with health/status. No project-level canvas in v1.
4. **Assistant editor** — form + permanent live **test chat** (right, ~40%).
   Left: plain-language capability summary header → Instructions (with
   Guidance chips + per-field AI assist) → Tools → Files & memory → Inputs →
   trigger footnotes linking to workflows. Edits apply live (autosave).
   Hidden entirely: schemas, hooks, status templates, timeouts, temperature.
5. **Workflow editor** — horizontal strip: **When** card (trigger; typed
   config; public-URL warning) → **Then** card (assistant or function, with
   `@token` request template and explicit input mapping). Right rail: **Runs**
   (per-fire history, failures explained honestly) + "send a test event".
   The **on/off switch is the publish moment** — workflows assemble inert.

Runtime constraints are baked into the UI: chat → assistant; webhook →
function (via adapter → assistant); schedule → either. Only valid flows are
constructible.

Design mocks for screens 4–5 live in `design/mocks/` **on the design branch
only — never merged**; they are throwaway HTML used to settle these decisions.

## 4. The honesty rule

**Every string on screen names its source field.** Trigger titles come from
`webhook.description` (BA-authored at creation) → adapter function description
→ generic fallback. "via X" is the prettified target function name. "Accepts
fields" is the adapter's `input_schema`. Failure messages state only what the
platform knows (e.g. schema-validation misses); anything AI-phrased —
"explain this error", "improve instructions" — is generated **on demand and
labeled**, never pre-rendered. No fiction in the UI.

## 5. The adapter contract (webhook → assistant, until native support)

An **adapter** is an admin-authored function that bridges a trigger to an
assistant. The contract is declarative:

- Its `input_schema` declares the trigger payload (this powers "accepts
  fields" and `@token` availability).
- It accepts reserved parameters — `studio_agent`, `studio_message_template`,
  and an input-mapping object — supplied via the webhook's `default_values`.
  Reserved names are prefixed to avoid collisions.
- **Recognition:** a function that declares the reserved parameters *is* an
  adapter — detection reads the same schema the runtime executes; no
  namespace, description, or tag heuristics. A first-class `role` field on
  Function may formalize this later.
- One generic adapter covers all plain webhook→assistant workflows;
  integration-specific adapters (Jira, Slack Events) add only signature
  verification and payload normalization.

This is bridge infrastructure: when Sinas gains **native trigger→agent
targets** (see §7), the template moves onto the webhook itself and generic
adapters retire.

## 6. The companion package (`studio-runtime`)

Studio's AI features require resources that vanilla Sinas doesn't have. They
ship as a versioned `SinasPackage` in this directory:

- `studio/copilot` agent — AI assist (improve/draft instructions, extract
  guidance into a skill), "explain this error", plain-language read-backs.
- `studio/ask-agent` — the generic adapter function (§5).

Rules:
- **Installation is an admin act** (`packages.install` is admin-only): the
  one-time "connect Studio to this workspace" setup installs the package and
  creates the Studio role.
- **Graceful degradation:** Studio must run against vanilla Sinas. AI buttons
  render as "needs Studio setup — ask your admin" when `studio/copilot` is
  absent; webhook-workflow creation is hidden when no adapter exists;
  everything else works regardless.
- **Versioning:** the app pins the `studio-runtime` version it requires,
  checks on connect, and offers admins a one-click upgrade (idempotent apply
  makes reinstall safe).

## 7. Sinas backend follow-ups (tracked, not required for v1)

Ordered by value to Studio:

1. **Native trigger→agent targets** on webhooks and CDC triggers
   (polymorphic `{function | agent}` + payload→input mapping; sync mode
   returns the agent reply). Retires generic adapters; also the zero-glue
   enabler for Slack/Telegram message hooks.
2. **`layout` column on manifests** (nullable JSON) — project board state;
   Studio uses local storage until then.
3. **Manifest validator type map** — extend `RESOURCE_TYPE_MAP` beyond
   agent/function/skill/collection so project health checks cover all types.
4. **`role` field on Function** (`webhook_adapter`, …) — formalizes adapter
   recognition.
5. **Permissions on `/auth/me`** — lets Studio adapt UI to the caller's role
   instead of discovering by 403.
6. **Record admission failures as executions** — found during live testing:
   when a webhook fires but the platform can't run the function (e.g.
   "No workers available"), the caller gets a 500 and **no execution row is
   written**. Studio's Runs rail honestly shows "no runs recorded", but the
   workflow silently swallowed a real event. Failed admission should create
   a failed execution so it's visible after the fact.
7. **`@sinas/cli install` can't pass variables** — `install` reads
   `opts.variables` but registers no `--variables`/`--set` option, so any
   package with required variables (like studio-runtime's `WORKSPACE_URL`)
   can't be installed via the CLI. Needs a `--set KEY=VALUE` flag.
8. **Package uninstall breaks on function version history** — found during
   live testing: `PackageService.uninstall` bulk-deletes managed functions
   without first removing their `function_versions` rows, so uninstalling
   any package containing a function that has version history 500s on the
   foreign key. Delete versions first (or cascade).

## 8. Repository layout & delivery

Studio lives in this monorepo (same license and release train as the
platform; extract to its own repo only if that ever changes):

```
studio/
├── README.md          # this contract
├── app/               # the web app
└── packages/
    └── studio-runtime.yaml
```

**Distribution (settled):** Studio ships OSS in this repo, on the platform's
release train — in-repo bundling makes version skew structurally impossible
for self-hosters. Default serving is **bundled**: the app builds with base
`/studio/` and is served from the workspace's own origin (same-origin, no
CORS, no extra service). When served this way it detects the workspace via
`GET /info` on its own origin, skips the workspace step, and tucks
"connect to a different workspace" behind an unobtrusive link. A standalone
deployment (own domain) and a future managed/hosted Studio are distribution
options built from the same code; the managed variant handles version skew
via `/info` version detection + graceful degradation, and is a roadmap item,
not an architecture decision.

## 9. Local development & testing

Against a local Sinas (backend on `http://localhost:8000`, CORS is open):

```bash
cd studio/app && npm install && npm run dev
# open http://localhost:5180/studio/ → enter http://localhost:8000 as the workspace
```

Install the companion package (admin credentials; the workspace URL variable
must be reachable *from function sandboxes* — for local Docker that is
http://host.docker.internal:8000):

```bash
mkdir -p /tmp/studio-pkg && cp studio/packages/studio-runtime.yaml /tmp/studio-pkg/sinas-package.yaml
cd /tmp/studio-pkg && npx @sinas/cli login && npx @sinas/cli validate && npx @sinas/cli install
```

**Bundled deployment (wired):** the console image builds Studio in its own
stage and serves it at `/studio/`:

- `console/Dockerfile` builds with the **repo root** as context
  (`docker build -f console/Dockerfile .`) so it can see `studio/app`; the
  root `.dockerignore` allowlists only `console/` and `studio/app/`.
- `console/nginx.conf` serves `/studio/` with its own SPA fallback
  (`/studio` 301-redirects to `/studio/`).
- The production `Caddyfile` routes `{$DOMAIN}/studio*` to the console
  container **on the main domain** — same origin as the API, which is what
  makes the workspace auto-detect via `GET /info` work.
- `docker-compose.dev.yml` and the CI image matrix use the widened context.

On the dev compose (no Caddy), bundled Studio is at
`http://localhost:51245/studio/` — cross-origin to the backend, so it falls
back to the standalone connect flow, which is correct.
