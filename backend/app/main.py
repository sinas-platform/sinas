import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.runtime import runtime_router
from app.api.v1 import router as api_v1_router
from app.core.auth import initialize_default_roles, initialize_superadmin
from app.core.database import AsyncSessionLocal, get_db
from app.core.templates import initialize_default_templates
from app.middleware.request_logger import RequestLoggerMiddleware
from app.services.clickhouse_logger import clickhouse_logger
from app.services.mcp import mcp_client
from app.services.openapi_generator import generate_runtime_openapi

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - Redis connection
    from app.core.redis import close_redis, get_redis

    redis = await get_redis()
    await redis.ping()
    print("✅ Redis connection established")

    # --- Idempotent tasks: safe on all replicas ---

    # Initialize default roles
    async with AsyncSessionLocal() as db:
        await initialize_default_roles(db)

    # Initialize superadmin user
    async with AsyncSessionLocal() as db:
        await initialize_superadmin(db)

    # Initialize default templates
    async with AsyncSessionLocal() as db:
        await initialize_default_templates(db)

    # Initialize MCP client (each replica needs its own connections)
    async with AsyncSessionLocal() as db:
        await mcp_client.initialize(db)

    # Discover existing Docker containers so /api/v1/containers and /workers
    # endpoints can report accurate state.  The workers/pool are *created* by
    # the arq worker process or explicit scale calls; here we only discover.
    try:
        from app.services.container_pool import container_pool

        await container_pool._discover_existing_containers()
        container_pool._initialized = True
        print(
            f"✅ Discovered {len(container_pool.idle)} pool containers"
        )
    except Exception as e:
        print(f"⚠️  Container pool discovery skipped: {e}")

    try:
        from app.services.shared_worker_manager import shared_worker_manager

        await shared_worker_manager._discover_existing_workers()
        shared_worker_manager._initialized = True
        print(
            f"✅ Discovered {len(shared_worker_manager.workers)} shared workers"
        )
    except Exception as e:
        print(f"⚠️  Shared worker discovery skipped: {e}")

    yield

    # Shutdown
    clickhouse_logger.close()
    await close_redis()


# Create main application with runtime API documentation
app = FastAPI(
    title="SINAS Runtime API",
    description="Execute AI agents, webhooks, and continue conversations",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,  # We'll create custom docs endpoint
    openapi_url=None,  # We'll create custom OpenAPI endpoint
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request logging middleware
app.add_middleware(RequestLoggerMiddleware)

# Create management API sub-application
management_app = FastAPI(
    title="SINAS Management API",
    description="Manage agents, functions, webhooks, schedules, and configuration",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# Include management routes in sub-app
management_app.include_router(api_v1_router)

# Mount management app at /api/v1
app.mount("/api/v1", management_app)

# Include runtime API routes (root level)
app.include_router(runtime_router)


# Dynamic OpenAPI endpoint for runtime API
@app.get("/openapi.json", include_in_schema=False)
async def get_runtime_openapi(db: AsyncSession = Depends(get_db)):
    """Generate dynamic OpenAPI spec showing all active webhooks and agents."""
    spec = await generate_runtime_openapi(db)
    return JSONResponse(content=spec)


# Custom docs endpoint that uses dynamic OpenAPI
@app.get("/docs", include_in_schema=False)
async def get_runtime_docs():
    """Runtime API documentation using dynamic OpenAPI spec."""
    return get_swagger_ui_html(
        openapi_url="/openapi.json", title="SINAS Runtime API - Documentation"
    )


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
