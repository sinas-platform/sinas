"""Poll provider-native batches (execution_mode="provider") to completion.

Runs as a scheduler interval job. For each unfinished provider-mode batch:
check the provider's batch status; once ended, write each result back as an
assistant message on its chat, mark the child Execution terminal, record
llm_usage, and hand off to batch_service.on_execution_terminated for the
aggregate status + batch callback.

Processing is idempotent: already-terminal executions are skipped, so a
crash mid-processing is repaired on the next tick.
"""
import logging
import uuid as uuid_lib
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import LLMProvider, LLMUsage
from app.models.batch import Batch
from app.models.chat import Message
from app.models.execution import Execution, ExecutionStatus

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    "succeeded": ExecutionStatus.COMPLETED,
    "errored": ExecutionStatus.FAILED,
    "expired": ExecutionStatus.FAILED,
    "cancelled": ExecutionStatus.CANCELLED,
}


async def poll_provider_batches() -> None:
    """Poll every unfinished provider-mode batch once."""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(Batch.id).where(
                Batch.execution_mode == "provider",
                Batch.finished_at.is_(None),
                Batch.provider_batch_id.isnot(None),
            )
        )
        batch_ids = [row[0] for row in rows]

    for batch_id in batch_ids:
        try:
            await _poll_one(batch_id)
        except Exception:
            logger.exception("Failed to poll provider batch %s", batch_id)


async def _poll_one(batch_id: uuid_lib.UUID) -> None:
    from app.providers import create_provider
    from app.services import batch_service

    async with AsyncSessionLocal() as db:
        batch = (
            await db.execute(select(Batch).where(Batch.id == batch_id))
        ).scalar_one_or_none()
        if not batch or batch.finished_at is not None:
            return

        row = await db.execute(
            select(LLMProvider).where(LLMProvider.id == batch.llm_provider_id)
        )
        provider_row = row.scalar_one_or_none()
        if not provider_row:
            logger.error(
                "Provider batch %s references missing LLM provider %s",
                batch.id, batch.llm_provider_id,
            )
            return
        provider = await create_provider(provider_name=provider_row.name, db=db)

        status = await provider.get_batch_status(batch.provider_batch_id)
        if not status.get("ended"):
            return

        results = await provider.fetch_batch_results(batch.provider_batch_id)
        results_by_id = {r["custom_id"]: r for r in results if r.get("custom_id")}

        exec_rows = await db.execute(
            select(Execution).where(Execution.batch_id == batch.id)
        )
        executions = exec_rows.scalars().all()

        # Model for usage attribution: same resolution as at submit time.
        from app.models.agent import Agent

        agent = await Agent.get_by_name(db, batch.target_namespace, batch.target_name)
        model = (agent.model if agent else None) or provider_row.default_model

        now = datetime.now(timezone.utc)
        processed = 0
        for execution in executions:
            if execution.status in (
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.CANCELLED,
            ):
                continue

            result = results_by_id.get(execution.execution_id)
            if result is None:
                # Ended batch with no verdict for this request (e.g. expired
                # OpenAI batch omits unprocessed items).
                execution.status = ExecutionStatus.FAILED
                execution.error = "missing from provider batch results"
            else:
                execution.status = _STATUS_MAP.get(
                    result["status"], ExecutionStatus.FAILED
                )
                if execution.status == ExecutionStatus.COMPLETED:
                    content = result.get("content") or ""
                    db.add(Message(
                        chat_id=execution.chat_id, role="assistant", content=content
                    ))
                    execution.output_data = {"final_message": content}
                    usage = result.get("usage") or {}
                    db.add(LLMUsage(
                        user_id=batch.user_id,
                        chat_id=execution.chat_id,
                        agent=f"{batch.target_namespace}/{batch.target_name}",
                        source="provider_batch",
                        provider_name=provider_row.name,
                        provider_type=provider_row.provider_type,
                        model=model,
                        prompt_tokens=usage.get("prompt_tokens", 0) or 0,
                        completion_tokens=usage.get("completion_tokens", 0) or 0,
                        total_tokens=usage.get("total_tokens", 0) or 0,
                        cache_read_tokens=usage.get("cache_read_tokens", 0) or 0,
                        cache_write_tokens=usage.get("cache_write_tokens", 0) or 0,
                        streamed=False,
                    ))
                else:
                    execution.error = result.get("error") or result["status"]

            execution.completed_at = now
            if execution.started_at:
                execution.duration_ms = int(
                    (now - execution.started_at).total_seconds() * 1000
                )
            processed += 1

        await db.commit()

        logger.info(
            "Provider batch %s (%s) ended: processed %d executions",
            batch.id, batch.provider_batch_id, processed,
        )

        # Derive terminal batch status + fire the aggregate callback.
        await batch_service.on_execution_terminated(db=db, batch_id=batch.id)
