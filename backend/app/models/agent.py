import uuid
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk
from .mixins import PermissionMixin


class Agent(Base, PermissionMixin):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("namespace", "name", name="uix_agent_namespace_name"),)

    id: Mapped[uuid_pk]
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id"), index=True
    )  # NULL = workspace-wide
    namespace: Mapped[str] = mapped_column(
        String(255), nullable=False, default="default", index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # LLM Configuration
    llm_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("llm_providers.id"), index=True
    )  # NULL = use default provider
    model: Mapped[Optional[str]] = mapped_column(String(100))  # NULL = use provider's default model
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    max_tokens: Mapped[Optional[int]] = mapped_column(Integer)  # NULL = use provider's default

    # System Prompt (supports Jinja2 templates)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text)

    # Input/Output Schemas (like Functions)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # JSON Schema defining what input variables are required/allowed
    # Example: {"type": "object", "properties": {"tag_definitions": {"type": "array"}}}

    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # JSON Schema defining expected response structure
    # Empty dict {} means no schema enforcement

    # Few-shot learning: initial message history
    initial_messages: Mapped[Optional[list[dict[str, str]]]] = mapped_column(JSON)
    # Example: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]

    # Tool access
    enabled_functions: Mapped[list[str]] = mapped_column(
        JSON, default=list
    )  # List of "namespace/name" strings
    enabled_mcp_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled_agents: Mapped[list[str]] = mapped_column(JSON, default=list)  # List of agent names
    enabled_skills: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )  # List of {"skill": "namespace/name", "preload": bool}
    function_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # {"namespace/name": {"param": "value or {{template}}"}}
    mcp_tool_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # State access
    state_namespaces_readonly: Mapped[list[str]] = mapped_column(
        JSON, default=list
    )  # Readonly state namespaces
    state_namespaces_readwrite: Mapped[list[str]] = mapped_column(
        JSON, default=list
    )  # Read-write state namespaces

    # Collection access
    enabled_collections: Mapped[list[str]] = mapped_column(
        JSON, default=list
    )  # List of "namespace/name" collection references

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]
    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)

    # Relationships
    user: Mapped[Optional["User"]] = relationship("User", back_populates="agents")
    llm_provider: Mapped[Optional["LLMProvider"]] = relationship("LLMProvider")
    chats: Mapped[list["Chat"]] = relationship("Chat", back_populates="agent")

    @classmethod
    async def get_by_name(
        cls, db: AsyncSession, namespace: str, name: str, user_id: Optional[uuid.UUID] = None
    ) -> Optional["Agent"]:
        """Get agent by namespace and name, optionally filtered by user_id for ownership."""
        query = select(cls).where(
            cls.namespace == namespace, cls.name == name, cls.is_active == True
        )
        if user_id is not None:
            query = query.where(cls.user_id == user_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()
