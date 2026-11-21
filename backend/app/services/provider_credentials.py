"""A chat's per-run provider credentials: given, stored, inherited, resolved.

The request side of app/providers/run_credentials.py. A caller holding
`sinas.chats.assign_provider_credential:own` sends

    "provider_credentials": {"<provider name>": {"credential": "...", "expires_in": 86400}}

on chat create, invoke or message send; `{"<provider name>": null}` removes
the chat's credential for that provider. Validated whole before anything is
stored (403 without the permission, 400 `invalid_request` for a provider
that doesn't exist or hasn't enabled run credentials, an empty credential, or
an expires_in out of range), then each entry replaces the chat's credential
for its provider. Error messages never quote a credential.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_permission
from app.providers import run_credentials
from app.providers.errors import INVALID_REQUEST, LLMError

logger = logging.getLogger(__name__)

PERMISSION = "sinas.chats.assign_provider_credential:own"

# Statuses of a chat's credential for a provider at call time.
ACTIVE = "active"
MISSING = "missing"
EXPIRED = "expired"


def _invalid(message: str) -> LLMError:
    return LLMError(INVALID_REQUEST, message, status=400)


@dataclass(frozen=True)
class CredentialChange:
    """One validated entry: set (credential, expires_at) or remove (None)."""

    provider_name: str
    credential: Optional[str]
    expires_at: Optional[datetime]

    def __repr__(self) -> str:  # never the credential
        action = "remove" if self.credential is None else f"set until {self.expires_at}"
        return f"<CredentialChange {self.provider_name!r} {action}>"


def check_assign_permission(permissions: dict[str, bool], http_request: Any = None) -> None:
    """403 unless the caller may assign provider credentials to its chats."""
    from app.core.auth import set_permission_used

    if not check_permission(permissions, PERMISSION):
        if http_request is not None:
            set_permission_used(http_request, PERMISSION, has_perm=False)
        raise HTTPException(403, f"Not authorized to assign provider credentials ({PERMISSION})")


async def _provider_settings(db: AsyncSession, name: str) -> run_credentials.RunCredentialSettings:
    """The run credential settings of the active provider `name`; 400 when
    there is no such provider or it hasn't enabled run credentials."""
    from app.models.llm_provider import LLMProvider

    row = (
        await db.execute(
            select(LLMProvider).where(LLMProvider.name == name, LLMProvider.is_active == True)  # noqa: E712
        )
    ).scalar_one_or_none()
    if row is None:
        raise _invalid(f"provider_credentials: unknown LLM provider '{name}'")
    settings = run_credentials.settings_of(row.config or {}, row.provider_type)
    if not settings.enabled:
        raise _invalid(
            f"provider_credentials: LLM provider '{name}' does not accept a run credential "
            "(an admin can enable config.run_credential on it)"
        )
    return settings


async def parse_request(
    db: AsyncSession,
    raw: Any,
    permissions: dict[str, bool],
    http_request: Any = None,
) -> list[CredentialChange]:
    """The validated changes a request's `provider_credentials` asks for
    (none when it has none). Raises HTTPException 403 or LLMError 400;
    stores nothing."""
    if raw is None or raw == {}:
        return []
    check_assign_permission(permissions, http_request)
    if not isinstance(raw, dict):
        raise _invalid("provider_credentials must be an object of provider name to credential")
    now = datetime.now(UTC)
    changes: list[CredentialChange] = []
    for name, entry in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise _invalid("provider_credentials: a provider name is empty")
        await _provider_settings(db, name)
        if entry is None:
            changes.append(CredentialChange(name, None, None))
            continue
        if not isinstance(entry, dict):
            raise _invalid(
                f"provider_credentials['{name}'] must be an object with credential and expires_in, or null"
            )
        unknown = set(entry) - {"credential", "expires_in"}
        if unknown:
            raise _invalid(
                f"provider_credentials['{name}']: unknown key(s) "
                f"{', '.join(sorted(map(str, unknown)))}; allowed: credential, expires_in"
            )
        try:
            credential = run_credentials.check_credential(entry.get("credential"))
            expires_in = run_credentials.check_expires_in(entry.get("expires_in"))
        except ValueError as e:
            raise _invalid(f"provider_credentials['{name}']: {e}") from None
        changes.append(CredentialChange(name, credential, now + timedelta(seconds=expires_in)))
    return changes


async def apply_changes(
    db: AsyncSession, chat_id: Any, changes: list[CredentialChange], user_id: Any
) -> None:
    """Store `changes` on the chat (flushed, not committed): each set
    replaces the chat's credential for its provider, each null removes it."""
    if not changes:
        return
    from app.core.encryption import encryption_service
    from app.models.chat_provider_credential import ChatProviderCredential

    chat_uuid = uuid.UUID(str(chat_id))
    set_by = uuid.UUID(str(user_id)) if user_id else None
    for change in changes:
        if change.credential is None:
            await db.execute(
                delete(ChatProviderCredential).where(
                    ChatProviderCredential.chat_id == chat_uuid,
                    ChatProviderCredential.provider_name == change.provider_name,
                )
            )
            continue
        ciphertext = encryption_service.encrypt(change.credential)
        await _upsert(db, chat_uuid, change.provider_name, ciphertext, change.expires_at, set_by)
    await db.flush()


async def _upsert(
    db: AsyncSession,
    chat_id: uuid.UUID,
    provider_name: str,
    ciphertext: str,
    expires_at: datetime,
    set_by: Optional[uuid.UUID],
) -> None:
    from app.models.chat_provider_credential import ChatProviderCredential

    stmt = insert(ChatProviderCredential).values(
        id=uuid.uuid4(),
        chat_id=chat_id,
        provider_name=provider_name,
        ciphertext=ciphertext,
        expires_at=expires_at,
        set_by=set_by,
    )
    await db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_chat_provider_credentials_chat_provider",
            set_={
                "ciphertext": stmt.excluded.ciphertext,
                "expires_at": stmt.excluded.expires_at,
                "set_by": stmt.excluded.set_by,
                "updated_at": datetime.now(UTC),
            },
        )
    )


async def credentials_set(db: AsyncSession, chat_ids: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """Per chat id (str): the providers it has an unexpired credential for,
    with when each expires. Never the credential."""
    from app.models.chat_provider_credential import ChatProviderCredential

    ids = [uuid.UUID(str(c)) for c in chat_ids if c]
    out: dict[str, list[dict[str, Any]]] = {}
    now = datetime.now(UTC)
    for start in range(0, len(ids), 1000):  # a whole unpaginated chat list: bounded statements
        rows = (
            await db.execute(
                select(
                    ChatProviderCredential.chat_id,
                    ChatProviderCredential.provider_name,
                    ChatProviderCredential.expires_at,
                )
                .where(
                    ChatProviderCredential.chat_id.in_(ids[start:start + 1000]),
                    ChatProviderCredential.expires_at > now,
                )
                .order_by(ChatProviderCredential.provider_name)
            )
        ).all()
        for chat_id, provider_name, expires_at in rows:
            out.setdefault(str(chat_id), []).append({"provider": provider_name, "expires_at": expires_at})
    return out


async def resolve(db: Any, chat_id: Any, provider_name: str) -> tuple[Optional[str], str]:
    """(credential, status) of the chat's credential for a provider at call
    time: ACTIVE with the value, or MISSING / EXPIRED with None. One that
    can no longer be decrypted (ENCRYPTION_KEY changed) counts as missing."""
    from app.core.encryption import encryption_service
    from app.models.chat_provider_credential import ChatProviderCredential

    try:
        chat_uuid = uuid.UUID(str(chat_id))
    except (TypeError, ValueError):
        return None, MISSING
    row = (
        await db.execute(
            select(ChatProviderCredential.ciphertext, ChatProviderCredential.expires_at).where(
                ChatProviderCredential.chat_id == chat_uuid,
                ChatProviderCredential.provider_name == provider_name,
            )
        )
    ).one_or_none()
    if row is None:
        return None, MISSING
    ciphertext, expires_at = row
    if expires_at <= datetime.now(UTC):
        return None, EXPIRED
    try:
        return encryption_service.decrypt(ciphertext), ACTIVE
    except Exception:
        logger.warning(
            "The run credential of chat %s for LLM provider '%s' could not be decrypted",
            chat_id, provider_name,
        )
        return None, MISSING


async def inherit(db: AsyncSession, parent_chat_id: Any, child_chat_id: Any) -> int:
    """Make a sub-agent's chat hold exactly the parent chat's unexpired
    credentials, so the sub-agent's calls present what the parent's do now:
    a credential the parent rotated is replaced, one it removed (or let
    expire) is removed. Flushed, not committed. Returns how many it holds."""
    from app.models.chat_provider_credential import ChatProviderCredential

    if not parent_chat_id or not child_chat_id:
        return 0
    rows = (
        await db.execute(
            select(ChatProviderCredential).where(
                ChatProviderCredential.chat_id == uuid.UUID(str(parent_chat_id)),
                ChatProviderCredential.expires_at > datetime.now(UTC),
            )
        )
    ).scalars().all()
    child = uuid.UUID(str(child_chat_id))
    await db.execute(
        delete(ChatProviderCredential).where(
            ChatProviderCredential.chat_id == child,
            ChatProviderCredential.provider_name.notin_([row.provider_name for row in rows]),
        )
    )
    for row in rows:
        await _upsert(db, child, row.provider_name, row.ciphertext, row.expires_at, row.set_by)
    await db.flush()
    return len(rows)


async def refuse_request_credential_handoff(db: AsyncSession, agent: Any) -> Optional[str]:
    """Why delegating to `agent` must be refused, or None. A credential the
    OpenAI-compatible adapter got for one request (X-Sinas-Provider-Credential)
    is stored nowhere, so a sub-agent's job (run by a worker) can't present
    it: delegating to a sub-agent on that provider is refused outright,
    rather than its calls failing or falling back to the configured key."""
    from app.models.llm_provider import LLMProvider
    from app.providers.run_credentials import REQUEST_CREDENTIAL

    request_credential = REQUEST_CREDENTIAL.get()
    if request_credential is None:
        return None
    if agent.llm_provider_id:
        stmt = select(LLMProvider.name).where(LLMProvider.id == agent.llm_provider_id)
    else:
        stmt = select(LLMProvider.name).where(
            LLMProvider.is_default == True, LLMProvider.is_active == True  # noqa: E712
        )
    name = (await db.execute(stmt)).scalar_one_or_none()
    if name != request_credential[0]:
        return None
    return (
        f"Sub-agent '{agent.namespace}/{agent.name}' uses LLM provider '{name}', whose credential "
        "for this run was given for one request only (X-Sinas-Provider-Credential) and can't be "
        "passed to a sub-agent. Give the chat its credential with provider_credentials instead."
    )


async def delete_expired(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Delete every expired credential (the caller commits). Returns how many."""
    from app.models.chat_provider_credential import ChatProviderCredential

    result = await db.execute(
        delete(ChatProviderCredential).where(
            ChatProviderCredential.expires_at <= (now or datetime.now(UTC))
        )
    )
    return result.rowcount or 0


async def chat_has_credentials(db: AsyncSession, chat_ids: list[Any]) -> bool:
    """Whether any of these chats carries a run credential (expired or not)."""
    from app.models.chat_provider_credential import ChatProviderCredential

    ids = [uuid.UUID(str(c)) for c in chat_ids if c]
    if not ids:
        return False
    found = (
        await db.execute(
            select(ChatProviderCredential.id).where(ChatProviderCredential.chat_id.in_(ids)).limit(1)
        )
    ).first()
    return found is not None
