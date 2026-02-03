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
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.templates import initialize_default_templates
from app.middleware.request_logger import RequestLoggerMiddleware
from app.services.clickhouse_logger import clickhouse_logger
from app.services.mcp import mcp_client
from app.services.openapi_generator import generate_runtime_openapi
from app.services.scheduler import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await scheduler.start()

    # Initialize default roles
    async with AsyncSessionLocal() as db:
        await initialize_default_roles(db)

    # Initialize superadmin user
    async with AsyncSessionLocal() as db:
        await initialize_superadmin(db)

    # Initialize default templates
    async with AsyncSessionLocal() as db:
        await initialize_default_templates(db)

    # Initialize MCP client
    async with AsyncSessionLocal() as db:
        await mcp_client.initialize(db)

    # Apply declarative configuration (if enabled)
    if settings.config_file and settings.auto_apply_config:
        logger = logging.getLogger(__name__)
        logger.info(f"🔧 AUTO_APPLY_CONFIG enabled, applying config from {settings.config_file}...")
        async with AsyncSessionLocal() as db:
            from app.services.config_apply import ConfigApplyService
            from app.services.config_parser import ConfigParser

            try:
                # Read config file
                with open(settings.config_file) as f:
                    config_yaml = f.read()

                # Parse and validate (with database-aware checking)
                config, validation = await ConfigParser.parse_and_validate(
                    config_yaml, db=db, strict=False
                )

                if not validation.valid:
                    logger.error("❌ Config validation failed:")
                    for error in validation.errors:
                        logger.error(f"  - {error.path}: {error.message}")
                    raise RuntimeError("Config validation failed")

                if validation.warnings:
                    logger.warning("⚠️  Config validation warnings:")
                    for warning in validation.warnings:
                        logger.warning(f"  - {warning.path}: {warning.message}")

                # Apply configuration
                apply_service = ConfigApplyService(db, config.metadata.name)
                result = await apply_service.apply_config(config, dry_run=False)

                if not result.success:
                    logger.error("❌ Config application failed:")
                    for error in result.errors:
                        logger.error(f"  - {error}")
                    raise RuntimeError("Config application failed")

                # Log summary
                logger.info("✅ Config applied successfully!")
                if result.summary.created:
                    logger.info(f"  Created: {dict(result.summary.created)}")
                if result.summary.updated:
                    logger.info(f"  Updated: {dict(result.summary.updated)}")
                if result.summary.unchanged:
                    logger.info(f"  Unchanged: {dict(result.summary.unchanged)}")

            except FileNotFoundError:
                logger.error(f"❌ Config file not found: {settings.config_file}")
                raise
            except Exception as e:
                logger.error(f"❌ Failed to apply config: {e}", exc_info=True)
                raise

    # Start container manager cleanup task
    from app.services.user_container_manager import container_manager

    await container_manager.start_cleanup_task()

    # Initialize shared worker manager
    from app.services.shared_worker_manager import shared_worker_manager

    await shared_worker_manager.initialize()

    yield
    # Shutdown
    await scheduler.stop()
    clickhouse_logger.close()


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


@app.get("/scheduler/status")
async def scheduler_status():
    return scheduler.get_scheduler_status()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
