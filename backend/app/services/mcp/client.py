"""MCP (Model Context Protocol) client for external tool integration."""
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import httpx
import websockets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MCPServer

logger = logging.getLogger(__name__)


class MCPToolHandler:
    """Wrapper for an MCP tool to make it callable."""

    def __init__(self, server_url: str, tool_name: str, tool_schema: Dict[str, Any], protocol: str = "http", api_key: Optional[str] = None):
        self.server_url = server_url
        self.tool_name = tool_name
        self.tool_schema = tool_schema
        self.protocol = protocol
        self.api_key = api_key

    async def execute(self, arguments: Dict[str, Any]) -> Any:
        """Execute the MCP tool with given arguments."""
        if self.protocol == "http":
            return await self._execute_http(arguments)
        elif self.protocol == "websocket":
            return await self._execute_websocket(arguments)
        else:
            raise ValueError(f"Unsupported protocol: {self.protocol}")

    async def _execute_http(self, arguments: Dict[str, Any]) -> Any:
        """Execute tool via HTTP."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            payload = {
                "method": "tools/call",
                "params": {
                    "name": self.tool_name,
                    "arguments": arguments
                }
            }

            response = await client.post(
                self.server_url,
                json=payload,
                headers=headers
            )
            response.raise_for_status()
            return response.json()

    async def _execute_websocket(self, arguments: Dict[str, Any]) -> Any:
        """Execute tool via WebSocket."""
        extra_headers = {}
        if self.api_key:
            extra_headers["Authorization"] = f"Bearer {self.api_key}"

        async with websockets.connect(self.server_url, extra_headers=extra_headers) as websocket:
            request = {
                "method": "tools/call",
                "params": {
                    "name": self.tool_name,
                    "arguments": arguments
                }
            }

            await websocket.send(json.dumps(request))
            response = await websocket.recv()
            return json.loads(response)

    def to_openai_tool(self) -> Dict[str, Any]:
        """Convert MCP tool schema to OpenAI tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.tool_schema.get("description", f"MCP tool: {self.tool_name}"),
                "parameters": self.tool_schema.get("inputSchema", {
                    "type": "object",
                    "properties": {}
                })
            }
        }


class MCPClient:
    """Client for managing MCP server connections and tool execution."""

    def __init__(self):
        self.servers: Dict[str, MCPServer] = {}
        self.tools: Dict[str, MCPToolHandler] = {}

    async def initialize(self, db: AsyncSession):
        """
        Initialize client by loading all active MCP servers from database.

        Args:
            db: Database session
        """
        result = await db.execute(
            select(MCPServer).where(MCPServer.is_active == True)
        )
        servers = result.scalars().all()

        for server in servers:
            try:
                await self.connect_server(db, server)
            except Exception as e:
                logger.error(f"Failed to connect to MCP server {server.name}: {e}")
                server.connection_status = "error"
                server.error_message = str(e)
                await db.commit()

    async def connect_server(self, db: AsyncSession, server: MCPServer) -> List[str]:
        """
        Connect to an MCP server and discover available tools.

        Args:
            db: Database session
            server: MCPServer model instance

        Returns:
            List of discovered tool names
        """
        try:
            # Discover tools from server
            tools = await self._discover_tools(server)

            # Store server
            self.servers[server.name] = server

            # Create tool handlers
            discovered_tool_names = []
            for tool in tools:
                tool_name = tool.get("name")
                handler = MCPToolHandler(
                    server_url=server.url,
                    tool_name=tool_name,
                    tool_schema=tool,
                    protocol=server.protocol,
                    api_key=server.api_key
                )
                self.tools[tool_name] = handler
                discovered_tool_names.append(tool_name)

            # Update server status
            server.connection_status = "connected"
            server.last_connected = datetime.now(timezone.utc)
            server.error_message = None
            await db.commit()

            logger.info(f"Connected to MCP server {server.name}, discovered {len(discovered_tool_names)} tools")
            return discovered_tool_names

        except Exception as e:
            server.connection_status = "error"
            server.error_message = str(e)
            await db.commit()
            raise

    async def disconnect_server(self, db: AsyncSession, server_name: str):
        """
        Disconnect from an MCP server and remove its tools.

        Args:
            db: Database session
            server_name: Name of the server to disconnect
        """
        if server_name in self.servers:
            server = self.servers[server_name]

            # Remove all tools from this server
            tools_to_remove = [
                tool_name for tool_name, handler in self.tools.items()
                if handler.server_url == server.url
            ]
            for tool_name in tools_to_remove:
                del self.tools[tool_name]

            # Remove server
            del self.servers[server_name]

            # Update database
            result = await db.execute(
                select(MCPServer).where(MCPServer.name == server_name)
            )
            db_server = result.scalar_one_or_none()
            if db_server:
                db_server.connection_status = "disconnected"
                await db.commit()

            logger.info(f"Disconnected from MCP server {server_name}")

    async def _discover_tools(self, server: MCPServer) -> List[Dict[str, Any]]:
        """
        Discover available tools from an MCP server.

        Args:
            server: MCPServer instance

        Returns:
            List of tool schemas
        """
        if server.protocol == "http":
            return await self._discover_tools_http(server)
        elif server.protocol == "websocket":
            return await self._discover_tools_websocket(server)
        else:
            raise ValueError(f"Unsupported protocol: {server.protocol}")

    async def _discover_tools_http(self, server: MCPServer) -> List[Dict[str, Any]]:
        """Discover tools via HTTP."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Content-Type": "application/json"}
            if server.api_key:
                headers["Authorization"] = f"Bearer {server.api_key}"

            payload = {
                "method": "tools/list",
                "params": {}
            }

            response = await client.post(
                server.url,
                json=payload,
                headers=headers
            )
            response.raise_for_status()
            data = response.json()

            # MCP response format: {"tools": [...]}
            return data.get("tools", [])

    async def _discover_tools_websocket(self, server: MCPServer) -> List[Dict[str, Any]]:
        """Discover tools via WebSocket."""
        extra_headers = {}
        if server.api_key:
            extra_headers["Authorization"] = f"Bearer {server.api_key}"

        async with websockets.connect(server.url, extra_headers=extra_headers) as websocket:
            request = {
                "method": "tools/list",
                "params": {}
            }

            await websocket.send(json.dumps(request))
            response = await websocket.recv()
            data = json.loads(response)

            return data.get("tools", [])

    async def get_available_tools(
        self,
        enabled_tools: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all available MCP tools in OpenAI tool format.

        Args:
            enabled_tools: Optional list of tool names to filter by

        Returns:
            List of tools in OpenAI format
        """
        tools = []

        for tool_name, handler in self.tools.items():
            # Filter by enabled_tools if provided
            if enabled_tools is not None and tool_name not in enabled_tools:
                continue

            tools.append(handler.to_openai_tool())

        return tools

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Any:
        """
        Execute an MCP tool.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments

        Returns:
            Tool execution result

        Raises:
            ValueError: If tool not found
        """
        if tool_name not in self.tools:
            raise ValueError(f"MCP tool not found: {tool_name}")

        handler = self.tools[tool_name]
        return await handler.execute(arguments)

    def get_tool_names(self) -> List[str]:
        """Get list of all available tool names."""
        return list(self.tools.keys())


# Global MCP client instance
mcp_client = MCPClient()
