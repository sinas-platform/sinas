# SINAS Documentation

## Introduction — What is SINAS?

SINAS is a platform for building AI-powered applications. It brings together multi-provider LLM agents, serverless Python functions, persistent state, database querying, file storage, and template rendering — all behind a single API with role-based access control.

**What you can do with SINAS:**

- **Build AI agents** with configurable LLM providers (OpenAI, Anthropic, Mistral, Ollama), tool calling, streaming responses, and agent-to-agent orchestration.
- **Run Python functions** in isolated Docker containers, triggered by agents, webhooks, cron schedules, or the API.
- **Store and retrieve state** across conversations with namespace-based access control.
- **Query external databases** (PostgreSQL, ClickHouse, Snowflake) through saved SQL templates that agents can use as tools.
- **Manage files** with versioning, metadata validation, and upload processing hooks.
- **Render templates** using Jinja2 for emails, notifications, and dynamic content.
- **Define everything in YAML** for GitOps workflows with idempotent apply, change detection, and dry-run.

SINAS runs as a set of Docker services: the API server, queue workers (for functions and agents), a scheduler, PostgreSQL, PgBouncer, Redis, ClickHouse (optional for request logging), and a web console.

---

## Getting Started: Quick Install

### Prerequisites

- Docker and Docker Compose
- An SMTP server for OTP email authentication (or a service like Mailgun, SendGrid)

### 1. Clone and configure

```bash
git clone <repository-url> && cd SINAS
cp .env.example .env
```

Edit `.env` and set the required values:

```bash
# Required
DATABASE_PASSWORD=your-secure-password
SECRET_KEY=your-secret-key
ENCRYPTION_KEY=<fernet-key>           # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SUPERADMIN_EMAIL=you@example.com      # First admin user

# SMTP (required for OTP login)
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-smtp-user
SMTP_PASSWORD=your-smtp-password
SMTP_DOMAIN=example.com
```

### 2. Start the application

```bash
docker-compose up
```

This starts all services: PostgreSQL, PgBouncer, Redis, ClickHouse, the backend API (port 8000), queue workers, the scheduler, and the web console (port 5173). Migrations run automatically on startup.

### 3. Log in

1. Open the console at `http://localhost:5173`
2. Enter your `SUPERADMIN_EMAIL` address
3. Check your inbox for the 6-digit OTP code
4. Enter the code to receive your access token

### 4. Configure an LLM provider

Before agents can work, you need at least one LLM provider:

```bash
curl -X POST http://localhost:8000/api/v1/llm-providers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "openai",
    "provider_type": "openai",
    "api_key": "sk-...",
    "default_model": "gpt-4o",
    "is_default": true
  }'
```

### 5. Start chatting

A default agent is created on startup. Create a chat and send a message:

```bash
# Create a chat with the default agent
curl -X POST http://localhost:8000/agents/default/default/chats \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'

# Send a message (use the chat_id from the response)
curl -X POST http://localhost:8000/chats/{chat_id}/messages/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello!"}'
```

---

## Minimum Configuration

The simplest useful setup requires only a configured LLM provider. Everything else (functions, skills, state, etc.) is optional and can be added incrementally.

**Required environment variables:**

| Variable | Purpose |
|---|---|
| `DATABASE_PASSWORD` | PostgreSQL password |
| `SECRET_KEY` | JWT signing key |
| `ENCRYPTION_KEY` | Fernet key for encrypting stored credentials |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_DOMAIN` | OTP email delivery |

**Recommended:**

| Variable | Purpose | Default |
|---|---|---|
| `SUPERADMIN_EMAIL` | Auto-created admin user on first startup | _(none)_ |
| `DOMAIN` | Domain for Caddy reverse proxy (automatic HTTPS) | `localhost` |

All other settings have sensible defaults. See `.env.example` for the full list.

---

## Concepts

### Namespaces

Most resources are organized by **namespace** and **name**. A namespace groups related resources (e.g., `support/ticket-agent`, `analytics/daily-report`). The default namespace is `default`. Resources are uniquely identified by their `namespace/name` pair.

### Tools

Agents interact with the outside world through **tools** — capabilities you enable per agent:

| Tool type | What it does |
|---|---|
| **Functions** | Execute Python code in isolated containers |
| **Agents** | Call other agents as sub-agents |
| **Skills** | Retrieve instruction/knowledge documents |
| **Queries** | Run SQL against external databases |
| **Collections** | Search uploaded files |
| **States** | Read and write persistent key-value data |

### Trigger Types

Functions and agents can be triggered in multiple ways:

- **Manual** — Via the API or console
- **Agent** — Called as a tool during a chat conversation
- **Webhook** — Via an HTTP request to a configured endpoint
- **Schedule** — Via a cron expression on a timer

### Declarative Configuration

All resources can be defined in a YAML file and applied idempotently via the API or on startup. Config-managed resources are tracked with checksums for change detection. See [Config Manager](#config-manager) for details.

---

## Authentication

SINAS supports two authentication methods: OTP (email-based) and external OIDC providers.

### OTP Authentication (Built-in)

1. Client sends email to `POST /auth/login`
2. SINAS sends a 6-digit code to that email (valid for 10 minutes by default)
3. Client submits code to `POST /auth/verify-otp`
4. SINAS returns an **access token** (short-lived JWT, default 15 min) and a **refresh token** (long-lived, default 30 days)
5. Use the access token in the `Authorization: Bearer <token>` header
6. When the access token expires, use `POST /auth/refresh` to get a new one

**Endpoints:**

```
POST   /auth/login                # Send OTP to email
POST   /auth/verify-otp           # Verify OTP, receive tokens
POST   /auth/refresh              # Get new access token using refresh token
POST   /auth/logout               # Revoke refresh token
GET    /auth/me                   # Get current user info
```

### External Authentication (OIDC)

SINAS can authenticate users via external OIDC providers (Authentik, Auth0, Keycloak, etc.). Configure in `.env`:

```bash
EXTERNAL_AUTH_ENABLED=true
OIDC_ISSUER=https://authentik.company.com/application/o/sinas/
OIDC_AUDIENCE=sinas
OIDC_GROUPS_CLAIM=groups           # JWT claim containing user groups
AUTO_PROVISION_USERS=true          # Create users on first login
DEFAULT_GROUP_NAME=Users           # Fallback role for new users
```

External groups can be mapped to SINAS roles via the Roles API.

### API Keys

For programmatic access (scripts, CI/CD, integrations), create API keys instead of using short-lived JWT tokens. Each key has its own set of permissions (a subset of the creating user's permissions).

```
POST   /api/v1/api-keys           # Create key (plaintext returned once)
GET    /api/v1/api-keys           # List keys
GET    /api/v1/api-keys/{id}      # Get key details
DELETE /api/v1/api-keys/{id}      # Revoke key
```

API keys can be used via `Authorization: Bearer <key>` or `X-API-Key: <key>` headers. Keys can have optional expiration dates.

---

## Role-Based Access Control (RBAC)

### Overview

Users are assigned to **roles**, and roles define **permissions**. A user's effective permissions are the union of all permissions from all their roles (OR logic). Permissions are loaded from the database on every request — changes take effect immediately.

### Default Roles

| Role | Description |
|---|---|
| **Admins** | Full access to everything (`sinas.*:all`) |
| **Users** | Create and manage own resources, chat with any agent, execute own functions |
| **GuestUsers** | Read and update own profile only |

### Permission Format

```
<service>.<resource>[/<path>].<action>:<scope>
```

**Components:**

| Part | Description | Examples |
|---|---|---|
| **Service** | Top-level namespace | `sinas`, or a custom prefix like `titan`, `acme` |
| **Resource** | Resource type | `agents`, `functions`, `states`, `users` |
| **Path** | Optional namespace/name path | `/marketing/send_email`, `/*` |
| **Action** | What operation is allowed | `create`, `read`, `update`, `delete`, `execute`, `chat` |
| **Scope** | Ownership scope | `:own` (user's resources), `:all` (all resources) |

### Permission Matching Rules

**Scope hierarchy:** `:all` automatically grants `:own`. A user with `sinas.agents.read:all` passes any check for `sinas.agents.read:own`.

**Wildcards** can be used at any level:

| Pattern | Matches |
|---|---|
| `sinas.*:all` | Everything in SINAS (admin access) |
| `sinas.agents/*/*.chat:all` | Chat with any agent in any namespace |
| `sinas.functions/marketing/*.execute:own` | Execute any function in the `marketing` namespace |
| `sinas.states/*.read:own` | Read own states in any namespace |
| `sinas.chats.*:own` | All chat actions (read, update, delete) on own chats |

**Namespaced resource permissions** use slashes in the resource path:

```
sinas.agents/support/ticket-bot.chat:own        # Chat with specific agent
sinas.functions/*/send_email.execute:own         # Execute send_email in any namespace
sinas.states/api_keys.read:all                   # Read all shared states in api_keys namespace
```

**Non-namespaced resource permissions** use simple dot notation:

```
sinas.webhooks.create:own                        # Create webhooks
sinas.schedules.read:own                         # Read own schedules
sinas.users.update:own                           # Update own profile
```

### Custom Permissions

The permission system is not limited to `sinas.*`. You can define permissions with any service prefix for your own applications:

```
titan.student_profile.read:own
titan.courses/math/*.enroll:own
acme.billing.invoices.read:all
myapp.*:all
```

These work identically to built-in permissions — same wildcard matching, same scope hierarchy. This lets you use SINAS as the authorization backend for external applications.

### Checking Permissions from External Services

Use the `POST /auth/check-permissions` endpoint to verify whether the current user (identified by their Bearer token or API key) has specific permissions:

```bash
curl -X POST http://localhost:8000/auth/check-permissions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "permissions": ["titan.student_profile.read:own", "titan.courses.enroll:own"],
    "logic": "AND"
  }'
```

Response:

```json
{
  "result": true,
  "logic": "AND",
  "checks": [
    {"permission": "titan.student_profile.read:own", "has_permission": true},
    {"permission": "titan.courses.enroll:own", "has_permission": true}
  ]
}
```

- **`logic: "AND"`** — User must have ALL listed permissions (default)
- **`logic: "OR"`** — User must have AT LEAST ONE of the listed permissions

This makes SINAS usable as a centralized authorization service for any number of external applications.

### Managing Roles

```
POST   /api/v1/roles                        # Create role
GET    /api/v1/roles                        # List roles
GET    /api/v1/roles/{name}                 # Get role details
PATCH  /api/v1/roles/{name}                 # Update role
DELETE /api/v1/roles/{name}                 # Delete role
POST   /api/v1/roles/{name}/members         # Add user to role
DELETE /api/v1/roles/{name}/members/{id}    # Remove user from role
POST   /api/v1/roles/{name}/permissions     # Set permission
DELETE /api/v1/roles/{name}/permissions     # Remove permission
GET    /api/v1/permissions/reference        # List all known permissions
```

---

## Runtime API vs Management API

SINAS has two API layers:

### Runtime API (`/`)

The runtime API is mounted at the root. It handles authentication, chat, execution, state, file operations, and discovery. These are the endpoints your applications and end users interact with.

```
/auth/...              # Authentication
/agents/...            # Create chats with agents
/chats/...             # Send messages, manage chats
/webhooks/...          # Trigger webhook-linked functions
/executions/...        # View execution history and results
/jobs/...              # Check job status
/states/...            # Key-value state storage
/files/...             # Upload, download, search files
/templates/...         # Render and send templates
/discovery/...         # List resources visible to the current user
```

### Management API (`/api/v1/`)

The management API handles CRUD operations on all resources. These are typically used by admins, the console UI, and configuration tools.

```
/api/v1/agents/...                 # Agent CRUD
/api/v1/functions/...              # Function CRUD + execution
/api/v1/skills/...                 # Skill CRUD
/api/v1/llm-providers/...         # LLM provider management (admin)
/api/v1/database-connections/...  # DB connection management (admin)
/api/v1/queries/...               # Query CRUD
/api/v1/collections/...           # Collection CRUD
/api/v1/templates/...             # Template CRUD
/api/v1/webhooks/...              # Webhook CRUD
/api/v1/schedules/...             # Schedule CRUD
/api/v1/apps/...                  # App registration CRUD
/api/v1/roles/...                 # Role & permission management
/api/v1/users/...                 # User management
/api/v1/api-keys/...              # API key management
/api/v1/packages/...              # Package approval (admin)
/api/v1/workers/...               # Worker management (admin)
/api/v1/containers/...            # Container pool management (admin)
/api/v1/config/...                # Declarative config apply/validate/export (admin)
/api/v1/request-logs/...          # Request log search (admin)
```

### Interactive API Docs

Swagger UI is available at `/docs` (runtime API) and `/api/v1/docs` (management API) for exploring all endpoints and schemas interactively.

### Discovery Endpoints

The discovery API returns resources visible to the current user, optionally filtered by app context:

```
GET    /discovery/agents           # Agents the user can chat with
GET    /discovery/functions        # Functions the user can see
GET    /discovery/skills           # Skills the user can see
GET    /discovery/collections      # Collections the user can access
GET    /discovery/templates        # Templates the user can use
```

Pass an app context via the `X-Application` header or `?app=namespace/name` query parameter to filter results to a specific app's exposed namespaces.

---

## Reference

### Agents

#### Agents

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
| `enabled_collections` | File collections the agent can search |
| `state_namespaces_readonly` | State namespaces the agent can read |
| `state_namespaces_readwrite` | State namespaces the agent can read and write |

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

**How chat works:**

1. Create a chat linked to an agent (optionally with input variables validated against `input_schema`)
2. Send a message — SINAS builds the conversation context with the system prompt, preloaded skills, message history, and available tools
3. The LLM generates a response, possibly calling tools
4. If tools are called, SINAS executes them (in parallel where possible) and sends results back to the LLM for a follow-up response
5. The final response is streamed to the client via Server-Sent Events

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

#### LLM Providers

LLM providers connect SINAS to language model APIs.

**Supported providers:**

| Type | Description |
|---|---|
| `openai` | OpenAI API (GPT-4, GPT-4o, o1, etc.) and OpenAI-compatible endpoints |
| `anthropic` | Anthropic API (Claude 3, Claude 4, etc.) |
| `mistral` | Mistral AI (Mistral Large, Pixtral, etc.) |
| `ollama` | Local models via Ollama |

**Key properties:**

| Property | Description |
|---|---|
| `name` | Unique provider name |
| `provider_type` | `openai`, `anthropic`, `mistral`, or `ollama` |
| `api_key` | API key (encrypted at rest, never returned in API responses) |
| `api_endpoint` | Custom endpoint URL (required for Ollama, useful for proxies) |
| `default_model` | Model used when agents don't specify one |
| `config` | Additional settings (e.g., `max_tokens`, `organization_id`) |
| `is_default` | Whether this is the system-wide default provider |

**Provider resolution for agents:**
1. Agent's explicit `llm_provider_id` if set
2. Agent's `model` field with the resolved provider
3. Provider's `default_model`
4. System default provider as final fallback

**Endpoints (admin only):**

```
POST   /api/v1/llm-providers             # Create provider
GET    /api/v1/llm-providers             # List providers
GET    /api/v1/llm-providers/{name}      # Get provider
PATCH  /api/v1/llm-providers/{id}        # Update provider
DELETE /api/v1/llm-providers/{id}        # Delete provider
```

#### Skills

Skills are reusable instruction documents that give agents specialized knowledge or guidelines.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier |
| `description` | What the skill helps with (shown to the LLM as the tool description) |
| `content` | Markdown instructions |

**Two modes:**

| Mode | Behavior | Best for |
|---|---|---|
| **Preloaded** (`preload: true`) | Injected into the system prompt | Tone guidelines, safety rules, persona traits |
| **Progressive** (`preload: false`) | Exposed as a tool the LLM calls when needed | Research methods, domain expertise, task-specific instructions |

Example agent configuration:

```yaml
enabled_skills:
  - skill: "default/tone_guidelines"
    preload: true       # Always present in system prompt
  - skill: "default/web_research"
    preload: false      # LLM decides when to retrieve it
```

**Endpoints:**

```
POST   /api/v1/skills                       # Create skill
GET    /api/v1/skills                       # List skills
GET    /api/v1/skills/{namespace}/{name}    # Get skill
PUT    /api/v1/skills/{namespace}/{name}    # Update skill
DELETE /api/v1/skills/{namespace}/{name}    # Delete skill
```

#### Schedules

Schedules trigger functions or agents on a cron timer.

**Key properties:**

| Property | Description |
|---|---|
| `name` | Unique name (per user) |
| `schedule_type` | `function` or `agent` |
| `target_namespace` / `target_name` | Function or agent to trigger |
| `cron_expression` | Standard cron expression (e.g., `0 9 * * MON-FRI`) |
| `timezone` | Schedule timezone (default: `UTC`) |
| `input_data` | Input passed to the function or agent |
| `content` | Message content (agent schedules only) |

For agent schedules, a new chat is created for each run with the schedule name and timestamp as the title.

**Endpoints:**

```
POST   /api/v1/schedules              # Create schedule
GET    /api/v1/schedules              # List schedules
GET    /api/v1/schedules/{name}       # Get schedule
PATCH  /api/v1/schedules/{name}       # Update schedule
DELETE /api/v1/schedules/{name}       # Delete schedule
```

---

### Functions

#### Functions

Functions are Python code that runs in isolated Docker containers. They can be used as agent tools, triggered by webhooks or schedules, or executed directly.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier |
| `code` | Python source code |
| `description` | Shown to the LLM when used as an agent tool |
| `input_schema` | JSON Schema for input validation |
| `output_schema` | JSON Schema for output validation |
| `requirements` | Python packages needed (must be admin-approved) |
| `enabled_namespaces` | Namespaces of other functions this function can call |
| `shared_pool` | Run in shared worker instead of isolated container (admin-only) |
| `requires_approval` | Require user approval when called by an agent |

**Function signature:**

Every function receives two arguments — `input` (the validated input data) and `context` (execution metadata):

```python
def my_function(input, context):
    # input: dict validated against input_schema
    # context keys:
    #   user_id         - ID of the user who triggered the execution
    #   user_email      - User's email
    #   access_token    - JWT token for making API calls back to SINAS
    #   execution_id    - Unique execution ID
    #   trigger_type    - WEBHOOK, AGENT, SCHEDULE, or MANUAL
    #   chat_id         - Chat ID if triggered from a conversation
    return {"result": "value"}
```

The `access_token` lets functions call back into the SINAS API with the triggering user's identity — useful for reading state, triggering other functions, or accessing any other endpoint.

**Execution:** Functions run in pre-warmed Docker containers from a managed pool. Input is validated before execution, output is validated after. All executions are logged with status, duration, input/output, and any errors.

**Endpoints:**

```
POST   /api/v1/functions                                   # Create function
GET    /api/v1/functions                                   # List functions
GET    /api/v1/functions/{namespace}/{name}                # Get function
PUT    /api/v1/functions/{namespace}/{name}                # Update function
DELETE /api/v1/functions/{namespace}/{name}                # Delete function
POST   /api/v1/functions/{namespace}/{name}/execute        # Execute directly
GET    /api/v1/functions/{namespace}/{name}/versions       # List code versions
```

**Execution history:**

```
GET    /executions                          # List executions
GET    /executions/{execution_id}           # Get execution details
GET    /executions/{execution_id}/steps     # Get execution steps (nested calls)
```

#### Webhooks

Webhooks expose functions as HTTP endpoints. When a request arrives at a webhook path, SINAS executes the linked function with the request data.

**Key properties:**

| Property | Description |
|---|---|
| `path` | URL path (e.g., `stripe/payment-webhook`) |
| `http_method` | GET, POST, PUT, DELETE, or PATCH |
| `function_namespace` / `function_name` | Target function |
| `requires_auth` | Whether the caller must provide a Bearer token |
| `default_values` | Default parameters merged with request data (request takes priority) |

**How input is extracted:**

- `POST`/`PUT`/`PATCH` with JSON body → body becomes the input
- `GET` → query parameters become the input
- Default values are merged underneath (request data overrides)

**Example:**

```bash
# Create a webhook
curl -X POST http://localhost:8000/api/v1/webhooks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "stripe/payment",
    "function_namespace": "payments",
    "function_name": "process_webhook",
    "http_method": "POST",
    "requires_auth": false,
    "default_values": {"source": "stripe"}
  }'

# Trigger it
curl -X POST http://localhost:8000/webhooks/stripe/payment \
  -H "Content-Type: application/json" \
  -d '{"event": "charge.succeeded", "amount": 1000}'

# Function receives: {"source": "stripe", "event": "charge.succeeded", "amount": 1000}
```

**Endpoints:**

```
POST   /api/v1/webhooks              # Create webhook
GET    /api/v1/webhooks              # List webhooks
GET    /api/v1/webhooks/{path}       # Get webhook
PATCH  /api/v1/webhooks/{path}       # Update webhook
DELETE /api/v1/webhooks/{path}       # Delete webhook
```

---

### Data

#### Database Connections

Database connections store credentials and manage connection pools for external databases.

**Supported databases:** PostgreSQL, ClickHouse, Snowflake

**Key properties:**

| Property | Description |
|---|---|
| `name` | Unique connection name |
| `connection_type` | `postgresql`, `clickhouse`, or `snowflake` |
| `host`, `port`, `database`, `username`, `password` | Connection details |
| `ssl_mode` | Optional SSL configuration |
| `config` | Pool settings (`min_pool_size`, `max_pool_size`) |

Passwords are encrypted at rest. Connection pools are managed automatically and invalidated when settings change.

**Endpoints (admin only):**

```
POST   /api/v1/database-connections                    # Create connection
GET    /api/v1/database-connections                    # List connections
GET    /api/v1/database-connections/{name}             # Get by name
PATCH  /api/v1/database-connections/{id}               # Update
DELETE /api/v1/database-connections/{id}               # Delete
POST   /api/v1/database-connections/test               # Test raw connection params
POST   /api/v1/database-connections/{id}/test          # Test saved connection
```

#### Queries

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

**Endpoints:**

```
POST   /api/v1/queries                              # Create query
GET    /api/v1/queries                              # List queries
GET    /api/v1/queries/{namespace}/{name}           # Get query
PUT    /api/v1/queries/{namespace}/{name}           # Update query
DELETE /api/v1/queries/{namespace}/{name}           # Delete query
POST   /api/v1/queries/{namespace}/{name}/execute   # Execute with parameters
```

---

### Resources

#### Templates

Templates are Jinja2-based documents for emails, notifications, and dynamic content.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier |
| `title` | Optional title template (e.g., email subject) |
| `html_content` | Jinja2 HTML template |
| `text_content` | Optional plain-text fallback |
| `variable_schema` | JSON Schema for validating template variables |

HTML output is auto-escaped to prevent XSS. Missing variables cause errors (strict mode).

**Management endpoints:**

```
POST   /api/v1/templates                                   # Create template
GET    /api/v1/templates                                   # List templates
GET    /api/v1/templates/{id}                              # Get by ID
GET    /api/v1/templates/by-name/{namespace}/{name}        # Get by name
PATCH  /api/v1/templates/{id}                              # Update
DELETE /api/v1/templates/{id}                              # Delete
```

**Runtime endpoints:**

```
POST   /templates/{id}/render        # Render template with variables
POST   /templates/{id}/send          # Render and send as email
```

#### Collections & Files

Collections are containers for file uploads with versioning, metadata validation, and processing hooks.

**Collection properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier |
| `metadata_schema` | JSON Schema that file metadata must conform to |
| `content_filter_function` | Function that runs on upload to approve/reject files |
| `post_upload_function` | Function that runs after upload for processing |
| `max_file_size_mb` | Per-file size limit (default: 100 MB) |
| `max_total_size_gb` | Total collection size limit (default: 10 GB) |

**File features:**

- **Versioning** — Every upload creates a new version. Previous versions are preserved.
- **Metadata** — Each file carries JSON metadata validated against the collection's schema.
- **Visibility** — Files can be `private` (owner only) or `shared` (users with collection `:all` access).
- **Content filtering** — Optional function runs on upload that can approve, reject, or modify the file.

**Management endpoints:**

```
POST   /api/v1/collections                              # Create collection
GET    /api/v1/collections                              # List collections
GET    /api/v1/collections/{namespace}/{name}           # Get collection
PUT    /api/v1/collections/{namespace}/{name}           # Update
DELETE /api/v1/collections/{namespace}/{name}           # Delete (cascades to files)
```

**Runtime file endpoints:**

```
POST   /files/{namespace}/{collection}                   # Upload file
GET    /files/{namespace}/{collection}                   # List files
GET    /files/{namespace}/{collection}/{filename}        # Download file
PATCH  /files/{namespace}/{collection}/{filename}        # Update metadata
DELETE /files/{namespace}/{collection}/{filename}        # Delete file
POST   /files/{namespace}/{collection}/{filename}/url    # Generate temporary download URL
POST   /files/{namespace}/{collection}/search            # Search files
```

#### States

States are a persistent key-value store organized by namespace. Agents use states to maintain memory and context across conversations.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` | Organizational grouping (e.g., `preferences`, `memory`, `api_keys`) |
| `key` | Unique key within user + namespace |
| `value` | Any JSON data |
| `visibility` | `private` (owner only) or `shared` (users with namespace `:all` permission) |
| `description` | Optional description |
| `tags` | Tags for filtering and search |
| `relevance_score` | Priority for context retrieval (0.0–1.0, default: 1.0) |
| `expires_at` | Optional expiration time |

**Agent state access** is declared per agent:

```yaml
state_namespaces_readonly: ["shared_knowledge"]
state_namespaces_readwrite: ["conversation_memory", "user_profile"]
```

Read-only agents get a `retrieve_context` tool. Read-write agents additionally get `save_context`, `update_context`, and `delete_context`.

**Endpoints:**

```
POST   /states              # Create state entry
GET    /states              # List (supports namespace, visibility, tags, search filters)
GET    /states/{id}         # Get state
PUT    /states/{id}         # Update state
DELETE /states/{id}         # Delete state
```

---

### Admin

#### Apps

Apps are registered applications that declare their resource dependencies. They serve as an organizational and discovery mechanism — when an app context is active, discovery endpoints filter results to show only the app's relevant resources.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier |
| `description` | App description |
| `required_resources` | Resource references: `[{"type": "agent", "namespace": "...", "name": "..."}]` |
| `required_permissions` | Permissions the app needs |
| `optional_permissions` | Optional permissions for extended features |
| `exposed_namespaces` | Namespace filter per resource type (e.g., `{"agents": ["support"]}`) |

**Endpoints:**

```
POST   /api/v1/apps                              # Create app
GET    /api/v1/apps                              # List apps
GET    /api/v1/apps/{namespace}/{name}           # Get app
PUT    /api/v1/apps/{namespace}/{name}           # Update
DELETE /api/v1/apps/{namespace}/{name}           # Delete
```

#### Users & Roles

**Users** are identified by email. They can be created automatically on first login (OTP or OIDC) or provisioned via the management API or declarative config.

**Roles** group permissions together. Users can belong to multiple roles. All permissions across all of a user's roles are combined.

**User endpoints:**

```
GET    /api/v1/users                    # List users (admin)
POST   /api/v1/users                    # Create user (admin)
GET    /api/v1/users/{id}              # Get user
PATCH  /api/v1/users/{id}              # Update user
DELETE /api/v1/users/{id}              # Delete user (admin)
```

**Role endpoints:** See [Managing Roles](#managing-roles).

#### Permissions

See [Role-Based Access Control (RBAC)](#role-based-access-control-rbac) for the full permission system documentation, including format, matching rules, custom permissions, and the check-permissions endpoint.

**Quick reference of action verbs:**

| Verb | Usage |
|---|---|
| `create` | Create a resource |
| `read` | View/list a resource |
| `update` | Modify a resource |
| `delete` | Remove a resource |
| `execute` | Run a function or query |
| `chat` | Chat with an agent |
| `render` | Render a template |
| `send` | Send a rendered template |
| `upload` | Upload a file |
| `download` | Download a file |
| `install` | Approve a package |

#### System Workers & Containers

SINAS has a dual-execution model for functions, plus dedicated queue workers for async job processing.

##### Sandbox Container Pool

The sandbox pool is a set of **pre-warmed, generic Docker containers** for executing untrusted user code. This is the default execution mode for all functions (`shared_pool=false`).

**How it works:**

- On startup, the pool creates `pool_min_size` containers (default: 4) ready to accept work.
- When a function executes, a container is acquired from the idle pool, used, and returned.
- Containers are recycled (destroyed and replaced) after `pool_max_executions` uses (default: 100) to prevent state leakage between executions.
- If a container errors during execution, it's marked as tainted and destroyed immediately.
- A background replenishment loop monitors the idle count and creates new containers whenever it drops below `pool_min_idle` (default: 2), up to `pool_max_size` (default: 20).
- Health checks run every 60 seconds to detect and replace dead containers.

**Isolation guarantees:**

Each container runs with strict resource limits and security hardening:

| Constraint | Default |
|---|---|
| Memory | 512 MB (`MAX_FUNCTION_MEMORY`) |
| CPU | 1.0 cores (`MAX_FUNCTION_CPU`) |
| Disk | 1 GB (`MAX_FUNCTION_STORAGE`) |
| Execution time | 300 seconds (`FUNCTION_TIMEOUT`) |
| Temp storage | 100 MB tmpfs at `/tmp` |
| Capabilities | All dropped, only `CHOWN`/`SETUID`/`SETGID` added |
| Privilege escalation | Disabled (`no-new-privileges`) |

**Runtime scaling:**

The pool can be scaled up or down at runtime without restarting the application:

```bash
# Check current pool state
GET /api/v1/containers/stats
# → {"idle": 2, "in_use": 3, "total": 5, "max_size": 20, ...}

# Scale up for high load
POST /api/v1/containers/scale
{"target": 15}
# → {"action": "scale_up", "previous": 5, "current": 15, "added": 10}

# Scale back down (only removes idle containers — never interrupts running executions)
POST /api/v1/containers/scale
{"target": 4}
# → {"action": "scale_down", "previous": 15, "current": 4, "removed": 11}
```

**Package installation:**

When new packages are approved, existing containers don't have them yet. Use the reload endpoint to install approved packages into all idle containers:

```bash
POST /api/v1/containers/reload
# → {"status": "completed", "idle_containers": 4, "success": 4, "failed": 0}
```

Containers that are currently executing are unaffected. New containers created by the replenishment loop automatically include all approved packages.

##### Shared Worker Pool

Functions marked `shared_pool=true` run in **persistent worker containers** instead of the sandbox pool. This is an admin-only option for trusted code that benefits from longer-lived containers.

**Differences from sandbox:**

| | Sandbox Pool | Shared Workers |
|---|---|---|
| **Trust level** | Untrusted user code | Trusted admin code only |
| **Isolation** | Per-request (recycled after N uses) | Shared (persistent containers) |
| **Lifecycle** | Created/destroyed automatically | Persist until explicitly scaled down |
| **Scaling** | Auto-replenishment + manual | Manual via API only |
| **Load balancing** | First available idle container | Round-robin across workers |
| **Best for** | User-submitted functions | Admin functions, long-startup libraries |

**When to use `shared_pool=true`:**

- Functions created and maintained by admins (not user-submitted code)
- Functions that import heavy libraries (pandas, scikit-learn) where container startup cost matters
- Performance-critical functions that benefit from warm containers

**Management:**

```bash
# List workers
GET /api/v1/workers

# Check count
GET /api/v1/workers/count
# → {"count": 4}

# Scale workers
POST /api/v1/workers/scale
{"target_count": 6}
# → {"action": "scale_up", "previous_count": 4, "current_count": 6, "added": 2}

# Reload packages in all workers
POST /api/v1/workers/reload
# → {"status": "completed", "total_workers": 6, "success": 6, "failed": 0}
```

##### Queue Workers

All function and agent executions are processed asynchronously through Redis-based queues (arq). Two separate worker types handle different workloads:

| Worker | Docker service | Queue | Concurrency | Retries |
|---|---|---|---|---|
| **Function workers** | `queue-worker` | `sinas:queue:functions` | 10 jobs/worker | Up to 3 |
| **Agent workers** | `queue-agent` | `sinas:queue:agents` | 5 jobs/worker | None (not idempotent) |

**Function workers** dequeue function execution jobs, route them to either the sandbox pool or shared workers, track results in Redis, and handle retries. Failed jobs that exhaust retries are moved to a **dead letter queue** (DLQ) for inspection and manual retry.

**Agent workers** handle chat message processing — they call the LLM, execute tool calls, and stream responses back via Redis Streams. Agent jobs don't retry because LLM calls with tool execution have side effects.

**Scaling** is controlled via Docker Compose replicas:

```yaml
# docker-compose.yml
queue-worker:
  command: python -m arq app.queue.worker.WorkerSettings
  deploy:
    replicas: ${QUEUE_WORKER_REPLICAS:-2}

queue-agent:
  command: python -m arq app.queue.worker.AgentWorkerSettings
  deploy:
    replicas: ${QUEUE_AGENT_REPLICAS:-2}
```

Each worker sends a **heartbeat** to Redis every 10 seconds (TTL: 30 seconds). If a worker dies, its heartbeat key auto-expires, making it easy to detect dead workers.

**Job status tracking:**

```bash
# Check job status
GET /jobs/{job_id}
# → {"status": "completed", "execution_id": "...", ...}

# Get job result
GET /jobs/{job_id}/result
# → {function output}
```

Jobs go through states: `queued` → `running` → `completed` or `failed`. Results are stored in Redis with a 24-hour TTL.

##### Package Management

Functions can only use Python packages that have been approved by an admin. This prevents untrusted code from installing arbitrary dependencies.

**Approval flow:**

1. Admin approves a package (optionally pinning a version)
2. Package becomes available in newly created containers and workers
3. Use `POST /containers/reload` or `POST /workers/reload` to install into existing containers

```
POST   /api/v1/packages              # Approve package (admin)
GET    /api/v1/packages              # List approved packages
DELETE /api/v1/packages/{id}         # Remove approval (admin)
```

Optionally restrict which packages can be approved with a whitelist:

```bash
# In .env — only these packages can be approved
ALLOWED_PACKAGES=requests,pandas,numpy,redis,boto3
```

##### Configuration Reference

**Container pool:**

| Variable | Default | Description |
|---|---|---|
| `POOL_MIN_SIZE` | 4 | Containers created on startup |
| `POOL_MAX_SIZE` | 20 | Maximum total containers |
| `POOL_MIN_IDLE` | 2 | Replenish when idle count drops below this |
| `POOL_MAX_EXECUTIONS` | 100 | Recycle container after this many uses |
| `POOL_ACQUIRE_TIMEOUT` | 30 | Seconds to wait for an available container |

**Function execution:**

| Variable | Default | Description |
|---|---|---|
| `FUNCTION_TIMEOUT` | 300 | Max execution time in seconds |
| `MAX_FUNCTION_MEMORY` | 512 | Memory limit per container (MB) |
| `MAX_FUNCTION_CPU` | 1.0 | CPU cores per container |
| `MAX_FUNCTION_STORAGE` | 1g | Disk storage limit |
| `FUNCTION_CONTAINER_IDLE_TIMEOUT` | 3600 | Idle container cleanup (seconds) |

**Workers and queues:**

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_WORKER_COUNT` | 4 | Shared workers created on startup |
| `QUEUE_WORKER_REPLICAS` | 2 | Function queue worker processes |
| `QUEUE_AGENT_REPLICAS` | 2 | Agent queue worker processes |
| `QUEUE_FUNCTION_CONCURRENCY` | 10 | Concurrent jobs per function worker |
| `QUEUE_AGENT_CONCURRENCY` | 5 | Concurrent jobs per agent worker |
| `QUEUE_MAX_RETRIES` | 3 | Retry attempts before DLQ |
| `QUEUE_RETRY_DELAY` | 10 | Seconds between retries |

**Packages:**

| Variable | Default | Description |
|---|---|---|
| `ALLOW_PACKAGE_INSTALLATION` | true | Enable pip in containers |
| `ALLOWED_PACKAGES` | _(empty)_ | Comma-separated whitelist (empty = all allowed) |

#### Config Manager

The config manager supports GitOps-style declarative configuration. Define all your resources in a YAML file and apply it idempotently.

**YAML structure:**

```yaml
apiVersion: sinas.co/v1
kind: SinasConfig
metadata:
  name: my-config
  description: Production configuration
spec:
  groups:              # Roles and permissions
  users:               # User provisioning
  llmProviders:        # LLM provider connections
  databaseConnections: # External database credentials
  skills:              # Instruction documents
  functions:           # Python functions
  queries:             # Saved SQL templates
  collections:         # File storage collections
  apps:                # App registrations
  agents:              # AI agent configurations
  webhooks:            # HTTP triggers for functions
  schedules:           # Cron-based triggers
```

All sections are optional — include only what you need.

**Key behaviors:**

- **Idempotent** — Applying the same config twice does nothing. Unchanged resources are skipped (SHA256 checksum comparison).
- **Config-managed tracking** — Resources created via config are tagged with `managed_by: "config"`. The system won't overwrite resources that were created manually (it warns instead).
- **Environment variable interpolation** — Use `${VAR_NAME}` in values (e.g., `apiKey: "${OPENAI_API_KEY}"`).
- **Reference validation** — Cross-references (e.g., an agent referencing a function) are validated before applying.
- **Dry run** — Set `dryRun: true` to preview changes without applying.

**Endpoints (admin only):**

```
POST   /api/v1/config/validate       # Validate YAML syntax and references
POST   /api/v1/config/apply          # Apply config (supports dryRun and force flags)
GET    /api/v1/config/export         # Export current configuration as YAML
```

**Auto-apply on startup:**

```bash
# In .env
CONFIG_FILE=config/production.yaml
AUTO_APPLY_CONFIG=true
```

**Apply via API:**

```bash
curl -X POST http://localhost:8000/api/v1/config/apply \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"config\": \"$(cat config.yaml)\", \"dryRun\": false}"
```
