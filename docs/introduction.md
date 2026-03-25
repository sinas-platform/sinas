# Sinas Documentation
## Introduction — What is Sinas?

Sinas is a platform for building AI-powered applications. It brings together multi-provider LLM agents, serverless Python functions, persistent state, database querying, file storage, and template rendering — all behind a single API with role-based access control.

**What you can do with Sinas:**

- **Build AI agents** with configurable LLM providers (OpenAI, Anthropic, Mistral, Ollama), tool calling, streaming responses, and agent-to-agent orchestration.
- **Run Python functions** in isolated Docker containers, triggered by agents, webhooks, cron schedules, or the API.
- **Store and retrieve state** across conversations with namespace-based access control.
- **Query external databases** (PostgreSQL, ClickHouse, Snowflake) through saved SQL templates that agents can use as tools.
- **Manage files** with versioning, metadata validation, and upload processing hooks.
- **Render templates** using Jinja2 for emails, notifications, and dynamic content.
- **Define everything in YAML** for GitOps workflows with idempotent apply, change detection, and dry-run.

Sinas runs as a set of Docker services: the API server, queue workers (for functions and agents), a scheduler, PostgreSQL, PgBouncer, Redis, ClickHouse (optional for request logging), and a web console.

---
