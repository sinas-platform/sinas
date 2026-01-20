# Monorepo Setup Guide

This document explains the SINAS monorepo structure and how to work with it.

## Architecture

SINAS uses a **multi-service monorepo** approach:

- **One repository** containing backend + frontend source code
- **Multiple Docker containers** deployed as separate services
- **Nginx for frontend**, FastAPI for backend (proper separation of concerns)

## Directory Structure

```
SINAS/
├── backend/              # Backend API (FastAPI)
│   ├── app/             # Python application code
│   │   ├── api/         # API endpoints
│   │   ├── models/      # Database models
│   │   ├── services/    # Business logic
│   │   └── core/        # Auth, config, permissions
│   ├── alembic/         # Database migrations
│   ├── Dockerfile       # Backend container
│   └── pyproject.toml   # Python dependencies
│
├── console/             # Frontend UI (React/TypeScript)
│   ├── src/            # Frontend source code
│   ├── public/         # Static assets
│   ├── Dockerfile      # Frontend container (multi-stage with Nginx)
│   ├── nginx.conf      # Nginx configuration
│   └── package.json    # Node dependencies
│
├── docker-compose.yml   # Orchestrates all services
├── Caddyfile           # Reverse proxy & SSL
└── .env                # Environment variables
```

## Port Configuration

| Environment | Backend | Console | Notes |
|-------------|---------|---------|-------|
| **Local** | http://localhost:8000 | http://localhost:51245 | Direct access, no Caddy |
| **VPS** | https://yourdomain.com | https://yourdomain.com:51245 | Via Caddy with auto SSL |

**Automatic:** Caddy only starts when `DOMAIN` is set to a real domain (not localhost).

## Development Workflow

### Backend Development

```bash
# Option 1: Run in Docker with hot reload
docker-compose up backend postgres

# Option 2: Run locally
cd backend/
poetry install
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend Development

```bash
# Option 1: Run in Docker
docker-compose up console

# Option 2: Run dev server (hot reload on localhost:5173)
cd console/
npm install
npm run dev

# Configure API URL in console/.env:
# VITE_API_BASE_URL=http://localhost:8000
```

### Full Stack Development

```bash
# Start everything
docker-compose up -d

# Access:
# - Backend API: http://localhost:8000
# - Console UI: http://localhost:51245
# - API Docs: http://localhost:8000/docs
```

## Production Deployment

### Building

```bash
# Build all services
docker-compose build

# Build specific service
docker-compose build backend
docker-compose build console
```

### Running on VPS

1. **Set environment variables** in `.env`:
   ```bash
   DOMAIN=dev.titan.sinas.cloud
   SUPERADMIN_EMAIL=admin@example.com
   ```

2. **Open firewall ports**: 80, 443, 51245

3. **Point DNS**: A record to VPS IP

4. **Run install.sh or manually**:
   ```bash
   docker-compose up -d
   ```

Caddy will auto-obtain Let's Encrypt certificates for both backend and console.

### Logs and Monitoring

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f backend
docker-compose logs -f console
docker-compose logs -f caddy

# Check status
docker-compose ps
```

## How It Works

**Local Development (`DOMAIN` not set or `=localhost`):**
- Backend and Console expose ports directly (8000, 51245)
- Caddy service doesn't start (uses docker compose profile)
- No SSL certificates

**VPS Production (`DOMAIN=yourdomain.com`):**
- Caddy proxies both services with automatic Let's Encrypt SSL
- Backend: https://yourdomain.com (port 443)
- Console: https://yourdomain.com:51245

## Environment Variables

All services share the same `.env` file at the repository root.

**Backend-specific:**
- `DATABASE_*` - PostgreSQL connection
- `SMTP_*` - Email configuration
- `SECRET_KEY`, `ENCRYPTION_KEY` - Security
- `SUPERADMIN_EMAIL` - Initial admin user

**Frontend-specific:**
- `VITE_API_BASE_URL` - Backend API URL (set in console/.env for dev)

**Shared:**
- `DOMAIN` - Your domain name (for VPS/SSL)

## CI/CD Considerations

### Versioning Strategy

**Option 1: Unified versioning** (Recommended)
- Single version number for entire system
- `git tag v1.2.3`
- Both backend and console get same version

**Option 2: Independent versioning**
- Separate versions in `backend/pyproject.toml` and `console/package.json`
- Tag format: `backend-v1.2.3`, `console-v2.0.1`

### GitHub Actions Example

```yaml
name: Build and Deploy

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      # Build backend
      - name: Build backend
        run: docker build -t sinas-backend:latest ./backend

      # Build console
      - name: Build console
        run: docker build -t sinas-console:latest ./console

      # Push to registry
      - name: Push images
        run: |
          docker push sinas-backend:latest
          docker push sinas-console:latest
```

## FAQ

### Why not embedded frontend?

Serving static files from FastAPI is possible but:
- ❌ Slower than Nginx for static assets
- ❌ Couples frontend/backend releases
- ❌ Larger backend container size
- ✅ Separate services scale independently
- ✅ Nginx optimized for static file serving

### Why not separate repos?

- ✅ Easier local development (one clone)
- ✅ Atomic commits for API + UI changes
- ✅ Single version number
- ✅ Simplified CI/CD
- ❌ Larger repo size (but manageable)

### Can I deploy backend without console?

Yes! Just exclude console from docker-compose:

```bash
docker-compose up backend postgres clickhouse caddy
```

The backend will work fine, you just won't have the web UI.

### How do I update dependencies?

**Backend:**
```bash
cd backend/
poetry add <package>
poetry lock
docker-compose build backend
```

**Frontend:**
```bash
cd console/
npm install <package>
docker-compose build console
```

## Troubleshooting

### Console build fails

Check that TypeScript compiles:
```bash
cd console/
npm install
npm run build
```

### Backend can't connect to database

Check that postgres service is running:
```bash
docker-compose ps postgres
docker-compose logs postgres
```

### Caddy SSL certificate errors

- Ensure `DOMAIN` is set in `.env`
- Verify DNS points to your server
- Check ports 80 and 443 are open
- View Caddy logs: `docker-compose logs caddy`

### Port conflicts

If you see "port already allocated":
```bash
# Check what's using the port
lsof -i :8000
lsof -i :51245

# Stop conflicting services or change ports in docker-compose.yml
```

## Best Practices

1. **Always use docker-compose** for consistency
2. **Keep .env.example updated** with all required vars
3. **Document breaking changes** in CHANGELOG.md
4. **Version lock dependencies** (poetry.lock, package-lock.json)
5. **Test both dev and production builds** before deploying
