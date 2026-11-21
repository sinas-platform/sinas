"""Drop switchboard audit rows past SWITCHBOARD_AUDIT_RETENTION_DAYS."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.switchboard_call import SwitchboardCall

logger = logging.getLogger(__name__)


async def cleanup_switchboard_calls() -> int:
    cutoff = datetime.now(UTC) - timedelta(days=settings.switchboard_audit_retention_days)
    async with AsyncSessionLocal() as db:
        result = await db.execute(delete(SwitchboardCall).where(SwitchboardCall.created_at < cutoff))
        await db.commit()
    removed = result.rowcount or 0
    if removed:
        logger.info("Removed %d switchboard call(s) older than %d days", removed, settings.switchboard_audit_retention_days)
    return removed
