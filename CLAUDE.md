# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Docker (Recommended)

```bash
# Start application (always includes postgres)
docker-compose up

# Run migrations in container
docker exec -it sinas-backend alembic upgrade head

# Create new migration
docker exec -it sinas-backend alembic revision --autogenerate -m "description"

# Access container shell
docker exec -it sinas-backend sh
```

### Local Development (Without Docker)

```bash
# Install dependencies
poetry install

# Run server with hot reload
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Database migrations
poetry run alembic upgrade head
poetry run alembic revision --autogenerate -m "description"

# Code quality
poetry run black .
poetry run ruff check .
poetry run mypy .

# Tests
poetry run pytest
```

### Declarative Configuration

```bash
# Apply configuration from YAML file
curl -X POST http://localhost:8000/api/v1/config/apply \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"config\": \"$(cat config/example-simple.yaml)\"}"

# Validate configuration without applying
curl -X POST http://localhost:8000/api/v1/config/validate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"config\": \"$(cat config/example-simple.yaml)\"}"

# Dry run (preview changes without applying)
curl -X POST http://localhost:8000/api/v1/config/apply \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"config\": \"$(cat config/example-simple.yaml)\", \"dryRun\": true}"

# Auto-apply config on startup
# Set in .env:
# CONFIG_FILE=config/default-data.yaml
# AUTO_APPLY_CONFIG=true
```

### Testing Authentication

```bash
# Get token for API testing (set admin email in .env first)
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# Use in curl
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/...
```

## Architecture Overview

### Three Core Subsystems

SINAS is built around three independent but integrated subsystems:

1. **AI Chat & Agents** - Multi-provider LLM integration with agentic workflows
2. **Function Execution** - Python runtime with webhooks, scheduling, and automatic tracking
3. **State Management** - Key-value state storage with namespaces for agent persistence

### Permission System

**Format:** `sinas.{service}.{resource}.{...segments}.{action}:{scope}`

**Action Verbs (CRUD):**
- **Standard**: `create`, `read`, `update`, `delete`
- **Domain-specific**: `execute` (functions), `render`/`send` (templates), `install` (packages), etc.
- **Never use HTTP verbs** (get/post/put) - always use CRUD verbs

**Scope Hierarchy (automatic):**
- `:all` grants `:own` (admins have full access)
- `:own` is the standard user scope
- Admins have `sinas.*:all` (full system access)

**Key Implementation Details:**
- `check_permission()` in `app/core/permissions.py` handles ALL permission checks
- Scope hierarchy is automatic - never manually check multiple scopes
- Wildcards supported: `sinas.functions.*.create:own` matches any function namespace
- Pattern matching in `matches_permission_pattern()` handles both wildcards and scope hierarchy

**Common Pattern (CORRECT):**
```python
# Only check the requested scope - hierarchy is automatic
if check_permission(permissions, f"sinas.functions.{namespace}.{name}.read:own"):
    # Users with :all automatically get access
```

**Anti-Pattern (WRONG):**
```python
# Never do this - inefficient and unnecessary
if check_permission(permissions, perm_own) or check_permission(permissions, perm_all):
```

**Namespace-Based Permissions:**
Resources like agents, functions, states use namespace-based permissions:
- `sinas.agents/{namespace}/{name}.read:own` - Read specific agent
- `sinas.functions/{namespace}/{name}.execute:own` - Execute specific function
- `sinas.states/{namespace}.create:own` - Create state in namespace
- `sinas.states/api_keys.read:all` - Read shared states in api_keys namespace

### Agent System Architecture

Agents are AI assistants with configurable LLM providers, tools, and behavior.

**Core Components:**
- **Agent**: Configured AI assistant with system prompt, LLM settings, and tool access
  - `namespace`/`name`: Unique identifier (e.g., "default/customer-support")
  - `llm_provider_id`: Optional specific provider (null = use default)
  - `model`: Optional model override
  - `system_prompt`: Jinja2-templated instructions
  - `input_schema`/`output_schema`: JSON Schema for input variables and output validation
  - `enabled_functions`: List of functions the agent can call
  - `function_parameters`: Default parameter values per function (supports Jinja2)
  - `enabled_agents`: Other agents this agent can call as tools
  - `enabled_skills`: List of skill configs with `{"skill": "namespace/name", "preload": bool}` - preload injects into system prompt
  - `state_namespaces_readonly`/`state_namespaces_readwrite`: State access permissions

**Agent Features:**
- **Tool Calling**: Agents can call functions, other agents, and retrieve skills
- **Function Parameter Defaults**: Configure default parameter values per function
  - Example: `{"email/send_email": {"sender": "{{company_email}}", "priority": "high"}}`
  - Supports Jinja2 templates that reference agent input variables
- **Skills**: Retrieve instruction modules on-demand (progressive) or preload into system prompt (always-on)
- **Multi-Modal Support**: Images, audio, files in chat messages
- **Streaming Responses**: SSE (Server-Sent Events) for real-time output

**Key Files:**
- `app/models/agent.py` - Agent model
- `app/services/message_service.py` - Chat message handling and LLM orchestration
- `app/services/function_tools.py` - Function tool conversion and parameter defaults
- `app/api/runtime/endpoints/chats.py` - Chat endpoints with streaming

### Skills System

**Skills** are reusable instruction modules that agents can retrieve on-demand for guidance on specific tasks.

**How It Works:**
1. Skills are stored in the database with `namespace`, `name`, `description`, and `content` (markdown)
2. Agents enable skills via `enabled_skills` with two modes:
   - **Progressive disclosure** (`preload: false`): Exposed as tools, LLM calls when needed
   - **Preloaded** (`preload: true`): Injected into system prompt, always present
3. Progressive skills are exposed as tools to the LLM with their description
4. When the LLM calls `get_skill_namespace_name()`, the markdown content is returned
5. Preloaded skills are injected into the system prompt at chat initialization

**Example:**
```yaml
skills:
  - namespace: "default"
    name: "tone_guidelines"
    description: "Professional communication tone guidelines"
    content: |
      # Communication Tone

      Always maintain a professional, helpful tone.
      Avoid jargon. Be concise but thorough.

  - namespace: "default"
    name: "web_research"
    description: "Get instructions for conducting thorough web research"
    content: |
      # Web Research Guidelines

      When researching topics:
      1. Use multiple search queries with different phrasings
      2. Prioritize authoritative sources (.gov, .edu, news)
      3. Cross-reference facts across 3+ sources
      4. Always cite sources with URLs

agents:
  - namespace: "support"
    name: "research_assistant"
    enabled_skills:
      - skill: "default/tone_guidelines"
        preload: true  # Always present in system prompt
      - skill: "default/web_research"
        preload: false  # Tool that LLM can call when needed (default)
```

**Runtime Behavior:**
- **Preloaded skills**: Tone guidelines are injected into system prompt at chat start → LLM always follows them
- **Progressive skills**: User asks "Research climate policy trends"
  - LLM decides it needs research guidelines → Calls `get_skill_default_web_research()`
  - System returns markdown content → LLM follows guidelines
  - LLM performs research using web search following best practices

**When to Preload vs. Progressive:**
- **Preload**: Core behavior/persona that should always apply (tone, safety rules, universal guidelines)
- **Progressive**: Task-specific expertise that may not be needed in every conversation (research methods, specific domain knowledge)

**Key Files:**
- `app/models/skill.py` - Skill model
- `app/services/skill_tools.py` - Converts skills to LLM tools, handles retrieval
- `app/api/v1/endpoints/skills.py` - CRUD endpoints with namespace-based permissions
- `console/src/pages/Skills.tsx` - Frontend skill management

**Permissions:**
- `sinas.skills.create:own` - Create skills
- `sinas.skills/{namespace}/{name}.read:own` - Read specific skill
- `sinas.skills/{namespace}/{name}.update:own` - Update skill
- `sinas.skills/{namespace}/{name}.delete:own` - Delete skill
- Admins get `sinas.*:all` (full access)

### Function Execution System

**Execution Flow:**
1. Function code parsed and validated
2. AST injection adds `@track` decorator to all function definitions
3. Code executed in isolated Docker container with tracking and context
4. Functions can call other functions - tracked as step executions
5. All calls logged to Execution and StepExecution tables

**Function Signature:**
All functions receive two parameters:
```python
def my_function(input, context):
    """
    Args:
        input: Input data validated against input_schema
        context: Execution context dict containing:
            - user_id: Authenticated user's ID
            - user_email: User's email address
            - access_token: JWT token for making authenticated API calls
            - execution_id: Current execution ID
            - trigger_type: How function was triggered (WEBHOOK, AGENT, SCHEDULE)
            - chat_id: Optional chat ID if triggered from a chat
    """
    # Use access_token to make authenticated API calls
    import requests
    headers = {"Authorization": f"Bearer {context['access_token']}"}
    response = requests.get("http://host.docker.internal:8000/api/v1/...", headers=headers)
    return response.json()
```

**Key Components:**
- `app/services/execution_engine.py` - Core execution with AST injection for tracking
- `app/services/user_container_manager.py` - Docker container management for isolated execution
- `backend/container_executor.py` - Container runtime that executes functions
- `app/services/tracking.py` - ExecutionTracker for multi-step function calls
- `app/models/execution.py` - Execution, StepExecution models
- `app/api/v1/endpoints/webhook_handler.py` - HTTP webhook triggers

**Tracking Implementation:**
- `ASTInjector.inject_tracking_decorator()` modifies function AST to add `@track` decorators
- `TrackingDecorator` wraps function calls to record StepExecutions
- Execution tree captured: parent function → child function calls → grandchild calls, etc.

**Important:**
- Functions execute with `dill` serialization for complex types, `jsonschema` validation on inputs/outputs
- Context object is automatically injected with fresh JWT token on each execution
- Access token allows functions to make authenticated API calls back to SINAS

### State Management System

**Purpose:** Persistent key-value storage for agents to maintain state across conversations, with namespace-based permission control for sharing.

**Core Components:**
- **State**: Key-value pair with namespace organization
  - `namespace`: Organization level (e.g., "preferences", "memory", "api_keys")
  - `key`: Unique identifier within namespace
  - `value`: JSON data (dict/object)
  - `visibility`: `private` (owner only) or `shared` (accessible with namespace permission)
  - `ttl`: Optional time-to-live in seconds
  - `user_id`: Owner of the state

**Access Control (Namespace-Based):**
- **Permissions**: `sinas.states/{namespace}.{action}:scope`
  - Example: `sinas.states/preferences.read:own` - read own preferences
  - Example: `sinas.states/api_keys.read:all` - read shared API keys
- **Visibility**:
  - `private`: Only owner can access (regardless of namespace permissions)
  - `shared`: Users with namespace `:all` permission can access
- **Agent Access**: Agents declare state access via `state_namespaces_readonly` and `state_namespaces_readwrite`

**Common Use Cases:**
```python
# User preferences (private)
namespace="preferences", key="language", visibility="private"
# Only owner can access

# Shared API keys (shared)
namespace="api_keys", key="stripe", visibility="shared"
# Users with sinas.states/api_keys.read:all can access

# User memory (private)
namespace="memory", key="name", visibility="private"
# Agent can access if granted memory namespace
```

**Key Files:**
- `app/models/state.py` - State model
- `app/api/runtime/endpoints/states.py` - State CRUD endpoints (runtime API)
- `app/services/state_tools.py` - LLM tools for state access

### Database Architecture

**Primary Database (PostgreSQL):**
- User accounts, roles, permissions
- Chat history, messages, agents
- Function definitions and execution history
- State storage (key-value)
- LLM provider configurations

**Note on Roles vs Groups:**
- **Roles** (formerly "groups"): Access control roles with permissions
- Database tables: `roles`, `user_roles`, `role_permissions`
- Resources (agents, functions, etc.) are **owned by users**, not roles
- Roles grant permissions, not ownership

**Redis:**
- Execution logs (before persisting to ClickHouse)
- Real-time streaming of execution output
- Session management for OTP authentication

**ClickHouse (Optional):**
- Request logging and analytics
- HTTP request/response tracking via `RequestLoggerMiddleware`
- Table: `request_logs` (auto-created on startup)
- **Security:** Auth endpoint request bodies are NOT logged (no token/OTP leakage)

### Startup Sequence (app/main.py)

1. Redis connection established
2. APScheduler started for cron jobs
3. Default roles created (GuestUsers, Users, Admins)
4. Superadmin user created if `SUPERADMIN_EMAIL` set and Admins role empty
5. Declarative config applied if `CONFIG_FILE` and `AUTO_APPLY_CONFIG=true`
6. Default agents created

### Declarative Configuration (Preferred)

SINAS supports **declarative configuration** via YAML files for GitOps and Infrastructure as Code workflows.

**Configuration Files:**
- `config/default-data.yaml` - Full demo configuration
- `config/example-simple.yaml` - Minimal example for testing

**Auto-Apply on Startup:**
```bash
# In .env
CONFIG_FILE=config/default-data.yaml
AUTO_APPLY_CONFIG=true
```

**Features:**
- ✅ **Idempotent** - Safe to apply multiple times
- ✅ **Change Detection** - Only updates what changed (hash-based)
- ✅ **Resource Tracking** - Marks resources as config-managed
- ✅ **Environment Variables** - Supports `${VAR_NAME}` interpolation
- ✅ **Dry Run** - Preview changes without applying
- ✅ **Validation** - Schema and reference validation before apply

**See:** `FEATURES.md` Section 14 for complete documentation

### Authentication Flow

1. User requests OTP via `/api/auth/login`
2. System generates 6-digit code, sends via SMTP
3. User submits OTP via `/api/auth/verify-otp`
4. System returns access_token and refresh_token (JWT)
5. Access token used in `Authorization: Bearer {token}` header
6. Refresh token used to get new access tokens without re-authenticating

**Security:**
- Request bodies for auth endpoints are NOT logged to ClickHouse
- Sensitive fields (`password`, `otp`, `token`, `refresh_token`, `api_key`) are redacted in all other logs

### Database Migrations

**Creating Migrations:**
```bash
# Auto-generate migration from model changes
alembic revision --autogenerate -m "add feature"

# Review generated migration in alembic/versions/
# Edit if needed (auto-generation may miss some changes)

# Apply migration
alembic upgrade head
```

**Migration Strategy:**
- Application model changes require Alembic migrations
- Always review auto-generated migrations before applying
- Test migrations in dev environment first

### Common Gotchas

1. **Permission Checking:** Always use `check_permission()`, never manually check multiple scopes. Scope hierarchy (:all → :own) is automatic. Use CRUD verbs (create/read/update/delete), not HTTP verbs.

2. **Async Context:** Most DB operations are async. Use `AsyncSession`, `await db.execute()`, and `await db.commit()`.

3. **Function Execution:** Functions can call other functions. The ExecutionTracker builds a tree of StepExecution records. Parent execution ID must be passed through tracking context.

4. **Agent vs Assistant:** Use "agent" terminology consistently in code and UI. The backend models still use `Agent` but some schemas reference `Assistant` for historical reasons.

5. **Function Parameter Defaults:** When setting default parameters for functions in agents, use Jinja2 templates to reference agent input variables: `{{variable_name}}`.

6. **Message Content Format:** Messages support multimodal content stored as JSON arrays: `[{"type": "text", "text": "..."}, {"type": "image", "image": "data:image/..."}]`. Frontend must parse JSON strings from database.

7. **Provider Type Detection:** When agents don't have explicit `llm_provider_id`, the system falls back to default provider. Ensure `provider_type` is determined before building messages for content conversion.

8. **Chats and Agents:** Chats are always linked to agents via `agent_id`, `agent_namespace`, and `agent_name`. Chat access is controlled by agent permissions (`sinas.agents/{namespace}/{name}.read:own`), not separate chat permissions. Chats are created via `/api/runtime/agents/{namespace}/{agent_name}/chats`.

9. **State Namespaces:** States use namespace-based permissions similar to agents and functions. Users can create private states in any namespace they have permission for. Shared states require both `visibility="shared"` AND the viewer must have `sinas.states/{namespace}.read:all` permission.

10. **CRUD Verbs:** Always use CRUD action verbs in permissions: `create`, `read`, `update`, `delete`, `execute`, `render`, `send`, etc. Never use HTTP verbs like `get`, `post`, `put`. Example: `sinas.functions.read:own` not `sinas.functions.get:own`.

## Key Integration Points

### Adding New Permission-Protected Endpoints

```python
from app.core.auth import get_current_user
from app.core.permissions import check_permission
from app.middleware.request_logger import set_permission_used

@router.get("/resource")
async def list_resource(
    request: Request,
    current_user_data: tuple = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id, permissions = current_user_data

    # Check permission (scope hierarchy automatic)
    if not check_permission(permissions, "sinas.resource.read:own"):
        set_permission_used(request, "sinas.resource.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized")

    set_permission_used(request, "sinas.resource.read:own", has_perm=True)
    # ... implementation
```

### Adding New LLM Providers

1. Implement provider client in `app/providers/` (e.g., `mistral_provider.py`)
2. Inherit from `BaseLLMProvider` and implement `stream()` and `complete()` methods
3. Add to factory in `app/providers/factory.py`
4. Ensure response format matches: `{"content": "...", "tool_calls": [...], "finish_reason": "..."}`
5. Register via admin UI at `/api/v1/llm-providers` (no code changes needed)

### Adding Scheduled Jobs

Jobs can be added programmatically or via API:

```python
from app.services.scheduler import scheduler

# Via API
POST /api/v1/schedules
{
  "name": "job_name",
  "function_name": "my_function",
  "cron_expression": "0 * * * *",  # Every hour
  "input_data": {...}
}

# Programmatically
await scheduler.schedule_function(
    function_name="my_function",
    cron_expression="0 * * * *",
    input_data={...}
)
```

## Environment Variables Reference

**Required:**
- `SECRET_KEY` - JWT signing key
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_DOMAIN` - Email for OTP

**Database:**
- `DATABASE_URL` - PostgreSQL connection string (optional, overrides local postgres)
- `DATABASE_PASSWORD` - Password for local postgres (required if DATABASE_URL not set)
- `DATABASE_USER`, `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME` - Optional postgres config
- `REDIS_URL` - Redis connection (default: redis://redis:6379/0)
- `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, etc. - ClickHouse config (optional)

**LLM Providers:**
- LLM providers are now managed via the `/api/v1/llm-providers` API (admin only)
- No environment variables needed - configure through the database after startup
- API keys are encrypted in the database using `ENCRYPTION_KEY`

**Admin:**
- `SUPERADMIN_EMAIL` - Auto-create admin user on startup if Admins role empty

**Function Execution:**
- `FUNCTION_TIMEOUT` - Max execution time in seconds (default: 300)
- `MAX_FUNCTION_MEMORY` - Max memory in MB (default: 512)
- `ALLOW_PACKAGE_INSTALLATION` - Allow pip install in functions (default: true)

**Security:**
- `ENCRYPTION_KEY` - Fernet key for encrypting LLM provider API keys
