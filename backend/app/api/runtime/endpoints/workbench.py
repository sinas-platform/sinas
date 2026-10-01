"""Runtime endpoints for a chat's workbench.

The workbench is addressed exclusively through its chat — these endpoints
are the only HTTP surface for it (workbench backing collections are fenced
out of the collections/files APIs by kind). Authorization is chat
ownership plus the agent chat permission, mirroring the other /chats
endpoints; there is no collection permission involved.

Front-ends use this to attach files to a conversation (uploads land in the
workbench, not a collection — promote publishes them later) and to browse
the tree the agent is working in.
"""
import base64
import uuid as uuid_lib
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.chat import Chat
from app.models.file import File, FileVersion
from app.services.collection_tools import _infer_content_type
from app.services.file_storage import get_storage
from app.services.workbench import (
    _validate_path,
    _write_bytes,
    get_or_create_workbench,
    run_content_filter,
)

router = APIRouter(tags=["runtime-workbench"])


class WorkbenchFileUpload(BaseModel):
    """Upload into a chat's workbench. Unlike collection uploads, names are
    relative paths ('data/input.csv') and visibility is always private."""

    name: str = Field(..., min_length=1, max_length=255)
    content_base64: str = Field(..., description="Base64-encoded file content")
    content_type: Optional[str] = Field(None, max_length=255, description="Defaults to a guess from the extension")
    file_metadata: dict[str, Any] = Field(default_factory=dict)


class WorkbenchFileResponse(BaseModel):
    name: str
    content_type: str
    current_version: int
    size_bytes: Optional[int] = None
    file_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class WorkbenchFileContent(BaseModel):
    name: str
    content_base64: str
    content_type: str
    version: int
    file_metadata: dict[str, Any]


async def _load_owned_chat(
    chat_id: str,
    http_request: Request,
    db: AsyncSession,
    current_user_data: tuple,
) -> Chat:
    """Resolve the chat, enforcing ownership + the agent chat permission."""
    user_id, permissions = current_user_data
    result = await db.execute(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))
    chat = result.scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    if chat.agent_namespace and chat.agent_name:
        perm = f"sinas.agents/{chat.agent_namespace}/{chat.agent_name}.chat:all"
        if not check_permission(permissions, perm):
            set_permission_used(http_request, perm, has_perm=False)
            raise HTTPException(
                403, f"Not authorized to chat with agent '{chat.agent_namespace}/{chat.agent_name}'"
            )
        set_permission_used(http_request, perm)
    return chat


@router.get("/chats/{chat_id}/workbench/files", response_model=list[WorkbenchFileResponse])
async def list_workbench_files(
    chat_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List the chat's workbench tree."""
    chat = await _load_owned_chat(chat_id, http_request, db, current_user_data)
    workbench = await get_or_create_workbench(db, chat)

    rows = (
        await db.execute(
            select(File, FileVersion.size_bytes)
            .outerjoin(
                FileVersion,
                (FileVersion.file_id == File.id)
                & (FileVersion.version_number == File.current_version),
            )
            .where(File.collection_id == workbench.id)
            .order_by(File.name)
        )
    ).all()
    return [
        WorkbenchFileResponse(
            name=f.name,
            content_type=f.content_type,
            current_version=f.current_version,
            size_bytes=size,
            file_metadata=f.file_metadata or {},
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f, size in rows
    ]


@router.get("/chats/{chat_id}/workbench/files/{path:path}", response_model=WorkbenchFileContent)
async def read_workbench_file(
    chat_id: str,
    path: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Read one workbench file's current version (content as base64)."""
    chat = await _load_owned_chat(chat_id, http_request, db, current_user_data)
    workbench = await get_or_create_workbench(db, chat)

    f = (
        await db.execute(
            select(File).where(File.collection_id == workbench.id, File.name == path)
        )
    ).scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail=f"File '{path}' not found in workbench")
    version = (
        await db.execute(
            select(FileVersion).where(
                FileVersion.file_id == f.id,
                FileVersion.version_number == f.current_version,
            )
        )
    ).scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail=f"File '{path}' has no stored version")
    try:
        content = await get_storage().read(version.storage_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Stored content for '{path}' is missing")
    return WorkbenchFileContent(
        name=f.name,
        content_base64=base64.b64encode(content).decode(),
        content_type=f.content_type,
        version=f.current_version,
        file_metadata=f.file_metadata or {},
    )


@router.post(
    "/chats/{chat_id}/workbench/files",
    response_model=WorkbenchFileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_workbench_file(
    chat_id: str,
    file_data: WorkbenchFileUpload,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Upload a file into the chat's workbench (always private).

    When the deployment configures workbench_content_filter_function, the
    filter runs before any bytes persist — the same guarantee collection
    uploads have.
    """
    user_id, _permissions = current_user_data
    chat = await _load_owned_chat(chat_id, http_request, db, current_user_data)

    err = _validate_path(file_data.name)
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        content = base64.b64decode(file_data.content_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 content: {e}")

    # Metering leaf: same op as collection uploads
    from app.services import metering

    await metering.record(metering.OperationKind.UPLOAD)

    content_type = file_data.content_type or _infer_content_type(file_data.name)

    if settings.workbench_content_filter_function:
        filter_err = await run_content_filter(
            db,
            settings.workbench_content_filter_function,
            namespace="_chat",
            collection_name=str(chat.id),
            filename=file_data.name,
            content=content,
            content_type=content_type,
            user_id=str(user_id),
        )
        if filter_err:
            raise HTTPException(
                status_code=422,
                detail=filter_err.get("message") or filter_err.get("error", "Rejected by content filter"),
            )

    workbench = await get_or_create_workbench(db, chat)
    result = await _write_bytes(
        db,
        get_storage(),
        workbench,
        filename=file_data.name,
        content=content,
        content_type=content_type,
        user_id=str(user_id),
        visibility="private",
        file_metadata={**file_data.file_metadata, "origin": "upload"},
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    await db.flush()

    f = (
        await db.execute(
            select(File).where(File.collection_id == workbench.id, File.name == file_data.name)
        )
    ).scalar_one()
    return WorkbenchFileResponse(
        name=f.name,
        content_type=f.content_type,
        current_version=f.current_version,
        size_bytes=len(content),
        file_metadata=f.file_metadata or {},
        created_at=f.created_at,
        updated_at=f.updated_at,
    )


@router.delete("/chats/{chat_id}/workbench/files/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workbench_file(
    chat_id: str,
    path: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Delete a file from the chat's workbench."""
    chat = await _load_owned_chat(chat_id, http_request, db, current_user_data)
    workbench = await get_or_create_workbench(db, chat)

    f = (
        await db.execute(
            select(File).where(File.collection_id == workbench.id, File.name == path)
        )
    ).scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail=f"File '{path}' not found in workbench")

    storage = get_storage()
    versions = (
        await db.execute(select(FileVersion).where(FileVersion.file_id == f.id))
    ).scalars().all()
    for ver in versions:
        try:
            await storage.delete(ver.storage_path)
        except Exception:
            pass
    await db.delete(f)
    await db.flush()
    return None
