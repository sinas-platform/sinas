"""Drop gateway audit rows past GATEWAY_AUDIT_RETENTION_DAYS."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.gateway_call import GatewayCall

logger = logging.getLogger(__name__)


async def cleanup_gateway_calls() -> int:
    cutoff = datetime.now(UTC) - timedelta(days=settings.gateway_audit_retention_days)
    async with AsyncSessionLocal() as db:
        result = await db.execute(delete(GatewayCall).where(GatewayCall.created_at < cutoff))
        await db.commit()
    removed = result.rowcount or 0
    if removed:
        logger.info("Removed %d gateway call(s) older than %d days", removed, settings.gateway_audit_retention_days)
    return removed
