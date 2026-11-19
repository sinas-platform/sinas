"""Poll provider-native batches (execution_mode="provider") to completion.

Runs as a scheduler interval job. For each unfinished provider-mode batch:
check the provider's batch status; once ended, write each result back as an
assistant message on its chat, mark the child Execution terminal, record
llm_usage (marked batch, priced at batch rates), and hand off to
batch_service.on_execution_terminated for the aggregate status + batch
callback.

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
from app.providers.tracking import raw_finish_reason, normalize_finish_reason
from app.services.llm_pricing import price_usage

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
        # The batch is the submitter's: its identity headers and customer
        # (app/providers/attribution.py), as at submission.
        attribution_context = {
            "user_id": str(batch.user_id) if batch.user_id else None,
            "agent": f"{batch.target_namespace}/{batch.target_name}",
            "source": "provider_batch",
        }
        # Status and results of a batch already sent: the configured key,
        # as it was sent with (batches never carry a run credential).
        provider = await create_provider(
            provider_name=provider_row.name,
            db=db,
            usage_context=attribution_context,
            run_credentials=False,
        )

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

        # Each result is attributed by its own chat's snapshot (taken when the
        # batch created it).
        from app.core.config import settings
        from app.models.chat import Chat
        from app.providers.attribution import clean_value

        customer_field = (settings.usage_customer_field or "").strip()
        customers: dict = {}
        chat_ids = [e.chat_id for e in executions if e.chat_id]
        if customer_field and chat_ids:
            for chat_id, snapshot in (
                await db.execute(select(Chat.id, Chat.attribution).where(Chat.id.in_(chat_ids)))
            ).all():
                if isinstance(snapshot, dict):
                    customers[chat_id] = clean_value(snapshot.get(customer_field), max_length=255)

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
                    values = {
                        "user_id": batch.user_id,
                        "chat_id": execution.chat_id,
                        "agent": f"{batch.target_namespace}/{batch.target_name}",
                        "source": "provider_batch",
                        "provider_name": provider_row.name,
                        "provider_type": provider_row.provider_type,
                        "model": model,
                        "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
                        "completion_tokens": usage.get("completion_tokens", 0) or 0,
                        "total_tokens": usage.get("total_tokens", 0) or 0,
                        "cache_read_tokens": usage.get("cache_read_tokens", 0) or 0,
                        "cache_write_tokens": usage.get("cache_write_tokens", 0) or 0,
                        "streamed": False,
                        "finish_reason": normalize_finish_reason(result.get("finish_reason")),
                        "finish_reason_raw": raw_finish_reason(result.get("finish_reason")),
                        # Priced at the provider's batch rates
                        "batch": True,
                        "customer": customers.get(execution.chat_id),
                    }
                    await price_usage(db, values)
                    db.add(LLMUsage(**values))
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
