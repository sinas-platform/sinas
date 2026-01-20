# SINAS - AI Agent & Automation Orchestration Platform

**Lean, effective platform for AI agent orchestration and serverless automation with RBAC.**

Core capabilities:
- 🤖 **AI Agents** - Multi-LLM chat with tool calling and MCP integration
- ⚡ **Function Execution** - Container-isolated Python runtime with automatic tracking
- 🌐 **Webhooks & Scheduling** - HTTP triggers and cron-based automation
- 💾 **State Store** - Flexible key-value storage for agent/function/workflow state
- 🔐 **RBAC** - Group-based permissions with hierarchical scopes (:own/:group/:all)
- 🖥️ **Management Console** - Web UI for managing agents, functions, and system configuration

---

## Repository Structure

This is a **monorepo** containing both backend and frontend:

```
SINAS/
├── backend/           # FastAPI backend (Python)
│   ├── app/          # Application code
│   ├── alembic/      # Database migrations
│   └── Dockerfile    # Backend container
├── console/          # Management console frontend (React/Vue)
│   ├── src/          # Frontend source (copy from SINAS_CONSOLE)
│   └── Dockerfile    # Frontend container (Nginx)
├── docker-compose.yml # Orchestrates all services
└── Caddyfile         # Reverse proxy configuration
```

**Services:**
- `backend` - FastAPI API server (port 8000)
- `console` - Nginx serving management UI (port 80)
- `postgres` - PostgreSQL database
- `clickhouse` - Analytics database (optional)
- `caddy` - HTTPS reverse proxy

**Routing (via Caddy):**
- `https://yourdomain.com/console/*` → Console UI
- `https://yourdomain.com/api/v1/*` → Management API
- `https://yourdomain.com/*` → Runtime API (webhooks, agents, auth)

---

## Installation

### Prerequisites

- Docker & Docker Compose

### Setup

**1. Clone and configure:**
```bash
git clone <repository-url>
cd SINAS
cp .env.example .env
```

**2. Generate keys:**
```bash
# Generate encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Generate secret key
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**3. Edit `.env` with required settings (see [Environment Variables](#environment-variables) for full reference):**
```bash
# Security (REQUIRED)
SECRET_KEY=<output-from-secret-key-generation>
ENCRYPTION_KEY=<output-from-encryption-key-generation>

# Database (use docker-compose postgres)
DATABASE_PASSWORD=your-secure-postgres-password

# SMTP for OTP authentication (REQUIRED)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_DOMAIN=yourdomain.com

# Admin user (created on first startup)
SUPERADMIN_EMAIL=admin@yourdomain.com
```

**4. Setup console (optional but recommended):**

The console frontend is not included by default. To add it:

```bash
# Copy SINAS_CONSOLE into console/ directory
cd console/
rsync -av --exclude node_modules --exclude dist ../../SINAS_CONSOLE/ ./
cd ..
```

See [console/README.md](console/README.md) for details.

**5. Start the application:**
```bash
# Without console (backend only)
docker-compose up backend postgres clickhouse caddy

# With console (full stack)
docker-compose up
```

**5. Access the API:**
- API: http://localhost:51245
- API Docs: http://localhost:51245/docs
- Health: http://localhost:51245/health

**6. Login as admin:**
```bash
# Request OTP
curl -X POST http://localhost:51245/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@yourdomain.com"}'

# Verify OTP (check your email)
curl -X POST http://localhost:51245/api/v1/auth/verify-otp \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "otp_code": "123456"}'
```


---

## APIs

### Runtime API (Execution)

Execute webhooks and interact with agents:

- **Webhooks:** `{GET|POST|PUT|DELETE|PATCH} /webhooks/{path}` - Trigger functions via HTTP
- **Agent Chats:** `POST /agents/{namespace}/{name}/chats` - Create chat with agent
- **Stream Messages:** `POST /chats/{id}/messages/stream` - Send message and stream response (SSE)

**Documentation:** http://localhost:51245/docs (dynamically generated based on active webhooks/agents)

### Management API (Configuration)

Full CRUD for all resources at `/api/v1/`:

- **Agents:** `/api/v1/agents`
- **Functions:** `/api/v1/functions`
- **Webhooks:** `/api/v1/webhooks`
- **Schedules:** `/api/v1/schedules`
- **Executions:** `/api/v1/executions`
- **States:** `/api/v1/states`
- **Chats:** `/api/v1/chats`
- **Users & Groups:** `/api/v1/users`, `/api/v1/groups`
- **LLM Providers:** `/api/v1/llm-providers`
- **MCP Servers:** `/api/v1/mcp-servers`
- **Packages:** `/api/v1/packages`

**Documentation:** http://localhost:51245/api/v1/docs

### Declarative Configuration

Apply YAML config for GitOps workflows at `/api/v1/config/`:

- `POST /api/v1/config/validate` - Validate config without applying
- `POST /api/v1/config/apply` - Apply config (idempotent)
- `GET /api/v1/config/export` - Export current config to YAML

See `config/default-data.yaml` for examples.

---

## Environment Variables

### Required Variables

These variables **must** be set - they have no defaults:

#### Security

- **`SECRET_KEY`** - Secret key for JWT token signing
  - Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
  - **Security critical** - keep secret, change in production

- **`ENCRYPTION_KEY`** - Encryption key for sensitive data (DB credentials, API keys)
  - Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
  - **Security critical** - keep secret, never rotate after initial setup (data will be lost)

#### Database

- **`DATABASE_PASSWORD`** - PostgreSQL password
  - Used with docker-compose postgres service
  - Alternatively, set `DATABASE_URL` to use external database (overrides all other DB settings)

#### SMTP (Email for OTP)

- **`SMTP_HOST`** - SMTP server hostname (e.g., `smtp.gmail.com`)
- **`SMTP_PORT`** - SMTP server port (typically `587` for TLS, `465` for SSL)
- **`SMTP_USER`** - SMTP username/email
- **`SMTP_PASSWORD`** - SMTP password (use app-specific password for Gmail)
- **`SMTP_DOMAIN`** - Domain name for "from" email address

#### Admin User

- **`SUPERADMIN_EMAIL`** - Email address for initial admin user
  - User is auto-created on startup and added to Admins group
  - Only creates user if Admins group is empty

---

### Optional Variables

These have sensible defaults but can be customized:

#### Database Configuration

- `DATABASE_USER` - PostgreSQL username (default: `postgres`)
- `DATABASE_HOST` - PostgreSQL host (default: `postgres`)
- `DATABASE_PORT` - PostgreSQL port (default: `5432`)
- `DATABASE_NAME` - PostgreSQL database name (default: `sinas`)
- `DATABASE_URL` - Full database connection string (overrides all other DB settings)
  - Example: `postgresql://user:password@host:5432/database`

#### Application Settings

- `APP_PORT` - Port for the SINAS API server (default: `51245`)
- `DEBUG` - Enable debug mode with verbose logging (default: `false`)
- `ALGORITHM` - JWT signing algorithm (default: `HS256`)

#### JWT Token Expiration

- `ACCESS_TOKEN_EXPIRE_MINUTES` - Access token lifetime (default: `15` minutes)
  - Best practice: Keep short for security
- `REFRESH_TOKEN_EXPIRE_DAYS` - Refresh token lifetime (default: `30` days)
  - Users stay logged in by refreshing access tokens

#### OTP Configuration

- `OTP_EXPIRE_MINUTES` - One-time password validity period (default: `10` minutes)

#### HTTPS & Domain

- `DOMAIN` - Domain name for automatic HTTPS with Let's Encrypt (default: `localhost`)
  - For local development, leave as `localhost` (no HTTPS)
  - For production, set to your domain (e.g., `api.yourdomain.com`)

#### External Authentication (OIDC/OAuth2)

Enable integration with external identity providers (Authentik, Auth0, Keycloak, etc.):

- `EXTERNAL_AUTH_ENABLED` - Enable external auth (default: `false`)
- `OIDC_ISSUER` - OIDC provider issuer URL
  - Example: `https://authentik.company.com/application/o/sinas/`
- `OIDC_AUDIENCE` - Expected audience claim (default: `sinas`)
- `OIDC_GROUPS_CLAIM` - JWT claim containing user groups (default: `groups`)

**User/Group Provisioning:**
- `AUTO_PROVISION_USERS` - Auto-create users on first login (default: `true`)
- `AUTO_PROVISION_GROUPS` - Auto-create SINAS groups for unmapped external groups (default: `false`)
- `DEFAULT_GROUP_NAME` - Fallback group for users without mapped groups (default: `Users`)

**Note:** Map external groups to SINAS groups via Management API:
`PATCH /api/v1/groups/{group_id}` with `{"external_group_id": "external-group-id"}`

#### Application Container Resources

Docker resource limits for the SINAS application container:

- `APP_CPU_LIMIT` - Maximum CPU cores (default: `2.0`)
- `APP_MEMORY_LIMIT` - Maximum RAM (default: `2G`)
- `APP_CPU_RESERVATION` - Guaranteed CPU cores (default: `0.5`)
- `APP_MEMORY_RESERVATION` - Guaranteed RAM (default: `512M`)

#### Function Execution

Control resource limits and behavior for serverless functions:

**Resource Limits:**
- `FUNCTION_TIMEOUT` - Max execution time in seconds (default: `300` = 5 minutes)
- `MAX_FUNCTION_MEMORY` - Memory limit in MB (default: `512`)
- `MAX_FUNCTION_CPU` - CPU cores (default: `1.0` = 1 full core, `0.5` = half core)
- `MAX_FUNCTION_STORAGE` - Disk storage limit (default: `1g`, format: `500m`, `1g`)

**Container Configuration:**
- `FUNCTION_CONTAINER_IMAGE` - Base Docker image (default: `python:3.11-slim`)
- `FUNCTION_CONTAINER_IDLE_TIMEOUT` - Seconds before idle container cleanup (default: `3600` = 1 hour)

**Package Management:**
- `ALLOW_PACKAGE_INSTALLATION` - Allow `pip install` in functions (default: `true`)
- `ALLOWED_PACKAGES` - Comma-separated package whitelist (default: empty = all allowed)
  - Example: `requests,pandas,numpy`

#### Declarative Configuration (GitOps)

- `CONFIG_FILE` - Path to YAML configuration file
  - Example: `config/default-data.yaml`
- `AUTO_APPLY_CONFIG` - Automatically apply config file on startup (default: `false`)
  - Set to `true` for GitOps workflows where config is version-controlled

---

## Development

**Docker commands:**
```bash
docker-compose up                               # Start
docker logs -f sinas-app                        # View logs
docker exec -it sinas-app sh                    # Access shell
docker exec -it sinas-app alembic upgrade head  # Run migrations
```

**Local development:**
```bash
poetry install                                  # Install deps
poetry run uvicorn app.main:app --reload       # Start server
poetry run alembic upgrade head                 # Run migrations
poetry run black . && poetry run ruff check .   # Format & lint
```

**Documentation:**
- [FEATURES.md](FEATURES.md) - Complete feature reference
- [CLAUDE.md](CLAUDE.md) - Development guide for Claude Code

---

## License

Dual licensed: **AGPL v3.0** (open source) or **Commercial License** (proprietary use)
