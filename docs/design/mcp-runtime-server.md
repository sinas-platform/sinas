# Sinas as an MCP server for the runtime API

- **Status:** Proposed
- **Date:** 2026-10-20
- **Authors:** Kjeld Oostra, Claude
- **Target:** the release after 0.5.0. Implementation lands on `dev` in
  phases (§12); this document only records the design.
- **Code base:** written against the 0.5.0 code base (`release/0.5.0`, which
  carries #286–#300). Open PRs it builds on are named where they matter:
  #292 (session-scoped `context`) and #299 (LLM error envelope).
- **Related code (today):**
  - `backend/app/models/manifest.py`, `backend/app/schemas/spec/manifest.py`, `backend/app/services/resources/manifests.py`: manifests and their applier
  - `backend/app/api/runtime/dependencies.py`, `backend/app/api/runtime/endpoints/discovery.py`: `X-Application` / `?app=` filtering
  - `backend/app/services/resource_resolver.py`: ref patterns (`ns/name`, `ns/*`, `*/*`), #298
  - `backend/app/core/token_signing.py`, `backend/app/core/auth.py`, `backend/app/api/runtime/endpoints/oidc.py`: token signing, the verify choke point, JWKS, userinfo
  - `backend/app/core/oauth_state.py`: PKCE and single-use state (connector OAuth, client side)
  - `backend/app/api/runtime/endpoints/chats.py`, `backend/app/services/sync_turns.py`: invoke, pending inputs, approvals
  - `backend/app/services/mcp_client.py`, `backend/app/services/mcp_tasks.py`: Sinas as an MCP *client*
- **Related docs:** `backend/docs/adrs/2026-10-09-mcp-client.md`,
  `backend/docs/adrs/2026-10-12-mcp-tasks.md`,
  `backend/docs/adrs/2026-09-02-deferred-tool-rounds-pending-completions.md`,
  `backend/docs/adrs/2026-10-20-deferred-rounds-in-inline-turns.md`,
  `docs/design/oidc-sso.md`, `docs/design/config-apply-unification.md`,
  `docs-mint/platform/verifying-tokens.mdx`, `docs-mint/admin/manifests.mdx`

## 1. Context

People increasingly work inside an MCP client: GitHub Copilot in VS Code,
Claude Code, Claude (claude.ai and Desktop, through connectors), ChatGPT,
Cursor. Sinas holds agents and pipelines that organisations have built,
permissioned and budgeted. Today a person who wants one of those agents in
their MCP client has no route: the client speaks MCP, Sinas speaks its REST
runtime API. This design makes Sinas an MCP server, so a client can list and
call the agents (and pipelines; optionally functions and queries) its user
may use, one "app" at a time.

### 1.1 What exists

| Piece | Where | Relevance |
|---|---|---|
| Manifests: an "app" declaring required resources, permissions, `exposed_namespaces` per kind, `public_info` | `models/manifest.py`; spec `schemas/spec/manifest.py`; applier `services/resources/manifests.py` (config section `manifests`, change history, packageable) | the scoping unit |
| App context on the runtime API: `X-Application: ns/name` or `?app=ns/name` narrows discovery to `exposed_namespaces` | `api/runtime/dependencies.py` (`get_manifest_context`, `get_namespace_filter`); used only by `api/runtime/endpoints/discovery.py` | the filter the MCP server reuses; today namespace-level only |
| Ref patterns `ns/name`, `ns/*`, `*/*` with "wildcards only within the user's permissions" | `services/resource_resolver.py` (#298) | the exposure grammar |
| One-shot agent invoke with `session_key`, `input`, typed turn outcome (`completed`, `input_required`, `in_progress`, `injected`), per-request `usage` | `POST /agents/{ns}/{name}/invoke` in `api/runtime/endpoints/chats.py`; `InvokeResponse`, `TurnState`, `TurnUsage` in `schemas/chat.py`; `services/sync_turns.py` (#281, #288) | what an agent tool call does |
| ask_user questions as pending inputs; answer endpoint (`wait: true` returns the resumed turn) | `services/ask_user_tools.py`, `services/deferred_completions.py`, `GET/POST /chats/{id}/pending-input[/{tool_call_id}]` | maps to elicitation |
| Tool approvals (rules, always-allow, expiry) | `services/approval_rules.py`, `models/pending_approval.py`, `POST /chats/{id}/approve-tool/{tool_call_id}` (#179, #286, #287) | **non-streaming turns refuse an "ask" approval** (`message_service.py`, `run_turn` path: "Please use streaming mode to handle approval flow") |
| Background runs and wakeups | `services/chat_wakeups.py` (#246) | long-running work |
| Pipelines run sync (under `syncTimeoutSeconds`) or async | `POST /pipelines/{ns}/{name}/run` in `api/runtime/endpoints/pipelines.py` | typed tools |
| Functions and queries execute endpoints | `api/runtime/endpoints/functions.py`, `queries.py` | optional tools |
| Agent-as-tool schema: `prompt` + the agent's `input_schema` properties + `_chat_id` | `services/tool_discovery.py` (sub-agent tools) | precedent for the agent tool's input schema |
| Access tokens: HS256 by default, RS256 with `iss`/`aud`/`kid`, keys persisted encrypted (`jwt_signing_keys`), `/.well-known/jwks.json`, `/userinfo` | `core/token_signing.py`, `api/runtime/endpoints/oidc.py` (#117, issue #101) | signing and the verify choke point (`decode_access_token`) |
| Refresh tokens in Postgres, hashed, revocable; **not rotated** (`/auth/refresh` reuses them) | `core/auth.py` (`create_refresh_token`, `validate_refresh_token`, `revoke_all_refresh_tokens`), `api/runtime/endpoints/authentication.py` | MCP grants need rotation |
| API keys with their own (narrower) permissions, editable by owners | `core/auth.py` (`validate_api_key`, `bind_api_key`), #294, #300 | plain bearer for MCP |
| Console login: password, OTP, OIDC SSO with `sessionHours` | `api/runtime/endpoints/authentication.py`, `services/sso.py` (#263, #264) | the consent screen's login |
| PKCE S256 pairs, Redis single-use state (`GETDEL`), browser-binding cookie | `core/oauth_state.py` | pattern for codes and pending authorizations |
| Redis rate limiter with `Retry-After` | `core/rate_limit.py` | AS and MCP endpoint limits |
| Attribution snapshot on chats; identity headers to model APIs; budget refusals | `providers/attribution.py` (`new_chat_attribution`), #290 | applies to MCP-originated chats |
| Session-scoped `context` claim on tokens, refresh tokens and API keys | **open PR #292** (`current_credential_context()`) | MCP grants must carry it |
| LLM error envelope (`code`, `retryable`, `upstream_status`, `retry_after`) | **open PR #299** (`providers/errors.py`) | maps to tool errors |
| OpenTelemetry: `conversation.turn`, `llm.call`, `agent.delegate` spans, FastAPI server spans | `core/telemetry.py`, `services/message_service.py` (#291) | MCP spans nest under these |
| Request log (ClickHouse) with `permission_used` | `services/clickhouse_logger.py`, `set_permission_used` | audit of MCP calls |
| The official MCP SDK, `mcp>=2.2.0,<3` (2.3.0 locked), used as a client | `pyproject.toml`, `uv.lock`, `services/mcp_client.py` (#229) | ships the server side too (D8) |

Three facts shape the design:

1. **The console keeps its tokens in `localStorage`** (`console/src/lib/api.ts`),
   so the backend has no cookie session. An OAuth `/authorize` that needs
   "the signed-in user" has to hand over to the console.
2. **`decode_access_token` already rejects any token carrying an `aud` it
   doesn't expect, in both modes.** Under RS256 it passes
   `audience=settings.jwt_audience`; under HS256 it passes none, and
   python-jose (3.5.0) then fails any token that has an `aud` claim
   ("Invalid audience"). A token minted for an MCP URL is therefore refused
   by every existing endpoint without a code change: audience isolation
   comes for free, and the MCP endpoint verifies with its own audience.
3. **`user_token` travels.** The bearer of a request is handed to the turn
   (`user_token` in `invoke_agent`, `run_pipeline_endpoint`), forwarded to
   connectors with `auth.type: sinas_token` (`connector_service.py`) and to
   function executions. An MCP-issued token must never take that path
   (§6, step 7).

A smaller inconsistency to fix on the way: `ManifestCreate`/`ManifestUpdate`
(`schemas/manifest.py`) only allow `exposed_namespaces` keys
`agents, functions, skills, templates, collections, components, stores`,
while `discovery.py` also filters `pipelines` and `queries`, and the applier
spec accepts any key. REST cannot set what discovery reads.

### 1.2 The protocol, as of this writing

The current MCP revision is **2026-07-28** (earlier: 2025-11-25, 2025-06-18,
2025-03-26, 2024-11-05). Relevant to a server
([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)):
- **Stateless:** no `initialize`, no `Mcp-Session-Id`, no GET stream, no
  SSE resumability. Every request carries its protocol version and client
  capabilities in `_meta`; servers MUST implement `server/discover`; HTTP
  requests carry `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name`
  headers. Cross-call state uses "explicit, server-minted handles passed as
  ordinary tool arguments".
- **Multi round-trip requests (MRTR)** replace server-initiated requests:
  a `tools/call` may answer `resultType: "input_required"` with
  `inputRequests` (e.g. `elicitation/create`) and an opaque `requestState`;
  the client retries the call with `inputResponses`.
- **Tasks** left the core spec for the `io.modelcontextprotocol/tasks`
  extension (`tasks/get`, `tasks/update`, `tasks/cancel`; the server
  decides per request; no per-tool `execution.taskSupport`).
- **Authorization:** CIMD (Client ID Metadata Documents) is the SHOULD;
  **Dynamic Client Registration is deprecated** ("MAY support", kept for
  backwards compatibility); RFC 9207 `iss` in authorization responses is a
  SHOULD.
- HTTP+SSE stays deprecated (since 2025-03-26).

Clients are a mix of eras: Claude Code's current runtime speaks 2026-07-28;
the claude.ai connectors document the 2025-03-26 through 2025-11-25
authorization specs; VS Code and others are on 2025-11-25 or earlier (§10).
A server has to serve both generations on one endpoint, which the spec
allows ("an `initialize` request selects legacy semantics").

## 2. Goals and non-goals

Goals:
- An MCP client connects to one app (manifest) of one Sinas instance and
  sees, as tools, exactly the exposed resources the user may use.
- Works with clients that only do OAuth discovery (Claude connectors,
  ChatGPT, VS Code by default) and with clients configured with a static
  bearer header (Claude Code, Cursor, GitHub's coding agent, scripts).
- Permissions, budgets, tool approvals, attribution, audit and tracing
  behave as for any other runtime call.
- Stateless across backend replicas.

Non-goals (first cut):
- MCP resources, prompts, completions, sampling. Tools only.
- Exposing the management API (`/api/v1`) over MCP.
- Sinas as a general OpenID Provider (no ID tokens, no
  `openid-configuration`). The authorization server issues MCP access tokens
  only (§5.10 mentions widening it later).
- A manifest-less "everything I can access" server.

## 3. Decisions at a glance

| # | Decision | Rejected |
|---|---|---|
| D1 | One MCP server per manifest at `/mcp/{namespace}/{name}`; a manifest is required | per-agent "user facing" flag; a global server; `?app=` / `X-Application` |
| D2 | Manifest exposure becomes resource-level: `exposedResources` with ref patterns, `exposedNamespaces` kept as input; an `mcp` block turns the endpoint on | a separate MCP resource kind; MCP-only exposure lists |
| D3 | Per-resource MCP hints (name, description, annotations) in the manifest, phase 3 | hints on the agent itself |
| D4 | Two ways in: plain bearer (Sinas access token, API key) from phase 1; Sinas as an OAuth 2.1 authorization server from phase 2 | delegating to the external IdP; bearer only |
| D5 | Agents are one-shot invoke tools, continued through a server-minted `session` handle; pipelines are typed tools; functions and queries only when the manifest opts in | binding conversations to the MCP session; client-chosen session keys; every exposed kind a tool |
| D6 | ask_user and approvals map to MRTR elicitation (approvals never to a model-callable tool), long work to the Tasks extension, each with a fallback; approvals park in sync turns | failing calls that need a human |
| D7 | Usage in `_meta`, execution errors as `isError` results with the #299 code, attribution and `context` as for any runtime call | errors as JSON-RPC errors |
| D8 | Streamable HTTP only, stateless, on the official SDK's low-level server; no legacy SSE | hand-rolled protocol; stateful sessions with sticky routing |

## 4. Decisions

### D1. One MCP server per manifest

**Endpoint:** `{public_base_url}/mcp/{manifest_namespace}/{manifest_name}`,
for example `https://sinas.example.com/mcp/support/helpdesk`. That URL is
the server's identity: the RFC 8707 resource indicator, the token audience,
and the path suffix of its RFC 9728 metadata
(`/.well-known/oauth-protected-resource/mcp/support/helpdesk`).

A manifest is required. There is no `/mcp` "everything I can access"
server: an admin holds `sinas.*:all` and would get hundreds of tools, which
no client handles well (tool-selection quality drops, and some clients cap
the number of tools per server), and the person setting up a connector
should pick what they connect to.

**Why manifests and not a per-agent "user facing" flag.** Whether an agent
belongs in someone's MCP client depends on the client and the app, not on
the agent: the same triage agent is right for the helpdesk app and noise in
the finance app. A global flag would also be a third visibility system next
to permissions (who may) and manifests/namespaces (what an app shows), with
its own drift. Manifests already are "an app's view of Sinas", already carry
`exposed_namespaces`, and are on the applier write path with change history,
config apply and packages.

**Why a path and not `?app=` or `X-Application`.**
- RFC 8707 resource indicators are absolute URIs that SHOULD NOT carry a
  query component; the MCP authorization spec requires clients to send the
  canonical server URI as `resource`. A path makes each app its own resource
  with its own audience; a query string or header would make every app the
  same resource and one token good for all of them.
- RFC 9728 metadata is located by inserting `/.well-known/oauth-protected-resource`
  before the resource's path. Path-per-app gives each app its own metadata
  (its own `resource`, `scopes_supported`, `resource_name`).
- Many clients cannot send custom headers on a connector (ChatGPT takes a
  URL only; claude.ai's static headers are a limited beta and exclude
  `Authorization` together with OAuth).
- The URL is what users paste. Claude requires the metadata's `resource`
  to equal the entered URL exactly, so the canonical form (no trailing
  slash) is what the console shows with a copy button.
- Renaming a manifest changes its URL; that is acceptable (the manifest
  key is already part of every `X-Application` caller's configuration), and
  existing grants for the old URL simply stop matching (§5.4).

Rejected as well: `/mcp/{app}` with a slug that isn't the manifest key (a
second naming system), and subdomain-per-app (needs DNS and certificates
per app).

### D2. Manifest exposure: resource-level, ref patterns

**Schema.** A manifest gets `exposedResources`: per kind, a list of ref
patterns with the grammar and matching of `resource_resolver.py`:

```yaml
manifests:
  - namespace: support
    name: helpdesk
    description: Helpdesk tools for the support team
    exposedResources:
      agents: ["support/*", "ops/triage"]   # a namespace, plus one agent from another
      pipelines: ["support/escalate"]
      functions: ["support/lookup_customer"]
      queries: []
    mcp:
      enabled: true                          # default false: no endpoint
      title: Helpdesk                        # serverInfo / consent screen; default: manifest name
      instructions: |                        # MCP server instructions, optional
        Use triage first; escalate only after triage says so.
      tools: [agents, pipelines, functions]  # kinds offered as tools; default [agents, pipelines]
```

- Allowed kinds: `agents, pipelines, functions, queries, skills, templates,
  collections, components, stores` (the union of what REST validated and
  what discovery reads, fixing the inconsistency in §1.1). Only the first
  four can become MCP tools.
- Entries: `ns/name` (exact), `ns/*`, `*/*`, as validated by
  `ref_specificity`/`wildcard_ref_problem` (an unsupported pattern fails the
  apply instead of matching nothing).
- **`exposedNamespaces` stays valid input.** The spec normalises it into
  `exposedResources`: a plain namespace `support` is `support/*`, `*` is
  `*/*`, and an entry that already has a slash (stores were documented as
  `default/preferences`) is kept as a ref. When both are given for one kind
  they are merged. Because the applier diffs canonical specs
  (`resources/base.py`), re-applying an old YAML is `unchanged` (at most a
  checksum restamp), never a revision.
- **Exposure is a filter, never a grant.** A resource is a tool when it is
  exposed **and** the user may use it (`sinas.agents/<ns>/<name>.chat`,
  `sinas.pipelines/<ns>/<name>.run`, function `execute`, query `execute`).
  Unlike agents' `enabled_*` lists, an *exact* entry grants nothing either:
  in an agent the author's exact reference is a deliberate delegation, in a
  manifest it is only "show this if the user may". So every match goes
  through `can_access` (`user_has` / `user_can` in the resolver).
- An exposed agent still runs with its own `enabled_*` tools under the
  user's permissions; manifest exposure limits what the MCP client calls
  directly, not what the agent does.

**Storage and migration.** New column `manifests.exposed_resources JSON NOT
NULL DEFAULT '{}'`. One additive migration copies every row's
`exposed_namespaces` into it, normalised as above. `exposed_namespaces`
stays in the table for one release, written by the applier as the
namespace-level projection of `exposed_resources` (`ns/*` entries), so
readers that still use it (older SDKs, `get_namespace_filter`) keep working;
a later migration drops it. New column `manifests.mcp JSON NULL` (null:
MCP off).

**Applier and API changes** (`services/resources/manifests.py`,
`schemas/spec/manifest.py`, `schemas/manifest.py`, `schemas/config.py`):
`exposed_resources` and `mcp` in `ManifestSpec`, `spec_from_row`,
`write_fields`, export (`exposedResources`, `mcp`; `exposedNamespaces` no
longer exported), REST create/update/response (`exposed_namespaces` in the
response becomes derived and deprecated). `X-Application` discovery switches
from `get_namespace_filter` to the resolver, so the runtime API and the MCP
server filter identically. Packages may ship manifests with `mcp.enabled`:
exposure grants nothing, and the instance switch (§12) gates the endpoint.

Rejected: a separate `mcpServers`-like resource kind for the server side
(a second "app" concept beside manifests, and confusing next to the
existing `mcpServers` kind, which is the client side); MCP-only exposure
lists (the MCP view and the app's REST view of the same app would drift).

### D3. Per-resource MCP hints (phase 3)

```yaml
    mcp:
      hints:
        - agent: support/triage
          name: triage_ticket
          description: Classify a support ticket and propose next steps.
          annotations: {readOnlyHint: true, openWorldHint: false}
        - pipeline: support/escalate
          annotations: {destructiveHint: false, idempotentHint: false}
```

Keyed like `enabled_*` dict entries (one ref per kind; patterns allowed,
most specific wins as in `matching_entries`). Name overrides must be unique
within the manifest and match the tool-name rules (D5); the apply fails
otherwise. Hints live on the manifest, not on the agent, for the D1 reason:
how a tool should be presented depends on the app.

Without hints every tool still carries all four annotations explicitly,
at the conservative end: `readOnlyHint: false`, `destructiveHint: true`,
`idempotentHint: false`, `openWorldHint: true` (the spec's defaults, made
explicit because Claude's directory requires `readOnlyHint` and
`destructiveHint` on every tool). The cost is client-side confirmation
prompts (VS Code skips them only for `readOnlyHint: true`; ChatGPT treats
tools without it as writes; GitHub's code review only uses read-only tools);
a wrong `readOnlyHint: true` would skip confirmation for a tool that
writes. Hints exist to relax this per resource. Annotations are hints to
clients, never trusted by Sinas.

### D4. Authentication: bearer first, Sinas as authorization server next

Two ways to authenticate on `/mcp/...`, both resolving to `(user_id,
permissions, context, api_key_id | grant_id)`:

1. **Plain bearer** (phase 1): a Sinas access token (`aud` = `JWT_AUDIENCE`
   or absent under HS256) or an API key, in `Authorization: Bearer` (and
   `X-API-Key`, as `verify_jwt_or_api_key` accepts). For Claude Code, Cursor,
   VS Code `headers`, CI and scripts. An API key's narrower permissions
   apply, as everywhere. Tokens with any other audience are refused (§1.1,
   fact 2), so MCP-issued tokens never cross over.
2. **OAuth 2.1, Sinas as the authorization server** (phase 2), for clients
   that only connect through OAuth discovery (claude.ai/Desktop connectors,
   ChatGPT, VS Code's default). Clients register by CIMD, by DCR, or as
   admin-registered clients (§5.5). Designed in §5.

**Why Sinas as the AS rather than the external IdP.** The MCP spec lets the
protected resource name any authorization server. Pointing clients at the
organisation's IdP looks attractive but fails on every specific:
- Not every instance has an IdP: password and OTP users are first-class
  (`AUTH_MODE`), and the IdP is optional (`docs/design/oidc-sso.md`).
- The token must be bound to one app's MCP URL (RFC 8707). Most IdPs
  ignore `resource` or support it only with per-API configuration that each
  Sinas admin would have to replicate per manifest.
- Clients register dynamically. Few enterprise IdPs allow open DCR, and
  admins rarely want arbitrary clients registered in their IdP.
- Sinas would have to accept foreign access tokens, map their subjects and
  groups (the SSO account-linking rules, again, on every request), and
  could not revoke a single client's access.
- The consent screen must name the Sinas app and the tools it exposes; an
  IdP knows neither.
The IdP still authenticates the user: the consent step runs in the console,
whose login is SSO when configured. "Sinas as AS, IdP for login" is the
common pattern and keeps the external IdP out of MCP entirely. Where an
organisation wants the IdP to decide which clients may connect, the
MCP `enterprise-managed-authorization` extension (an IdP-issued identity
assertion exchanged at Sinas's token endpoint, SEP-990; the Python SDK
already models it as `exchange_identity_assertion`) slots in later as one
more grant type at the same AS (§13).

### D5. Tool mapping

**Tool list** = exposed ∩ permitted, recomputed per `tools/list` (cheap:
one resolver pass per kind; cached in-process for a few seconds per
`(manifest.updated_at, user, permissions digest)`), in deterministic order.
The spec allows the list to vary "by the authorization presented on the
request", never by connection; 2026-07-28 results carry `cacheScope:
"private"` and a short `ttlMs` (60 s). No `listChanged` capability in the
first cut (it needs `subscriptions/listen` streams and a shared bus across
replicas); clients re-list on reconnect or after `ttlMs`.

**Names.** Kind verb + namespace + name, so kinds never collide and names
stay stable when resources are added:

| Kind | Tool name | Example |
|---|---|---|
| agent | `ask_{ns}__{name}` | `ask_support__triage` |
| pipeline | `run_{ns}__{name}` | `run_support__escalate` |
| function | `call_{ns}__{name}` | `call_support__lookup_customer` |
| query | `query_{ns}__{name}` | `query_reports__open_tickets` |

The manifest key is not repeated (clients already prefix the server).
The mapping must be injective, since two resources must never share a
tool name, and stable, depending on nothing but the resource's own ref:
- A ref is *clean* when both its namespace and its name match
  `[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*` (no `__`, no leading or trailing `_`).
  Clean refs map as in the table; the `__` separator then cannot be
  produced any other way, so distinct clean refs give distinct names.
- Any other ref (spaces, dots, `__` inside a part) is sanitised (other
  characters become `_`) and always gets `_` plus an 8-character hash of
  the exact ref, so `support/risk analyst` and `support/risk_analyst` stay
  two tools (`ask_support__risk_analyst_3f2a9c1e` and
  `ask_support__risk_analyst`).
- A name over 64 characters is cut to 55 plus `_` and the same hash.
- Calls are dispatched through the name-to-ref map built for the listing,
  never by parsing the name. If two refs still produce one name (a hash
  collision, or a clean name that happens to end like a hash suffix),
  neither is listed and the manifest's status reports it, rather than one
  call reaching the wrong resource.
The strict alphabet and the 64 limit are tighter than MCP's own rule
(1 to 128 characters of `A-Za-z0-9_-.`) because several clients turn MCP
tools into model function names (OpenAI's `^[a-zA-Z0-9_-]{1,64}$`,
Anthropic's equivalent), often with a server prefix. `title` carries the
display name; `description` the resource's description. D3 hints override.

**Agents: one-shot invoke.** A call runs `POST /agents/{ns}/{name}/invoke`
semantics through the same service code (not over HTTP):

- `inputSchema`: `message` (string, required) plus the properties of the
  agent's `input_schema` (top-level, as `tool_discovery.py` already does for
  sub-agent tools), plus `session` (string, optional). An `input_schema`
  property named `message` or `session` is nested under `input` instead.
  Content parts (images) are not offered in phase 1.
- `outputSchema` always describes the whole envelope, so a schema-checking
  client never rejects a successful call:
  `{status: enum[completed, input_required, approval_required,
  in_progress], session: string, chat_id: string, reply: string,
  output: <the agent's output_schema>}`, with `status` and `session`
  required. `output` (present only for `completed` turns of agents with an
  `output_schema`) is nested, never merged, so the agent's own
  `required` and `additionalProperties` keep their meaning.
- Result: `structuredContent` = that envelope; `content` = the reply text
  (and the JSON of `output` when there is one, as the spec suggests for
  older clients); `_meta["co.sinas/usage"]` = the invoke's `TurnUsage`.
- Pipelines, functions and queries follow the same rule: their
  `outputSchema` is the envelope `{status, run_id?, output: <the
  resource's output schema, if any>}`.

**Conversations: a server-minted `session` handle.** No `session`
argument starts a fresh chat. Every agent result returns a `session`
handle (22 random URL-safe characters); passing it back continues that
conversation. It is stored as the invoke's `session_key` with an `mcp:`
prefix (so it never collides with an app's own keys) and resolved exactly
as invoke resolves session keys: per agent, and only for the chat's own
user (`Chat.user_id` checked), so possession of a handle authenticates
nothing (the spec's "state handle hijacking" rule). An unknown or foreign
handle is an `isError` result ("unknown session"), never a new chat
under that name. This is the pattern 2026-07-28 prescribes for cross-call
state: "explicit, server-minted handles passed as ordinary tool
arguments".

Rejected:
- Mapping the MCP session to `session_key`. 2026-07-28 has no session at
  all; in older revisions `Mcp-Session-Id` is a transport artefact (a
  reconnect or a second window gets a new one), shared by every tool call
  of the connection, so two parallel calls to one agent would collide on
  one chat and one would come back `injected`.
- Client-chosen session strings. Models reuse obvious names ("default"),
  which silently merges unrelated conversations of the same user.

**Pipelines** are typed tools: `inputSchema` = the pipeline's
`input_schema`; the call is a sync run under `syncTimeoutSeconds`
(`run_pipeline_endpoint` semantics); the result is the envelope above
built from `PipelineRunResponse` (`status`, `run_id`, `output`; the step
details stay in the run history), with `output` also as text. A run past its budget is
handed to Tasks or the fallback (D6). `allUsers` fan-out is never offered.

**Functions and queries** only when the manifest lists them in
`mcp.tools` *and* exposes them: `inputSchema`/`outputSchema` from the
resource; sync execute semantics. They are opt-in because they are
building blocks without an agent's guard rails (no approval rules of their
own, and a query can be a write).

### D6. Interaction: ask_user, approvals, long-running work

The decisive fact: in the 2026-07-28 revision, a server does not send
requests to the client during a call. It answers `tools/call` with an
`InputRequiredResult` (`resultType: "input_required"`, `inputRequests`,
an opaque `requestState`), and the client retries the same call with
`inputResponses` and the echoed `requestState` (multi round-trip requests;
`mcp_types` `InputRequiredResult` / `InputResponseRequestParams`, sealed by
the SDK's `RequestStateBoundary` with AES-GCM). That fits Sinas's parked
rounds exactly: a parked round is durable state in Postgres, the retry can
land on any replica, and nothing is held open.

**ask_user → elicitation (form mode).** An invoke that ends
`input_required` returns `InputRequiredResult` with one elicitation per
pending input, keyed by `tool_call_id`:
`{message: question, requestedSchema: {answer: string | enum(options)}}`.
The schema is flat and primitive, as form mode requires; nothing
sensitive is ever asked in form mode (the spec forbids it; an agent that
needs a secret is told to use a Secret, not ask_user). `requestState`
holds `chat_id`, the tool call ids and the session; the SDK seals it
(AES-GCM, 600 s TTL, bound to the principal), and the sealing keys are
derived from `ENCRYPTION_KEY` so every replica can open what another
sealed (`RequestStateSecurity(keys=...)`). On retry, each `accept` goes
through the answer path of `POST .../pending-input/{id}` with `wait: true`,
and the call settles as invoke does. One-time use is enforced by the
pending completion itself: a question that was already answered (a
replayed retry, or an answer given in the console meanwhile) is skipped.
`decline` and `cancel` answer the question with "The user declined to
answer." so the round resumes rather than staying parked behind a client
that has moved on; a client that never retries leaves the question to
ask_user's own expiry. Elicitation is used only when the client declares
the capability (and the mode) in that request.

**Approvals → elicitation, never a model-callable tool.** Today a
non-streaming turn refuses a call that needs approval
(`message_service.py`). The MCP server needs approvals to park like
ask_user. Decision: extend the sync-turn path with a fourth parked state,
`approval_required`, that creates the `PendingToolApproval` rows the
streaming path already creates and ends the turn like `input_required`
(`sync_turns.settle_turn` reports it with the pending approvals). This also
fixes invoke and `POST /chats/{id}/messages` for REST callers, which get the
same status. Over MCP, each pending approval becomes a form elicitation:
`{message: "<agent> wants to run <tool> with <args>", requestedSchema:
{decision: enum [approve, reject], always_allow?: boolean}}`; the retry
resolves it through the `approve-tool` code path (rules, expiry, always-
allow per #286). With `mcp.approvals: console` on the manifest, and a
client that supports URL mode, the elicitation is a URL to the console
chat instead: the approval is made in Sinas's own UI, by a console
session whose user must be the MCP caller (the spec's "same user" rule for
URL mode), and the retry reads the outcome.

**Fallbacks** (client without elicitation, or a handshake-era client on the
stateless transport, which cannot carry server-to-client requests):
- ask_user: the tool result (not an error) has `status: "input_required"`,
  the questions, a console link to the chat, and the instruction to ask the
  user. The server additionally lists one control tool,
  `answer_question {session, question_id, answer}`, so the client's model
  can relay the user's answer.
- approvals: the result has `status: "approval_required"` and a console
  link, **and no tool to approve with**. An approval exists to put a human
  between the agent and an action; a tool would let the calling model
  approve its own request.

**Long-running work → Tasks.** The 2026-07-28 revision moved Tasks to the
`io.modelcontextprotocol/tasks` extension (`tasks/get`, `tasks/update`,
`tasks/cancel`; statuses `working`, `input_required`, `completed`,
`failed`, `cancelled`), which Sinas already speaks as a client
(`backend/docs/adrs/2026-10-12-mcp-tasks.md`). When the request declares the
extension:
- an invoke that comes back `in_progress` (a background run outlived the
  budget, #246/#288), and a pipeline run past its sync budget (continued as
  an async run), return a `CreateTaskResult`;
- never for a request that did not declare the extension (spec MUST);
- task id = a sealed reference to `(kind, chat_id | run_id, user, grant)`,
  verified on every call (the spec allows task ids to be treated as bearer
  handles, so they are unguessable and bound to the principal);
- `tasks/get` reads the chat's turn state or the run's status;
  `input_required` carries the pending inputs as `inputRequests`, answered
  with `tasks/update`; `tasks/cancel` is the chat interrupt or the run's
  cancel.
Without the extension: the tool waits up to the call budget
(`MCP_TOOL_CALL_TIMEOUT_SECONDS`, default 120, never beyond the agent's job
timeout), then returns `status: "in_progress"` with the session (or the
pipeline's `run_id`, the run continuing asynchronously) and a console link,
and the control tool `check_status {session | run_id}` returns the latest
outcome. The 2025-11-25 experimental core tasks are not
implemented (the client side doesn't support them either).

Tasks are phase 3 and may wait for demand: the Python SDK does not
implement the extension's server side yet (its experimental tasks API was
removed in 2.x), and no client in the spec's extension matrix lists Tasks
support. The server side would be built on the 2026-07-28 task types
`mcp_client.py` already defines for the client side. The fallback above is
therefore the main path for long work in practice.

**Cancellation.** On 2026-07-28, closing the response stream is the
cancellation. For an agent call Sinas interrupts the turn (the
`POST /chats/{id}/interrupt` path); the chat keeps what happened so far.
On handshake-era connections a dropped stream is not a cancellation (the
2025-11-25 transport says so), so the turn runs on and `check_status`
finds it.
Pipeline, function and query runs are not killed: they may already have
had side effects, so they finish and stay visible in the run history.

**Progress.** When the request has a `progressToken`, turn events become
`notifications/progress` (with a monotonically increasing `progress`
counter and a `message`) on the request's SSE response: `tool_start` (with the
agent's status templates), sub-agent delegation, pipeline steps. Reply
text is not streamed (MCP has no partial results). The SDK's SSE keepalive
holds the response open behind proxies.

### D7. Results, usage, errors, attribution

- **Usage:** `_meta["co.sinas/usage"]` on every agent tool result, the
  invoke's `TurnUsage` (#281). `_meta` is invisible to the model in most
  clients and costs nothing; clients that surface `_meta` can show cost.
- **Errors.** Protocol-level problems are JSON-RPC errors: unknown or
  hidden tool, invalid arguments (`-32602`), a malformed `requestState`.
  Anything that happened while running the resource is a tool result with
  `isError: true`, so the client's model sees it and can react: text =
  the envelope's `detail`; the #299 fields `{code, retryable,
  upstream_status?, retry_after?}` (`budget_exceeded`, `rate_limited`,
  `provider_*`, `job_timeout`, ...) go in `_meta["co.sinas/error"]`, not
  in `structuredContent`, which stays reserved for results that match the
  advertised `outputSchema`.
  A pipeline `PipelineBusyError` is `isError` with `code: "busy"`. A tool
  the user may not call is "unknown tool" (never "forbidden": it was never
  listed). HTTP-level: 401 with the challenge (§5.3), 403
  `insufficient_scope`, 429 from the MCP endpoint's own limiter with
  `Retry-After`.
- **Attribution.** An MCP call creates its chat through the same path as
  invoke, so `new_chat_attribution` applies; with #292, the credential's
  `context` is bound per request (`current_credential_context()`) from the
  API key, the Sinas token's claim, or the grant (§5.4). Chats are marked
  `chat_metadata.source = {"mcp": {"manifest", "client_id", "client_name",
  "grant_id"}}` and titled `mcp:{client_name}`.

### D8. Transport and SDK

- **Streamable HTTP only.** The HTTP+SSE transport has been deprecated
  since 2025-03-26 (and is reclassified, not removed, in 2026-07-28);
  every client in §10 speaks Streamable HTTP. Not supporting SSE means one
  less code path and no long-lived GET streams. A URL ending in `/sse`
  makes Claude pick SSE, so Sinas's URLs never do.
- **Both protocol generations on one endpoint, stateless.** 2026-07-28
  requests are self-contained POSTs. Handshake-era clients (2025-03-26
  through 2025-11-25) are served by the SDK's stateless legacy mode
  (`StreamableHTTPSessionManager(stateless=True)`, which only affects the
  legacy path): a fresh transport per request, no `Mcp-Session-Id`, GET and
  DELETE answered `405`. Consequences: any replica serves any request, no
  Redis session store, no sticky routing; handshake-era clients get no
  server-to-client requests (elicitation) and use the fallbacks of D6.
  Rejected: the SDK's stateful legacy sessions. They live in process
  memory (`_server_instances`), so they need sticky routing per session and
  a shared event store for resumability (the SDK ships none), for a
  protocol generation that is on its way out.
- **The official SDK, low-level `Server`.** `mcp` 2.x (2.3.0 locked; the
  current `mcp>=2.2.0,<3` pin already covers the server side) ships what we
  would otherwise write: version negotiation across both eras
  (`server/_streamable_http_modern.py`, `server/discover`), `Mcp-Method` /
  `Mcp-Name` header checks, `InputRequiredResult` with sealed `requestState`
  (`server/request_state.py`), SSE keepalive, request body limits,
  Origin/Host validation (`TransportSecuritySettings`), and OTel hooks
  (`server/_otel.py`). One `Server` with dynamic `list_tools` / `call_tool`
  handlers reads the manifest and principal from the request context; the
  session manager runs in the backend's lifespan and is mounted as an ASGI
  app under `/mcp`. `MCPServer` (the 2.x `FastMCP`, decorator-registered
  tools) does not fit a per-user, per-manifest tool list. SSE responses,
  not `json_response=True`, because JSON mode has no progress
  notifications.
- **Not the SDK's authorization-server framework.** `auth_server_provider`
  / `OAuthAuthorizationServerProvider` is documented by the SDK as legacy
  and discouraged, and assumes the AS and one resource server live in one
  MCP app. Sinas's AS spans many resources (one per manifest), hands
  consent to the console, and needs the registration policies, abuse
  limits and audit of §5. The SDK's pydantic models (`mcp.shared.auth`:
  client metadata, AS and protected-resource metadata) are reused for
  validation and response shapes; the SDK's resource-server side
  (`TokenVerifier`, `RequireAuthMiddleware`) is not, because the challenge
  must name a per-manifest metadata URL and bearer auth also accepts
  Sinas's own credentials.

## 5. The authorization server

### 5.1 Spec requirements this follows

From the 2026-07-28 authorization specification
(`/specification/2026-07-28/basic/authorization` and its subpages
*authorization-server-discovery*, *client-registration*,
*security-considerations*); 2025-11-25 differs where noted:
- "MCP servers MUST implement OAuth 2.0 Protected Resource Metadata
  (RFC9728)", with `authorization_servers` naming at least one AS. The
  server MUST make it findable by `resource_metadata` in the 401's
  `WWW-Authenticate` **or** at the well-known URI (path-suffixed first,
  then root); clients try the header first. Sinas does both.
- The AS "MUST provide at least one of" RFC 8414 metadata or OIDC
  Discovery (clients MUST support both); the metadata's `issuer` must be
  identical to the one the URL was built from. Sinas: RFC 8414 at
  `/.well-known/oauth-authorization-server`, issuer without a path.
- "Authorization servers MUST implement OAuth 2.1 ... for both
  confidential and public clients". PKCE S256 (clients refuse to proceed
  when `code_challenge_methods_supported` is missing). All AS endpoints over
  HTTPS; redirect URIs are localhost or HTTPS and matched exactly.
- RFC 8707: the `resource` parameter "MUST be included in both
  authorization requests and token requests", as the server's canonical
  URI; servers "MUST validate that access tokens were issued specifically
  for them as the intended audience".
- Token passthrough: servers "MUST NOT accept or transit any other
  tokens", and MUST NOT pass the client's token to upstream APIs.
- "For public clients, authorization servers MUST rotate refresh tokens";
  the AS SHOULD issue short-lived access tokens.
- Registration, in the client's order: pre-registered, CIMD (AS and
  clients "SHOULD support"), DCR ("MAY", deprecated in 2026-07-28; plain
  MAY in 2025-11-25). DCR clients MUST send `application_type`.
- RFC 9207 `iss` in authorization responses (SHOULD; advertised with
  `authorization_response_iss_parameter_supported`). ChatGPT rejects a
  response without it.
- Scopes: the server SHOULD send `scope` in the challenge (clients treat it
  as authoritative), answers missing scope with 403 `insufficient_scope`,
  and SHOULD NOT list `offline_access`.
- Invalid or expired tokens get 401; tokens never in query strings.

### 5.2 Endpoints

All on the backend origin; the issuer is `public_base_url` (not
`JWT_ISSUER`, which may name something else; MCP tokens are only ever
verified by Sinas).

| Endpoint | Purpose |
|---|---|
| `GET /.well-known/oauth-authorization-server` | RFC 8414: `issuer`, `authorization_endpoint`, `token_endpoint`, `registration_endpoint` (only when DCR is enabled), `revocation_endpoint`, `response_types_supported: [code]`, `grant_types_supported: [authorization_code, refresh_token]`, `code_challenge_methods_supported: [S256]`, `token_endpoint_auth_methods_supported: [none, client_secret_basic, client_secret_post]`, `scopes_supported: [mcp]`, `client_id_metadata_document_supported: true` (when CIMD is on), `authorization_response_iss_parameter_supported: true` |
| `GET /.well-known/oauth-protected-resource/mcp/{ns}/{name}` | RFC 9728: `resource` (the canonical MCP URL), `authorization_servers: [issuer]`, `scopes_supported: [mcp]`, `bearer_methods_supported: [header]`, `resource_name` (the manifest's `mcp.title`). 404 for an unknown or MCP-disabled manifest |
| `GET /oauth/authorize` | validates, parks the request, redirects to the console |
| `GET /oauth/requests/{id}`, `POST /oauth/requests/{id}/decision` | the console's consent API (console bearer) |
| `POST /oauth/token` | `authorization_code` + PKCE, `refresh_token`; form-encoded; dead refresh tokens get `invalid_grant` |
| `POST /oauth/register` | RFC 7591 DCR, per policy |
| `POST /oauth/revoke` | RFC 7009 |
| `GET/DELETE /api/v1/oauth/grants[/{id}]` | the user's connected clients (own); admins all |
| `/api/v1/oauth-clients` | admin CRUD over the `oauthClients` applier (§5.8) |

### 5.3 Flow

```
MCP client                       Sinas backend                         console (browser)
POST /mcp/support/helpdesk ───▶  401  WWW-Authenticate: Bearer
                                   resource_metadata=".../.well-known/oauth-protected-resource/mcp/support/helpdesk",
                                   scope="mcp"
GET  PRM, then AS metadata ───▶  {authorization_servers:[issuer]}, RFC 8414 doc
POST /oauth/register       ───▶  {client_id}            (DCR; or CIMD / pre-registered)
browser → /oauth/authorize?response_type=code&client_id&redirect_uri&state
          &code_challenge&code_challenge_method=S256&resource=<MCP URL>&scope=mcp
                           ───▶  validate client, redirect_uri (exact), S256, resource
                                 (an MCP-enabled manifest), scope
                                 park request → Redis (10 min, id = 32 random bytes)
                                 302 ─────────────────────────────────▶ /ui/oauth/authorize?request=<id>
                                                                       login if needed (password/OTP/SSO)
                                 GET /oauth/requests/<id>   ◀───────── (console bearer)
                                 binds the request to this user (first reader wins)
                                 {client, redirect host, manifest, tools, context} ──▶ consent screen
                                 POST /oauth/requests/<id>/decision {allow}  ◀────
                                 check user may read manifest; create/extend grant;
                                 code → Redis (60 s, single use, bound to client,
                                 redirect_uri, challenge, resource, grant)
                                 {redirect_to: redirect_uri?code&state&iss} ─────▶ location.assign
POST /oauth/token (code, verifier, redirect_uri, resource) ──▶ GETDEL code, check S256, client, resource
                           ◀───  {access_token (aud = MCP URL), refresh_token, expires_in, scope}
POST /mcp/support/helpdesk  Authorization: Bearer <access_token>
```

- Errors before the redirect URI is validated are shown in the console,
  never redirected (open-redirect rule). After that, `access_denied`,
  `invalid_scope`, `invalid_target` go back to the client per RFC 6749.
- `iss` is added to the authorization response (RFC 9207) against mix-up.
- The console never sees the code's PKCE verifier or any token; it only
  relays the decision and follows `redirect_to`.
- **Remembered consent:** when the user already holds an active grant for
  the same client, resource and redirect URI, the console shows a short
  "Continuing to <client>" interstitial and approves without the full
  screen. The user must still have a console session, so a silent
  token for a third party is never minted from a link alone.

### 5.4 Grants and tokens

A **grant** is (user, client, manifest/resource, scope, context), created at
consent. Tokens hang off it:

- **Access token:** a JWT signed with the access-token signing context
  (RS256 or HS256, whichever is configured), claims `iss` (AS issuer),
  `aud` = the canonical MCP URL, `sub`, `email`, `client_id`, `scope`,
  `gid` (grant id), `jti`, `iat`, `exp`, `context` (#292, from the grant),
  and `token_use: "mcp"`. Lifetime `MCP_ACCESS_TOKEN_MINUTES` (default 60;
  longer than the console's 15 because every refresh is a client round trip
  through the token endpoint). Verified at `/mcp/...` with the audience set
  to that endpoint's canonical URL; the grant is checked active (cached
  ≤ 30 s) and the user active, so a revoked grant stops within 30 s.
- **Refresh token:** opaque, hashed in its own table,
  `oauth_refresh_tokens` (not `refresh_tokens`): `validate_refresh_token`
  accepts any live row of `refresh_tokens` and `/auth/refresh` turns it
  into an ordinary, audience-less Sinas access token, so sharing that table
  would let an MCP client trade its refresh token for access beyond its
  app. Only `/oauth/token` reads `oauth_refresh_tokens`, and it only ever
  mints MCP-audience tokens. **Rotated on every use** (the old row gets
  `used_at` and `replaced_by`). Presenting an already-used refresh token
  revokes the whole grant (reuse detection, OAuth 2.1 / Security BCP),
  after a 10 s grace in which the same successor is returned (clients that
  retry a timed-out refresh). Console refresh tokens keep today's
  behaviour; rotating them too is a separate change.
- **Lifetimes:** idle expiry `MCP_GRANT_IDLE_DAYS` (30, refresh not used),
  absolute `MCP_GRANT_MAX_DAYS` (90). A grant consented in an SSO session
  is capped by that provider's `sessionHours` when the user's domain is in
  its `enforceForDomains`, consistent with `oidc-sso.md` §5 ("a user removed
  at the IdP keeps no access past the session's end"); the client then runs
  the authorization flow again, through SSO.
- **Context (#292):** the consent request is authenticated by the
  console's token; its `context` is stored on the grant, shown on the
  consent screen ("acting for orgId org-2"), signed into each access token
  and re-issued unchanged at refresh. With `ATTRIBUTION_MEMBERSHIPS`, a
  refresh fails (and the grant is revoked) once the membership is gone,
  as #292 does for console refresh tokens.
- **Revocation:** `POST /oauth/revoke` (either token revokes the grant),
  the user's "Connected apps" page, the admin grants page, disabling the
  client, disabling the manifest's MCP, deactivating the user and a
  password change all end grants: `revoke_all_refresh_tokens` gains the
  grants, since MCP refresh tokens live in their own table.
- **Scope:** one scope, `mcp`: "use this app's tools as me". The audience
  already pins the app, so per-app scopes would repeat it, and finer scopes
  (per tool) would duplicate permissions. Requests for unknown scopes get
  `invalid_scope`; `offline_access` is not listed (the spec says not to)
  but accepted and ignored, since refresh tokens are always issued to MCP
  clients. A future read-only scope maps naturally onto D3
  annotations (open question).

### 5.5 Client registration

Three ways, one `oauth_clients` table, in the order clients try them:

| Kind | How | Policy |
|---|---|---|
| Admin-registered | `oauthClients` applier kind (REST, config apply, change history; packages may not declare it, like `identityProviders`) | always allowed; may be confidential (the secret is a shared Secret's name, as for identity providers). For clients that accept a static client id (Claude's "use your own OAuth client", Claude Code `--client-id`, VS Code's prompt, Cursor `auth`) |
| Client ID Metadata Document (CIMD) | `client_id` is an `https` URL with a path; the AS fetches the client's metadata from it at `/authorize` | `MCP_CLIENT_ID_METADATA`: `on` (default when OAuth is on), `off`, or a host allowlist (e.g. `claude.ai,chatgpt.com`). The spec's SHOULD; what Claude and ChatGPT prefer |
| Dynamic (RFC 7591) | `POST /oauth/register`, unauthenticated | `MCP_CLIENT_REGISTRATION`: `open` (default when OAuth is on), `approval` (registered as `pending`; `/authorize` explains that an admin must approve it in the console), `closed` (no `registration_endpoint` advertised). Deprecated by the spec but still what VS Code and Claude Code use by default |

Both CIMD and DCR are on by default because each major client depends on
one of them (§10), and the consent screen, not registration, is where
trust is decided: registration only proves control of a redirect URI.
Organisations that want a gate set DCR to `approval` and CIMD to an
allowlist.

**CIMD.** The document must contain `client_id` (equal to its URL),
`client_name` and `redirect_uris`; the request's `redirect_uri` must be in
it. Fetching is an SSRF surface: `https` only, public addresses only (the
resolved IP is checked, no redirects followed), 5 s timeout, 5 KB limit,
cached per `Cache-Control` for at most 24 h. A row is upserted in
`oauth_clients` (`registration = metadata_document`) so grants, the
connected-apps page and audit have something to point at. Claude only uses
CIMD when the AS lists `none` among token endpoint auth methods, which it
does. `private_key_jwt` (ChatGPT can use it) is a later addition; CIMD
clients are public clients with PKCE until then.

**DCR abuse limits.** Per-IP rate limit (`MCP_DCR_PER_IP_PER_HOUR`, 10);
`application_type` required (`native`: loopback or private-use redirects
only; `web`: `https` only); `client_name` ≤ 100 printable characters; at
most 5 `redirect_uris`, each validated (§5.6); `logo_uri`, `client_uri`,
`policy_uri`, `tos_uri` stored but never rendered; registrations not used
to complete a grant within 24 h are deleted, and unused clients after 90
days; a global cap (`MCP_DCR_MAX_CLIENTS`, 10 000) answers `503`. Dynamic
clients get `token_endpoint_auth_method: none` (public, PKCE) unless they
ask for a confidential method, in which case the secret is returned once.
No RFC 7592 management endpoint. Some clients register on every new
connection (Claude does), which the garbage collection absorbs.

### 5.6 Redirect URIs

- Exact string match against the registered list (no wildcards, no prefix
  match), except that the port of a loopback URI may vary (RFC 8252 §7.3):
  Claude Code's metadata document lists `http://localhost/callback` and
  uses an ephemeral port.
- Allowed: `https://`; `http://127.0.0.1`, `http://[::1]` and
  `http://localhost` on any port; private-use schemes of reverse-DNS form
  (RFC 8252 §7.1), i.e. a scheme containing a dot. Never `javascript:`,
  `data:`, `file:`, fragments.
- The consent screen shows the redirect host, as the spec requires for
  CIMD ("MUST clearly display the redirect URI hostname"), or "an app on
  this computer" for loopback and private-use schemes, with a warning when
  a client claiming a well-known name redirects only to localhost (any
  local process can claim it).

### 5.7 Consent screen (console `/ui/oauth/authorize`)

Shows: the client's name, qualified by where it comes from (admin-
registered: "registered by your administrator"; CIMD: "published by
<host of the client_id URL>"; DCR: an "unverified" badge, since anyone can
register any name); where it sends you (redirect host);
the app (manifest `mcp.title`, description) and the tools it will see for
this user (count and names); "as <email>" and the attribution context;
Allow / Deny. Wording: "<client> wants to use the <app> tools in Sinas as
you."

- **Clickjacking:** the route is served with `Content-Security-Policy:
  frame-ancestors 'none'` and `X-Frame-Options: DENY` (the console sends
  `SAMEORIGIN` today: `console/nginx.conf`, `Caddyfile`). The decision
  button is disabled until the page has been visible for a moment, against
  double-click and overlay tricks.
- **CSRF:** the decision is an XHR with the console's bearer token, not a
  cookie-authenticated form post, so cross-site requests can't make it.
  The parked request is bound to the first console user who reads it and
  is single-use; a request id leaked to another user is refused.
- **Phishing:** the screen is the defence against a malicious client
  asking for a real user's tools; hence no rendered logos for unverified
  clients, the explicit redirect host, and the `approval` / `closed` DCR
  policies for organisations that want a gate.

### 5.8 Data model

- `oauth_clients`: `client_id` (random, or the CIMD URL), `secret_hash`
  or `secret` (a Secret's name for admin clients), `name`, `redirect_uris`,
  `grant_types`, `token_endpoint_auth_method`, `registration`
  (`admin` | `dynamic` | `metadata_document`), `status` (`active` |
  `pending` | `disabled`), `registered_ip`, `software_id`,
  `software_version`, `last_used_at`, the applier's `managed_by` /
  `config_name` / `config_checksum` (admin clients only), timestamps.
- `oauth_grants`: `id`, `user_id`, `client_id`, `manifest_id`, `resource`,
  `scope`, `context`, `identity_provider` (SSO cap), `expires_at`,
  `last_used_at`, `revoked_at`, `revoked_reason`, timestamps.
- `oauth_refresh_tokens`: `token_hash`, `token_prefix`, `grant_id` (FK),
  `expires_at`, `used_at`, `replaced_by`, `created_at`. Separate from
  `refresh_tokens` so the console's refresh path can never redeem one.
- Redis: parked authorization requests (10 min), codes (60 s), both
  `GETDEL`, as `oauth_state.py` does.

One additive migration; nothing existing changes shape.

### 5.9 Rate limits and audit

- Limits (Redis, `core/rate_limit.py`): `/oauth/register` per IP,
  `/oauth/authorize` per IP, `/oauth/token` per client and per IP, failed
  PKCE or code reuse per client (and a code is burned on the first
  failure); `/mcp/...` per user and per grant (`MCP_RATE_LIMIT_PER_MINUTE`,
  120), with `Retry-After`. LLM budgets and rate limits (#290) apply
  inside, as for every call.
- Audit: grants and dynamic clients are runtime rows, like API keys and
  memberships, so they have no `config_revisions` entries; admin-registered
  clients do (applier). Every AS endpoint and every MCP request goes to the
  request log with a `permission_used` label (`sinas.oauth.authorize`,
  `sinas.oauth.token`, `sinas.oauth.register`, and for tool calls the
  underlying resource permission). Consent decisions, grant creation,
  revocation, refresh-token reuse and client approval also emit structured
  log lines (user id, client id, grant id, manifest, IP), exported as OTel
  logs when enabled.

### 5.10 Out of scope for the AS

No implicit, password or `client_credentials` grants (API keys cover
machine access), no ID tokens or `openid` scope, no RFC 7592 client
management, no introspection endpoint (only Sinas verifies these tokens),
no PAR in the first cut. The same endpoints could later serve the Sinas
CLI and "Sign in with Sinas" for third-party apps (§13); nothing in the
data model prevents it (a grant's `resource` is just a URL).

## 6. Request handling on `/mcp/{ns}/{name}`

1. **Feature gates:** `MCP_SERVER_ENABLED`, then the manifest exists, is
   active and has `mcp.enabled`; else `404` (no information about which).
2. **Origin check:** a request with an `Origin` header must match
   `MCP_ALLOWED_ORIGINS`, else `403` (the transport spec's MUST, against DNS
   rebinding); no `Origin` (native clients, server-side connectors)
   passes. The SDK's Host check is configured with `DOMAIN`. CORS for the
   allowed origins exposes `WWW-Authenticate` and `MCP-Protocol-Version`.
3. **Authenticate:** bearer → MCP access token (audience = this URL,
   grant active) or Sinas access token / API key (`verify_jwt_or_api_key`
   rules). Nothing → `401` with `WWW-Authenticate: Bearer
   resource_metadata="...", scope="mcp"` (the metadata only when OAuth is
   enabled). A token for another MCP URL → `401 invalid_token`.
4. **Authorise the app:** the user must be able to read the manifest
   (`sinas.manifests/{ns}/{name}.read`, granted `:all` by default).
5. **Bind the request:** user, permissions, `api_key_id` (`bind_api_key`)
   or `grant_id`, `context`, client info; then hand to the SDK.
6. **`tools/list`:** D5. **`tools/call`:** re-resolve exposure and
   permission for that one tool (never trust the list), then run.
7. **Internal credential (no token passthrough):** the turn or run gets a
   fresh short-lived Sinas access token as its `user_token`, never the
   presented MCP token. That token must not be broader than the caller:
   today's minted tokens (`webhooks.py`, `chat_wakeups.py`) carry only the
   user, and a `sinas_token` connector forwards `user_token` to an
   endpoint that then loads the user's full permissions, while
   `bind_api_key` narrows permissions only inside the current task. So the
   internal token carries the credential it derives from: `akid` (the API
   key id) or `gid` (the grant id). `verify_jwt_or_api_key`, the choke
   point every runtime request goes through, then applies the key's
   permissions (and refuses the token once the key is deleted or expired)
   when `akid` is present, and refuses it once the grant is revoked when
   `gid` is present. The same claim closes the gap for the tokens wakeups
   and webhooks already mint on behalf of an API-key turn. Continuations
   that run later (wakeups, resumed rounds) record `grant_id` next to
   `api_key_id` and mint their tokens the same way.

## 7. Security considerations

| Threat | Mitigation |
|---|---|
| **Confused deputy** (an MCP client, or a third-party AS in front of Sinas, gets Sinas to act for a user who never consented to that client) | Sinas is its own AS: every grant is a consent by the user, per client and per app. No static client id shared across third parties; DCR clients are distinct; remembered consent only for the same client + resource + redirect URI. |
| **Token passthrough** (forbidden by the spec) | The MCP endpoint accepts only tokens whose audience is itself, or Sinas's own credentials. It never forwards the presented token: turns get a freshly minted internal token carrying the caller's API key or grant, so it is never broader than the caller (§6, step 7); `sinas_token` connectors and functions see that one. MCP refresh tokens live in their own table and can't be redeemed at `/auth/refresh`. MCP tokens fail on every other endpoint (audience, §1 fact 2). |
| **Token reuse across apps** | `aud` = canonical MCP URL, checked on every request; `resource` required at authorize and token, and must match the grant. |
| **Mix-up** (a client talking to several authorization servers is tricked into sending Sinas's code elsewhere, or another AS's code to Sinas) | RFC 9207 `iss` on every authorization response, advertised in metadata; PKCE and `resource` alone don't prevent it. |
| **SSRF through CIMD** (a `client_id` URL pointing at internal addresses) | `https` only, resolved address must be public, no redirects, size and time limits, optional host allowlist. |
| **Localhost redirect impersonation** (a local process claims a well-known client's metadata document) | The consent screen names the redirect as "an app on this computer" and warns when a well-known client redirects only to loopback. |
| **Session handle hijacking** (a guessed or leaked `session` handle) | Handles are random, and resolved only for the chat's own user; possession grants nothing. |
| **Stolen refresh token** | Rotation with reuse detection revokes the grant; grants idle out and have an absolute lifetime. |
| **Consent phishing** (a malicious client, registered dynamically, lures a user into granting access) | Consent screen with unverified badge, redirect host, app and tools listed; no rendered logos for unverified clients; `approval`/`closed` DCR policies; users and admins can revoke; request log shows which client did what. |
| **DCR abuse** (registration floods, junk clients, storage exhaustion) | Per-IP limits, a global cap, garbage collection of never-used clients, field length limits, no rendering of client-supplied URLs. |
| **Authorization code interception / injection** | PKCE S256 required, code single-use (60 s), bound to client, redirect URI, challenge and resource; `iss` in the response (mix-up); exact redirect match. |
| **Open redirect** | Errors before the redirect URI is validated stay in the console; `redirect_to` only ever a registered URI. |
| **Clickjacking / CSRF on consent** | `frame-ancestors 'none'`; bearer-authenticated XHR decision; single-use, user-bound parked requests. |
| **DNS rebinding against a local network deployment** | `Origin` validation; Host validation via the SDK's transport security settings. |
| **Prompt injection through tool output** (an agent's reply, built from untrusted data, instructs the client's model) | Out of Sinas's control once returned; mitigations: results are data in `content`, never in tool descriptions; descriptions and titles come from admins (resources, manifest hints), not from users or tool output; annotations default to non-read-only so clients keep confirming; approvals stay in Sinas and are never answerable by the calling model (D6). |
| **Prompt injection into Sinas** (the client's model sends hostile `message` text) | Same as any user message: permissions, the agent's own tool set, approval rules and budgets apply. |
| **Over-broad exposure** | Exposure is a filter only; permissions stay authoritative, wildcards resolve within the user's permissions, `tools/call` re-checks. |
| **Task id / requestState forgery** | Both sealed (AES-GCM via the SDK, key from `ENCRYPTION_KEY` derivation) and bound to the principal; Sinas re-checks chat ownership on use. |
| **Enumeration** | Unknown manifest, MCP disabled and unauthorised all look alike where possible (`404` before auth only reveals that the URL isn't an MCP server). |

Permissions plus the agent's tool approvals stay authoritative throughout:
nothing in this design lets a client do something the user couldn't do in
the console, and nothing lets the client's model approve what Sinas asks a
human to approve.

## 8. Observability

- FastAPI's server span covers each HTTP request (#291). When the
  request's `_meta` carries `traceparent` / `tracestate` (the spec's
  OpenTelemetry keys), that context becomes the parent, so a tracing client
  sees Sinas's work inside its own trace (`extract_trace_context`,
  `core/telemetry.py`). Under it:
  - `mcp.request` per JSON-RPC message: `mcp.method`, `mcp.protocol_version`,
    `sinas.manifest`, `sinas.mcp.client_id`, `sinas.mcp.auth`
    (`oauth` | `token` | `api_key`), `enduser.id`; the SDK's own spans
    (`server/_otel.py`) nest or are disabled to avoid duplicates
    (implementation detail).
  - `mcp.tool_call` per `tools/call`: `mcp.tool`, `sinas.resource.kind`,
    `sinas.resource` (`ns/name`), outcome (`completed`,
    `input_required`, `approval_required`, `in_progress`, `error`),
    `error.type` = the #299 code. The existing `conversation.turn`,
    `llm.call`, `agent.delegate` spans are its children.
  - AS endpoints: `oauth.authorize`, `oauth.token` (`grant_type`),
    `oauth.register`, never with secrets, codes or tokens as attributes.
- Request log rows for every MCP request (path, user, `permission_used`).
- Metrics later: tool calls per manifest/client, token refreshes, DCR
  registrations.

## 9. Configuration

| Setting | Default | |
|---|---|---|
| `MCP_SERVER_ENABLED` | `false` | serves `/mcp/...` (phase 1) |
| `MCP_OAUTH_ENABLED` | `false` | the AS endpoints and the `resource_metadata` challenge (phase 2) |
| `MCP_CLIENT_REGISTRATION` | `open` | `open` / `approval` / `closed` |
| `MCP_CLIENT_ID_METADATA` | `on` | `off` / `on` / comma-separated host allowlist |
| `MCP_ALLOWED_ORIGINS` | empty | browser origins allowed to call `/mcp/...` |
| `MCP_ACCESS_TOKEN_MINUTES` | `60` | |
| `MCP_GRANT_IDLE_DAYS` / `MCP_GRANT_MAX_DAYS` | `30` / `90` | |
| `MCP_TOOL_CALL_TIMEOUT_SECONDS` | `120` | sync budget before Tasks / `in_progress`; keep under the clients' own limits (Claude: 240 s) |
| `MCP_RATE_LIMIT_PER_MINUTE` | `120` | per user and grant |
| `MCP_DCR_PER_IP_PER_HOUR` / `MCP_DCR_MAX_CLIENTS` | `10` / `10000` | |

`DOMAIN` must be right: it forms the MCP URLs, the issuer and every
metadata document. The AS works with HS256 and RS256; RS256 is recommended
for other reasons (`verifying-tokens.mdx`) but not required.

## 10. Compatibility matrix

From the clients' own documentation as of 2026-10-20; *unverified* marks
what the docs don't state (or only third parties do). Every client below
speaks Streamable HTTP.

| Client | Sinas route | OAuth client registration | Static bearer header | Elicitation | Tasks ext. | Notes |
|---|---|---|---|---|---|---|
| Claude: claude.ai, Desktop, mobile (custom connectors) | OAuth (phase 2) | CIMD when the AS advertises it with `none` auth, else DCR (a new registration per connection); or "use your own OAuth client" (id, optional secret) | `static_headers` is a limited beta, not with OAuth | *unverified* | *unverified* | Callback `https://claude.ai/api/mcp/auth_callback`. Needs a 401 to start sign-in; uses only the first `authorization_servers` entry; PRM `resource` must equal the entered URL; follows the 2025-03-26 to 2025-11-25 auth specs; 240 s tool timeout, ~150k-character results |
| Claude Code | bearer (phase 1) or OAuth | DCR by default, CIMD discovered automatically (`https://claude.ai/oauth/claude-code-client-metadata`, loopback on an ephemeral port), or `--client-id` / `--client-secret` | `claude mcp add --transport http <name> <url> --header "Authorization: Bearer <key>"` | form and URL | not listed | Speaks 2026-07-28 (with legacy fallback); results capped at 25k tokens by default |
| ChatGPT (apps, developer mode) | OAuth (phase 2) | CIMD preferred (`https://chatgpt.com/oauth/client.json`), DCR (once per connector), or static credentials | no (no custom API keys) | documented; modes *unverified* | *unverified* | **Requires RFC 9207 `iss`**. Redirect `https://chatgpt.com/connector_platform_oauth_redirect`. Tools without `readOnlyHint: true` are treated as writes and confirmed. Protocol revision *unverified* |
| VS Code (GitHub Copilot agent mode) | bearer (phase 1, `headers` with `${input:...}`) or OAuth | DCR first, then prompts for a client id and secret; CIMD *unverified* | yes | form (official docs); URL mode *unverified* | *unverified* | Redirects `http://127.0.0.1:33418`, `https://vscode.dev/redirect`. `readOnlyHint: true` skips confirmation. Revision *unverified* (third parties say 2025-11-25) |
| GitHub Copilot coding agent and code review (github.com) | bearer only | none: "do not currently support remote MCP servers that leverage OAuth" | yes (secrets prefixed `COPILOT_MCP_`) | no | no | Tools only, with a required `tools` allowlist; code review only uses `readOnlyHint: true` tools |
| Cursor | bearer (phase 1) or OAuth | static client via `auth: {CLIENT_ID, CLIENT_SECRET}`; DCR and CIMD *unverified* | yes (`${env:...}`) | yes | *unverified* | Redirects `https://www.cursor.com/agents/mcp/oauth/callback`, `http://localhost:8787/callback`. Revision *unverified* |

What follows from it:
- Phase 1 (bearer) already serves Claude Code, Cursor, VS Code and
  GitHub's coding agent. claude.ai and ChatGPT need phase 2.
- Phase 2 needs CIMD **and** DCR **and** admin-registered clients to cover
  everyone, plus RFC 9207 `iss` for ChatGPT.
- Only Claude Code is documented on 2026-07-28 with elicitation, so MRTR
  elicitation reaches few clients today; the D6 fallbacks are the common
  path, and elicitation for handshake-era clients would need stateful
  sessions (rejected in D8).
- No client documents the Tasks extension, which is why it is last.

## 11. Testing

- Protocol: the SDK's in-process client against the mounted ASGI app (as
  the client-side tests do), on 2025-06-18, 2025-11-25 and 2026-07-28:
  list, call, MRTR elicitation, fallbacks, progress, cancellation.
- Exposure: resolver cases (legacy namespaces, patterns, permission
  filtering, exact entries not granting) and manifest applier config parity.
- AS: the happy path and every rejection (verifier, code reuse, redirect,
  resource, scope, client status); rotation and reuse detection; audience
  isolation both ways; revocation latency; DCR limits; CIMD SSRF guards.
- End to end against a throwaway backend: Claude Code with a header, the
  MCP Inspector for OAuth (as `tests/e2e/oidc/` does for SSO).

## 12. Rollout

**Phase 1: MCP endpoint with bearer auth.**
Manifest `exposedResources` + `mcp` (D2, migration), the resolver in
`X-Application` discovery, `/mcp/{ns}/{name}` on the SDK (D8), agent and
pipeline tools (D5), function/query opt-in, errors/usage/attribution (D7),
`approval_required` for sync turns (D6), the control-tool fallbacks,
OTel and request log. Clients: Claude Code, Cursor, VS Code with headers,
scripts. Behind `MCP_SERVER_ENABLED`.

**Phase 2: Sinas as the authorization server.**
§5 in full: metadata (with RFC 9207 `iss`), authorize + console consent,
token with rotation, CIMD, DCR, admin-registered clients, the
connected-apps and OAuth-clients pages, revocation, limits. Clients:
claude.ai / Desktop connectors, ChatGPT, VS Code's OAuth flow. Behind
`MCP_OAUTH_ENABLED`.

**Phase 3: richer protocol.**
MRTR elicitation for ask_user and approvals, the Tasks extension, progress
notifications, D3 hints; image content parts in `message`.

Phase 3's elicitation could move into phase 1 for 2026-07-28 clients if
client support is there by then; the fallbacks make the order safe.

**Docs:** a new `docs-mint/platform/mcp-server.mdx` (connecting each client,
the URL, bearer vs OAuth, what tools look like); `admin/manifests.mdx`
(`exposedResources`, `mcp`); a section in `platform/authentication.mdx`
(connected apps, revocation); `admin` page for OAuth clients and the DCR
policy; `getting-started/environment-variables.mdx`; a note in
`platform/verifying-tokens.mdx` that MCP tokens carry the MCP URL as `aud`
and are not meant for other services. An ADR per phase in
`backend/docs/adrs/` as each lands.

## 13. Open questions

1. **Grants and SSO sessions.** §5.4 caps a grant at the identity
   provider's `sessionHours` for SSO-enforced domains, so a user removed at
   the IdP loses MCP access with the session. That means re-authorising
   (a browser hop) every `sessionHours` (12 h by default). Accept, raise the
   cap for MCP only, or wait for SCIM to deprovision instead?
2. **Who may turn MCP on.** `mcp.enabled` is part of the manifest, so
   whoever may update the manifest (and any package shipping one) opens an
   external surface, bounded by permissions and the instance switch.
   Enough, or a separate permission (`sinas.manifests.mcp:all`)?
3. **Exact entries in exposure.** D2 makes even an exact `ns/name` entry
   a filter (unlike an agent's `enabled_*`, where it is the author's
   grant). Any case where an app should expose a resource its users
   can't otherwise use (e.g. an agent reachable only through the app)?
   That would need a delegation model, not exposure.
4. **Default registration policy.** CIMD and DCR `open` by default when
   OAuth is on (what the ecosystem expects), or `approval` by default (one
   admin click per new client type, but the first connection fails)?
5. **Remembered consent.** Skip the full consent screen for an existing
   grant (§5.3), or always show it for DCR clients?
6. **Choosing the context at consent.** With #292 memberships, a user in
   several organisations consents with whatever context the console
   session has. Should the consent screen offer a choice of context
   (validated against memberships)?
7. **Approvals over elicitation.** Form-mode elicitation shows the
   approval in the client's UI, which is the user's agent but not Sinas.
   Keep it the default and `mcp.approvals: console` the opt-in, or the
   other way round?
8. **Scopes beyond `mcp`.** A read-only scope (only tools hinted
   `readOnlyHint: true`), with `insufficient_scope` step-up for the rest?
   Useful only once hints exist (phase 3).
9. **Shared conversations.** MCP session handles are prefixed `mcp:`, so a
   conversation can't be continued from the app with its own session key
   or vice versa. Should a handle be usable from both?
10. **Refresh-token rotation for the console.** MCP refresh tokens rotate
    (spec MUST for public clients); console refresh tokens don't. Align
    them in the same release?
11. **Result size.** Claude caps results at about 150k characters and
    Claude Code at 25k tokens by default. Truncate agent replies and
    pipeline outputs at a configurable size with a console link to the
    full result, or leave it to the clients?
12. **More of the protocol.** `listChanged` via `subscriptions/listen` (a
    cross-replica bus); MCP resources (a chat or a run as a resource),
    prompts (templates); MCP Apps (`io.modelcontextprotocol/ui`) for
    components; the `oauth-client-credentials` and
    `enterprise-managed-authorization` extensions (vs API keys and the
    consent flow). All deferred; which first?
13. **One AS for everything.** Should the console, the CLI and
    third-party apps move to the same authorization server (§5.10), and
    does that change any naming now (`/oauth/...` paths, the `mcp` scope)?
