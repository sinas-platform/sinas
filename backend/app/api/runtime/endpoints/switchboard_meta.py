"""Protected-resource metadata for the switchboard (RFC 9728).

An OAuth-capable MCP client that gets 401 from POST /mcp/{ns}/{name} follows
the `resource_metadata` URL in the WWW-Authenticate challenge to here, learns
which authorization servers issue tokens for the switchboard, and obtains one.
Unauthenticated, like every discovery document; it names no secrets.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.manifest import Manifest
from app.services import switchboard_auth

router = APIRouter()


@router.get("/.well-known/oauth-protected-resource/mcp/{namespace}/{name}", include_in_schema=False)
async def switchboard_protected_resource_metadata(namespace: str, name: str, db: AsyncSession = Depends(get_db)):
    manifest = await Manifest.get_by_name(db, namespace, name)
    if manifest is None or manifest.is_active is False:
        raise HTTPException(status_code=404, detail=f"Manifest '{namespace}/{name}' not found")
    doc = await switchboard_auth.protected_resource_metadata(db, namespace, name, manifest.description)
    return JSONResponse(doc, headers={"Cache-Control": "public, max-age=300"})
