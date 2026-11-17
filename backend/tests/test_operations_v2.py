"""Provider switches require admin, valid configuration and an idle generation queue."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_admin_status_and_mode_validation_never_expose_secrets(tmp_path):
    settings = Settings(_env_file=None, app_env="test", data_dir=tmp_path, database_path=tmp_path / "app.db")
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        assert client.get("/api/v2/admin/status").status_code == 401
        verification = client.post("/api/v2/auth/verification", json={"address": "ops@test.local", "purpose": "register"}).json()
        account = client.post("/api/v2/auth/register", json={"address": "ops@test.local", "password": "test-password-123", "nickname": "运行测试",
            "challengeId": verification["challengeId"], "code": verification["developmentCode"]}).json()["account"]
        assert client.post("/api/v2/admin/services", json={"llmMode": "mock", "imageMode": "mock"}).status_code == 403
        with client.app.state.studio.db.transaction() as connection:
            connection.execute("UPDATE accounts SET role='admin' WHERE id=?", (account["id"],))
        status = client.get("/api/v2/admin/status")
        assert status.status_code == 200 and not status.json()["llmConfigured"]
        assert "password" not in status.text.lower() and "api_key" not in status.text.lower()
        for mode in [{"llmMode": "real", "imageMode": "mock"}, {"llmMode": "mock", "imageMode": "real"}]:
            assert client.post("/api/v2/admin/services", json=mode).status_code == 503
            assert client.get("/api/health").json()["mode"] == "mock"
        assert client.post("/api/v2/admin/services", json={"llmMode": "mock", "imageMode": "mock"}).status_code == 200
