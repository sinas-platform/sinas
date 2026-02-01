"""MCP server endpoints."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_db
from app.core.auth import require_permission
from app.models import MCPServer
from app.schemas.mcp import (
    MCPServerCreate,
    MCPServerUpdate,
    MCPServerResponse,
    MCPToolExecuteRequest,
)
from app.services.mcp import mcp_client

router = APIRouter()


@router.post("/servers", response_model=MCPServerResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    request: MCPServerCreate,
    user_id: str = Depends(require_permission("sinas.mcp_servers.create:all")),
    db: AsyncSession = Depends(get_db)
):
    """
    Connect to an MCP server and discover tools.

    Requires admin permission.
    """
    # Check if server with this name already exists
    result = await db.execute(
        select(MCPServer).where(MCPServer.name == request.name)
    )
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"MCP server with name '{request.name}' already exists"
        )

    # Create server
    server = MCPServer(
        name=request.name,
        url=request.url,
        protocol=request.protocol,
        api_key=request.api_key,
        is_active=True,
        connection_status="disconnected"
    )

    db.add(server)
    await db.commit()
    await db.refresh(server)

    # Connect and discover tools
    try:
        await mcp_client.connect_server(db, server)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to connect to MCP server: {str(e)}"
        )

    await db.refresh(server)
    return MCPServerResponse.model_validate(server)


@router.get("/servers", response_model=List[MCPServerResponse])
async def list_mcp_servers(
    user_id: str = Depends(require_permission("sinas.mcp_servers.read:all")),
    db: AsyncSession = Depends(get_db)
):
    """List all MCP servers."""
    result = await db.execute(
        select(MCPServer).order_by(MCPServer.created_at.desc())
    )
    servers = result.scalars().all()

    return [MCPServerResponse.model_validate(server) for server in servers]


@router.get("/servers/{name}", response_model=MCPServerResponse)
async def get_mcp_server(
    name: str,
    user_id: str = Depends(require_permission("sinas.mcp_servers.read:all")),
    db: AsyncSession = Depends(get_db)
):
    """Get an MCP server."""
    server = await MCPServer.get_by_name(db, name)

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{name}' not found"
        )

    return MCPServerResponse.model_validate(server)


@router.put("/servers/{name}", response_model=MCPServerResponse)
async def update_mcp_server(
    name: str,
    request: MCPServerUpdate,
    user_id: str = Depends(require_permission("sinas.mcp_servers.update:all")),
    db: AsyncSession = Depends(get_db)
):
    """Update an MCP server."""
    server = await MCPServer.get_by_name(db, name)

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{name}' not found"
        )

    # Update fields
    if request.url is not None:
        server.url = request.url
    if request.protocol is not None:
        server.protocol = request.protocol
    if request.api_key is not None:
        server.api_key = request.api_key
    if request.is_active is not None:
        server.is_active = request.is_active

    await db.commit()

    # Reconnect if active
    if server.is_active:
        try:
            await mcp_client.disconnect_server(db, server.name)
            await mcp_client.connect_server(db, server)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to reconnect to MCP server: {str(e)}"
            )

    await db.refresh(server)
    return MCPServerResponse.model_validate(server)


@router.delete("/servers/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    name: str,
    user_id: str = Depends(require_permission("sinas.mcp_servers.delete:all")),
    db: AsyncSession = Depends(get_db)
):
    """Disconnect and delete an MCP server."""
    server = await MCPServer.get_by_name(db, name)

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{name}' not found"
        )

    # Disconnect
    await mcp_client.disconnect_server(db, server.name)

    # Delete from database
    await db.delete(server)
    await db.commit()

    return None


@router.get("/tools", response_model=List[dict])
async def list_mcp_tools(
    user_id: str = Depends(require_permission("sinas.mcp_tools.read:all"))
):
    """List all available MCP tools."""
    tools = await mcp_client.get_available_tools()
    return tools


@router.post("/tools/{tool_name}/execute")
async def execute_mcp_tool(
    tool_name: str,
    request: MCPToolExecuteRequest,
    user_id: str = Depends(require_permission("sinas.mcp_tools.execute:all"))
):
    """Execute an MCP tool directly."""
    try:
        result = await mcp_client.execute_tool(tool_name, request.arguments)
        return {"result": result}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Tool execution failed: {str(e)}"
        )
