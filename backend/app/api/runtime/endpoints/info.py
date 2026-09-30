"""Public, unauthenticated instance info for SDK/client discovery."""

from fastapi import APIRouter

from app import __version__
from app.core.config import settings
from app.schemas.auth import InfoResponse

router = APIRouter()


@router.get("/info", response_model=InfoResponse)
async def get_info() -> InfoResponse:
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
    )
