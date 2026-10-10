"""MCP server endpoints.

Writes go through McpServerApplier — the same path config apply and package
install use — so validation, ownership and change history are identical on
every channel. The live tools/list is a runtime operation and stays here.
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.mcp_server import McpServer
from app.schemas.mcp_server import (
    McpServerCreate,
    McpServerResponse,
    McpServerToolInfo,
    McpServerToolsResponse,
    McpServerUpdate,
)
from app.services import mcp_client
from app.services.mcp_tools import server_allows
from app.services.resources import rest
from app.services.resources.mcp_servers import McpServerApplier

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])

_applier = McpServerApplier()


@router.post("", response_model=McpServerResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    request: Request,
    data: McpServerCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create an MCP server."""
    user_id, permissions = current_user_data

    permission = "sinas.mcp_servers.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create MCP servers")
    set_permission_used(request, permission)

    ctx = rest.api_context(db, user_id)
    result = await rest.write(
        _applier, ctx, rest.parse_spec(_applier, data.model_dump()), must_create=True
    )
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return McpServerResponse.model_validate(result.obj)


@router.get("", response_model=list[McpServerResponse])
async def list_mcp_servers(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List MCP servers."""
    user_id, permissions = current_user_data

    servers = await McpServer.list_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read"
    )
    set_permission_used(request, "sinas.mcp_servers.read")
    return [McpServerResponse.model_validate(s) for s in servers]


@router.get("/{namespace}/{name}", response_model=McpServerResponse)
async def get_mcp_server(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific MCP server."""
    user_id, permissions = current_user_data

    server = await McpServer.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.mcp_servers/{namespace}/{name}.read")
    return McpServerResponse.model_validate(server)


@router.put("/{namespace}/{name}", response_model=McpServerResponse)
async def update_mcp_server(
    request: Request,
    namespace: str,
    name: str,
    data: McpServerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update an MCP server. Fields left out (or sent as null) are
    unchanged; auth, headers and the tool lists are replaced whole."""
    user_id, permissions = current_user_data

    server = await McpServer.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="update",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.mcp_servers/{namespace}/{name}.update")

    ctx = rest.api_context(db, user_id)
    server = await rest.locked(_applier, ctx, server)
    patch = {key: value for key, value in data.model_dump().items() if value is not None}
    await rest.write(_applier, ctx, rest.patch_spec(_applier, server, patch), existing=server)
    await rest.commit(db, ctx)
    await db.refresh(server)
    return McpServerResponse.model_validate(server)


@router.delete("/{namespace}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete an MCP server."""
    user_id, permissions = current_user_data

    server = await McpServer.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="delete",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.mcp_servers/{namespace}/{name}.delete")

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, server), ctx)
    await rest.commit(db, ctx)
    return None


@router.post("/{namespace}/{name}/tools", response_model=McpServerToolsResponse)
async def list_mcp_server_tools(
    request: Request,
    namespace: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Connect to the server now and list its tools (bypassing the cache):
    the console's connection test. 502 when the server can't be reached."""
    user_id, permissions = current_user_data

    server = await McpServer.get_with_permissions(
        db=db, user_id=user_id, permissions=permissions, action="read",
        namespace=namespace, name=name,
    )
    set_permission_used(request, f"sinas.mcp_servers/{namespace}/{name}.read")

    started = time.time()
    try:
        listed = await mcp_client.list_tools(db, server, str(user_id), use_cache=False)
    except mcp_client.McpClientError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return McpServerToolsResponse(
        tools=[
            McpServerToolInfo(
                name=t.name,
                title=t.title,
                description=t.description,
                input_schema=t.input_schema,
                annotations=t.annotations,
                allowed=server_allows(server, t.name),
            )
            for t in listed
        ],
        elapsed_ms=round((time.time() - started) * 1000, 1),
    )
