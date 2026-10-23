"""Unknown keys in a SinasConfig / SinasPackage are errors, per resource.

The config models used pydantic's default `extra="ignore"`: a misspelt key
(`output_schema:` for `outputSchema:`) parsed, `sinas validate` said "Valid",
and the apply silently left the field out. Every config model now forbids
unknown keys, and ConfigParser names the resource, the key and, where it can
tell, the field that was meant.
"""

import re
import typing
import uuid
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from app.schemas.config import ConfigModel, ConfigSpec, SinasConfig
from app.services.config_parser import ConfigParser
from tests.conftest import auth_headers

# A minimal valid entry for every spec section, and the key its errors name.
MINIMAL: dict[str, tuple[dict, str]] = {
    "variables": ({"name": "GREETING", "type": "text"}, "GREETING"),
    "roles": ({"name": "readers"}, "readers"),
    "users": ({"email": "ann@example.com"}, "ann@example.com"),
    "llmProviders": ({"name": "llm", "type": "openai"}, "llm"),
    "databaseConnections": (
        {"name": "db", "connectionType": "postgresql", "host": "h", "port": 5432,
         "database": "d", "username": "u"},
        "db",
    ),
    "dependencies": ({"packageName": "requests"}, "requests"),
    "secrets": ({"name": "API_KEY"}, "API_KEY"),
    "connectors": ({"namespace": "acme", "name": "api", "baseUrl": "https://example.com"}, "acme/api"),
    "mcpServers": ({"name": "mcp", "url": "https://example.com/mcp"}, "default/mcp"),
    "packageRepositories": ({"name": "repo", "url": "https://example.com/index.json"}, "repo"),
    "identityProviders": (
        {"name": "idp", "displayName": "IdP", "issuer": "https://idp.example.com", "clientId": "c"},
        "idp",
    ),
    "skills": ({"namespace": "acme", "name": "s", "description": "d", "content": "c"}, "acme/s"),
    "components": ({"name": "c", "sourceCode": "x"}, "default/c"),
    "functions": ({"name": "f", "code": "def handler(i, c):\n    return 1\n"}, "default/f"),
    "queries": (
        {"name": "q", "connectionName": "db", "operation": "read", "sql": "select 1"},
        "default/q",
    ),
    "pipelines": ({"name": "p"}, "default/p"),
    "collections": ({"name": "c"}, "default/c"),
    "templates": ({"name": "t", "htmlContent": "<p>x</p>"}, "default/t"),
    "stores": ({"name": "s"}, "default/s"),
    "manifests": ({"name": "m"}, "default/m"),
    "agents": ({"namespace": "support", "name": "triage"}, "support/triage"),
    "webhooks": ({"path": "hook", "functionName": "acme/f"}, "hook"),
    "schedules": (
        {"name": "nightly", "functionName": "acme/f", "cronExpression": "0 0 * * *"},
        "nightly",
    ),
    "databaseTriggers": (
        {"name": "t", "connectionName": "db", "tableName": "x", "functionName": "acme/f",
         "pollColumn": "id"},
        "t",
    ),
}


def _doc(spec: dict, kind: str = "SinasConfig", **top) -> dict:
    doc = {"apiVersion": "sinas.co/v1", "kind": kind}
    if kind == "SinasConfig":
        doc["metadata"] = {"name": "cfg"}
    else:
        doc["package"] = {"name": "pkg"}
    doc.update(top)
    doc["spec"] = spec
    return doc


async def _errors(doc: dict) -> list[tuple[str, str]]:
    config, validation = await ConfigParser.parse_and_validate(yaml.safe_dump(doc, sort_keys=False))
    assert config is None
    return [(e.path, e.message) for e in validation.errors]


def test_every_spec_section_is_covered():
    assert set(MINIMAL) == set(ConfigSpec.model_fields)


def _models(annotation, seen):
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return
        seen.add(annotation)
        for field in annotation.model_fields.values():
            _models(field.annotation, seen)
        return
    for arg in typing.get_args(annotation):
        _models(arg, seen)


def test_every_model_a_document_parses_into_forbids_unknown_keys():
    seen: set = set()
    _models(SinasConfig, seen)
    assert len(seen) > 30
    for model in seen:
        assert issubclass(model, ConfigModel), model.__name__
        assert model.model_config.get("extra") == "forbid", model.__name__


@pytest.mark.parametrize("section", sorted(MINIMAL))
def test_the_minimal_entry_is_valid(section):
    SinasConfig.model_validate(_doc({section: [MINIMAL[section][0]]}))


@pytest.mark.parametrize("section", sorted(MINIMAL))
async def test_an_unknown_key_is_rejected_naming_the_resource(section):
    item, key = MINIMAL[section]
    errors = await _errors(_doc({section: [{**item, "bogusField": 1}]}))
    assert len(errors) == 1, errors
    path, message = errors[0]
    assert path == f"spec.{section}[0].bogusField"
    assert f"'{key}'" in message and "unknown field 'bogusField'" in message
    assert "did you mean" not in message


@pytest.mark.parametrize(
    "section, extra, field",
    [
        ("connectors", {"auth": {"type": "bearer", "tokenUrll": "x"}}, "auth.tokenUrll"),
        ("connectors", {"retry": {"maxAttempts": 2, "backof": "x"}}, "retry.backof"),
        ("connectors", {"auth": {"tokenResponsePaths": {"accesToken": "a"}}}, "auth.tokenResponsePaths.accesToken"),
        ("connectors", {"operations": [{"name": "o", "method": "GET", "path": "/", "x": 1}]}, "operations[0].x"),
        ("mcpServers", {"auth": {"type": "bearer", "x": 1}}, "auth.x"),
        ("packageRepositories", {"auth": {"x": 1}}, "auth.x"),
        ("identityProviders", {"roleMappings": [{"group": "g", "roles": ["r"], "x": 1}]}, "roleMappings[0].x"),
        ("identityProviders", {"claimMappings": [{"claim": "c", "field": "f", "x": 1}]}, "claimMappings[0].x"),
        ("roles", {"permissions": [{"key": "k", "value": True, "x": 1}]}, "permissions[0].x"),
        ("users", {"permissions": [{"key": "k", "value": True, "x": 1}]}, "permissions[0].x"),
        ("users", {"identities": [{"provider": "p", "subject": "s", "x": 1}]}, "identities[0].x"),
        ("webhooks", {"dedup": {"key": "k", "ttl": 1}}, "dedup.ttl"),
        ("manifests", {"requiredResources": [{"type": "agent", "name": "a", "x": 1}]}, "requiredResources[0].x"),
        ("databaseConnections", {"annotations": [{"tableName": "t", "x": 1}]}, "annotations[0].x"),
        ("agents", {"enabledSkills": ["a/b", {"skill": "a/c", "x": 1}]}, "enabledSkills[1].x"),
        ("agents", {"enabledStores": [{"store": "a/b", "x": 1}]}, "enabledStores[0].x"),
        ("agents", {"enabledCollections": [{"collection": "a/b", "x": 1}]}, "enabledCollections[0].x"),
        ("agents", {"enabledMcpServers": [{"server": "a/b", "x": 1}]}, "enabledMcpServers[0].x"),
        ("components", {"enabledStores": [{"store": "a/b", "x": 1}]}, "enabledStores[0].x"),
    ],
)
async def test_an_unknown_key_in_a_nested_model_is_rejected(section, extra, field):
    item, key = MINIMAL[section]
    errors = await _errors(_doc({section: [{**item, **extra}]}))
    # One error: a value tried against every member of a union (`- a/b` or
    # `- {skill: ...}`) doesn't add "should be a valid string" noise.
    assert len(errors) == 1, errors
    path, message = errors[0]
    assert path == f"spec.{section}[0].{field}"
    assert f"'{key}': unknown field '{field}'" in message


@pytest.mark.parametrize(
    "top, path",
    [
        ({"metadata": {"name": "cfg", "lables": {}}}, "metadata.lables"),
        ({"bogus": 1}, "bogus"),
    ],
)
async def test_an_unknown_key_outside_the_resources_is_rejected(top, path):
    errors = await _errors(_doc({}, **top))
    assert [p for p, _ in errors] == [path]


async def test_an_unknown_package_key_is_rejected():
    doc = _doc({}, kind="SinasPackage", package={"name": "pkg", "versoin": "1.0.0"})
    assert await _errors(doc) == [
        ("package.versoin", "unknown field 'package.versoin' (did you mean 'version'?)")
    ]
    doc = _doc({}, kind="SinasPackage", package={"name": "pkg", "requires": [{"name": "x", "verison": ">=1"}]})
    assert [p for p, _ in await _errors(doc)] == ["package.requires[0].verison"]


@pytest.mark.parametrize(
    "section, key, hint",
    [
        ("agents", "output_schema", "outputSchema"),  # snake_case of a camelCase field
        ("agents", "OutputSchema", "outputSchema"),
        ("agents", "system_prompt", "systemPrompt"),
        ("functions", "input_schema", "inputSchema"),
        ("mcpServers", "toolDney", "toolDeny"),  # a typo
        ("pipelines", "output_expr", "outputExpr"),
        ("schedules", "cron_expression", "cronExpression"),
    ],
)
async def test_did_you_mean(section, key, hint):
    item, name = MINIMAL[section]
    errors = await _errors(_doc({section: [{**item, key: 1}]}))
    assert any(
        message.endswith(f"'{name}': unknown field '{key}' (did you mean '{hint}'?)")
        for _, message in errors
    ), errors


async def test_did_you_mean_in_a_nested_model_and_for_a_section():
    errors = await _errors(_doc({"connectors": [{**MINIMAL["connectors"][0], "auth": {"token_url": "x"}}]}))
    assert errors[0][1].endswith("unknown field 'auth.token_url' (did you mean 'tokenUrl'?)")
    errors = await _errors(_doc({"agent": [{"name": "x"}]}))
    assert errors == [("spec.agent", "unknown field 'spec.agent' (did you mean 'agents'?)")]


async def test_the_reported_case_names_both_keys():
    """The report: an agent with `output_schema:` and a bogus key parsed, and
    only its name and namespace survived."""
    agent = {
        "namespace": "support", "name": "triage",
        "output_schema": {"type": "object"}, "totally_bogus_field": 123,
    }
    assert sorted(await _errors(_doc({"agents": [agent]}))) == [
        ("spec.agents[0].output_schema",
         "agent 'support/triage': unknown field 'output_schema' (did you mean 'outputSchema'?)"),
        ("spec.agents[0].totally_bogus_field",
         "agent 'support/triage': unknown field 'totally_bogus_field'"),
    ]


async def test_other_schema_errors_are_per_resource_too():
    errors = await _errors(_doc({"agents": [{"name": "a", "temperature": "hot"}, {"description": "x"}]}))
    assert ("spec.agents[1].name", "agent #2: name: Field required") in errors
    assert any(
        p == "spec.agents[0].temperature" and m.startswith("agent 'default/a': temperature: ")
        for p, m in errors
    ), errors


# ------------------------------------------------------------- still accepted


def test_the_accepted_spellings_still_parse():
    config = SinasConfig.model_validate(_doc({
        "pipelines": [
            {"name": "a", "output.$": "$.steps.last"},
            {"name": "b", "outputExpr": "$.steps.first"},
            {"name": "c", "output": {"x": 1}},
        ],
        "agents": [{
            "name": "a",
            "enabledSkills": ["a/b", {"skill": "a/c", "preload": True}],
            "enabledStores": ["a/s", {"store": "a/t", "access": "readwrite"}],
            "enabledCollections": ["a/c", {"collection": "a/d"}],
            "enabledMcpServers": ["a/m", {"server": "a/n", "tools": ["x*"]}],
            "enabledConnectors": [{"connector": "a/c", "operations": ["op"], "anything": 1}],
            "systemTools": ["codeExecution", {"name": "databaseIntrospection", "connections": ["db"]}],
            "hooks": {"onUserMessage": [{"function": "a/f", "free": "form"}]},
            "retentionDays": "never",
        }],
        "dependencies": [{"packageName": "requests==2.0"}],
        "functions": [{"name": "f", "code": "x", "inputSchema": {"type": "object", "anyKey": 1}}],
    }))
    a, b, c = config.spec.pipelines
    assert a.output_mapping() == {"output.$": "$.steps.last"}
    assert b.output_mapping() == {"output.$": "$.steps.first"}
    assert c.output_mapping() == {"output": {"x": 1}}
    assert config.spec.dependencies[0].version == "2.0"


def test_a_package_with_requires_none_still_parses():
    SinasConfig.model_validate(_doc({}, kind="SinasPackage", package={"name": "p", "requires": None}))


# ------------------------------------------------------------- the repo's YAML

REPO = Path(__file__).resolve().parents[3]
_SECTIONS = set(ConfigSpec.model_fields)


def _repo_documents():
    """Every whole config document and every config fragment (`spec:` or
    `agents:` blocks) in the repo's YAML files and docs."""
    for path in sorted(REPO.glob("backend/tests/**/*.y*ml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind") in ("SinasConfig", "SinasPackage"):
                yield str(path.relative_to(REPO)), doc
    for path in sorted(REPO.glob("docs-mint/**/*.md*")) + sorted(REPO.glob("docs/**/*.md")):
        for i, block in enumerate(re.findall(r"```ya?ml[^\n]*\n(.*?)```", path.read_text(), re.S)):
            try:
                doc = yaml.safe_load(block)
            except yaml.YAMLError:
                continue  # templated or illustrative, not a config
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") not in ("SinasConfig", "SinasPackage"):
                spec = doc["spec"] if isinstance(doc.get("spec"), dict) else doc
                if not set(spec) & _SECTIONS:
                    continue
                doc = _doc(spec)
            yield f"{path.relative_to(REPO)}#{i}", doc


def test_no_config_in_the_repo_uses_an_unknown_key():
    if not (REPO / "docs-mint").is_dir():
        pytest.skip("not a full checkout")
    checked, offenders = 0, []
    for where, doc in _repo_documents():
        checked += 1
        try:
            SinasConfig.model_validate(doc)
        except ValidationError as e:
            # Docs fragments may leave out required fields; only unknown keys count.
            offenders += [
                f"{where}: {'.'.join(map(str, err['loc']))}"
                for err in e.errors() if err["type"] == "extra_forbidden"
            ]
    assert checked > 20
    assert offenders == []


# ------------------------------------------------------------- every channel

_BAD = (
    "apiVersion: sinas.co/v1\nkind: SinasConfig\nmetadata: {name: cfg}\nspec:\n"
    "  agents:\n    - {namespace: support, name: triage, output_schema: {type: object}}\n"
)
_EXPECTED = "agent 'support/triage': unknown field 'output_schema' (did you mean 'outputSchema'?)"


async def test_validate_reports_it(client, admin_user):
    r = await client.post("/api/v1/config/validate", json={"config": _BAD}, headers=auth_headers(admin_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is False
    assert body["errors"] == [{"path": "spec.agents[0].output_schema", "message": _EXPECTED}]


@pytest.mark.parametrize("flags", [{}, {"dryRun": True}, {"force": True}])
async def test_apply_reports_it_even_when_forced(client, admin_user, flags):
    r = await client.post(
        "/api/v1/config/apply", json={"config": _BAD, **flags}, headers=auth_headers(admin_user)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is False
    assert body["errors"] == [f"spec.agents[0].output_schema: {_EXPECTED}"]


def _bad_package() -> tuple[str, str]:
    pkg = f"p-{uuid.uuid4().hex[:8]}"
    text = (
        f"apiVersion: sinas.co/v1\nkind: SinasPackage\npackage: {{name: {pkg}}}\nspec:\n"
        f"  agents:\n    - {{namespace: {pkg}, name: triage, output_schema: {{type: object}}}}\n"
    )
    return pkg, text


async def test_package_preview_and_install_report_it(db, admin_user):
    from app.services.package_service import PackageService

    pkg, text = _bad_package()
    expected = f"agent '{pkg}/triage': unknown field 'output_schema' (did you mean 'outputSchema'?)"
    with pytest.raises(ValueError, match="Package validation failed") as e:
        await PackageService(db).preview(text, str(admin_user.id))
    assert expected in str(e.value)
    with pytest.raises(ValueError, match="Package validation failed") as e:
        await PackageService(db).install(text, str(admin_user.id))
    assert expected in str(e.value)


async def test_package_validate_tool_reports_it(db, admin_user):
    from app.services.package_tools import _validate

    _pkg, text = _bad_package()
    result = await _validate(db, {"yaml": text}, {"sinas.config.validate:all": True})
    assert result["valid"] is False
    assert "unknown field 'output_schema' (did you mean 'outputSchema'?)" in result["errors"][0]["message"]
