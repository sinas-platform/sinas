"""Hard-delete chats whose expires_at has passed."""

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models.chat import Chat

logger = logging.getLogger(__name__)


async def cleanup_expired_chats() -> None:
    """Delete expired chats in batches to avoid long transactions."""
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        total_deleted = 0
        while True:
            result = await db.execute(
                select(Chat.id)
                .where(Chat.expires_at.isnot(None), Chat.expires_at < now)
                .limit(100)
            )
            ids = [r[0] for r in result.all()]
            if not ids:
                break
            await db.execute(delete(Chat).where(Chat.id.in_(ids)))
            await db.commit()
            total_deleted += len(ids)

        if total_deleted:
            logger.info(f"Cleaned up {total_deleted} expired chat(s)")
