## Admin

### Integration Packages

Integration packages bundle agents, functions, skills, components, templates, and other resources into a shareable YAML file that can be installed with one click.

**How packages work:**

1. **Create**: Select resources from your Sinas instance → export as `SinasPackage` YAML
2. **Share**: Distribute the YAML file (GitHub, email, package registry)
3. **Install**: Paste/upload the YAML → preview changes → confirm install
4. **Uninstall**: Removes all resources created by the package in one operation

**Package YAML format:**

```yaml
apiVersion: sinas.co/v1
kind: SinasPackage
package:
  name: crm-integration
  version: "1.0.0"
  description: "CRM support agents and functions"
  author: "team@company.com"
  url: "https://github.com/company/sinas-crm"
spec:
  agents: [...]
  functions: [...]
  skills: [...]
  connectors: [...]
  components: [...]
  templates: [...]
  queries: [...]
  collections: [...]
  stores: [...]
  webhooks: [...]
  schedules: [...]
  manifests: [...]
  databaseTriggers: [...]
  dependencies: [...]
```

**Key behaviors:**

- Resources created by packages are tagged with `managed_by: "pkg:<name>"`
- **Detach-on-edit**: Editing a package-managed resource clears `managed_by` — the resource survives uninstall
- **Uninstall**: Deletes all resources where `managed_by = "pkg:<name>"` + the package record
- **Upgrade**: Re-installing an existing package updates its resources in place (idempotent apply)
- **Excluded types**: Packages cannot include roles, users, LLM providers, or database connections (these are environment-specific)
- **Dependencies**: Packages can declare Python dependencies — these are recorded in the database and installed in containers on worker restart

**Endpoints:**

```
POST   /api/v1/packages/install       # Install package from YAML
POST   /api/v1/packages/preview       # Preview install (dry run)
POST   /api/v1/packages/create        # Create package YAML from selected resources
GET    /api/v1/packages               # List installed packages
GET    /api/v1/packages/{name}        # Get package details
DELETE /api/v1/packages/{name}        # Uninstall package
GET    /api/v1/packages/{name}/export # Export original YAML
```

**Creating a package from existing resources:**

```bash
curl -X POST https://yourdomain.com/api/v1/packages/create \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-package",
    "version": "1.0.0",
    "description": "My integration package",
    "resources": [
      {"type": "agent", "namespace": "support", "name": "ticket-bot"},
      {"type": "function", "namespace": "support", "name": "lookup-customer"},
      {"type": "template", "namespace": "support", "name": "ticket-reply"},
      {"type": "schedule", "namespace": "default", "name": "daily-digest"}
    ]
  }'
```

Supported resource types: `agent`, `function`, `skill`, `connector`, `manifest`, `component`, `query`, `collection`, `store`, `template`, `webhook`, `schedule`, `database_trigger`.

### Manifests

Manifests are application declarations that describe what resources, permissions, and stores an application built on Sinas requires. They enable a single API call to validate whether a user has everything needed to run the application.

**Key properties:**

| Property | Description |
|---|---|
| `namespace` / `name` | Unique identifier |
| `description` | What this manifest declares |
| `required_resources` | Resource references: `[{"type": "agent", "namespace": "...", "name": "..."}]` |
| `required_permissions` | Permissions the application needs |
| `optional_permissions` | Optional permissions for extended features |
| `exposed_namespaces` | Namespace filter per resource type (e.g., `{"agents": ["support"]}`) |
| `store_dependencies` | Stores the application expects: `[{"store": "ns/name", "key": "optional_key"}]` |

**Endpoints:**

```
POST   /api/v1/manifests                              # Create manifest
GET    /api/v1/manifests                              # List manifests
GET    /api/v1/manifests/{namespace}/{name}           # Get manifest
PUT    /api/v1/manifests/{namespace}/{name}           # Update
DELETE /api/v1/manifests/{namespace}/{name}           # Delete
```

**Runtime status validation:**

```
GET    /api/runtime/manifests/{namespace}/{name}/status   # Validate dependencies
```

Returns `ready: true/false` with details on satisfied/missing resources, granted/missing permissions, and existing/missing store dependencies.

### Users & Roles

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

### Permissions

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

### System Workers & Containers

Sinas has a dual-execution model for functions, plus dedicated queue workers for async job processing.

#### Sandbox Containers

The sandbox container pool is a set of **pre-warmed, generic Docker containers** for executing untrusted user code. This is the default execution mode for all functions (`shared_pool=false`).

**How it works:**

- On startup, the pool creates `sandbox_min_size` containers (default: 4) ready to accept work.
- When a function executes, a container is acquired from the idle pool, used, and returned.
- Containers are recycled (destroyed and replaced) after `sandbox_max_executions` uses (default: 100) to prevent state leakage between executions.
- If a container errors during execution, it's marked as tainted and destroyed immediately.
- A background replenishment loop monitors the idle count and creates new containers whenever it drops below `sandbox_min_idle` (default: 2), up to `sandbox_max_size` (default: 20).
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

#### Shared Containers

Functions marked `shared_pool=true` run in **persistent shared containers** instead of sandbox containers. This is an admin-only option for trusted code that benefits from longer-lived containers.

**Differences from sandbox:**

| | Sandbox Containers | Shared Containers |
|---|---|---|
| **Trust level** | Untrusted user code | Trusted admin code only |
| **Isolation** | Per-request (recycled after N uses) | Shared (persistent containers) |
| **Lifecycle** | Created/destroyed automatically | Persist until explicitly scaled down |
| **Scaling** | Auto-replenishment + manual | Manual via API only |
| **Load balancing** | First available idle container | Round-robin across workers |
| **Best for** | User-submitted functions | Admin functions, long-startup libraries |

**When to use `shared_pool=true` (shared containers):**

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

#### Queue Workers

All function and agent executions are processed asynchronously through Redis-based queues (arq). Two separate worker types handle different workloads:

| Worker | Docker service | Queue | Concurrency | Retries |
|---|---|---|---|---|
| **Function workers** | `queue-worker` | `sinas:queue:functions` | 10 jobs/worker | Up to 3 |
| **Agent workers** | `queue-agent` | `sinas:queue:agents` | 5 jobs/worker | None (not idempotent) |

**Function workers** dequeue function execution jobs, route them to either sandbox or shared containers, track results in Redis, and handle retries. Failed jobs that exhaust retries are moved to a **dead letter queue** (DLQ) for inspection and manual retry.

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

Jobs go through states: `queued` → `running` → `completed` or `failed`. Stale or orphaned jobs can be cancelled via the admin API:

```bash
# Cancel a running or queued job
POST /api/v1/queue/jobs/{job_id}/cancel
# → {"status": "cancelled", "job_id": "..."}
```

Cancellation updates the Redis status to `cancelled` and marks the DB execution record as `CANCELLED`. It also publishes to the done channel so any waiters unblock. This is a soft cancel — it does not kill running containers.

Results are stored in Redis with a 24-hour TTL.

#### System Endpoints

Admin endpoints for monitoring and managing the Sinas deployment. All require `sinas.system.read:all` or `sinas.system.update:all` permissions.

**Health check:**

```bash
GET /api/v1/system/health
```

Returns a comprehensive health report:

- **`services`** — All Docker Compose containers with status, health, uptime, CPU %, and memory usage. Infrastructure containers (redis, postgres, pgbouncer) are listed first, followed by application containers sorted alphabetically. Sandbox and shared worker containers are included.
- **`host`** — Host-level CPU, memory, and disk usage (read from `/proc` on Linux).
- **`warnings`** — Auto-generated alerts at three levels:
  - `critical` — No queue workers running, or infrastructure services (redis, postgres, pgbouncer) down
  - `warning` — Non-infrastructure services down, unhealthy containers, DLQ items, queue backlog >50, disk/memory >90%
  - `info` — Disk/memory >75%

**Container restart:**

```bash
POST /api/v1/system/containers/{container_name}/restart
# → {"status": "restarted", "container": "sinas-backend"}
```

Restarts any Docker container by name (15-second timeout). Returns 404 if the container doesn't exist.

**Flush stuck jobs:**

```bash
POST /api/v1/system/flush-stuck-jobs
```

Cancels all jobs that have been stuck in `running` state for over 2 hours. Useful for recovering from worker crashes or orphaned jobs.

#### Dependencies (Python Packages)

Functions can only use Python packages that have been approved by an admin. This prevents untrusted code from installing arbitrary dependencies.

**Approval flow:**

1. Admin approves a dependency (optionally pinning a version)
2. Package becomes available in newly created containers and workers
3. Use `POST /containers/reload` or `POST /workers/reload` to install into existing containers

```
POST   /api/v1/dependencies              # Approve dependency (admin)
GET    /api/v1/dependencies              # List approved dependencies
DELETE /api/v1/dependencies/{id}         # Remove approval (admin)
```

Optionally restrict which packages can be approved with a whitelist:

```bash
# In .env — only these packages can be approved
ALLOWED_PACKAGES=requests,pandas,numpy,redis,boto3
```

#### Configuration Reference

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

### Icons

Agents and functions support configurable icons via the `icon` field. Two formats are supported:

| Format | Example | Description |
|---|---|---|
| `url:<url>` | `url:https://example.com/icon.png` | Direct URL to an image |
| `collection:<ns>/<coll>/<file>` | `collection:assets/icons/bot.png` | File stored in a Sinas collection |

Collection-based icons generate signed JWT URLs for private files and direct URLs for public collection files. Icons are resolved at read time via the icon resolver service.

### Config Manager

The config manager supports GitOps-style declarative configuration. Define all your resources in a YAML file and apply it idempotently.

**YAML structure:**

```yaml
apiVersion: sinas.co/v1
kind: SinasConfig
metadata:
  name: my-config
  description: Production configuration
spec:
  roles:               # Roles and permissions
  users:               # User provisioning
  llmProviders:        # LLM provider connections
  databaseConnections: # External database credentials
  dependencies:        # Python packages (pip)
  secrets:             # Encrypted credentials (values omitted on export)
  connectors:          # HTTP connectors with typed operations
  skills:              # Instruction documents
  components:          # UI components
  functions:           # Python functions
  queries:             # Saved SQL templates
  collections:         # File storage collections
  templates:           # Jinja2 templates
  stores:              # State store definitions
  manifests:           # Application manifests
  agents:              # AI agent configurations
  webhooks:            # HTTP triggers for functions
  schedules:           # Cron-based triggers
  databaseTriggers:    # CDC polling triggers
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
curl -X POST https://yourdomain.com/api/v1/config/apply \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"config\": \"$(cat config.yaml)\", \"dryRun\": false}"
```
