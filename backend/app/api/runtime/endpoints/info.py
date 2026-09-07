"""Public, unauthenticated instance info for SDK/client discovery."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.core.config import settings
from app.core.database import get_db
from app.models.manifest import Manifest
from app.schemas.auth import InfoResponse

router = APIRouter()


async def published_services(db: AsyncSession) -> dict[str, dict]:
    """The merged public_info of every active manifest, keyed by namespace.
    Manifests are few and public_info is small, so this reads them all."""
    result = await db.execute(
        select(Manifest.namespace, Manifest.public_info)
        .where(Manifest.is_active == True)  # noqa: E712
        .order_by(Manifest.namespace, Manifest.name)
    )
    services: dict[str, dict] = {}
    for namespace, info in result.all():
        if not info:
            continue
        services.setdefault(namespace, {}).update(info)
    return services


@router.get("/info", response_model=InfoResponse)
async def get_info(db: AsyncSession = Depends(get_db)) -> InfoResponse:
    """
    Return instance-level configuration that clients (custom frontends, JS/Python
    SDKs, the official console) need before login. Unauthenticated by design.
    """
    return InfoResponse(
        auth_mode=settings.auth_mode,  # type: ignore[arg-type]
        version=__version__,
        features={
            "clickhouse": bool(settings.clickhouse_host),
            "smtp": bool(settings.smtp_host),
            # Master toggle for all user-code execution — the console greys
            # out function execution and the codeExecution tool when false.
            "code_execution": settings.code_execution_enabled,
        },
        services=await published_services(db),
    )
