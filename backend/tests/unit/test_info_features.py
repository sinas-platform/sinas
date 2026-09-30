"""/info feature flags — unauthenticated instance discovery for clients."""

from app.core.config import settings


class TestInfoFeatures:
    async def test_code_execution_flag_reflects_setting(self, client, monkeypatch):
        monkeypatch.setattr(settings, "code_execution_enabled", False)
        resp = await client.get("/info")
        assert resp.status_code == 200
        assert resp.json()["features"]["code_execution"] is False

        monkeypatch.setattr(settings, "code_execution_enabled", True)
        resp = await client.get("/info")
        assert resp.json()["features"]["code_execution"] is True

    async def test_info_is_unauthenticated(self, client):
        resp = await client.get("/info")
        assert resp.status_code == 200
        assert "version" in resp.json()
