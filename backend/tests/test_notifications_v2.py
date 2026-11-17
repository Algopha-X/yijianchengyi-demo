"""Queue ordering and completion receipts stay scoped to their owner."""

import time
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_notices_and_queue_positions_are_persistent_and_private(tmp_path):
    settings = Settings(_env_file=None, app_env="test", data_dir=tmp_path, database_path=tmp_path / "app.db")
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        task = client.post("/api/tasks", json={"scenario": "poster", "rawText": "标题：通知测试。正文：测试文字。", "assetIds": []}).json()
        job = client.post(f"/api/tasks/{task['id']}/analyze", headers={"Idempotency-Key": str(uuid4())}, json={"expectedRevision": 1}).json()
        for _ in range(100):
            current = client.get(f"/api/v2/jobs/{job['id']}").json()
            if current["status"] not in {"queued", "running"}:
                break
            if current["status"] == "queued":
                assert current["queuePosition"] == 1
            time.sleep(.01)
        assert current["status"] == "succeeded" and current["queuePosition"] is None
        notices = client.get("/api/v2/notifications").json()["items"]
        assert len(notices) == 1 and not notices[0]["read"]
        assert client.post(f"/api/v2/notifications/{job['id']}/read").status_code == 200
        assert client.get("/api/v2/notifications").json()["items"][0]["read"]
        client.cookies.clear()
        client.post("/api/session")
        assert client.get("/api/v2/notifications").json()["items"] == []
        assert client.get(f"/api/v2/jobs/{job['id']}").status_code == 404
        assert client.post(f"/api/v2/notifications/{job['id']}/read").status_code == 404


def test_queue_position_counts_actual_fifo_and_unfinished_cannot_be_read(tmp_path):
    settings = Settings(_env_file=None, app_env="test", data_dir=tmp_path, database_path=tmp_path / "app.db")
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        client.portal.call(client.app.state.studio.stop)
        queued = []
        for index in range(2):
            task = client.post("/api/tasks", json={"scenario": "poster", "rawText": f"排队测试{index}", "assetIds": []}).json()
            queued.append(client.post(f"/api/tasks/{task['id']}/analyze", headers={"Idempotency-Key": str(uuid4())}, json={"expectedRevision": 1}).json())
        for index, job in enumerate(queued):
            status = client.get(f"/api/v2/jobs/{job['id']}").json()
            assert status["status"] == "queued" and status["queuePosition"] == index + 1
            assert client.post(f"/api/v2/notifications/{job['id']}/read").status_code == 409
