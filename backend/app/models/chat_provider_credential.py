"""A chat's own credential for an LLM provider (a per-run credential).

Given by the service that starts a run (`provider_credentials` on chat
create, invoke and message send) and presented, in place of the provider's
configured key or in a header of its own, on every LLM call the chat makes
(app/providers/run_credentials.py). Encrypted at rest with the platform's
ENCRYPTION_KEY, like LLMProvider.api_key; never returned, logged or copied
anywhere but into a sub-agent's chat. Expired rows are treated as absent and
deleted by the scheduler (cleanup_expired_provider_credentials).
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import GUID, Base, created_at, updated_at, uuid_pk


class ChatProviderCredential(Base):
    __tablename__ = "chat_provider_credentials"

    id: Mapped[uuid_pk]
    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The LLM provider's name (llm_providers.name), as the caller gave it.
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # EncryptionService ciphertext of the credential.
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # Who set it: the user the request ran as (a sub-agent's copy keeps it).
    set_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    __table_args__ = (
        UniqueConstraint("chat_id", "provider_name", name="uq_chat_provider_credentials_chat_provider"),
    )

    def __repr__(self) -> str:  # never the ciphertext
        return (
            f"<ChatProviderCredential chat={self.chat_id} provider={self.provider_name!r} "
            f"expires_at={self.expires_at}>"
        )
