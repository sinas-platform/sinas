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

Deploy Sinas on any VPS with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/sinas-platform/sinas/main/install.sh | sudo bash
```

The installer will:
- Install Docker if needed
- Generate secure keys automatically
- Prompt for SMTP, domain, and admin email
- Pull pre-built images and start all services
- Provision SSL certificates via Let's Encrypt

Once running, open the console at `https://yourdomain.com:51245` and log in with your admin email.

**Update to latest version:**
```bash
cd /opt/sinas && docker compose pull && docker compose up -d
```

See [DOCS.md](DOCS.md) for the full deployment and configuration reference.

## Development

For local development, clone the repo and use the dev compose file:

```bash
git clone https://github.com/sinas-platform/sinas.git && cd sinas
cp .env.example .env   # Edit with your keys and SMTP config
docker compose -f docker-compose.dev.yml up -d --build
```

- **Console**: http://localhost:51245
- **API Docs**: http://localhost:8000/docs

See [INSTALL.md](INSTALL.md) for the full development setup guide.

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
├── docker-compose.yml    # Production stack (pre-built images)
├── docker-compose.dev.yml # Development stack (local builds + hot reload)
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

## Contributing

Contributions are welcome. By submitting a pull request, you agree to the [Contributor License Agreement](CLA.md).

## Documentation

- [DOCS.md](DOCS.md) — Deployment, configuration, and production reference
- [INSTALL.md](INSTALL.md) — Development environment setup
- Interactive API docs at `/docs` (runtime) and `/api/v1/docs` (management)

## License

Dual licensed under **AGPL v3.0** (open source) and a **Commercial License** (proprietary use).

See [LICENSE](LICENSE) for the AGPL terms. For commercial licensing, contact [hello@sinas.co](mailto:sales@sinas.co).

Copyright (c) 2026 Pulsr B.V.
