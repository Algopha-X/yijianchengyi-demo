"""Server validated object edits and immutable render snapshots."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, app_env="test", data_dir=tmp_path, database_path=tmp_path / "app.db"))) as client:
        client.post("/api/session")
        yield client


def create(client):
    task = client.post("/api/tasks", json={"scenario": "poster", "rawText": "标题：排版测试。正文：文字必须完整保留。", "assetIds": []}).json()
    return client.patch(f"/api/tasks/{task['id']}", json={"expectedRevision": 1, "templateId": "poster-fresh-v1"}).json()


def test_layout_edits_increment_revision_invalidate_and_copy(client):
    task = create(client)
    path = f"/api/v2/tasks/{task['id']}/layout"
    original = client.get(path).json()
    assert not original["customized"]
    response = client.patch(path, json={"expectedRevision": task["revision"], "fields": {"title": {"color": "#123456", "align": "center"}}})
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["task"]["revision"] == task["revision"] + 1
    assert edited["task"]["confirmedRevision"] is None and edited["customized"]
    assert edited["document"]["fields"]["title"]["color"] == "#123456"
    assert client.get("/api/templates/poster-fresh-v1").json()["fields"]["title"]["color"] != "#123456"
    copied = client.post(f"/api/v2/tasks/{task['id']}/copy", json={"expectedRevision": edited["task"]["revision"]}).json()
    assert client.get(f"/api/v2/tasks/{copied['id']}/layout").json()["document"] == edited["document"]
    assert client.patch(path, json={"expectedRevision": task["revision"], "reset": True}).status_code == 409
    reset = client.patch(path, json={"expectedRevision": edited["task"]["revision"], "reset": True}).json()
    assert not reset["customized"] and reset["document"] == original["document"]


@pytest.mark.parametrize("fields", [
    {"title": {"color": "url(https://example.com)"}},
    {"title": {"fontFamily": "<script>"}},
    {"evil": {"fontSize": 20}},
    {"title": {"box": {"x": 1000, "y": 1, "width": 400, "height": 200}}},
    {"title": {"fontSize": 12, "minFontSize": 48}},
    {"title": {"fontSize": True}},
])
def test_layout_rejects_untrusted_or_invalid_values(client, fields):
    task = create(client)
    response = client.patch(f"/api/v2/tasks/{task['id']}/layout", json={"expectedRevision": task["revision"], "fields": fields})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/tasks/{task['id']}").json()["revision"] == task["revision"]


def test_job_snapshot_captures_layout_and_private_boundaries(client):
    task = create(client)
    path = f"/api/v2/tasks/{task['id']}/layout"
    task = client.patch(path, json={"expectedRevision": task["revision"], "fields": {"title": {"color": "#123456"}}}).json()["task"]
    job = client.post(f"/api/tasks/{task['id']}/analyze", json={"expectedRevision": task["revision"]}, headers={"Idempotency-Key": str(uuid4())}).json()
    with client.app.state.studio.db.transaction() as connection:
        snapshot = json.loads(connection.execute("SELECT snapshot_json FROM jobs WHERE id=?", (job["id"],)).fetchone()[0])
    assert snapshot["template"]["fields"]["title"]["color"] == "#123456"
    client.cookies.clear()
    client.post("/api/session")
    assert client.get(path).status_code == 404
    assert client.patch(path, json={"expectedRevision": task["revision"], "reset": True}).status_code == 404
