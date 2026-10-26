"""The `context["secrets"]` every trusted executor hands a function.

One loader for all of them (Docker shared workers, k8s shared pods, in
process), so a function reading `context["secrets"]` sees the same thing
whichever trusted executor the deployment runs. Sandboxed executors never
get secrets.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def load_trusted_secrets(db: AsyncSession, user_id: Optional[str] = None) -> dict[str, str]:
    """{name: decrypted value}: every shared secret, then the user's private
    ones on top (a private secret overrides the shared one of the same name).
    A secret that fails to decrypt is skipped with a warning naming it; values
    are never logged."""
    from app.core.encryption import encryption_service
    from app.models.secret import Secret

    secrets: dict[str, str] = {}
    result = await db.execute(select(Secret).where(Secret.visibility == "shared"))
    for secret in result.scalars().all():
        try:
            secrets[secret.name] = encryption_service.decrypt(secret.encrypted_value)
        except Exception:
            logger.warning(f"Failed to decrypt secret '{secret.name}', skipping")

    if user_id:
        result = await db.execute(
            select(Secret).where(and_(Secret.user_id == user_id, Secret.visibility == "private"))
        )
        for secret in result.scalars().all():
            try:
                secrets[secret.name] = encryption_service.decrypt(secret.encrypted_value)
            except Exception:
                logger.warning(f"Failed to decrypt private secret '{secret.name}', skipping")

    return secrets
