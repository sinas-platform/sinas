---
title: "Configure"
---

## Configure

### Skills

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

### LLM Providers

LLM providers connect Sinas to language model APIs.

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

### Database Connections

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

### Templates

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

---
