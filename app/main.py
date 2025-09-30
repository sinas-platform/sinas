from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.core.config import settings
from app.services.scheduler import scheduler
from app.services.redis_logger import redis_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await redis_logger.connect()
    await scheduler.start()
    yield
    # Shutdown
    await scheduler.stop()
    await redis_logger.disconnect()


app = FastAPI(
    title="Maestro Cloud Functions Platform",
    description="Execute Python functions via webhooks and schedules",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/scheduler/status")
async def scheduler_status():
    return scheduler.get_scheduler_status()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)