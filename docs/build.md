## Build

### Agents

Agents are configurable AI assistants. Each agent has an LLM provider, a system prompt, and a set of enabled tools.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier (e.g., `support/ticket-agent`) |
| `llm_provider_id` | LLM provider to use (null = system default) |
| `model` | Model override (null = provider's default) |
| `system_prompt` | Jinja2 template for the system message |
| `temperature` | Sampling temperature (default: 0.7) |
| `max_tokens` | Max token limit for responses |
| `input_schema` | JSON Schema for validating chat input variables |
| `output_schema` | JSON Schema for validating agent output |
| `initial_messages` | Few-shot example messages |
| `enabled_functions` | Functions available as tools (list of `namespace/name`) |
| `function_parameters` | Default parameter values per function (supports Jinja2) |
| `enabled_agents` | Other agents callable as sub-agents |
| `enabled_skills` | Skills available to the agent |
| `enabled_queries` | Database queries available as tools |
| `query_parameters` | Default query parameter values |
| `enabled_collections` | File collections the agent can access. Plain string (readonly) or `{"collection": "namespace/name", "access": "readonly\|readwrite"}`. Readwrite enables write/edit/delete file tools. |
| `enabled_stores` | Stores the agent can access. List of `{"store": "namespace/name", "access": "readonly"}` or `{"store": "namespace/name", "access": "readwrite"}` |
| `enabled_connectors` | Connectors available as tools. List of `{"connector": "namespace/name", "operations": [...], "parameters": {"op_name": {"param": "value"}}}`. Parameters support Jinja2 templates and are locked (hidden from LLM). |
| `hooks` | Message lifecycle hooks. `{"on_user_message": [...], "on_assistant_message": [...]}` |
| `system_tools` | Platform capabilities. List of strings or config objects. See [System Tools](#system-tools). |
| `icon` | Icon reference (see [Icons](#icons)) |

**Message hooks:** Functions that run before/after agent messages. Each hook has:
- `function`: reference to a function (`namespace/name`)
- `async`: if true, fire-and-forget (no impact on latency)
- `on_timeout`: `block` (stop pipeline) or `passthrough` (continue) — sync hooks only

Hook functions receive `{"message": {"role": "...", "content": "..."}, "chat_id": "...", "agent": {"namespace": "...", "name": "..."}, "user_id": "..."}` and can return:
- `{"content": "..."}` to mutate the message
- `{"block": true, "reply": "..."}` to stop the pipeline
- `null` to pass through unchanged

```yaml
agents:
  - name: my-agent
    hooks:
      onUserMessage:
        - function: default/guardrail
          async: false
          onTimeout: block
      onAssistantMessage:
        - function: default/pii-filter
          async: false
          onTimeout: passthrough
```

**Management endpoints:**

```
POST   /api/v1/agents                       # Create agent
GET    /api/v1/agents                       # List agents
GET    /api/v1/agents/{namespace}/{name}    # Get agent
PUT    /api/v1/agents/{namespace}/{name}    # Update agent
DELETE /api/v1/agents/{namespace}/{name}    # Delete agent
```

**Runtime endpoints (chats):**

```
POST   /agents/{namespace}/{name}/invoke             # Invoke (sync request/response)
POST   /agents/{namespace}/{name}/chats              # Create chat
GET    /chats                                        # List user's chats
GET    /chats/{id}                                   # Get chat with messages
PUT    /chats/{id}                                   # Update chat
DELETE /chats/{id}                                   # Delete chat
POST   /chats/{id}/messages                          # Send message
POST   /chats/{id}/messages/stream                   # Send message (SSE streaming)
GET    /chats/{id}/stream/{channel_id}               # Reconnect to active stream
POST   /chats/{id}/approve-tool/{tool_call_id}       # Approve/reject a tool call
```

**Invoke endpoint:** A synchronous request/response alternative to the two-step chat flow. Intended for integrations (Slack, Telegram, webhooks) that need a simple call-and-response.

```bash
# Simple invoke
curl -X POST https://yourdomain.com/agents/support/helper/invoke \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the status of order #123?"}'
# → {"reply": "Your order is on its way...", "chat_id": "c_abc123"}

# With session key (conversation continuity)
curl -X POST https://yourdomain.com/agents/support/helper/invoke \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Follow up on that", "session_key": "slack:U09ABC123"}'
# → Same chat_id as previous call with this session_key
```

- `session_key`: Maps an external identifier (Slack channel, Telegram chat, WhatsApp number) to a persistent Sinas chat. One chat per `(agent_id, session_key)` pair.
- `reset: true`: Archives the existing session and starts a new conversation.
- `input`: Agent input variables, only used when creating a new chat.
- Streams internally, returns assembled reply as a single JSON payload.

**How chat works:**

1. Create a chat linked to an agent (optionally with input variables validated against `input_schema`)
2. Send a message — Sinas builds the conversation context with the system prompt, preloaded skills, message history, and available tools
3. The LLM generates a response, possibly calling tools
4. If tools are called, Sinas executes them (in parallel where possible) and sends results back to the LLM for a follow-up response
5. The final response is streamed to the client via Server-Sent Events

**Ephemeral chats** can be created with a TTL by passing `expires_in` (seconds) when creating the chat. Expired chats are automatically hard-deleted (with all messages) by a scheduled cleanup job:

```bash
# Create an ephemeral chat that expires in 1 hour
curl -X POST https://yourdomain.com/agents/default/default/chats \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"expires_in": 3600}'
```

**Chat archiving** — Chats can be archived via `PUT /chats/{id}` with `{"archived": true}`. Archived chats are hidden from the default list but can be included with `?include_archived=true`.

**Agent-to-agent calls** go through the Redis queue so sub-agents run in separate workers, avoiding recursive blocking. Results stream back via Redis Streams.

**Function parameter defaults** pre-fill values when an agent calls a function. Supports Jinja2 templates referencing the agent's input variables:

```json
{
  "email/send_email": {
    "sender": "{{company_email}}",
    "priority": "high"
  }
}
```

### System Tools

System tools are opt-in platform capabilities beyond the normal function/query toolkit. Enable them via the `system_tools` property on agents. Each tool is either a simple string (no config needed) or an object with a `name` and tool-specific configuration.

```yaml
agents:
  - name: my-agent
    systemTools:
      - codeExecution
      - packageManagement
      - configIntrospection
      - name: databaseIntrospection
        connections:
          - built-in
          - analytics-db
```

**Available system tools:**

| Tool | Type | Description |
|---|---|---|
| `codeExecution` | string | Generate and execute Python code in sandboxed containers |
| `configIntrospection` | string | Read-only inspection of the current Sinas configuration: list resource types, browse resources by name/description, read full resource detail |
| `packageManagement` | string | Validate, preview, install, and uninstall Sinas packages. Install and uninstall require user approval. |
| `databaseIntrospection` | object | Read-only schema inspection of database connections. Requires `connections` list specifying which connections the agent can access. |

**Config introspection tools:**

- `sinas_config_inspect` — Resource type counts (overview)
- `sinas_config_list(type, namespace?)` — Names + descriptions for a type
- `sinas_config_get(type, namespace, name)` — Full detail of one resource

**Package management tools:**

- `sinas_package_validate(yaml)` — Validate package YAML (syntax + schema)
- `sinas_package_preview(yaml)` — Dry-run install (shows what would change)
- `sinas_package_install(yaml)` — Install package (requires approval)
- `sinas_package_uninstall(name)` — Uninstall package (requires approval)
- `sinas_package_list()` — List installed packages
- `sinas_package_export(name)` — Export package as YAML

**Database introspection tools:**

- `sinas_db_list_tables(connectionName)` — List tables with schemas, types, row counts, and table annotations
- `sinas_db_describe_table(table, connectionName, schema?)` — Columns, types, indexes, foreign keys, and column annotations

The `databaseIntrospection` tool requires a `connections` list. The agent can only introspect connections listed in its config. This uses the same connection pool as regular queries and includes annotations from the semantic layer (table/column display names and descriptions).

**Collection file tools (readwrite access):**

When an agent has `access: readwrite` on an enabled collection, it gets additional tools:

- `write_file_{ns}_{name}(filename, content)` — Write/overwrite a file (creates new version)
- `edit_file_{ns}_{name}(filename, old_string, new_string)` — Surgical edit via exact string replacement
- `delete_file_{ns}_{name}(filename)` — Delete a file

These are in addition to the read tools (`search_collection_*`, `get_file_*`) that every enabled collection provides. `get_file` supports `offset` and `limit` parameters for reading specific line ranges of large files.

### Functions

Functions are Python code that runs in isolated Docker containers. They can be used as agent tools, triggered by webhooks or schedules, or executed directly.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier |
| `code` | Python source code |
| `description` | Shown to the LLM when used as an agent tool |
| `input_schema` | JSON Schema for input validation |
| `output_schema` | JSON Schema for output validation |
| `shared_pool` | Run in shared container instead of sandbox container (admin-only) |
| `requires_approval` | Require user approval when called by an agent |
| `timeout` | Per-function timeout in seconds (overrides global `FUNCTION_TIMEOUT`, default: null) |

#### Function signature

The entry point must be named `handler`. It receives two arguments — `input_data` (validated against input_schema) and `context` (execution metadata):

```python
def handler(input_data, context):
    # input_data: dict validated against input_schema
    #
    # context dict — always present:
    # {
    #     "user_id":        str,   # ID of the user who triggered execution
    #     "user_email":     str,   # Email of the triggering user
    #     "access_token":   str,   # Short-lived JWT for calling the Sinas API
    #     "execution_id":   str,   # Unique execution ID
    #     "trigger_type":   str,   # "AGENT" | "API" | "WEBHOOK" | "SCHEDULE" | "CDC" | "HOOK" | "MANUAL"
    #     "chat_id":        str,   # Chat ID (when triggered by an agent, empty otherwise)
    #     "secrets":        dict,  # Decrypted secrets (shared pool only): {"NAME": "value"}
    # }

    return {"result": "value"}  # Must match output_schema
```

> **Legacy:** Functions named after the resource name (e.g. `def send_email(input_data, context)`) still work but log a deprecation warning. Migrate to `def handler(...)`.

The `access_token` lets functions call back into the Sinas API with the triggering user's identity — useful for reading state, triggering other functions, or accessing any other endpoint.

#### Trigger-specific input_data

Depending on how the function is invoked, `input_data` is populated differently:

| Trigger | input_data contents |
|---|---|
| **AGENT / API / MANUAL / SCHEDULE** | Values matching your input schema, provided by the caller |
| **WEBHOOK** | Webhook `default_values` merged with request body/query params |
| **CDC** | `{"table": "schema.table", "operation": "CHANGE", "rows": [...], "poll_column": str, "count": int, "timestamp": str}` |
| **HOOK** | `{"message": {"role": "user"\|"assistant", "content": "..."}, "chat_id": str, "agent": {"namespace": str, "name": str}, "session_key": str\|null, "user_id": str}` |
| **Collection content filter** | `{"content_base64": str, "namespace": str, "collection": str, "filename": str, "content_type": str, "size_bytes": int, "user_metadata": dict, "user_id": str}` |
| **Collection post-upload** | `{"file_id": str, "namespace": str, "collection": str, "filename": str, "version": int, "file_path": str, "user_id": str, "metadata": dict}` |

#### Hook function return values

Functions used as message hooks (configured in agent's `hooks` field) can return:

| Return value | Effect |
|---|---|
| `None` or `{}` | Pass through unchanged |
| `{"content": "..."}` | Mutate the message content |
| `{"block": true, "reply": "..."}` | Block the pipeline, return reply to client (sync hooks only) |

Async hooks run fire-and-forget. For `on_assistant_message` async hooks, the return value retroactively updates the stored message (not the already-streamed response).

#### Interactive input (shared containers only)

Functions running in shared containers (`shared_pool=true`) can call `input()` to pause and wait for user input:

```python
def handler(input_data, context):
    name = input("What is your name?")    # Pauses execution
    confirm = input(f"Confirm {name}?")   # Can call multiple times
    return {"name": name, "confirmed": confirm}
```

When `input()` is called:
1. The execution status changes to `AWAITING_INPUT` with the prompt string
2. The function thread blocks until a resume value is provided
3. The calling agent or API client resumes execution with the user's response
4. Multiple `input()` calls are supported (each triggers a new pause/resume cycle)

In sandbox containers (`shared_pool=false`), calling `input()` raises a `RuntimeError`.

**Execution:** Functions run in pre-warmed Docker containers from a managed pool. Input is validated before execution, output is validated after. All executions are logged with status, duration, input/output, and any errors.

**Endpoints:**

```
POST   /functions/{namespace}/{name}/execute               # Execute (sync, waits for result)
POST   /functions/{namespace}/{name}/execute/async         # Execute (async, returns execution_id)

POST   /api/v1/functions                                   # Create function
GET    /api/v1/functions                                   # List functions
GET    /api/v1/functions/{namespace}/{name}                # Get function
PUT    /api/v1/functions/{namespace}/{name}                # Update function
DELETE /api/v1/functions/{namespace}/{name}                # Delete function
GET    /api/v1/functions/{namespace}/{name}/versions       # List code versions
```

### Secrets

Write-only credential store. Values are encrypted at rest and never returned via the API. Secrets are available in function context as `context['secrets']` — only for shared pool (trusted) functions. Connectors resolve auth from secrets automatically.

**Visibility:**

- `shared` (default) — global, available to all users and connectors
- `private` — per-user, only used when that user triggers a connector or function

Private secrets override shared secrets with the same name. This enables multi-tenant patterns: admin sets a shared `HUBSPOT_API_KEY`, individual users can override with their own private key.

**Endpoints:**

```
POST   /api/v1/secrets                  # Create or update (upsert by name+visibility)
GET    /api/v1/secrets                  # List names and descriptions (no values)
GET    /api/v1/secrets/{name}           # Get metadata (no value)
PUT    /api/v1/secrets/{name}           # Update value or description
DELETE /api/v1/secrets/{name}           # Delete
```

**Access at runtime (shared pool functions only):**

```python
def my_function(input, context):
    # Private secrets override shared for the calling user
    token = context['secrets']['SLACK_BOT_TOKEN']
```

**YAML config:**

```yaml
secrets:
  - name: SLACK_BOT_TOKEN
    value: xoxb-...          # omit to skip value update on re-apply
    description: Slack bot OAuth token
    # visibility defaults to "shared" in YAML config
```

### Connectors

Named HTTP client configurations with typed operations. Executed in-process in the backend (no container overhead). Operations are exposed as agent tools. Auth resolved from the Secrets store at call time.

**Endpoints:**

```
POST   /api/v1/connectors                                        # Create connector
GET    /api/v1/connectors                                        # List connectors
GET    /api/v1/connectors/{namespace}/{name}                     # Get connector
PUT    /api/v1/connectors/{namespace}/{name}                     # Update connector
DELETE /api/v1/connectors/{namespace}/{name}                     # Delete connector
POST   /api/v1/connectors/parse-openapi                          # Parse OpenAPI spec (no connector required)
POST   /api/v1/connectors/{namespace}/{name}/import-openapi      # Import operations from OpenAPI spec into connector
POST   /api/v1/connectors/{namespace}/{name}/test/{operation}    # Test an operation
```

**Auth types:** `bearer`, `basic`, `api_key`, `sinas_token` (forwards caller's JWT), `none`

Auth is resolved from the Secrets store. Private secrets override shared for the calling user — enabling multi-tenant patterns where each user can have their own API key for the same connector.

**Agent configuration:**

```yaml
agents:
  - name: slack-bot
    enabledConnectors:
      - connector: default/slack-api
        operations: [post_message, get_channel_info]
        parameters:
          post_message:
            channel: "{{ default_channel }}"
```

**YAML config:**

```yaml
connectors:
  - name: slack-api
    namespace: default
    baseUrl: https://slack.com/api
    auth:
      type: bearer
      secret: SLACK_BOT_TOKEN
    operations:
      - name: post_message
        method: POST
        path: /chat.postMessage
        parameters:
          type: object
          properties:
            channel: { type: string }
            text: { type: string }
          required: [channel, text]
```

**Execution history:**

```
GET    /executions                               # List executions (filterable by chat_id, trigger_type, status)
GET    /executions/{execution_id}                # Get execution details (includes tool_call_id link)
POST   /executions/{execution_id}/continue       # Resume a paused execution with user input
```

Executions include a `tool_call_id` field linking them to the tool call that triggered them, enabling execution tree visualization in the Logs page.

**Input schema presets:** The function editor includes built-in presets for common input/output schemas. Use the "Load preset" dropdown when editing schemas:

| Preset | Use case |
|---|---|
| **Pre-upload filter** | Content filtering before file upload (receives file content, returns approved/rejected) |
| **Post-upload** | Processing after successful file upload (receives file_id, metadata) |
| **CDC (Change Data Capture)** | Processing database changes (receives table, rows, poll_column, count) |
| **Message Hook** | Message lifecycle hook (receives message, chat_id, agent; returns content mutation or block) |

### Components

Components are embeddable UI widgets built with JSX/HTML/JS and compiled by Sinas into browser-ready bundles. They can call agents, functions, queries, and access state through proxy endpoints.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier |
| `title` | Display title |
| `source_code` | JSX/HTML/JS source |
| `compiled_bundle` | Auto-generated browser-ready JS |
| `input_schema` | JSON Schema for component configuration |
| `enabled_agents` | Agents the component can call |
| `enabled_functions` | Functions the component can call |
| `enabled_queries` | Queries the component can execute |
| `enabled_components` | Other components it can embed |
| `enabled_stores` | Stores the component can access (`{"store": "ns/name", "access": "readonly\|readwrite"}`) |
| `css_overrides` | Custom CSS |
| `visibility` | `private`, `shared`, or `public` |

Components use the `sinas-ui` library (loaded from npm/unpkg) for a consistent look and feel.

**Management endpoints:**

```
POST   /api/v1/components                                  # Create component
GET    /api/v1/components                                  # List components
GET    /api/v1/components/{namespace}/{name}               # Get component
PUT    /api/v1/components/{namespace}/{name}               # Update component
DELETE /api/v1/components/{namespace}/{name}               # Delete component
POST   /api/v1/components/{namespace}/{name}/compile       # Trigger compilation
```

**Share links** allow embedding components outside Sinas with optional expiration and view limits:

```
POST   /api/v1/components/{namespace}/{name}/shares        # Create share link
GET    /api/v1/components/{namespace}/{name}/shares        # List share links
DELETE /api/v1/components/{namespace}/{name}/shares/{token} # Revoke share link
```

**Runtime rendering:**

```
GET    /components/{namespace}/{name}/render               # Render as full HTML page
GET    /components/shared/{token}                          # Render via share token
```

**Proxy endpoints** allow components to call backend resources from the browser securely — the proxy enforces the component's `enabled_*` permissions:

```
POST   /components/{ns}/{name}/proxy/queries/{q_ns}/{q_name}/execute    # Execute query
POST   /components/{ns}/{name}/proxy/functions/{fn_ns}/{fn_name}/execute # Execute function
POST   /components/{ns}/{name}/proxy/states/{state_ns}                   # Access state
```

### Queries

Queries are saved SQL templates that can be executed directly or used as agent tools.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier |
| `database_connection_id` | Which database connection to use |
| `description` | Shown to the LLM as the tool description |
| `operation` | `read` or `write` |
| `sql` | SQL with `:param_name` placeholders |
| `input_schema` | JSON Schema for parameter validation |
| `output_schema` | JSON Schema for output validation |
| `timeout_ms` | Query timeout (default: 5000ms) |
| `max_rows` | Max rows returned for read operations (default: 1000) |

**Agent query parameters** support defaults and locking:

```yaml
query_parameters:
  "analytics/user_orders":
    "user_id":
      value: "{{user_id}}"    # Jinja2 template from agent input
      locked: true             # Hidden from LLM, always injected
    "status":
      value: "pending"
      locked: false            # Shown to LLM with default, LLM can override
```

Locked parameters prevent the LLM from seeing or modifying security-sensitive values (like `user_id`).

**Contextual parameters:** The following parameters are automatically injected into every query execution and can be referenced in SQL:

| Parameter | Description |
|---|---|
| `:user_id` | UUID of the user who triggered the query |
| `:user_email` | Email of the triggering user |

These are always available regardless of the query's `input_schema`. Use them for row-level security:

```sql
-- Only return orders belonging to the calling user
SELECT * FROM orders WHERE created_by = :user_id

-- Audit trail
INSERT INTO audit_log (action, performed_by) VALUES (:action, :user_email)
```

**Endpoints:**

```
POST   /queries/{namespace}/{name}/execute            # Execute with parameters (runtime)

POST   /api/v1/queries                              # Create query
GET    /api/v1/queries                              # List queries
GET    /api/v1/queries/{namespace}/{name}           # Get query
PUT    /api/v1/queries/{namespace}/{name}           # Update query
DELETE /api/v1/queries/{namespace}/{name}           # Delete query
```

---
