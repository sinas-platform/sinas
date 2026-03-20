# Development Environment Setup

This guide is for **contributors and developers** working on the Sinas codebase. For production deployment, see [DOCS.md](DOCS.md).

## Prerequisites

- Docker and Docker Compose
- Git
- An SMTP service for OTP login (SendGrid, Mailgun, etc.)

## Setup

```bash
git clone https://github.com/sinas-platform/sinas.git
cd sinas
cp .env.example .env
```

Edit `.env` with your configuration:

```bash
# Required
DATABASE_PASSWORD=devpassword
SECRET_KEY=$(openssl rand -hex 32)
ENCRYPTION_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
SUPERADMIN_EMAIL=you@example.com

# SMTP (required for OTP login)
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=your-api-key
SMTP_DOMAIN=example.com
```

## Start

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

This starts all services with hot-reload enabled:
- **Backend API**: http://localhost:8000 (with `--reload`)
- **API Docs**: http://localhost:8000/docs
- **Console**: http://localhost:51245
- **PostgreSQL**: localhost:5432 (via pgbouncer, for tests and tools)

Source code is volume-mounted, so changes to `backend/` are picked up automatically.

## Common Tasks

```bash
# Rebuild after dependency changes
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

# View logs
docker compose -f docker-compose.dev.yml logs -f backend

# Run database migrations
docker exec -it sinas-backend alembic upgrade head

# Create a new migration
docker exec -it sinas-backend alembic revision --autogenerate -m "description"

# Access backend shell
docker exec -it sinas-backend sh
```

## Running Tests

Tests use the dev database (via pgbouncer on localhost:5432) with transaction rollback per test.

```bash
cd backend
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## Local Backend (without Docker)

For faster iteration on the backend without Docker:

```bash
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Requires PostgreSQL, Redis, and ClickHouse running separately (e.g., via the dev compose with just infra services).

## Console Development

```bash
cd console
npm install
npm run dev
```

The console dev server runs on port 5173 and proxies API requests to localhost:8000.

## Code Quality

```bash
cd backend
black .          # Format
ruff check .     # Lint
mypy .           # Type check
```

## Architecture

See [README.md](README.md) for architecture overview and [DOCS.md](DOCS.md) for the full feature reference.
