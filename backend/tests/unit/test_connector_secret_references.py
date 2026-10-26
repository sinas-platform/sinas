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
    """Fails closed. Users, agents and pipelines get a general message; the
    admin-facing detail (the operation test) says which secret."""

    @pytest.mark.parametrize("auth_type", ["bearer", "basic", "api_key"])
    async def test_missing_secret_row(self, db: AsyncSession, admin_user, upstream, auth_type):
        name = f"MISSING_{_uniq().upper()}"
        connector = await _legacy_connector(
            db, admin_user, _url(upstream), {"type": auth_type, "secret": f"{{{{{name}}}}}"}
        )
        with pytest.raises(ConnectorAuthError) as err:
            await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert "not set up" in str(err.value) and name not in str(err.value)
        assert f"'{name}'" in err.value.detail  # the bare name, so it can be created as such
        assert _api_calls(upstream) == []  # nothing went out unauthenticated

    async def test_no_secret_configured(self, db: AsyncSession, admin_user, upstream):
        connector = await _legacy_connector(db, admin_user, _url(upstream), {"type": "bearer"})
        with pytest.raises(ConnectorAuthError) as err:
            await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert "names no secret" in err.value.detail
        assert _api_calls(upstream) == []

    async def test_client_credentials_missing_secret_names_it(self, db: AsyncSession, admin_user, upstream):
        name = f"MISSING_{_uniq().upper()}"
        connector = await _legacy_connector(db, admin_user, _url(upstream), {
            "type": "oauth2_client_credentials", "token_url": f"{_url(upstream)}/token",
            "client_id": f"cid-{name}", "secret": name,
        })
        with pytest.raises(ConnectorAuthError) as err:
            await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert f"'{name}'" in err.value.detail and name not in str(err.value)
        assert upstream.seen == []  # neither the token endpoint nor the API was called

    async def test_client_credentials_without_secret_says_so(self, db: AsyncSession, admin_user, upstream):
        """Was the generic "could not obtain an OAuth token"."""
        connector = await _legacy_connector(db, admin_user, _url(upstream), {
            "type": "oauth2_client_credentials", "token_url": f"{_url(upstream)}/token",
            "client_id": "cid",
        })
        with pytest.raises(ConnectorAuthError) as err:
            await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert "names no secret" in err.value.detail
        assert "not set up" in str(err.value)
        assert upstream.seen == []

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
        assert "do-not-leak" not in str(err.value) and "do-not-leak" not in err.value.detail

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


# A credential pasted where its NAME belongs. Built at runtime so no scanner
# mistakes these test strings for leaked tokens.
VALUE_LIKE_REFS = [
    "ghp" + "_realToken123",
    "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "sk" + "-proj-" + "Q7x9Lm2Pz8Rt4Vb6Nc1Kd3Fg5Hj",
    "xox" + "b-1234567890-abcdefghij",
    "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA",  # long letters+digits run, no prefix
    "me@example.com:api-token-123",  # basic-auth credentials, not a name at all
    "a23e4567-e89b-12d3-a456-426614174000",  # UUID-format API key
    "pat" + "-na1-a23e4567-e89b-12d3-a456-426614174000",  # HubSpot private app token
    "shpss" + "_0123456789abcdef0123456789abcdef",  # Shopify shared secret
    "re" + "_AbCdEf12_3456789GhIjKlMnOp",  # Resend API key
    "Summer2024",  # an identifier-shaped password
]


class TestValueLikeReferencesStayOut:
    """P2: a reference that looks like a credential never reaches the LLM,
    a chat, a pipeline record or an API error."""

    @pytest.mark.parametrize("ref", VALUE_LIKE_REFS)
    def test_shape_check(self, ref):
        from app.core.secret_refs import describe_secret_ref, looks_like_secret_name

        assert not looks_like_secret_name(ref)
        assert ref not in describe_secret_ref(ref)

    @pytest.mark.parametrize(
        "ref", ["GITHUB_TOKEN", "{{ JIRA_CREDENTIALS }}", "github_token", "GitHub_Token", "API_KEY_V2", "_X1"]
    )
    def test_names_are_shown(self, ref):
        from app.core.secret_refs import describe_secret_ref

        assert describe_secret_ref(ref) == f"secret '{secret_ref_name(ref)}'"

    @pytest.mark.parametrize("ref", VALUE_LIKE_REFS)
    async def test_agent_tool_error(self, db: AsyncSession, admin_user, upstream, ref, caplog):
        from app.services.connector_tools import ConnectorToolConverter

        connector = await _legacy_connector(db, admin_user, _url(upstream), {"type": "bearer", "secret": ref})
        result = await ConnectorToolConverter().execute_connector_tool(
            db, f"connector__{connector.namespace}__api__whoami", {}, user_id=str(admin_user.id),
        )
        assert "error" in result and "not set up" in result["error"]
        assert ref not in json.dumps(result)
        assert ref not in caplog.text
        assert _api_calls(upstream) == []

    @pytest.mark.parametrize("ref", VALUE_LIKE_REFS[:2])
    async def test_pipeline_step_error(self, db: AsyncSession, admin_user, upstream, ref, monkeypatch, caplog):
        import contextlib

        from app.services import pipeline_runner

        @contextlib.asynccontextmanager
        async def _same_session():
            yield db

        monkeypatch.setattr(pipeline_runner, "AsyncSessionLocal", _same_session)
        connector = await _legacy_connector(db, admin_user, _url(upstream), {"type": "bearer", "secret": ref})
        with pytest.raises(pipeline_runner.StepError) as err:
            await pipeline_runner._execute_connector_step(
                {"connector": f"{connector.namespace}/api", "operation": "whoami"}, {},
                user_id=str(admin_user.id), user_token=None,
            )
        assert "not set up" in str(err.value) and ref not in str(err.value)
        assert ref not in caplog.text
        assert _api_calls(upstream) == []

    @pytest.mark.parametrize("ref", VALUE_LIKE_REFS)
    async def test_connector_detail(self, db: AsyncSession, admin_user, upstream, ref, caplog):
        connector = await _legacy_connector(db, admin_user, _url(upstream), {"type": "bearer", "secret": ref})
        with pytest.raises(ConnectorAuthError) as err:
            await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert "the configured secret reference" in err.value.detail
        assert ref not in err.value.detail and ref not in str(err.value)
        assert ref not in caplog.text

    @pytest.mark.parametrize("ref", VALUE_LIKE_REFS)
    async def test_mcp_agent_path(self, db: AsyncSession, admin_user, ref, caplog):
        """What the model and the chat receive from a failed MCP tool call."""
        from app.services.mcp_tools import McpToolConverter

        server = McpServer(
            user_id=admin_user.id, namespace="tools", name=f"leak-{_uniq()}", url="http://unused.test/mcp",
            transport="streamable_http", auth={"type": "bearer", "secret": ref}, headers={},
            tool_allow=[], tool_deny=[], timeout_seconds=5, connect_timeout_seconds=5, is_active=True,
        )
        db.add(server)
        await db.flush()
        result = await McpToolConverter().execute_tool(
            db, f"mcp_tools__{server.name}__echo", {}, str(admin_user.id),
            {"tool_type": "mcp", "server_namespace": "tools", "server_name": server.name, "mcp_tool": "echo"},
        )
        assert "not set up" in result["error"]
        assert ref not in json.dumps(result)
        assert ref not in caplog.text

    @pytest.mark.parametrize("ref", VALUE_LIKE_REFS)
    async def test_mcp_detail_and_repository_error(self, db: AsyncSession, ref):
        from app.services.mcp_client import McpClientError, resolve_headers
        from app.services.package_repositories import RepositoryError, _auth_headers

        server = McpServer(namespace="t", name="m", url="https://x/mcp", headers={},
                           auth={"type": "bearer", "secret": ref})
        with pytest.raises(McpClientError) as err:
            await resolve_headers(db, server, None)
        assert "the configured secret reference" in err.value.detail
        assert ref not in err.value.detail and ref not in str(err.value)

        repo = PackageRepository(name="r", url="https://x", auth={"type": "bearer", "secret": ref})
        with pytest.raises(RepositoryError) as err:
            await _auth_headers(db, repo)
        assert ref not in str(err.value)

    @pytest.mark.parametrize("ref", VALUE_LIKE_REFS[:2])
    async def test_api_test_endpoint(self, client, db: AsyncSession, admin_user, upstream, ref):
        connector = await _legacy_connector(db, admin_user, _url(upstream), {"type": "bearer", "secret": ref})
        resp = await client.post(
            f"/api/v1/connectors/{connector.namespace}/api/test/whoami",
            json={"parameters": {}}, headers=auth_headers(admin_user),
        )
        assert resp.status_code == 400, resp.text
        assert "the configured secret reference" in resp.json()["detail"]
        assert ref not in resp.text

    async def test_client_credentials(self, db: AsyncSession, admin_user, upstream, caplog):
        ref = VALUE_LIKE_REFS[1]
        connector = await _legacy_connector(db, admin_user, _url(upstream), {
            "type": "oauth2_client_credentials", "token_url": f"{_url(upstream)}/token",
            "client_id": "cid-value-like", "secret": ref,
        })
        with pytest.raises(ConnectorAuthError) as err:
            await connector_service.execute_operation(db, connector, "whoami", {}, user_id=str(admin_user.id))
        assert ref not in str(err.value) and ref not in err.value.detail
        assert ref not in caplog.text

    async def test_mcp_server(self, db: AsyncSession):
        from app.services.mcp_client import McpClientError, resolve_headers

        ref = VALUE_LIKE_REFS[1]
        server = McpServer(namespace="t", name="m", url="https://x/mcp", headers={},
                           auth={"type": "bearer", "secret": ref})
        with pytest.raises(McpClientError) as err:
            await resolve_headers(db, server, None)
        assert ref not in str(err.value)


class TestWhoSeesWhichSecret:
    """The name of a missing secret is for those who may edit the resource;
    a reader of the test endpoints gets what an agent gets."""

    async def _reader(self, db, test_role):
        from app.models.user import RolePermission

        for key in ("sinas.connectors/*/*.read:all", "sinas.mcp_servers/*/*.read:all"):
            db.add(RolePermission(role_id=test_role.id, permission_key=key, permission_value=True))
        await db.flush()

    async def test_connector_test_endpoint(
        self, client, db: AsyncSession, admin_user, test_user, test_role, upstream
    ):
        await self._reader(db, test_role)
        name = f"MISSING_{_uniq().upper()}"
        connector = await _legacy_connector(db, admin_user, _url(upstream), {"type": "bearer", "secret": name})
        url = f"/api/v1/connectors/{connector.namespace}/api/test/whoami"

        reader = await client.post(url, json={"parameters": {}}, headers=auth_headers(test_user))
        assert reader.status_code == 400, reader.text
        assert name not in reader.text and "not set up" in reader.json()["detail"]

        editor = await client.post(url, json={"parameters": {}}, headers=auth_headers(admin_user))
        assert editor.status_code == 400 and name in editor.json()["detail"]

    async def test_mcp_tools_endpoint(self, client, db: AsyncSession, admin_user, test_user, test_role):
        await self._reader(db, test_role)
        name = f"MISSING_{_uniq().upper()}"
        server = McpServer(
            user_id=admin_user.id, namespace="tools", name=f"who-{_uniq()}", url="http://unused.test/mcp",
            transport="streamable_http", auth={"type": "bearer", "secret": name}, headers={},
            tool_allow=[], tool_deny=[], timeout_seconds=5, connect_timeout_seconds=5, is_active=True,
        )
        db.add(server)
        await db.flush()
        url = f"/api/v1/mcp-servers/tools/{server.name}/tools"

        reader = await client.post(url, headers=auth_headers(test_user))
        assert reader.status_code == 502, reader.text
        assert name not in reader.text and "not set up" in reader.json()["detail"]

        editor = await client.post(url, headers=auth_headers(admin_user))
        assert editor.status_code == 502 and name in editor.json()["detail"]


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


# --------------------------------------------------------------------------- review follow-ups


class TestPipelineRecredentialReset:
    async def test_reset_matches_a_stored_braced_reference(self, db: AsyncSession, admin_user, test_user):
        """A user re-entering their private secret clears the skip-list of
        per-user pipelines on connectors that reference it, also when the
        connector row still stores `{{NAME}}`."""
        from app.models.pipeline import Pipeline, PipelineCursor
        from app.services.pipeline_runner import reset_user_failure_state

        name = f"LEG_{_uniq().upper()}"
        connector = await _legacy_connector(
            db, admin_user, "https://x", {"type": "bearer", "secret": f"{{{{ {name} }}}}"}
        )
        pipeline = Pipeline(
            user_id=admin_user.id, namespace=f"p{_uniq()}", name="sync",
            per_user={"connector": f"{connector.namespace}/{connector.name}"},
        )
        db.add(pipeline)
        await db.flush()
        cursor = PipelineCursor(
            pipeline_id=pipeline.id, user_id=test_user.id, consecutive_failures=5, last_error="401",
        )
        db.add(cursor)
        await db.flush()

        await reset_user_failure_state(db, str(test_user.id), secret_name=name)

        row = (
            await db.execute(
                select(PipelineCursor.consecutive_failures, PipelineCursor.last_error)
                .where(PipelineCursor.id == cursor.id)
            )
        ).one()
        assert tuple(row) == (0, None)


class TestSecretResourceRef:
    async def test_shared_and_private_of_the_same_name(self, db: AsyncSession, admin_user, test_user):
        """Was MultipleResultsFound (a 500) once a private secret shared the name."""
        name = f"REF_{_uniq().upper()}"
        await _shared_secret(db, admin_user, name, "a")
        db.add(Secret(user_id=test_user.id, name=name, visibility="private",
                      encrypted_value=encryption_service.encrypt("b")))
        await db.flush()
        svc = PackageService(db)
        assert await svc._validate_resource_ref("secrets", name) is True
        assert await svc._validate_resource_ref("secrets", f"{{{{{name}}}}}") is True
        assert await svc._validate_resource_ref("secrets", f"NOPE_{name}") is False


class TestSecretNameRule:
    """Config and packages create secrets under the REST name rule, so a
    `{{ NAME }}` reference can always name them."""

    def _ctx(self, db, user):
        from app.services.resources import ApplyContext

        return ApplyContext(db=db, origin="api", actor_user_id=str(user.id), owner_user_id=str(user.id))

    @pytest.mark.parametrize("bad", ["my-secret", "api.key", "1TOKEN", "{{TOKEN}}", "has space"])
    async def test_create_refuses_other_names(self, db: AsyncSession, admin_user, bad):
        from app.services.resources.base import ApplierError
        from app.services.resources.secrets import SecretApplier

        applier = SecretApplier()
        spec = applier.spec_model.model_validate({"name": bad, "value": "v"})
        with pytest.raises(ApplierError, match="not allowed"):
            await applier.apply(spec, self._ctx(db, admin_user))

    async def test_package_secret_variable_with_another_name(self, db: AsyncSession, admin_user):
        """A package's secret variable creates the secret: same rule."""
        yaml = (
            "apiVersion: sinas.co/v1\nkind: SinasPackage\n"
            f"package:\n  name: secname-{_uniq()}\n  version: \"1.0.0\"\n"
            "spec:\n  variables:\n    - {name: bad-name, type: secret}\n"
        )
        with pytest.raises(Exception, match="not allowed"):
            await PackageService(db).install(yaml, str(admin_user.id), variables={"bad-name": "v"})

    async def test_existing_row_with_an_old_name_can_still_be_updated(self, db: AsyncSession, admin_user):
        from app.services.resources.secrets import SecretApplier

        old = f"legacy-name-{_uniq()}"  # created before the rule
        await _shared_secret(db, admin_user, old, "one")
        applier = SecretApplier()
        ctx = self._ctx(db, admin_user)
        result = await applier.apply(
            applier.spec_model.model_validate({"name": old, "value": "two"}), ctx,
            existing=await applier.find(ctx, old),
        )
        assert result.action == "update"
        assert await connector_service._resolve_secret_value(db, old, None) == "two"

    async def test_restoring_a_deleted_old_style_secret(self, client, db: AsyncSession, admin_user):
        """Restore recreates the row a revision recorded, under its old name."""
        from app.models.config_revision import ConfigRevision
        from app.services.resources.secrets import SecretApplier

        old = f"legacy-name-{_uniq()}"
        await _shared_secret(db, admin_user, old, "one")
        applier, ctx = SecretApplier(), self._ctx(db, admin_user)
        row = await applier.find(ctx, old)
        await applier.apply(  # recorded, so there is history to restore from
            applier.spec_model.model_validate({"name": old, "value": "two"}), ctx, existing=row,
        )
        await applier.delete(await applier.find(ctx, old), ctx)
        await db.flush()
        deleted = (
            await db.execute(
                select(ConfigRevision.id)
                .where(ConfigRevision.resource_kind == "secrets", ConfigRevision.resource_key == old,
                       ConfigRevision.action == "delete")
            )
        ).scalar_one()

        resp = await client.post(f"/api/v1/config/history/{deleted}/restore", headers=auth_headers(admin_user))
        assert resp.status_code == 200, resp.text
        assert await connector_service._resolve_secret_value(db, old, None) == "two"

    @pytest.mark.parametrize("good", ["GITHUB_TOKEN", "github_token", "_X1"])
    async def test_create_accepts_names(self, db: AsyncSession, admin_user, good):
        from app.services.resources.secrets import SecretApplier

        name = f"{good}_{_uniq()}"
        applier = SecretApplier()
        result = await applier.apply(
            applier.spec_model.model_validate({"name": name, "value": "v"}), self._ctx(db, admin_user)
        )
        assert result.action == "create"
