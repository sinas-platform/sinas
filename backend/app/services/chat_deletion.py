"""Hard-delete chats and the rows that hang off them.

Shared by the expired-chat cleanup and the chat retention job. The children
go first, explicitly: in a migrated database `messages`,
`pending_tool_approvals`, `pending_completions` and `executions` reference
`chats` with NO ACTION (migration 54056f649874 dropped the cascades
cd25b0f773ad had added), `tool_call_results` and workbench collections
have no foreign key at all, so a bare `DELETE FROM chats` either fails or
leaves orphans. Doing it here means neither caller depends on which
ON DELETE rules a given database ended up with. See
docs/design/chat-retention.md, "What references a chat".
"""

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat, Message
from app.models.chat_provider_credential import ChatProviderCredential
from app.models.chat_wakeup import ChatWakeup
from app.models.execution import Execution
from app.models.mcp_task import McpTask
from app.models.pending_approval import PendingToolApproval
from app.models.pending_completion import PendingCompletion
from app.models.tool_call_result import ToolCallResult

logger = logging.getLogger(__name__)


async def delete_workbenches_for_chats(db: AsyncSession, chat_ids: Sequence[Any]) -> list[str]:
    """Delete the workbench collections of `chat_ids` (rows only; the
    caller commits) and return the storage paths of their file versions."""
    from app.models.file import Collection, File, FileVersion

    names = [str(cid) for cid in chat_ids]
    workbench_ids = (
        await db.execute(
            select(Collection.id).where(
                Collection.kind == "workbench",
                Collection.namespace == "_chat",
                Collection.name.in_(names),
            )
        )
    ).scalars().all()
    if not workbench_ids:
        return []
    paths = (
        await db.execute(
            select(FileVersion.storage_path)
            .join(File, FileVersion.file_id == File.id)
            .where(File.collection_id.in_(workbench_ids))
        )
    ).scalars().all()
    await db.execute(delete(Collection).where(Collection.id.in_(workbench_ids)))
    return sorted(set(paths))


async def remove_stored_files(paths: Sequence[str]) -> None:
    """Best effort: a stale blob is a leak, never a reason to keep a chat."""
    if not paths:
        return
    from app.services.file_storage import get_storage

    storage = get_storage()
    for path in paths:
        try:
            await storage.delete(path)
        except Exception as e:  # pragma: no cover - storage-specific
            logger.warning("Could not remove stored file %s: %s", path, e)


async def delete_chats_and_rows(db: AsyncSession, chat_ids: Sequence[Any]) -> tuple[int, list[str]]:
    """Delete `chat_ids` with everything they own, children first. The
    caller decides which chats may go (and locks them) and commits; nothing
    is committed here. Function execution records outlive the chat (their
    own log), unlinked; `llm_usage` (no foreign key) is left alone.

    Returns (messages deleted, storage paths of the chats' workbench files
    to remove with `remove_stored_files` after the commit)."""
    ids = list(chat_ids)
    if not ids:
        return 0, []
    # pending_tool_approvals reference messages too: before the messages.
    await db.execute(delete(PendingToolApproval).where(PendingToolApproval.chat_id.in_(ids)))
    await db.execute(delete(PendingCompletion).where(PendingCompletion.chat_id.in_(ids)))
    await db.execute(update(Execution).where(Execution.chat_id.in_(ids)).values(chat_id=None))
    await db.execute(delete(ToolCallResult).where(ToolCallResult.chat_id.in_(ids)))
    await db.execute(delete(ChatWakeup).where(ChatWakeup.chat_id.in_(ids)))
    await db.execute(delete(McpTask).where(McpTask.chat_id.in_(ids)))
    await db.execute(delete(ChatProviderCredential).where(ChatProviderCredential.chat_id.in_(ids)))
    paths = await delete_workbenches_for_chats(db, ids)
    messages = (await db.execute(delete(Message).where(Message.chat_id.in_(ids)))).rowcount or 0
    await db.execute(delete(Chat).where(Chat.id.in_(ids)))
    return messages, paths
