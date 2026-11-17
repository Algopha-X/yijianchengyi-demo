"""Task metadata, immutable copies, isolation and revision races."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, app_env="test", data_dir=tmp_path, database_path=tmp_path / "app.db")
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/session")
        assert response.status_code == 200
        yield client


def create(client):
    return client.post("/api/tasks", json={
        "scenario": "poster", "style": "fresh", "rawText": "标题：测试作品", "assetIds": [],
    }).json()


def test_scope_stable_and_separate(client):
    first = client.post("/api/session").headers["X-Session-Scope"]
    assert len(first) == 64
    assert first == client.post("/api/session").headers["X-Session-Scope"]
    client.cookies.clear()
    assert first != client.post("/api/session").headers["X-Session-Scope"]


def test_rename_is_metadata_and_cas(client):
    task = create(client)
    path = f"/api/v2/tasks/{task['id']}"
    request = {"expectedRevision": 1, "expectedManagementRevision": 0, "displayName": "我的海报"}
    response = client.post(path + "/rename", json=request)
    assert response.status_code == 200, response.text
    renamed = response.json()
    assert renamed["revision"] == 1 and renamed["rawText"] == task["rawText"]
    assert renamed["displayName"] == "我的海报" and renamed["managementRevision"] == 1
    assert client.post(path + "/rename", json=request).status_code == 409
    assert client.get(path).json() == renamed
    assert client.post(path + "/rename", json={**request, "expectedManagementRevision": 1, "displayName": " "}).status_code == 422


def test_copy_is_new_unconfirmed_draft_delete_is_isolated(client):
    task = create(client)
    path = f"/api/v2/tasks/{task['id']}"
    response = client.post(path + "/copy", json={"expectedRevision": 1})
    assert response.status_code == 201, response.text
    copied = response.json()
    assert copied["id"] != task["id"] and copied["revision"] == 1
    assert copied["fields"] == task["fields"] and copied["rawText"] == task["rawText"]
    for key in ["analysis", "confirmedRevision", "previewAssetId", "activeJob"]:
        assert copied[key] is None
    assert client.delete(path + "?expectedRevision=2").status_code == 409
    assert client.delete(path + "?expectedRevision=1").status_code == 204
    assert client.get(path).status_code == 404
    assert client.get(f"/api/v2/tasks/{copied['id']}").status_code == 200
    client.cookies.clear()
    client.post("/api/session")
    assert client.get(f"/api/v2/tasks/{copied['id']}").status_code == 404
    assert client.get("/api/v2/tasks").json() == {"items": []}


def test_copy_stale_and_strict_revision(client):
    task = create(client)
    path = f"/api/v2/tasks/{task['id']}/copy"
    assert client.post(path, json={"expectedRevision": 2}).status_code == 409
    for value in [True, "1"]:
        assert client.post(path, json={"expectedRevision": value}).status_code == 422
