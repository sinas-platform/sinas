## Runtime API vs Management API

Sinas has two API layers:

### Runtime API (`/`)

The runtime API is mounted at the root. It handles authentication, chat, execution, state, file operations, and discovery. These are the endpoints your applications and end users interact with.

```
/auth/...              # Authentication
/agents/...            # Create chats with agents
/chats/...             # Send messages, manage chats
/functions/...         # Execute functions (sync and async)
/queries/...           # Execute database queries
/webhooks/...          # Trigger webhook-linked functions
/executions/...        # View execution history and results
/jobs/...              # Check job status
/states/...            # Key-value state storage
/files/...             # Upload, download, search files
/templates/...         # Render and send templates
/components/...        # Render components, proxy endpoints
/manifests/...         # Manifest status validation
/discovery/...         # List resources visible to the current user
```

### Management API (`/api/v1/`)

The management API handles CRUD operations on all resources. These are typically used by admins, the console UI, and configuration tools.

```
/api/v1/agents/...                 # Agent CRUD
/api/v1/functions/...              # Function CRUD
/api/v1/skills/...                 # Skill CRUD
/api/v1/llm-providers/...         # LLM provider management (admin)
/api/v1/database-connections/...  # DB connection management (admin)
/api/v1/queries/...               # Query CRUD
/api/v1/collections/...           # Collection CRUD
/api/v1/templates/...             # Template CRUD
/api/v1/webhooks/...              # Webhook CRUD
/api/v1/schedules/...             # Schedule CRUD
/api/v1/components/...            # Component CRUD + compilation + share links
/api/v1/manifests/...             # Manifest CRUD
/api/v1/roles/...                 # Role & permission management
/api/v1/users/...                 # User management
/api/v1/api-keys/...              # API key management
/api/v1/dependencies/...          # Python dependency approval (admin)
/api/v1/packages/...              # Integration package management
/api/v1/workers/...               # Worker management (admin)
/api/v1/containers/...            # Container pool management (admin)
/api/v1/config/...                # Declarative config apply/validate/export (admin)
/api/v1/queue/...                 # Queue stats, job list, DLQ, cancel (admin)
/api/v1/request-logs/...          # Request log search (admin)
```

### OpenAI SDK Adapter (`/adapters/openai`)

An OpenAI SDK-compatible API that maps to Sinas agents and LLM providers. Point any OpenAI SDK client at this endpoint to use Sinas agents.

```
POST   /adapters/openai/v1/chat/completions     # Chat completion (maps to agent or direct LLM)
GET    /adapters/openai/v1/models               # List available models (agents + provider models)
GET    /adapters/openai/v1/models/{model_id}    # Get model info
```

Agents are listed as models with names like `agent:namespace/name`. Provider models are listed with their provider prefix.

### Interactive API Docs

Swagger UI is available at `/docs` (runtime API), `/api/v1/docs` (management API), and `/adapters/openai/docs` (OpenAI adapter) for exploring all endpoints and schemas interactively.

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
