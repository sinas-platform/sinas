# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Docker (Recommended)

```bash
# Start with local database (development)
docker-compose --profile local-db up

# Start with external database (production)
docker-compose up

# Run migrations in container
docker exec -it sinas-app alembic upgrade head

# Create new migration
docker exec -it sinas-app alembic revision --autogenerate -m "description"

# Access container shell
docker exec -it sinas-app sh
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

1. **AI Chat & Assistants** - Multi-provider LLM integration with conversation management
2. **Ontology System** - Semantic data layer with three data modes (external query, synced, self-managed)
3. **Function Execution** - Python runtime with webhooks, scheduling, and automatic tracking

### Permission System

**Format:** `sinas.{service}.{resource}.{...segments}.{action}:{scope}`

**Scope Hierarchy (automatic):**
- `:all` grants `:group` and `:own`
- `:group` grants `:own`
- Admins have `sinas.*:all` (full system access)

**Key Implementation Details:**
- `check_permission()` in `app/core/permissions.py` handles ALL permission checks
- Scope hierarchy is automatic - never manually check multiple scopes
- Wildcards supported: `sinas.ontology.*.create:group` matches any ontology resource
- Pattern matching in `matches_permission_pattern()` handles both wildcards and scope hierarchy

**Common Pattern (CORRECT):**
```python
# Only check the requested scope - hierarchy is automatic
if check_permission(permissions, f"sinas.ontology.concepts.{namespace}.{concept}.read:group"):
    # Users with :all automatically get access
```

**Anti-Pattern (WRONG):**
```python
# Never do this - inefficient and unnecessary
if check_permission(permissions, perm_group) or check_permission(permissions, perm_all):
```

### Ontology System Architecture

The ontology system supports three data modes that can coexist:

#### 1. External Query Mode
- **Purpose:** Query external databases without copying data
- **Flow:** Request → QueryCompiler → QueryExecutor → External DB → Response
- **Key Files:**
  - `app/services/ontology/query_compiler.py` - Compiles endpoint config to SQL
  - `app/services/ontology/query_executor.py` - Executes against external datasources
  - `app/services/ontology/query_validator.py` - Validates SQL safety
- **Tables:** No local data storage, queries run on external datasource

#### 2. Synced Mode
- **Purpose:** Periodically sync external data to local tables
- **Flow:** Scheduler → SyncService → External DB → Local Table
- **Key Files:**
  - `app/services/ontology/sync_service.py` - Handles periodic data sync
  - `app/services/scheduler.py` - APScheduler integration for cron jobs
- **Tables:** `ontology_sync_{namespace}_{concept_name}`
- **Note:** Uses APScheduler with cron expressions for sync scheduling

#### 3. Self-Managed Mode
- **Purpose:** Fully managed data with auto-generated CRUD APIs
- **Flow:** SchemaManager creates tables → Auto-generated endpoints → Direct DB access
- **Key Files:**
  - `app/services/ontology/schema_manager.py` - Dynamic table creation/migration
  - `app/api/v1/endpoints/ontology_records.py` - Auto-generated CRUD
- **Tables:** `ontology_{namespace}_{concept_name}`
- **Schema Changes:**
  - Add property: `ALTER TABLE ADD COLUMN`
  - Change type: Old column → `{name}_{timestamp}`, new column created
  - Delete: Column → `deleted_{name}_{timestamp}`

**Critical Design Note:** A concept can have EITHER a ConceptQuery (modes 1 & 2) OR be self-managed (mode 3), never both.

### Function Execution System

**Execution Flow:**
1. Function code parsed and validated
2. AST injection adds `@track` decorator to all function definitions
3. Code executed in isolated namespace with tracking
4. Functions can call other functions - tracked as step executions
5. All calls logged to Execution and StepExecution tables

**Key Components:**
- `app/services/execution_engine.py` - Core execution with AST injection for tracking
- `app/services/tracking.py` - ExecutionTracker for multi-step function calls
- `app/models/execution.py` - Execution, StepExecution models
- `app/api/v1/endpoints/webhook_handler.py` - HTTP webhook triggers

**Tracking Implementation:**
- `ASTInjector.inject_tracking_decorator()` modifies function AST to add `@track` decorators
- `TrackingDecorator` wraps function calls to record StepExecutions
- Execution tree captured: parent function → child function calls → grandchild calls, etc.

**Important:** Functions execute with `dill` serialization for complex types, `jsonschema` validation on inputs/outputs.

### Database Architecture

**Primary Database (PostgreSQL):**
- User accounts, groups, permissions
- Chat history, messages, assistants
- Ontology metadata (concepts, properties, relationships, endpoints)
- Function definitions and execution history
- Self-managed and synced ontology data tables

**Redis:**
- Execution logs (before persisting to ClickHouse)
- Real-time streaming of execution output
- Session management for OTP authentication

**ClickHouse (Optional):**
- Request logging and analytics
- HTTP request/response tracking via `RequestLoggerMiddleware`
- Table: `request_logs` (auto-created on startup)

### Startup Sequence (app/main.py)

1. Redis connection established
2. APScheduler started for cron jobs
3. Default groups created (GuestUsers, Users, Admins)
4. Superadmin user created if `SUPERADMIN_EMAIL` set and Admins group empty
5. MCP (Model Context Protocol) client initialized

### Authentication Flow

1. User requests OTP via `/api/v1/auth/request-otp`
2. System generates 6-digit code, sends via SMTP
3. User submits OTP via `/api/v1/auth/verify-otp`
4. System returns JWT token (default expiry: 7 days)
5. Token used in `Authorization: Bearer {token}` header

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
- Ontology table changes (self-managed concepts) handled by SchemaManager at runtime
- Application model changes require Alembic migrations
- Always review auto-generated migrations before applying

### Common Gotchas

1. **Permission Checking:** Always use `check_permission()`, never manually check multiple scopes. Scope hierarchy (:all → :group → :own) is automatic.

2. **Ontology Mode Confusion:** Check if concept `is_self_managed` before attempting to query. Self-managed concepts don't have ConceptQuery records.

3. **Async Context:** Most DB operations are async. Use `AsyncSession`, `await db.execute()`, and `await db.commit()`.

4. **Encryption:** DataSource connection strings are encrypted with Fernet (ENCRYPTION_KEY env var). Use `EncryptionService` for encrypt/decrypt.

5. **Function Execution:** Functions can call other functions. The ExecutionTracker builds a tree of StepExecution records. Parent execution ID must be passed through tracking context.

6. **MCP Tools:** MCP (Model Context Protocol) servers provide tools to assistants. Configured per assistant. Tools are dynamically loaded from MCP servers on startup.

7. **Query Validation:** External SQL queries are validated to prevent dangerous operations (no DROP, DELETE, INSERT, UPDATE allowed in query mode - only SELECT with JOINs).

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
    if not check_permission(permissions, "sinas.resource.read:group"):
        set_permission_used(request, "sinas.resource.read:group", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized")

    set_permission_used(request, "sinas.resource.read:group", has_perm=True)
    # ... implementation
```

### Adding New Ontology Property Types

1. Add to `PropertyDataType` enum in `app/models/ontology.py`
2. Update type mapping in `SchemaManager._get_column_type()`
3. Update validation in `query_validator.py` if needed

### Adding New LLM Providers

1. Add provider to config in `app/core/config.py`
2. Implement provider client in `app/services/llm/`
3. Update provider selection in `app/services/message_service.py`
4. Add to Assistant model validation

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
- `ENCRYPTION_KEY` - Fernet key for encrypting datasource credentials
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_DOMAIN` - Email for OTP

**Database:**
- `DATABASE_URL` - PostgreSQL connection string (or use docker-compose postgres with `POSTGRES_PASSWORD`)
- `REDIS_URL` - Redis connection (default: localhost:6379)
- `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, etc. - ClickHouse config (optional)

**LLM Providers:**
- `OPENAI_API_KEY` - OpenAI API key
- `LOCAL_LLM_ENDPOINT` - Ollama endpoint (default: http://localhost:11434)
- `DEFAULT_LLM_PROVIDER` - Default provider (openai, ollama, etc.)

**Admin:**
- `SUPERADMIN_EMAIL` - Auto-create admin user on startup if Admins group empty

**Function Execution:**
- `FUNCTION_TIMEOUT` - Max execution time in seconds (default: 300)
- `MAX_FUNCTION_MEMORY` - Max memory in MB (default: 512)
- `ALLOW_PACKAGE_INSTALLATION` - Allow pip install in functions (default: true)
