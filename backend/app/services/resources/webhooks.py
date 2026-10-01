"""Webhooks applier."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.agent import Agent
from app.models.function import Function
from app.models.webhook import HTTPMethod, Webhook
from app.schemas.spec.webhook import WebhookDedupSpec, WebhookSpec
from app.services.resources.base import ApplyContext, ReferenceNotFound, ResourceApplier


def _stored_dedup(dedup: Any) -> WebhookDedupSpec | None:
    if not dedup:
        return None
    from app.services.dedup_service import _dedup_ttl

    # Rows written by config apply before #164 hold `ttlSeconds`; reading
    # both spellings keeps them from showing a phantom TTL change.
    return WebhookDedupSpec.model_construct(key=dedup.get("key"), ttl_seconds=_dedup_ttl(dedup))


class WebhookApplier(ResourceApplier[WebhookSpec]):
    kind = "webhooks"
    # The REST API has always answered "Webhook path 'x' already exists".
    label = "Webhook path"
    noun = "webhook"
    config_section = "webhooks"
    spec_model = WebhookSpec
    model = Webhook
    reference_fields = ("target_type", "target_namespace", "target_name")
    keep_unless_declared = ("is_active",)

    def key_of(self, spec: WebhookSpec) -> str:
        return spec.path

    def key_of_row(self, row: Webhook) -> str:
        return row.path

    def config_key(self, item: Any) -> str:
        return item.path

    async def find(self, ctx: ApplyContext, key: str) -> Webhook | None:
        # Paths are globally unique (ix_webhooks_path). The REST API used to
        # check only the caller's own paths, so another user's path was a 500.
        return (
            await ctx.db.execute(
                select(Webhook)
                .where(Webhook.path == key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Webhook) -> WebhookSpec:
        target_type = getattr(row, "target_type", None) or "function"
        namespace = getattr(row, f"{target_type}_namespace", None) or "default"
        method = getattr(row, "http_method", None) or "POST"
        return WebhookSpec.model_construct(
            path=row.path,
            target_type=target_type,
            target_namespace=namespace,
            target_name=getattr(row, f"{target_type}_name", None),
            message_template=row.message_template,
            session_key_template=row.session_key_template,
            http_method=str(getattr(method, "value", method)),
            description=row.description,
            default_values=row.default_values or {},
            is_active=getattr(row, "is_active", True) is not False,
            requires_auth=row.requires_auth is not False,
            response_mode=getattr(row, "response_mode", None) or "sync",
            dedup=_stored_dedup(getattr(row, "dedup", None)),
        )

    def new_row(self, spec: WebhookSpec, ctx: ApplyContext) -> Webhook:
        return Webhook(user_id=uuid.UUID(str(ctx.owner_user_id)))

    def write_fields(self, row: Webhook, spec: WebhookSpec) -> None:
        row.path = spec.path
        row.target_type = spec.target_type
        # Only the target's own reference is stored. Stale references for
        # other target types let a later type switch skip every check.
        for target_type in ("function", "agent", "pipeline"):
            own = spec.target_type == target_type
            namespace = spec.target_namespace if own else None
            if target_type == "function":
                namespace = namespace or "default"  # NOT NULL column
            setattr(row, f"{target_type}_namespace", namespace)
            setattr(row, f"{target_type}_name", spec.target_name if own else None)
        row.message_template = spec.message_template
        row.session_key_template = spec.session_key_template
        row.http_method = HTTPMethod(spec.http_method)
        row.description = spec.description
        row.default_values = dict(spec.default_values or {})
        row.is_active = spec.is_active
        row.requires_auth = spec.requires_auth
        row.response_mode = spec.response_mode
        row.dedup = (
            {"key": spec.dedup.key, "ttl_seconds": spec.dedup.ttl_seconds} if spec.dedup else None
        )

    async def check_references(self, spec: WebhookSpec, ctx: ApplyContext) -> None:
        """The REST API's rules and messages, now on every channel (config
        apply relied on a parser pre-pass that `force=true` skips). Who may
        run the target is a permission question, answered at the API
        boundary and again at execution time."""
        ns, name, db = spec.target_namespace, spec.target_name, ctx.db
        # A preview accepts a target the same config declares — active, where
        # the checks below require it (functions and agents; pipelines need
        # only exist).
        kind = {"function": "functions", "agent": "agents", "pipeline": "pipelines"}
        if ctx.declared(kind[spec.target_type], f"{ns}/{name}", active=spec.target_type != "pipeline"):
            return
        if spec.target_type == "function":
            scope = ctx.reference_scope_user_id
            if not await Function.get_by_name(db, ns, name, uuid.UUID(str(scope)) if scope else None):
                raise ReferenceNotFound(f"Function '{ns}.{name}' not found")
        elif spec.target_type == "pipeline":
            from app.models.pipeline import Pipeline

            if not await Pipeline.get_by_name(db, ns, name):
                raise ReferenceNotFound(f"Pipeline '{ns}/{name}' not found")
        elif not await Agent.get_by_name(db, ns, name):
            raise ReferenceNotFound(f"Agent '{ns}/{name}' not found")
