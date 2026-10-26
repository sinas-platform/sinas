"""Secret references: `NAME` and `{{ NAME }}` both name the Secret `NAME`.

The official packages (github, jira, gmail, google-calendar,
google-drive-docs) and the sinas-package-author docs write connector auth as
`secret: "{{GITHUB_TOKEN}}"`, and a package `type: secret` variable
substitutes to that same `{{NAME}}` form. Every resolver looked the string up
literally, found no Secret called `{{GITHUB_TOKEN}}`, logged a warning and
sent the request WITHOUT credentials: every installed integration package
called its API anonymously.
"""

import http.server
import json
import threading
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encryption_service
from app.core.secret_refs import secret_ref_name
from app.models.connector import Connector
from app.models.identity_provider import IdentityProvider
from app.models.mcp_server import McpServer
from app.models.package_repository import PackageRepository
from app.models.secret import Secret
from app.schemas.spec.connector import ConnectorSpec
from app.schemas.spec.identity_provider import IdentityProviderSpec
from app.schemas.spec.mcp_server import McpServerSpec
from app.schemas.spec.package_repository import PackageRepositorySpec
from app.services.connector_service import ConnectorAuthError, connector_service
from app.services.package_service import PackageService
from app.services.resources.connectors import ConnectorApplier
from tests.conftest import auth_headers

# --------------------------------------------------------------------------- mock upstream


class _Recorder(http.server.BaseHTTPRequestHandler):
    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        self.server.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.path.startswith("/token"):
            payload = {"access_token": "minted-token", "expires_in": 3600, "token_type": "Bearer"}
        else:
            payload = {"ok": True}
        out = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _handle

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture
def upstream():
    """A real HTTP server on an ephemeral port that records what it received."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _url(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def _api_calls(server) -> list[dict]:
    return [r for r in server.seen if not r["path"].startswith("/token")]


def _uniq() -> str:
    return uuid.uuid4().hex[:6]


async def _shared_secret(db: AsyncSession, owner, name: str, value: str) -> None:
    db.add(Secret(
        user_id=owner.id, name=name, visibility="shared",
        encrypted_value=encryption_service.encrypt(value),
    ))
    await db.flush()


async def _legacy_connector(db: AsyncSession, owner, base_url: str, auth: dict) -> Connector:
    """A row as stored before specs normalised the reference: written
    straight to the table, `auth.secret` still in braces."""
    connector = Connector(
        user_id=owner.id, namespace=f"legacy{_uniq()}", name="api", base_url=base_url,
        auth=auth, headers={}, retry={"max_attempts": 1, "backoff": "none"}, timeout_seconds=10,
        operations=[{"name": "whoami", "method": "GET", "path": "/whoami",
                     "request_body_mapping": "query"}],
    )
    db.add(connector)
    await db.flush()
    return connector


# --------------------------------------------------------------------------- the rule


class TestSecretRefName:
    @pytest.mark.parametrize(
        "ref, name",
        [
            ("GITHUB_TOKEN", "GITHUB_TOKEN"),
            ("{{GITHUB_TOKEN}}", "GITHUB_TOKEN"),
            ("{{ GITHUB_TOKEN }}", "GITHUB_TOKEN"),
            ("  {{\tGITHUB_TOKEN  }} ", "GITHUB_TOKEN"),
            ("  GITHUB_TOKEN ", "GITHUB_TOKEN"),
            (None, None),
            ("", ""),
            # Not a single wrapped name: left alone (and will not resolve).
            ("{{ }}", "{{ }}"),
            ("{{A}}{{B}}", "{{A}}{{B}}"),
            ("{GITHUB_TOKEN}", "{GITHUB_TOKEN}"),
        ],
    )
    def test_forms(self, ref, name):
        assert secret_ref_name(ref) == name


class TestSpecsStoreTheBareName:
    """Normalised at validation, so the row, change history and export all
    carry `NAME` whichever form was written."""

    @pytest.mark.parametrize("ref", ["TOK", "{{TOK}}", "{{ TOK }}"])
    def test_connector(self, ref):
        spec = ConnectorSpec.model_validate(
            {"name": "c", "base_url": "https://x", "auth": {"type": "bearer", "secret": ref}}
        )
        assert spec.auth.secret == "TOK"
        assert spec.to_config()["auth"]["secret"] == "TOK"

    @pytest.mark.parametrize("ref", ["TOK", "{{TOK}}", "{{ TOK }}"])
    def test_mcp_server(self, ref):
        spec = McpServerSpec.model_validate(
            {"name": "m", "url": "https://x/mcp", "auth": {"type": "bearer", "secret": ref}}
        )
        assert spec.auth.secret == "TOK"

    @pytest.mark.parametrize("ref", ["TOK", "{{TOK}}", "{{ TOK }}"])
    def test_package_repository(self, ref):
        spec = PackageRepositorySpec.model_validate(
            {"name": "r", "url": "https://x", "auth": {"type": "bearer", "secret": ref}}
        )
        assert spec.auth.secret == "TOK"

    @pytest.mark.parametrize("ref, name", [("TOK", "TOK"), ("{{ TOK }}", "TOK"), ("", None), (None, None)])
    def test_identity_provider(self, ref, name):
        spec = IdentityProviderSpec.model_validate({
            "name": "idp", "display_name": "IdP", "issuer": "https://idp.example.com",
            "client_id": "cid", "client_secret": ref,
        })
        assert spec.client_secret == name

    def test_no_secret_stays_none(self):
        spec = ConnectorSpec.model_validate({"name": "c", "base_url": "https://x"})
        assert spec.auth.secret is None


# --------------------------------------------------------------------------- package install, end to end

PACKAGE = """
apiVersion: sinas.co/v1
kind: SinasPackage
package:
  name: {pkg}
  version: "{version}"
spec:
  variables:
    - name: {var}
      type: secret
      description: API token
  connectors:
    - namespace: {ns}
      name: api
      baseUrl: "{base_url}"
      auth:
        type: {auth_type}
        secret: {ref}
      operations:
        - name: whoami
          method: GET
          path: /whoami
          requestBodyMapping: query
"""

REFS = {
    "braces": '"{{{{{var}}}}}"',  # the official packages: secret: "{{GITHUB_TOKEN}}"
    "braces-spaced": '"{{{{ {var} }}}}"',
    "variable": '"${{{{ vars.{var} }}}}"',  # substitutes to {{NAME}} at install
    "bare": "{var}",  # what the docs use
}


def _package(upstream, suffix, ref_kind, auth_type="bearer", version="1.0.0"):
    var = f"TOK_{suffix.upper()}"
    ns = f"secref{suffix}"
    yaml = PACKAGE.format(
        pkg=f"secref-{suffix}", version=version, var=var, ns=ns, base_url=_url(upstream),
        auth_type=auth_type, ref=REFS[ref_kind].format(var=var),
    )
    return yaml, var, ns


async def _installed_connector(db: AsyncSession, ns: str) -> Connector:
    return (
        await db.execute(select(Connector).where(Connector.namespace == ns, Connector.name == "api"))
    ).scalar_one()


class TestPackageInstallEndToEnd:
    """Install a package written the way the official ones are, call an
    operation against a live HTTP server, look at the headers it received."""

    @pytest.mark.parametrize("ref_kind", list(REFS))
    async def test_bearer_header_is_sent(self, db: AsyncSession, admin_user, upstream, ref_kind):
        yaml, var, ns = _package(upstream, _uniq(), ref_kind)
        await PackageService(db).install(yaml, str(admin_user.id), variables={var: "tok-value"})

        connector = await _installed_connector(db, ns)
        assert connector.auth["secret"] == var  # stored bare, whatever was written
        result = await connector_service.execute_operation(
            db, connector, "whoami", {}, user_id=str(admin_user.id)
        )

        assert result["status_code"] == 200
        assert _api_calls(upstream)[-1]["headers"].get("Authorization") == "Bearer tok-value"
        # Export shows the bare name too.
        assert ConnectorApplier().spec_from_row(connector).to_config()["auth"]["secret"] == var

    async def test_basic_auth_like_the_jira_package(self, db: AsyncSession, admin_user, upstream):
        yaml, var, ns = _package(upstream, _uniq(), "braces", auth_type="basic")
        await PackageService(db).install(yaml, str(admin_user.id), variables={var: "me@x.io:api-token"})

        connector = await _installed_connector(db, ns)
        await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))

        import base64

        expected = "Basic " + base64.b64encode(b"me@x.io:api-token").decode()
        assert _api_calls(upstream)[-1]["headers"].get("Authorization") == expected

    @pytest.mark.parametrize("ref_kind", ["variable", "braces"])
    async def test_upgrade_without_reentering_the_secret(
        self, db: AsyncSession, admin_user, upstream, ref_kind
    ):
        """On upgrade an existing secret isn't asked for again. That path
        substituted the literal `{{{{name}}}}` for `${{ vars.NAME }}`."""
        suffix = _uniq()
        yaml, var, ns = _package(upstream, suffix, ref_kind)
        svc = PackageService(db)
        await svc.install(yaml, str(admin_user.id), variables={var: "tok-value"})
        yaml2, _, _ = _package(upstream, suffix, ref_kind, version="1.0.1")
        await svc.install(yaml2, str(admin_user.id))  # no variables this time

        connector = await _installed_connector(db, ns)
        assert connector.auth["secret"] == var
        await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert _api_calls(upstream)[-1]["headers"].get("Authorization") == "Bearer tok-value"

    async def test_private_secret_overrides_shared_with_braced_reference(
        self, db: AsyncSession, admin_user, test_user, upstream
    ):
        yaml, var, ns = _package(upstream, _uniq(), "braces")
        await PackageService(db).install(yaml, str(admin_user.id), variables={var: "shared-value"})
        db.add(Secret(
            user_id=test_user.id, name=var, visibility="private",
            encrypted_value=encryption_service.encrypt("private-value"),
        ))
        await db.flush()

        connector = await _installed_connector(db, ns)
        await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(test_user.id))
        assert _api_calls(upstream)[-1]["headers"].get("Authorization") == "Bearer private-value"
        await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert _api_calls(upstream)[-1]["headers"].get("Authorization") == "Bearer shared-value"


# --------------------------------------------------------------------------- stored legacy rows


class TestLegacyRowsStillResolve:
    """Rows stored with `{{NAME}}` before this fix work without a migration."""

    @pytest.mark.parametrize(
        "auth, header, expected",
        [
            ({"type": "bearer"}, "Authorization", "Bearer v4lue"),
            ({"type": "api_key", "header": "X-Api-Key"}, "X-Api-Key", "v4lue"),
        ],
    )
    async def test_connector(self, db: AsyncSession, admin_user, upstream, auth, header, expected):
        name = f"LEG_{_uniq().upper()}"
        await _shared_secret(db, admin_user, name, "v4lue")
        connector = await _legacy_connector(
            db, admin_user, _url(upstream), {**auth, "secret": f"{{{{ {name} }}}}"}
        )
        await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert _api_calls(upstream)[-1]["headers"].get(header) == expected

    async def test_api_key_in_query(self, db: AsyncSession, admin_user, upstream):
        name = f"LEG_{_uniq().upper()}"
        await _shared_secret(db, admin_user, name, "v4lue")
        connector = await _legacy_connector(
            db, admin_user, _url(upstream),
            {"type": "api_key", "position": "query", "param_name": "key", "secret": f"{{{{{name}}}}}"},
        )
        await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert "key=v4lue" in _api_calls(upstream)[-1]["path"]

    async def test_oauth_client_credentials(self, db: AsyncSession, admin_user, upstream):
        name = f"LEG_{_uniq().upper()}"
        await _shared_secret(db, admin_user, name, "client-s3cret")
        connector = await _legacy_connector(db, admin_user, _url(upstream), {
            "type": "oauth2_client_credentials", "token_url": f"{_url(upstream)}/token",
            "client_id": f"cid-{name}", "secret": f"{{{{{name}}}}}",
        })
        await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        token_request = [r for r in upstream.seen if r["path"].startswith("/token")][-1]
        assert "client_secret=client-s3cret" in token_request["body"]
        assert _api_calls(upstream)[-1]["headers"].get("Authorization") == "Bearer minted-token"

    async def test_oauth_authorization_code_client_secret(self, db: AsyncSession, admin_user):
        """The gmail / google-calendar / google-drive-docs packages: the code
        exchange and refresh resolve the client secret by the same rule."""
        name = f"LEG_{_uniq().upper()}"
        await _shared_secret(db, admin_user, name, "client-s3cret")
        assert await connector_service._resolve_secret_value(db, f"{{{{{name}}}}}", None) == "client-s3cret"
        assert await connector_service._resolve_secret_value(db, name, None) == "client-s3cret"

    async def test_mcp_server(self, db: AsyncSession, admin_user):
        from app.services.mcp_client import resolve_headers

        name = f"LEG_{_uniq().upper()}"
        await _shared_secret(db, admin_user, name, "mcp-v4lue")
        for ref in (name, f"{{{{{name}}}}}", f"{{{{ {name} }}}}"):
            server = McpServer(
                namespace="t", name="m", url="https://x/mcp", headers={},
                auth={"type": "header", "header": "X-Key", "secret": ref},
            )
            assert (await resolve_headers(db, server, str(admin_user.id)))["X-Key"] == "mcp-v4lue"

    async def test_package_repository(self, db: AsyncSession, admin_user):
        from app.services.package_repositories import _auth_headers

        name = f"LEG_{_uniq().upper()}"
        await _shared_secret(db, admin_user, name, "repo-v4lue")
        for ref in (name, f"{{{{{name}}}}}"):
            repo = PackageRepository(name="r", url="https://x", auth={"type": "bearer", "secret": ref})
            assert (await _auth_headers(db, repo)) == {"Authorization": "Bearer repo-v4lue"}

    async def test_identity_provider_client_secret(self, db: AsyncSession, admin_user):
        from app.services.sso import client_secret

        name = f"LEG_{_uniq().upper()}"
        await _shared_secret(db, admin_user, name, "idp-v4lue")
        for ref in (name, f"{{{{ {name} }}}}"):
            provider = IdentityProvider(name="idp", client_secret=ref)
            assert await client_secret(db, provider) == "idp-v4lue"

    async def test_per_user_pipeline_enumerates_private_secret_holders(
        self, db: AsyncSession, admin_user, test_user
    ):
        from app.services.pipeline_runner import enumerate_connected_users

        name = f"LEG_{_uniq().upper()}"
        db.add(Secret(
            user_id=test_user.id, name=name, visibility="private",
            encrypted_value=encryption_service.encrypt("x"),
        ))
        await db.flush()
        connector = await _legacy_connector(
            db, admin_user, "https://x", {"type": "bearer", "secret": f"{{{{{name}}}}}"}
        )
        pipeline = SimpleNamespace(
            id=uuid.uuid4(), namespace="p", name="p",
            per_user={"connector": f"{connector.namespace}/{connector.name}"},
        )
        assert await enumerate_connected_users(db, pipeline) == [str(test_user.id)]


# --------------------------------------------------------------------------- missing secrets fail closed


class TestMissingSecretIsAClearError:
    @pytest.mark.parametrize("auth_type", ["bearer", "basic", "api_key"])
    async def test_missing_secret_row(self, db: AsyncSession, admin_user, upstream, auth_type):
        name = f"MISSING_{_uniq().upper()}"
        connector = await _legacy_connector(
            db, admin_user, _url(upstream), {"type": auth_type, "secret": f"{{{{{name}}}}}"}
        )
        with pytest.raises(ConnectorAuthError) as err:
            await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert f"'{name}'" in str(err.value)  # the bare name, so it can be created as such
        assert _api_calls(upstream) == []  # nothing went out unauthenticated

    async def test_no_secret_configured(self, db: AsyncSession, admin_user, upstream):
        connector = await _legacy_connector(db, admin_user, _url(upstream), {"type": "bearer"})
        with pytest.raises(ConnectorAuthError, match="names no secret"):
            await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert _api_calls(upstream) == []

    async def test_client_credentials_missing_secret_names_it(self, db: AsyncSession, admin_user, upstream):
        name = f"MISSING_{_uniq().upper()}"
        connector = await _legacy_connector(db, admin_user, _url(upstream), {
            "type": "oauth2_client_credentials", "token_url": f"{_url(upstream)}/token",
            "client_id": f"cid-{name}", "secret": name,
        })
        with pytest.raises(ConnectorAuthError) as err:
            await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert f"'{name}'" in str(err.value)
        assert upstream.seen == []  # neither the token endpoint nor the API was called

    async def test_secret_value_never_in_the_error(self, db: AsyncSession, admin_user, upstream):
        """A private secret of ANOTHER user is not this caller's credential:
        still missing for them, and its value appears nowhere."""
        name = f"OTHER_{_uniq().upper()}"
        other = Secret(
            user_id=admin_user.id, name=name, visibility="private",
            encrypted_value=encryption_service.encrypt("do-not-leak"),
        )
        db.add(other)
        await db.flush()
        connector = await _legacy_connector(db, admin_user, _url(upstream), {"type": "bearer", "secret": name})
        with pytest.raises(ConnectorAuthError) as err:
            await connector_service.execute_operation(db, connector, "whoami", {}, user_id=None)
        assert "do-not-leak" not in str(err.value)

    async def test_test_endpoint_reports_it(self, client, db: AsyncSession, admin_user, upstream):
        name = f"MISSING_{_uniq().upper()}"
        connector = await _legacy_connector(
            db, admin_user, _url(upstream), {"type": "bearer", "secret": f"{{{{{name}}}}}"}
        )
        resp = await client.post(
            f"/api/v1/connectors/{connector.namespace}/{connector.name}/test/whoami",
            json={"parameters": {}}, headers=auth_headers(admin_user),
        )
        assert resp.status_code == 400, resp.text
        assert name in resp.json()["detail"]
        assert _api_calls(upstream) == []


class TestNoneAuthUnaffected:
    async def test_none_sends_no_credentials_and_needs_no_secret(self, db: AsyncSession, admin_user, upstream):
        connector = await _legacy_connector(db, admin_user, _url(upstream), {"type": "none"})
        result = await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert result["status_code"] == 200
        assert "Authorization" not in _api_calls(upstream)[-1]["headers"]

    async def test_none_ignores_a_stray_secret(self, db: AsyncSession, admin_user, upstream):
        connector = await _legacy_connector(
            db, admin_user, _url(upstream), {"type": "none", "secret": "{{NOT_THERE}}"}
        )
        result = await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert result["status_code"] == 200

    async def test_package_with_none_auth(self, db: AsyncSession, admin_user, upstream):
        suffix = _uniq()
        yaml = (
            "apiVersion: sinas.co/v1\nkind: SinasPackage\n"
            f"package:\n  name: secref-none-{suffix}\n  version: \"1.0.0\"\n"
            "spec:\n  connectors:\n"
            f"    - namespace: secrefnone{suffix}\n      name: api\n      baseUrl: \"{_url(upstream)}\"\n"
            "      operations:\n        - {name: whoami, method: GET, path: /whoami, requestBodyMapping: query}\n"
        )
        await PackageService(db).install(yaml, str(admin_user.id))
        connector = await _installed_connector(db, f"secrefnone{suffix}")
        result = await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert result["status_code"] == 200
        assert "Authorization" not in _api_calls(upstream)[-1]["headers"]
