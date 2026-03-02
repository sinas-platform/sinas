"""Icon URL resolver for agents and functions."""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.file import Collection

logger = logging.getLogger(__name__)


def _build_public_file_url(namespace: str, collection: str, filename: str) -> str:
    """Build a public file URL."""
    domain = settings.domain
    if domain and domain.lower() not in ("localhost", "127.0.0.1"):
        return f"https://{domain}/files/public/{namespace}/{collection}/{filename}"
    return f"http://localhost:8000/files/public/{namespace}/{collection}/{filename}"


async def resolve_icon_url(icon: Optional[str], db: AsyncSession) -> Optional[str]:
    """Resolve an icon reference to a URL.

    - "url:https://..." → returns the URL as-is
    - "collection:ns/coll/filename" → public URL if collection.is_public, else signed JWT URL
    - None or empty → None
    """
    if not icon:
        return None

    if icon.startswith("url:"):
        return icon[4:]

    if icon.startswith("collection:"):
        ref = icon[11:]  # strip "collection:"
        parts = ref.split("/", 2)
        if len(parts) != 3:
            logger.warning(f"Invalid collection icon reference: {icon}")
            return None

        namespace, collection, filename = parts

        # Check if collection is public
        result = await db.execute(
            select(Collection.is_public).where(
                Collection.namespace == namespace,
                Collection.name == collection,
            )
        )
        row = result.first()
        if row is None:
            return None

        if row[0]:
            # Public collection → public URL
            return _build_public_file_url(namespace, collection, filename)
        else:
            # Private collection → signed JWT URL
            from app.services.file_storage import generate_file_url
            from app.models.file import File
            from sqlalchemy import and_

            # Look up the file to get its ID for signing
            coll_result = await db.execute(
                select(Collection.id).where(
                    Collection.namespace == namespace,
                    Collection.name == collection,
                )
            )
            coll_row = coll_result.first()
            if not coll_row:
                return None

            file_result = await db.execute(
                select(File.id, File.current_version).where(
                    and_(
                        File.collection_id == coll_row[0],
                        File.name == filename,
                    )
                )
            )
            file_row = file_result.first()
            if not file_row:
                return None

            url = generate_file_url(str(file_row[0]), file_row[1], expires_in=86400)
            return url

    logger.warning(f"Unrecognized icon format: {icon}")
    return None
