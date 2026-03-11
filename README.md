<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="console/public/sinas-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="console/public/sinas-logo-light.svg">
    <img alt="Sinas" src="console/public/sinas-logo-light.svg" height="48">
  </picture>
</p>

<p align="center"><strong>Open-source platform for building AI agents and serverless automation with fine-grained access control.</strong></p>

Sinas gives you a self-hosted backend for AI-powered applications: configure agents with any LLM provider, write Python functions that run in isolated containers, connect everything with webhooks and schedules, and control access with role-based permissions. Manage it all through a web console or declarative YAML config.

## Features

- **AI Agents** — Multi-provider LLM chat with tool calling, agent-to-agent orchestration, and streaming responses (SSE)
- **Functions** — Write Python that runs in isolated Docker containers with automatic execution tracking
- **Components** — Embeddable UI widgets (JSX/HTML/JS) compiled by Sinas, with built-in proxy to agents, functions, and queries
- **Webhooks & Schedules** — Trigger functions via HTTP endpoints or cron expressions
- **Skills** — Reusable instruction modules that agents retrieve on-demand or preload into their system prompt
- **State Stores** — Key-value storage with namespace-based access control for agent memory and shared state
- **Collections** — Document storage with metadata and search
- **Database Connections** — Connect external databases, run queries, and trigger functions on data changes (CDC)
- **Access Control** — Role-based permissions with hierarchical scopes (`:own` / `:all`), wildcard patterns, and namespace-level granularity for AI governance
- **Packages & Manifests** — Install resource packages and declare application dependencies
- **Declarative Config** — GitOps-friendly YAML configuration with idempotent apply, change detection, and dry run
- **Web Console** — Full management UI for agents, functions, permissions, logs, and system configuration

## Quick Start

```bash
git clone https://github.com/pulsr-ai/sinas.git
cd sinas
sudo bash install.sh
```

The installer will:
- Install Docker if needed
- Generate secure keys automatically
- Prompt for SMTP, domain, and admin email
- Create your `.env` and start all services
- Provision SSL certificates (if a domain is configured)

Once running, open the console in your browser and log in with your admin email.

See [INSTALL.md](INSTALL.md) for manual setup and detailed instructions.

### Local Development

For local development without the installer:

```bash
cp .env.example .env
# Edit .env with your keys and SMTP config (see Environment Variables below)
docker-compose up
```

- **Console**: http://localhost:5173
- **API Docs**: http://localhost:8000/docs

## Architecture

```
sinas/
├── backend/              # FastAPI + Python
│   ├── app/
│   │   ├── api/          # REST endpoints (v1 + runtime)
│   │   ├── models/       # SQLAlchemy models
│   │   ├── services/     # Business logic
│   │   ├── providers/    # LLM provider implementations
│   │   ├── queue/        # arq workers (functions + agents)
│   │   └── core/         # Auth, permissions, config
│   └── alembic/          # Database migrations
├── console/              # React + TypeScript + Vite
│   └── src/
│       ├── pages/        # Route pages
│       ├── components/   # Shared UI components
│       └── lib/          # API client, auth, utilities
├── docker-compose.yml    # Development stack
└── config_examples/      # Declarative config examples
```

### Services

| Service | Purpose |
|---------|---------|
| **backend** | FastAPI API server |
| **queue-worker** | Function execution workers |
| **queue-agent** | Agent message processing workers |
| **scheduler** | Cron-based schedule runner |
| **cdc-worker** | Database change data capture |
| **console** | React web UI |
| **postgres** | Primary database |
| **pgbouncer** | Connection pooling |
| **redis** | Queues, streaming, sessions |
| **clickhouse** | Request logging and analytics (optional) |
| **caddy** | Reverse proxy with auto-HTTPS |

## How It Works

### Agents

Create AI agents that combine an LLM with tools. Agents can call Python functions, query databases, use other agents as tools, and access persistent state.

```yaml
# config.yaml
agents:
  - namespace: support
    name: assistant
    model: gpt-4o
    system_prompt: "You are a helpful support agent."
    enabled_functions:
      - utils/search_docs
      - utils/create_ticket
    enabled_skills:
      - skill: support/tone_guide
        preload: true
    enabled_stores:
      - store: support/memory
        access: readwrite
```

### Functions

Write Python functions with a simple `(input, context)` signature. They run in isolated Docker containers with automatic execution tracking.

```python
def search_docs(input, context):
    """Search documentation for a query."""
    import requests
    headers = {"Authorization": f"Bearer {context['access_token']}"}
    results = requests.get(
        "http://host.docker.internal:8000/api/v1/collections/docs/search",
        params={"q": input["query"]},
        headers=headers,
    )
    return results.json()
```

### Access Control

Every resource in Sinas is protected by fine-grained, namespace-aware permissions. Roles define what users and agents can do — down to individual actions on specific resources.

```
sinas.agents/support/assistant.read:own      # Read a specific agent
sinas.functions/*/*.execute:own              # Execute any function (own scope)
sinas.states/memory.read:all                 # Read all users' state in a namespace
sinas.*:all                                  # Full admin access
```

Permissions use a hierarchical scope system: `:all` automatically grants `:own`, so you never need to assign both. Wildcards let you write broad grants like `sinas.functions/*/*.execute:own` without listing every function individually.

Roles are managed through the web console or declarative config, making it easy to set up governance policies for who can create agents, execute functions, access data, or manage the system.

### Declarative Config

Define your entire setup as YAML — agents, functions, webhooks, schedules, skills, roles, and permissions. Apply with a single API call.

```bash
curl -X POST http://localhost:8000/api/v1/config/apply \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"config\": \"$(cat config.yaml)\"}"
```

Or auto-apply on startup:
```bash
# .env
CONFIG_FILE=config/my-config.yaml
AUTO_APPLY_CONFIG=true
```

## API

Sinas exposes two API surfaces:

**Runtime API** — for building applications on top of Sinas:
- **Agents & Chats** — Create chat sessions, stream messages (SSE), manage conversations
- **Functions & Webhooks** — Execute functions, trigger webhooks, track executions
- **Queries** — Run database queries
- **Stores** — Read/write key-value state
- **Components** — Serve compiled UI widgets with proxy endpoints
- **Templates** — Render and send templates
- **Files** — Upload and retrieve files
- **Manifests** — Validate application dependencies at runtime
- **Discovery** — Enumerate available agents, functions, and resources

**Management API** — full CRUD at `/api/v1/`:
- `/api/v1/agents`, `/api/v1/functions`, `/api/v1/webhooks`, `/api/v1/schedules`
- `/api/v1/skills`, `/api/v1/collections`, `/api/v1/stores`, `/api/v1/templates`
- `/api/v1/database-connections`, `/api/v1/queries`, `/api/v1/database-triggers`
- `/api/v1/users`, `/api/v1/roles`, `/api/v1/llm-providers`, `/api/v1/packages`, `/api/v1/manifests`
- `/api/v1/config/apply`, `/api/v1/config/validate`, `/api/v1/config/export`

Interactive API docs are available at `/docs` (runtime) and `/api/v1/docs` (management).

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | JWT signing key |
| `ENCRYPTION_KEY` | Fernet key for encrypting sensitive data (LLM API keys, DB credentials) |
| `DATABASE_PASSWORD` | PostgreSQL password |
| `SMTP_HOST` | SMTP server for OTP emails |
| `SMTP_PORT` | SMTP port (typically 587) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | SMTP password |
| `SMTP_DOMAIN` | Email "from" domain |
| `SUPERADMIN_EMAIL` | Initial admin user email |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `DOMAIN` | `localhost` | Domain for Caddy auto-HTTPS |
| `DEBUG` | `false` | Verbose logging |
| `FUNCTION_TIMEOUT` | `300` | Max function execution time (seconds) |
| `MAX_FUNCTION_MEMORY` | `512` | Function memory limit (MB) |
| `ALLOW_PACKAGE_INSTALLATION` | `true` | Allow pip install in functions |
| `CONFIG_FILE` | — | YAML config file path |
| `AUTO_APPLY_CONFIG` | `false` | Apply config on startup |

See [INSTALL.md](INSTALL.md) for the full list including resource limits and container configuration.

## Development

```bash
# Start the full stack
docker-compose up

# Run database migrations
docker exec -it sinas-backend alembic upgrade head

# Create a new migration
docker exec -it sinas-backend alembic revision --autogenerate -m "description"

# Access backend shell
docker exec -it sinas-backend sh
```

## Documentation

- [INSTALL.md](INSTALL.md) — Installation and deployment guide
- [DOCS.md](DOCS.md) — Complete feature reference and API documentation
- Interactive API docs at `/docs` and `/api/v1/docs` when running

## License

Dual licensed under **AGPL v3.0** (open source) and a **Commercial License** (proprietary use).

See [LICENSE](LICENSE) for the AGPL terms. For commercial licensing, contact [hello@sinas.co](mailto:sales@sinas.co).

Copyright (c) 2026 Pulsr B.V.
