"""manifests.public_info — what an installed app publishes on the
unauthenticated GET /info, so clients can discover it (and, for a sibling
service such as Cassis or Cellar, its browser-reachable URL) without any
environment variable on Sinas and without a restart."""

import pytest

from app.schemas.manifest import ManifestCreate, validate_public_info
from tests.conftest import make_token


def _manifest(namespace: str, name: str, public_info: dict | None = None, **extra) -> dict:
    body = {
        "namespace": namespace,
        "name": name,
        "description": "test",
        "required_resources": [],
        "required_permissions": [],
        "optional_permissions": [],
        "exposed_namespaces": {},
        "store_dependencies": [],
    }
    if public_info is not None:
        body["public_info"] = public_info
    body.update(extra)
    return body


class TestPublicInfoOnInfo:
    async def test_info_has_no_services_without_manifests(self, client):
        resp = await client.get("/info")
        assert resp.status_code == 200
        assert resp.json()["services"] == {}

    async def test_manifest_public_info_appears_on_info(self, client, admin_user):
        headers = {"Authorization": f"Bearer {make_token(admin_user)}"}
        resp = await client.post(
            "/api/v1/manifests",
            json=_manifest(
                "cellar", "cellar", {"url": "https://cellar.example.com", "version": "0.1.0"}
            ),
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["public_info"] == {
            "url": "https://cellar.example.com",
            "version": "0.1.0",
        }

        # a manifest without public_info publishes nothing
        resp = await client.post("/api/v1/manifests", json=_manifest("one", "one"), headers=headers)
        assert resp.status_code == 201, resp.text

        info = (await client.get("/info")).json()
        assert info["services"] == {
            "cellar": {"url": "https://cellar.example.com", "version": "0.1.0"}
        }

    async def test_manifests_in_one_namespace_merge(self, client, admin_user):
        headers = {"Authorization": f"Bearer {make_token(admin_user)}"}
        for name, info in [("a", {"url": "https://x"}), ("b", {"docs": "https://x/docs"})]:
            resp = await client.post(
                "/api/v1/manifests", json=_manifest("acme", name, info), headers=headers
            )
            assert resp.status_code == 201, resp.text
        info = (await client.get("/info")).json()
        assert info["services"]["acme"] == {"url": "https://x", "docs": "https://x/docs"}

    async def test_update_and_deactivate(self, client, admin_user):
        headers = {"Authorization": f"Bearer {make_token(admin_user)}"}
        resp = await client.post(
            "/api/v1/manifests",
            json=_manifest("cellar", "cellar", {"url": "https://old"}),
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

        resp = await client.put(
            "/api/v1/manifests/cellar/cellar",
            json={"public_info": {"url": "https://new"}},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert (await client.get("/info")).json()["services"] == {"cellar": {"url": "https://new"}}

        resp = await client.put(
            "/api/v1/manifests/cellar/cellar", json={"is_active": False}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert (await client.get("/info")).json()["services"] == {}

    async def test_rejects_bad_public_info(self, client, admin_user):
        headers = {"Authorization": f"Bearer {make_token(admin_user)}"}
        for bad in [["list"], {"bad key": 1}, {"x": "y" * 5000}]:
            resp = await client.post(
                "/api/v1/manifests", json=_manifest("cellar", "cellar", bad), headers=headers
            )
            assert resp.status_code == 422, resp.text


class TestPublicInfoValidation:
    def test_defaults_and_shapes(self):
        assert validate_public_info(None) == {}
        assert validate_public_info({}) == {}
        assert validate_public_info({"url": "https://x", "nested": {"a": [1, 2]}}) == {
            "url": "https://x",
            "nested": {"a": [1, 2]},
        }
        assert ManifestCreate(name="m").public_info == {}

    @pytest.mark.parametrize("bad", [["x"], "str", {"1x": 1}, {"a-b": 1}, {"x": "y" * 5000}])
    def test_rejects(self, bad):
        with pytest.raises(ValueError):
            validate_public_info(bad)


class TestPublicInfoInConfig:
    async def test_yaml_public_info_is_parsed(self):
        from app.services.config_parser import ConfigParser

        yaml_str = """
apiVersion: sinas.co/v1
kind: SinasConfig
metadata:
  name: test
spec:
  manifests:
    - namespace: cellar
      name: cellar
      publicInfo:
        url: https://cellar.example.com
"""
        config, validation = await ConfigParser.parse_and_validate(yaml_str)
        assert validation.errors == [], [str(e) for e in validation.errors]
        assert config is not None
        assert config.spec.manifests[0].publicInfo == {"url": "https://cellar.example.com"}

    async def test_yaml_bad_public_info_is_an_error(self):
        from app.services.config_parser import ConfigParser

        yaml_str = """
apiVersion: sinas.co/v1
kind: SinasConfig
metadata:
  name: test
spec:
  manifests:
    - namespace: cellar
      name: cellar
      publicInfo: [not, an, object]
"""
        config, validation = await ConfigParser.parse_and_validate(yaml_str)
        assert config is None or validation.errors, "a non-object publicInfo must not parse"
