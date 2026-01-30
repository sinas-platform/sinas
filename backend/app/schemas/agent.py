"""Agent schemas."""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid


class AgentCreate(BaseModel):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r'^[a-zA-Z][a-zA-Z0-9_-]*$')
    name: str = Field(..., min_length=1, max_length=255, pattern=r'^[a-zA-Z][a-zA-Z0-9 _-]*$')
    description: Optional[str] = None
    llm_provider_id: Optional[uuid.UUID] = None  # NULL = use default provider
    model: Optional[str] = None  # NULL = use provider's default model
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None  # NULL = use provider's default
    system_prompt: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    initial_messages: Optional[List[Dict[str, str]]] = None
    enabled_functions: Optional[List[str]] = None  # List of "namespace/name" strings
    enabled_mcp_tools: Optional[List[str]] = None
    enabled_agents: Optional[List[str]] = None  # List of agent names that can be called as tools
    function_parameters: Optional[Dict[str, Any]] = None  # {"namespace/name": {"param": "value or {{template}}"}}
    mcp_tool_parameters: Optional[Dict[str, Any]] = None
    state_namespaces_readonly: Optional[List[str]] = None  # Readonly state namespaces
    state_namespaces_readwrite: Optional[List[str]] = None  # Read-write state namespaces


class AgentUpdate(BaseModel):
    namespace: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r'^[a-zA-Z][a-zA-Z0-9_-]*$')
    name: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r'^[a-zA-Z][a-zA-Z0-9 _-]*$')
    description: Optional[str] = None
    llm_provider_id: Optional[uuid.UUID] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    initial_messages: Optional[List[Dict[str, str]]] = None
    enabled_functions: Optional[List[str]] = None  # List of "namespace/name" strings
    enabled_mcp_tools: Optional[List[str]] = None
    enabled_agents: Optional[List[str]] = None  # List of agent names that can be called as tools
    function_parameters: Optional[Dict[str, Any]] = None  # {"namespace/name": {"param": "value or {{template}}"}}
    mcp_tool_parameters: Optional[Dict[str, Any]] = None
    state_namespaces_readonly: Optional[List[str]] = None
    state_namespaces_readwrite: Optional[List[str]] = None
    is_active: Optional[bool] = None


class AgentResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    namespace: str
    name: str
    description: Optional[str]
    llm_provider_id: Optional[uuid.UUID]
    model: Optional[str]
    temperature: float
    max_tokens: Optional[int]
    system_prompt: Optional[str]
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    initial_messages: Optional[List[Dict[str, str]]]
    enabled_functions: List[str]  # List of "namespace/name" strings
    enabled_mcp_tools: List[str]
    enabled_agents: List[str]  # List of agent names that can be called as tools
    function_parameters: Dict[str, Any]  # {"namespace/name": {"param": "value or {{template}}"}}
    mcp_tool_parameters: Dict[str, Any]
    state_namespaces_readonly: List[str]
    state_namespaces_readwrite: List[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
