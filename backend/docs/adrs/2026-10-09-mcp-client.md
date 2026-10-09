# ADR: MCP client — remote Model Context Protocol servers as a tool source

- **Status:** Accepted
- **Date:** 2026-10-09
- **Authors:** Kjeld Oostra (with Claude)
- **Related code:**
  - `backend/app/services/mcp_client.py` — connect, list (cached), call, result mapping
  - `backend/app/services/mcp_tools.py` — `McpToolConverter`: discovery + execution, the filters
  - `backend/app/models/mcp_server.py`, `backend/app/schemas/spec/mcp_server.py`, `backend/app/services/resources/mcp_servers.py` — the resource, its spec, its applier
  - `backend/app/api/v1/endpoints/mcp_servers.py` — REST over the applier + the live `tools` listing
  - `backend/app/services/tool_discovery.py`, `backend/app/services/tool_execution.py` — the two hooks into the agent loop
  - `backend/alembic/versions/m1c2p3s4r5v6_mcp_servers.py` — `mcp_servers` + `agents.enabled_mcp_servers`
  - `backend/tests/unit/test_mcp_tools.py`, `backend/tests/unit/config_parity/test_mcp_servers.py`

## Context

The Claude Code / Cowork gap analysis left one pure addition open: agents
cannot use tools published by Model Context Protocol servers. Every other
external tool source Sinas has is hand-declared — a connector lists its HTTP
operations one by one — while the ecosystem increasingly ships MCP endpoints
(issue trackers, docs, databases, vendor APIs) whose tool list is the
contract and changes server-side.

Connectors are the existing "external tool source" and already settle the
hard questions: a namespaced resource with ownership and change history,
auth that *names* a Secret rather than holding a value, per-agent enabling
with an optional sub-selection, tools surfaced in discovery and dispatched
on `_metadata.tool_type`. An MCP server should flow through the same seams
rather than invent new ones — and, since the maintainers are unifying every
management endpoint onto config-apply, it must be a declarative resource
with an applier first and an endpoint second.

Two recent mechanisms must keep working unchanged for MCP tools: per-agent
`tool_approvals` rules gate any tool by name glob, and workbench file
references (`{"$workbench": path}`) resolve before dispatch. The sandbox
runs untrusted code and must never hold MCP credentials.

## Decision

**Transport scope: remote HTTP only.** Streamable HTTP is the default and
the recommended transport; the legacy HTTP+SSE transport is a one-line
option in the SDK, so it is accepted too (`transport: sse`). **No stdio.**
The backend runs in containers; a stdio server is a subprocess with the
backend's environment, which would turn "add an MCP server" into arbitrary
process execution on the control plane. Anyone who wants a local stdio
server runs it behind an HTTP bridge, outside the backend.

**SDK.** The official `mcp` package, bounded to the 2.x line (`mcp>=2.2.0,<3`)
because 2.0 replaced the 1.x client API (`mcp.Client`, transports as async
context managers, `httpx2`). The in-process `Client(MCPServer)` path and the
real Streamable HTTP transport behind an ASGI app are what the tests use —
no sockets, no mocks of the protocol.

**Resource: `McpServer`** (`mcp_servers`, config section `mcpServers`),
keyed `namespace/name` like a connector:

- `url`, `transport`;
- `auth`: `{type: none|bearer|header, secret: <Secret name>, header?}` —
  the credential is a Secret, resolved and decrypted in the backend at call
  time (private overrides shared for the calling user, exactly as
  `connector_service` does it). A configured auth whose Secret is missing
  **refuses** the call rather than sending it unauthenticated;
- `headers`: static headers (redacted in change history, like a
  connector's);
- `toolAllow` / `toolDeny`: glob patterns on the server's tool names — what
  the resource exposes at all. Empty allow means every tool; deny wins;
- `timeoutSeconds` (per call) and `connectTimeoutSeconds` (connect +
  handshake + `tools/list`);
- `isActive`, and the `managed_by` / `config_name` / `config_checksum`
  triple every applier-managed kind carries.

The tool list is deliberately **not stored**: it is the server's contract,
fetched at discovery time.

**Agent binding: `enabledMcpServers`** (`agents.enabled_mcp_servers`),
plumbed exactly like `enabledConnectors`: model column (server default
`[]`), REST create/update/response, `config_apply/agents.py`, serializer,
export. Both forms are accepted and normalised to the dict form:

```yaml
enabledMcpServers:
  - tools/tracker                       # every tool the server allows
  - server: tools/docs
    tools: ["search_*"]                 # narrowed further for this agent
```

It enters the agent's config hash only when set, so agents that never
enabled a server keep their existing hash and an upgrade re-applies nothing.

**Tool naming: `mcp_<namespace>__<server>__<tool>`.** The `mcp_` prefix is
new in `tool_name_to_status_key` (→ `mcp:ns/server/tool`), collides with no
existing prefix, and is what approval rules match (`{"match": "mcp_*",
"action": "ask"}` gates every MCP tool). MCP allows characters OpenAI
function names don't; the model-facing name is sanitised and the real tool
name rides in `_metadata.mcp_tool`, which dispatch uses — the name is never
parsed back except as a fallback. `_metadata` is
`{tool_type: "mcp", server_namespace, server_name, mcp_tool, annotations?}`;
it deliberately has no `namespace`/`name` keys, which the approval check
reads as "a function tool".

**Discovery degrades, never fails.** `McpToolConverter.get_available_tools`
lists each enabled server (server filter, then the binding's patterns). A
server that is missing, inactive, unreachable, or whose Secret is missing
contributes no tools and one warning line — a chat turn is never failed by
a tool source. Listings are cached in-process per server *version*
(`id:updated_at`, so an edit invalidates) for `MCP_TOOL_LIST_TTL_SECONDS`
(60); a failed listing is cached for `MCP_TOOL_LIST_FAILURE_TTL_SECONDS`
(15) so a dead server is not re-handshaken on every message.

**Execution** is a branch in `execute_single_tool` beside the connector one,
*after* workbench reference resolution and *after* the approval gate, so
both apply to MCP calls by construction. One connection per operation
(connect, `tools/call`, close): simple, stateless across workers, and the
SDK's session caching gains nothing when every call is a fresh session.

**Result mapping** (`map_call_result`) — a tool result dict, never a raise:

| MCP content | Tool result |
|---|---|
| text blocks | joined into `text` |
| `structuredContent` | `structured_content`; a text block that only repeats it (or the SDK's `{"result": scalar}` wrap of a scalar return) is not duplicated |
| image / audio / binary resource | **with a workbench**: written to `tool_results/mcp_<tool>_<n>.<ext>` and returned as `{type, mime_type, workbench_file, size}`; **without**: inlined in the universal content shape (`{"type":"image","image":"data:…;base64,…"}`, `{"type":"audio","data","format"}`) |
| embedded text resource | `{type: resource, uri, mime_type, text}` |
| resource link | `{type: resource_link, uri, name, …}` |
| `isError` | `{"error": <text>}` — the model sees the failure and recovers |

Large text results go through the generic truncate-and-spill path like
every other tool result; nothing MCP-specific is needed there.

**Management API** at `/api/v1/mcp-servers` (`POST`, `GET`, `GET/PUT/DELETE
/{ns}/{name}`) over the applier via the shared `rest` helpers, plus `POST
/{ns}/{name}/tools`: a live, uncached `tools/list` (the console's "Test &
List Tools"), reporting which tools the server filters hide; 502 when the
server can't be reached. Permissions: `sinas.mcp_servers/*/*.{create,read,
update,delete}`, granted `:own` to users by default like connectors.

### API / schema / interface sketch

```yaml
mcpServers:
  - namespace: tools
    name: tracker
    url: https://mcp.tracker.example.com/mcp
    transport: streamable_http            # or sse
    auth: { type: bearer, secret: TRACKER_TOKEN }
    headers: { X-Tenant: acme }
    toolAllow: ["list_*", "get_*", "create_issue"]
    toolDeny: ["delete_*"]
    timeoutSeconds: 60
    connectTimeoutSeconds: 10
```

```python
# mcp_client
async with connect(db, server, user_id, read_timeout=...) as client: ...
await list_tools(db, server, user_id, use_cache=True) -> list[ListedTool]   # raises McpClientError
await call_tool(db, server, "create_issue", {...}, user_id) -> CallToolResult
await map_call_result(result, tool_name=..., store_blob=...) -> dict

# tool definition
{"type": "function", "function": {
   "name": "mcp_tools__tracker__create_issue",
   "description": "[tools/tracker] Create an issue",
   "parameters": <the server's inputSchema>,
   "_metadata": {"tool_type": "mcp", "server_namespace": "tools",
                 "server_name": "tracker", "mcp_tool": "create_issue"}}}
```

## Impact

| Component | Change |
|---|---|
| `pyproject.toml` | `mcp>=2.2.0,<3` |
| DB | `mcp_servers` table; `agents.enabled_mcp_servers JSON NOT NULL DEFAULT '[]'` — additive, existing rows and API shapes untouched |
| Config apply | `mcpServers` section with an applier (ownership, history, prune, restore); `enabledMcpServers` on agents |
| Export / packages | `mcpServers` exported (secret *names* only, as for connectors); `mcp_server` packageable; `MANAGED_MODELS` |
| Tool discovery / execution | one new source, one new dispatch branch; `mcp_` status key |
| Console | MCP Servers list + editor (Connectors-shaped, with a live tool listing); an **MCP** tab in the agent editor |
| Sandbox | untouched: calls run in the backend, credentials never leave it |

## Open questions

- **Native image passthrough.** Tool results are strings today, so an
  inlined image reaches the model as a data URL in text. The workbench
  path (a file the model can `workbench_read` or pass to code execution)
  is the useful one; giving providers a real image block for tool results
  is a cross-cutting change for all tool kinds.
- **Egress policy.** Connectors don't restrict `baseUrl` to public
  networks either (self-hosted servers on internal networks are the common
  case), so MCP follows suit. An instance-level allow/deny list for
  outbound tool traffic would belong to both.
- **Name length.** `mcp_<ns>__<server>__<tool>` can exceed OpenAI's 64-char
  function-name limit for long tool names; connectors have the same
  exposure and nothing enforces it today.
- **Annotations.** `readOnlyHint` / `destructiveHint` are carried in
  `_metadata.annotations` but not acted on; a default rule such as "ask for
  destructive MCP tools" would be a natural next approval-rules feature.

## What we'd NOT do

- **stdio servers** — see above; the backend must not spawn configured
  processes.
- **MCP resources, prompts, sampling, elicitation, roots.** Tools are the
  gap; the rest adds protocol surface without an agent-loop consumer yet.
  Sampling in particular would let a remote server drive *our* model.
- **OAuth flows for MCP servers** (the SDK's authorization-code client).
  Connectors already have a per-user authorization-code grant with its own
  callback and token rows; reusing it for MCP is a follow-up once someone
  needs a server that only speaks OAuth. Bearer/header secrets cover the
  current ecosystem.
- **Persistent connections / session pooling.** Per-call connections are
  simple and worker-safe; measure before optimising.
- **Storing the tool list on the resource.** It would drift; the server is
  the source of truth and the cache keeps discovery cheap.
- **A separate "MCP tool" approval kind.** Name-glob rules already cover it.

## Next steps

1. Act on tool annotations in approval rules (destructive → ask by default).
2. Per-user OAuth for MCP servers, reusing the connector grant machinery.
3. Native image/audio blocks in tool results across providers.
4. MCP *resources* as a workbench source (`workbench_fetch` from a server),
   once the file-tree work settles.
