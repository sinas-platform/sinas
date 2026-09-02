"""Deferred tool round checkpoints — pending completions.

Generalization of the suspend-on-delegate checkpoint (issue #90): a tool
round can suspend with one or more pending completions, each owned by a
completer (sub-agent, human input, …), and resumes when the last one
arrives. See `app/services/deferred_completions.py` for the mechanics.
"""
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, uuid_pk


class PendingCompletion(Base):
    """A tool round suspended on results that will arrive later.

    One row per suspended round (covers every deferred call in that round,
    across completer kinds). Formerly `PendingDelegation` /
    `pending_delegations` — sub-agent delegation is now one completer kind
    among several.
    """

    __tablename__ = "pending_completions"

    id: Mapped[uuid_pk]
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # The suspended round's SSE stream channel — completions publish
    # progressive tool_end events here, and the resume job publishes here
    # unless the completing caller supplies a fresh channel.
    channel_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # {tool_call_id: {"completer": kind, ...completer payload,
    #                 "expires_at": iso-datetime?}} still outstanding.
    # Rows written before the unification lack "completer" — they default
    # to "sub_agent" in code.
    pending: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # {tool_call_id: result content} collected from finished completions.
    results: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Outstanding count; decremented atomically as completions land. 0 → resume.
    remaining: Mapped[int] = mapped_column(Integer, nullable=False)

    # Provider/model/tools/etc. needed by the follow-up LLM turn — same shape
    # the approval flow stashes (see PendingToolApproval.conversation_context).
    conversation_context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Earliest per-entry deadline (the sweep's index); NULL = nothing in this
    # round expires. Individual deadlines live on the entries themselves.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    created_at: Mapped[created_at]
