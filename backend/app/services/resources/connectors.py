"""Connectors applier."""

from __future__ import annotations

import uuid
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import delete, select

from app.models.connector import Connector
from app.models.connector_oauth_token import ConnectorOAuthToken
from app.schemas.spec.connector import ConnectorSpec
from app.services.resources.base import ApplyContext, ResourceApplier
from app.services.connector_service import oauth_identity
from app.services.resources.history import redact

# Where a user's OAuth tokens are sent or were issued (connector_service). When
# one changes, the stored tokens belong to another identity provider (or app):
# a refresh would post the old refresh token, with the client secret, to the
# new token URL.
# Auth URLs that can carry credentials (userinfo, query) — redacted in history.
_AUTH_URLS = ("token_url", "authorize_url")


def _without_none(value: Any) -> Any:
    """Stored auth dicts differ by channel (REST kept explicit nulls, config
    stripped them); read both the same way. Only auth is normalised: an
    operation's parameter schema may legitimately hold nulls."""
    if isinstance(value, dict):
        return {k: _without_none(v) for k, v in value.items() if v is not None}
    return value


def _redacted_url(url: Optional[str]) -> Optional[str]:
    """A URL with its credentials redacted: the whole userinfo (a token often
    sits in the username slot, e.g. https://<token>@host) and the query (API
    keys as ?key=, Azure-style ?code=). Works on relative operation paths."""
    if not url:
        return url
    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        netloc = f"{redact(userinfo)}@{host}"
    query = redact(parts.query) if parts.query else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


class ConnectorApplier(ResourceApplier[ConnectorSpec]):
    kind = "connectors"
    label = "Connector"
    noun = "connector"
    config_section = "connectors"
    spec_model = ConnectorSpec
    model = Connector
    keep_unless_declared = ("is_active",)

    def key_of(self, spec: ConnectorSpec) -> str:
        return spec.key

    def key_of_row(self, row: Connector) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Connector | None:
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Connector)
                .where(Connector.namespace == namespace, Connector.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Connector) -> ConnectorSpec:
        auth = _without_none(row.auth or {"type": "none"})
        paths = auth.get("token_response_paths")
        retry = row.retry or {}
        return ConnectorSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            description=row.description or None,
            base_url=row.base_url,
            auth=_construct_auth(auth, paths),
            headers=dict(row.headers or {}),
            retry=_construct("ConnectorRetrySpec", {
                "max_attempts": retry.get("max_attempts", 1),
                "backoff": retry.get("backoff", "none"),
            }),
            timeout_seconds=row.timeout_seconds,
            operations=[_construct_operation(op) for op in (row.operations or [])],
            is_active=row.is_active is not False,
        )

    def new_row(self, spec: ConnectorSpec, ctx: ApplyContext) -> Connector:
        return Connector(user_id=uuid.UUID(str(ctx.owner_user_id)))

    async def write_row(self, row: Connector, spec: ConnectorSpec, ctx: ApplyContext) -> None:
        before = _without_none(row.auth or {}) if row.id is not None else None
        row.namespace = spec.namespace
        row.name = spec.name
        row.description = spec.description
        row.base_url = spec.base_url
        # Stored snake_case, without nulls — what the runtime reads.
        row.auth = spec.auth.model_dump(mode="json", exclude_none=True)
        row.headers = dict(spec.headers)
        row.retry = spec.retry.model_dump(mode="json")
        row.timeout_seconds = spec.timeout_seconds
        row.operations = [op.model_dump(mode="json") for op in spec.operations]
        row.is_active = spec.is_active
        if before is not None and oauth_identity(before) != oauth_identity(row.auth):
            # Users sign in again, against the new provider or app.
            await ctx.db.execute(
                delete(ConnectorOAuthToken).where(ConnectorOAuthToken.connector_id == row.id)
            )

    # ---- history: no secret values ------------------------------------------

    def history_spec(self, spec: ConnectorSpec) -> dict[str, Any]:
        """Headers and token parameters are where API keys and assertions end
        up, and a URL can carry credentials; history shows them redacted (a
        changed value still shows as a change). `auth.secret` is a Secret's
        name, not its value, and stays readable."""
        state = spec.canonical()
        state["headers"] = {key: redact(value) for key, value in (spec.headers or {}).items()}
        token_params = (spec.auth.token_params if spec.auth else None) or None
        if token_params:
            state["auth"]["token_params"] = {k: redact(v) for k, v in token_params.items()}
        state["base_url"] = _redacted_url(spec.base_url)
        for field in _AUTH_URLS:
            if spec.auth and getattr(spec.auth, field):
                state["auth"][field] = _redacted_url(getattr(spec.auth, field))
        for op_state, op in zip(state.get("operations") or [], spec.operations or []):
            op_state["path"] = _redacted_url(op.path)
        return state

    def secret_values(self, spec: ConnectorSpec) -> dict[str, Any]:
        secrets: dict[str, Any] = {}
        if spec.headers:
            secrets["headers"] = dict(spec.headers)
        if spec.auth and spec.auth.token_params:
            secrets["token_params"] = dict(spec.auth.token_params)
        if _redacted_url(spec.base_url) != spec.base_url:
            secrets["base_url"] = spec.base_url
        for field in _AUTH_URLS:
            url = getattr(spec.auth, field) if spec.auth else None
            if _redacted_url(url) != url:
                secrets[field] = url
        paths = {
            str(index): op.path
            for index, op in enumerate(spec.operations or [])
            if _redacted_url(op.path) != op.path
        }
        if paths:
            secrets["operation_paths"] = paths
        return secrets

    def with_secrets(self, state: dict[str, Any], secrets: dict[str, Any]) -> dict[str, Any]:
        state = {**state, "auth": dict(state.get("auth") or {})}
        if "headers" in secrets:
            state["headers"] = secrets["headers"]
        if "token_params" in secrets:
            state["auth"]["token_params"] = secrets["token_params"]
        if "base_url" in secrets:
            state["base_url"] = secrets["base_url"]
        for field in _AUTH_URLS:
            if field in secrets:
                state["auth"][field] = secrets[field]
        if "operation_paths" in secrets:
            operations = [dict(op) for op in state.get("operations") or []]
            for index, path in secrets["operation_paths"].items():
                operations[int(index)]["path"] = path
            state["operations"] = operations
        return state


def _construct(name: str, values: dict[str, Any]) -> Any:
    from app.schemas.spec import connector as specs

    return getattr(specs, name).model_construct(**values)


def _construct_auth(auth: dict[str, Any], paths: Any) -> Any:
    values = {k: v for k, v in auth.items() if k != "token_response_paths"}
    values.setdefault("type", "none")
    if isinstance(paths, dict) and any(paths.values()):
        values["token_response_paths"] = _construct("TokenResponsePathsSpec", dict(paths))
    else:
        values.pop("token_response_paths", None)
    return _construct("ConnectorAuthSpec", values)


def _construct_operation(op: dict[str, Any]) -> Any:
    return _construct("ConnectorOperationSpec", {
        "name": op.get("name"),
        "method": (op.get("method") or "").upper(),
        "path": op.get("path"),
        "description": op.get("description"),
        "parameters": op.get("parameters") if op.get("parameters") is not None
        else {"type": "object", "properties": {}},
        "request_body_mapping": op.get("request_body_mapping") or "json",
        "response_mapping": op.get("response_mapping") or "json",
    })
