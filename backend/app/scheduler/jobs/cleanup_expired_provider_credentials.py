"""Delete chats' run provider credentials whose expires_at has passed.

An expired credential is already treated as absent when a call resolves it
(app/services/provider_credentials.py); this removes its ciphertext too.
"""

import logging

from app.core.database import AsyncSessionLocal
from app.services.provider_credentials import delete_expired

logger = logging.getLogger(__name__)


async def cleanup_expired_provider_credentials() -> int:
    """Delete every expired run credential in one short transaction.
    Returns how many."""
    async with AsyncSessionLocal() as db:
        deleted = await delete_expired(db)
        await db.commit()
    if deleted:
        logger.info("Deleted %d expired run provider credential(s)", deleted)
    return deleted
