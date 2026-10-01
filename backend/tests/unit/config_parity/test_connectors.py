"""Connectors: one applier for every write channel.

The REST API and config apply used to keep separate field maps (four copies
of the auth fields), different validation (config accepted anything; a typo
in the auth type sent requests unauthenticated), and a re-apply re-enabled a
connector someone had disabled. Change history must never show secret
values, yet a restore must bring a connector back exactly.
"""

import json
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_revision import ConfigRevision
from app.models.connector import Connector
from app.models.connector_oauth_token import ConnectorOAuthToken
from app.schemas.config import SinasConfig
from app.schemas.spec.connector import ConnectorSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers

FIELDS = (
    "description", "base_url", "auth", "headers", "retry", "timeout_seconds", "operations",
    "is_active",
)
API_KEY = "sk-live-do-not-show"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _yaml_connector(name: str, **extra) -> dict:
    return {
        "namespace": "crm",
        "name": name,
        "description": "CRM",
        "baseUrl": "https://api.example.com/v1",
        "auth": {
            "type": "oauth2_client_credentials", "tokenUrl": "https://auth.example.com/token",
            "clientId": "sinas", "secret": "CRM_SECRET", "tokenParams": {"audience": "aud-secret-xyz"},
            "tokenResponsePaths": {"accessToken": "data.token"},
        },
        "headers": {"X-Api-Key": API_KEY},
        "retry": {"maxAttempts": 3, "backoff": "exponential"},
        "timeoutSeconds": 20,
        "operations": [{
            "name": "list_contacts", "method": "get", "path": "/contacts",
            "parameters": {"type": "object", "properties": {"q": {"type": "string", "default": None}}},
            "requestBodyMapping": "query",
        }],
        **extra,
    }


def _rest_connector(name: str, **extra) -> dict:
    return {
        "namespace": "crm",
        "name": name,
        "description": "CRM",
        "base_url": "https://api.example.com/v1",
        "auth": {
            "type": "oauth2_client_credentials", "token_url": "https://auth.example.com/token",
            "client_id": "sinas", "secret": "CRM_SECRET", "token_params": {"audience": "aud-secret-xyz"},
            "token_response_paths": {"access_token": "data.token"},
        },
        "headers": {"X-Api-Key": API_KEY},
        "retry": {"max_attempts": 3, "backoff": "exponential"},
        "timeout_seconds": 20,
        "operations": [{
            "name": "list_contacts", "method": "GET", "path": "/contacts",
            "parameters": {"type": "object", "properties": {"q": {"type": "string", "default": None}}},
            "request_body_mapping": "query",
        }],
        **extra,
    }


def _config(*connectors: dict) -> SinasConfig:
    return SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "cfg"},
        "spec": {"connectors": list(connectors)},
    })


async def _apply(db, owner, *connectors, managed_by="config"):
    svc = ConfigApplyService(
        db, "cfg", owner_user_id=str(owner.id), managed_by=managed_by, auto_commit=False
    )
    return svc, await svc.apply_config(_config(*connectors))


async def _row(db: AsyncSession, name: str) -> Connector | None:
    return (
        await db.execute(select(Connector).where(Connector.namespace == "crm", Connector.name == name))
    ).scalar_one_or_none()


async def _revisions(db: AsyncSession, name: str) -> list[ConfigRevision]:
    return list((await db.execute(
        select(ConfigRevision)
        .where(ConfigRevision.resource_kind == "connectors", ConfigRevision.resource_key == f"crm/{name}")
        .order_by(ConfigRevision.id)
    )).scalars())


# ------------------------------------------------------------------ spec


class TestConnectorSpec:
    def test_rest_and_config_shapes_are_the_same_spec(self):
        config = ConnectorSpec.model_validate(_yaml_connector("x"))
        rest = ConnectorSpec.model_validate(_rest_connector("x"))
        assert config == rest

    @pytest.mark.parametrize("bad", [
        {"auth": {"type": "oauth2"}},  # silently unauthenticated
        {"auth": {"type": "oauth2_client_credentials", "clientId": "c"}},  # no token URL
        {"retry": {"maxAttempts": 0}},  # every call failed with a TypeError
        {"timeoutSeconds": 0},  # every call timed out
        {"namespace": "team/api"},  # no "ns/name" reference could reach it
        {"operations": [{"name": "o", "method": "GET", "path": "/", "requestBodyMapping": "body"}]},
    ])
    def test_config_now_refuses_what_never_worked(self, bad):
        with pytest.raises(ValidationError):
            ConnectorSpec.model_validate({**_yaml_connector("x"), **bad})

    def test_empty_token_response_paths_mean_none(self):
        """{} was stored as {} but read back as None: a change on every apply."""
        spec = ConnectorSpec.model_validate({
            **_yaml_connector("x"),
            "auth": {**_yaml_connector("x")["auth"], "tokenResponsePaths": {}},
        })
        assert spec.auth.token_response_paths is None

    def test_config_keeps_accepting_what_worked(self):
        spec = ConnectorSpec.model_validate({
            **_yaml_connector("x"),
            "retry": {"maxAttempts": 25, "backoff": "exponental"},  # unknown backoff: none
            "auth": {"type": "api_key", "secret": "K", "position": "Query"},
            "operations": [{"name": "ping", "method": "head", "path": "/"}],
        })
        assert (spec.retry.max_attempts, spec.operations[0].method) == (25, "HEAD")


# ------------------------------------------------------------ one write path


class TestOneWritePath:
    async def test_api_and_config_write_identical_rows(self, client, db: AsyncSession, admin_user):
        api, cfg = f"api-{_uid()}", f"cfg-{_uid()}"
        response = await client.post(
            "/api/v1/connectors", json=_rest_connector(api), headers=auth_headers(admin_user)
        )
        assert response.status_code == 201, response.text
        _, result = await _apply(db, admin_user, _yaml_connector(cfg))
        assert result.success, result.errors

        api_row, cfg_row = await _row(db, api), await _row(db, cfg)
        await db.refresh(api_row)
        assert {f: getattr(api_row, f) for f in FIELDS} == {f: getattr(cfg_row, f) for f in FIELDS}
        # Stored the way the runtime reads it: snake_case, no nulls.
        assert cfg_row.auth["token_response_paths"] == {"access_token": "data.token"}
        assert None not in cfg_row.auth.values()

    async def test_a_rename_onto_an_existing_connector_is_a_400_not_a_500(self, client, admin_user):
        first, second, headers = f"a-{_uid()}", f"b-{_uid()}", auth_headers(admin_user)
        for name in (first, second):
            await client.post("/api/v1/connectors", json=_rest_connector(name), headers=headers)
        response = await client.put(f"/api/v1/connectors/crm/{first}", json={"name": second}, headers=headers)
        assert response.status_code == 400
        assert response.json()["detail"] == f"Connector 'crm/{second}' already exists"

    async def test_a_manual_edit_detaches_a_config_managed_connector(
        self, client, db: AsyncSession, admin_user
    ):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_connector(name))
        await client.put(
            f"/api/v1/connectors/crm/{name}", json={"timeout_seconds": 5}, headers=auth_headers(admin_user)
        )
        row = await _row(db, name)
        await db.refresh(row)
        assert row.managed_by is None


class TestEditsStayAuthorized:
    async def test_a_row_renamed_since_the_permission_check_is_not_written(
        self, client, db: AsyncSession, admin_user
    ):
        """Permissions are scoped by namespace/name: a connector renamed
        elsewhere after the check may now be outside the caller's scope."""
        from fastapi import HTTPException
        from sqlalchemy import update

        from app.api.v1.endpoints.connectors import _context, _locked

        name = f"api-{_uid()}"
        await client.post("/api/v1/connectors", json=_rest_connector(name), headers=auth_headers(admin_user))
        authorized = await _row(db, name)
        await db.execute(  # a concurrent rename, behind the session's back
            update(Connector).where(Connector.id == authorized.id).values(name=f"moved-{_uid()}")
            .execution_options(synchronize_session=False)
        )
        with pytest.raises(HTTPException) as raised:
            await _locked(_context(db, admin_user.id), authorized)
        assert raised.value.status_code == 409


# ------------------------------------------------------------ history


class TestHistoryKeepsSecretsOut:
    async def test_secret_bearing_fields_are_redacted(self, client, db: AsyncSession, admin_user):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post(
            "/api/v1/connectors",
            json=_rest_connector(name, base_url="https://user:hunter2@api.example.com/v1?key=abc"),
            headers=headers,
        )
        [created] = await _revisions(db, name)
        shown = json.dumps(created.spec) + json.dumps(created.changes)
        for secret in (API_KEY, "hunter2", "key=abc", "aud-secret-xyz"):
            assert secret not in shown, secret
        assert created.spec["auth"]["secret"] == "CRM_SECRET"  # a name, not a value

        listed = await client.get(
            f"/api/v1/config/history/{created.id}", headers=headers
        )
        assert API_KEY not in listed.text and "secret_state" not in listed.text

    async def test_credentials_in_any_url_are_redacted_and_restored(
        self, client, db: AsyncSession, admin_user
    ):
        """A token often sits in the username slot (https://<token>@host);
        keys ride in token URLs and operation paths too (?code=...)."""
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        body = _rest_connector(name, base_url="https://ghp_TOKEN1@api.example.com/v1")
        body["auth"] = {
            "type": "oauth2_authorization_code", "client_id": "sinas", "secret": "CRM_SECRET",
            "token_url": "https://auth.example.com/token?code=TOKEN2",
            "authorize_url": "https://TOKEN4@auth.example.com/authorize",
        }
        body["operations"][0]["path"] = "/contacts?code=TOKEN3"
        await client.post("/api/v1/connectors", json=body, headers=headers)
        [created] = await _revisions(db, name)
        shown = json.dumps(created.spec)
        assert not any(f"TOKEN{i}" in shown for i in range(1, 5)), shown

        await client.delete(f"/api/v1/connectors/crm/{name}", headers=headers)
        [deleted] = [r for r in await _revisions(db, name) if r.action == "delete"]
        response = await client.post(f"/api/v1/config/history/{deleted.id}/restore", headers=headers)
        assert response.status_code == 200, response.text
        row = await _row(db, name)
        assert row.base_url == "https://ghp_TOKEN1@api.example.com/v1"
        assert row.auth["token_url"].endswith("?code=TOKEN2")
        assert row.auth["authorize_url"] == "https://TOKEN4@auth.example.com/authorize"
        assert row.operations[0]["path"] == "/contacts?code=TOKEN3"

    async def test_a_restore_refuses_when_secrets_cannot_be_decrypted(
        self, client, db: AsyncSession, admin_user
    ):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/connectors", json=_rest_connector(name), headers=headers)
        await client.delete(f"/api/v1/connectors/crm/{name}", headers=headers)
        [deleted] = [r for r in await _revisions(db, name) if r.action == "delete"]
        deleted.secret_state = "not-a-fernet-token"
        await db.flush()
        response = await client.post(f"/api/v1/config/history/{deleted.id}/restore", headers=headers)
        assert response.status_code == 409
        assert await _row(db, name) is None  # no placeholder ever written

    async def test_a_changed_secret_value_is_still_recorded_as_a_change(
        self, client, db: AsyncSession, admin_user
    ):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/connectors", json=_rest_connector(name), headers=headers)
        await client.put(
            f"/api/v1/connectors/crm/{name}", json={"headers": {"X-Api-Key": "rotated"}}, headers=headers
        )
        revisions = await _revisions(db, name)
        assert [r.action for r in revisions] == ["create", "update"]
        assert list(revisions[1].changes) == ["headers"]
        assert "rotated" not in json.dumps(revisions[1].changes)

    async def test_a_restore_brings_the_real_values_back(self, client, db: AsyncSession, admin_user):
        name, headers = f"api-{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/connectors", json=_rest_connector(name), headers=headers)
        await client.delete(f"/api/v1/connectors/crm/{name}", headers=headers)
        [deleted] = [r for r in await _revisions(db, name) if r.action == "delete"]

        response = await client.post(f"/api/v1/config/history/{deleted.id}/restore", headers=headers)
        assert response.status_code == 200, response.text
        row = await _row(db, name)
        assert row.headers == {"X-Api-Key": API_KEY}
        assert row.auth["token_params"] == {"audience": "aud-secret-xyz"}


# ------------------------------------------------------------ OAuth tokens


class TestStoredTokens:
    async def _with_token(self, client, db, admin_user):
        name = f"api-{_uid()}"
        await client.post("/api/v1/connectors", json=_rest_connector(name), headers=auth_headers(admin_user))
        row = await _row(db, name)
        db.add(ConnectorOAuthToken(connector_id=row.id, user_id=admin_user.id, encrypted_access_token="x"))
        await db.flush()
        return name, row

    async def _tokens(self, db, row) -> int:
        return (await db.execute(
            select(func.count()).select_from(ConnectorOAuthToken)
            .where(ConnectorOAuthToken.connector_id == row.id)
        )).scalar_one()

    async def test_repointing_the_token_url_drops_stored_tokens(self, client, db: AsyncSession, admin_user):
        """A refresh would post the old refresh token, with the client
        secret, to the new token URL."""
        name, row = await self._with_token(client, db, admin_user)
        auth = {**_rest_connector(name)["auth"], "token_url": "https://elsewhere.example.com/token"}
        await client.put(f"/api/v1/connectors/crm/{name}", json={"auth": auth}, headers=auth_headers(admin_user))
        assert await self._tokens(db, row) == 0

    async def test_other_edits_keep_them(self, client, db: AsyncSession, admin_user):
        name, row = await self._with_token(client, db, admin_user)
        await client.put(
            f"/api/v1/connectors/crm/{name}", json={"description": "renamed"}, headers=auth_headers(admin_user)
        )
        assert await self._tokens(db, row) == 1


# ------------------------------------------------------------ is_active + export


class TestIsActiveAndExport:
    async def test_a_re_apply_does_not_re_enable_a_disabled_connector(
        self, client, db: AsyncSession, admin_user
    ):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_connector(name))
        await client.put(
            f"/api/v1/connectors/crm/{name}", json={"is_active": False}, headers=auth_headers(admin_user)
        )
        _, result = await _apply(db, admin_user, _yaml_connector(name))
        assert result.success, result.errors
        row = await _row(db, name)
        await db.refresh(row)
        assert row.is_active is False

    async def test_export_round_trips_including_nulls_in_parameter_schemas(
        self, db: AsyncSession, admin_user
    ):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_connector(name, isActive=False))
        exported = await ConfigExportService(db, managed_only=True)._export_connectors()
        [mine] = [c for c in exported if c["name"] == name]
        assert mine["isActive"] is False
        assert mine["operations"][0]["parameters"]["properties"]["q"]["default"] is None

        _, result = await _apply(db, admin_user, mine)
        assert result.summary.unchanged.get("connectors") == 1


# ------------------------------------------------------------ import-openapi + packages


class TestImportAndPackages:
    async def test_import_openapi_is_a_recorded_edit(self, client, db: AsyncSession, admin_user):
        name = f"cfg-{_uid()}"
        await _apply(db, admin_user, _yaml_connector(name))
        spec = json.dumps({
            "openapi": "3.0.0", "info": {"title": "t", "version": "1"},
            "paths": {"/deals": {"get": {"operationId": "list_deals", "responses": {"200": {"description": "ok"}}}}},
        })
        response = await client.post(
            f"/api/v1/connectors/crm/{name}/import-openapi", json={"spec": spec, "apply": True},
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 200, response.text
        row = await _row(db, name)
        await db.refresh(row)
        assert {op["name"] for op in row.operations} == {"list_contacts", "list_deals"}
        assert row.managed_by is None  # a manual edit detaches, as any other
        assert [r.action for r in await _revisions(db, name)] == ["create", "update"]

    async def test_uninstall_records_the_deletion(self, db: AsyncSession, admin_user, published):
        from app.services.package_service import PackageService

        pkg, name = f"pkg-{_uid()}", f"c-{_uid()}"
        package = "\n".join([
            "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
            "package:", f"  name: {pkg}", '  version: "1.0.0"', "spec:", "  connectors:",
            "    - namespace: crm", f"      name: {name}", "      baseUrl: https://api.example.com",
        ]) + "\n"
        service = PackageService(db)
        await service.install(package, str(admin_user.id))
        counts = await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert counts.get("connectors") == 1
        assert [r.action for r in await _revisions(db, name)] == ["create", "delete"]
