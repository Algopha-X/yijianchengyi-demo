"""Private/public boundaries, real decode/crop and explicit image-provider modes."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, app_env="test", data_dir=tmp_path, database_path=tmp_path / "app.db"))) as client:
        client.post("/api/session")
        yield client


def upload(client):
    output = io.BytesIO()
    Image.new("RGB", (800, 600), "red").save(output, format="PNG")
    return client.post("/api/assets", files={"file": ("photo.png", output.getvalue(), "image/png")}).json()


def test_library_search_favorite_thumbnail_and_crop(client):
    asset = upload(client)
    path = f"/api/v2/materials/{asset['id']}"
    assert len(client.get("/api/v2/materials").json()["items"]) == 1
    assert client.patch(path, json={"name": "校园图", "tags": ["校园"]}).status_code == 200
    assert len(client.get("/api/v2/materials?search=校园").json()["items"]) == 1
    assert client.get("/api/v2/materials?favorite=true").json()["items"] == []
    assert client.post(path + "/favorite", json={"favorite": True}).json()["favorite"]
    assert len(client.get("/api/v2/materials?favorite=true").json()["items"]) == 1
    thumbnail = client.get(path + "/thumbnail")
    assert thumbnail.status_code == 200
    with Image.open(io.BytesIO(thumbnail.content)) as image:
        assert image.size == (480, 360)
    cropped = client.post(path + "/crop", json={"x": 100, "y": 100, "width": 300, "height": 200})
    assert cropped.status_code == 201, cropped.text
    assert (cropped.json()["width"], cropped.json()["height"]) == (300, 200)
    assert client.post(path + "/crop", json={"x": 700, "y": 100, "width": 300, "height": 200}).status_code == 422
    assert client.post(path + "/crop", json={"x": True, "y": 0, "width": 1, "height": 1}).status_code == 422


def test_private_materials_cannot_be_read_or_reviewed_by_other_session(client):
    asset = upload(client)
    path = f"/api/v2/materials/{asset['id']}"
    assert client.patch(path, json={"name": "待审图片", "requestPublic": True}).json()["visibility"] == "pending"
    assert client.post(path + "/review", json={"decision": "approve", "expectedRevision": 1}).status_code == 403
    client.cookies.clear()
    client.post("/api/session")
    assert client.get("/api/v2/materials?scope=all").json()["items"] == []
    for suffix in ["/thumbnail", "/import"]:
        response = client.get(path + suffix) if suffix == "/thumbnail" else client.post(path + suffix)
        assert response.status_code == 404
    assert client.patch(path, json={"name": "偷改"}).status_code == 404


def test_public_review_import_and_owner_file_remains_private(client):
    asset = upload(client)
    path = f"/api/v2/materials/{asset['id']}"
    client.patch(path, json={"name": "已批准图片", "requestPublic": True})
    with client.app.state.studio.db.transaction() as connection:
        connection.execute("UPDATE material_details SET visibility='public' WHERE asset_id=?", (asset["id"],))
    client.cookies.clear()
    client.post("/api/session")
    assert len(client.get("/api/v2/materials?scope=public").json()["items"]) == 1
    assert client.get(f"/api/assets/{asset['id']}/file").status_code == 404
    imported = client.post(path + "/import")
    assert imported.status_code == 201 and imported.json()["id"] != asset["id"]
    assert client.get(imported.json()["fileUrl"]).status_code == 200


def test_generation_is_explicit_mock_and_output_passes_decoder(client):
    response = client.post("/api/v2/materials/generate", json={"prompt": "校园海报抽象背景", "width": 512, "height": 512})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["mode"] == "mock" and "Mock" in result["message"]
    with Image.open(io.BytesIO(client.get(result["asset"]["fileUrl"]).content)) as image:
        assert image.size == (512, 512)
    listing = client.get("/api/v2/materials").json()["items"]
    assert listing[0]["generatedMode"] == "mock"
    source = result["asset"]["id"]
    edited = client.post("/api/v2/materials/generate", json={"prompt": "颜色变化", "sourceAssetId": source})
    assert edited.status_code == 201 and edited.json()["mode"] == "mock"


def test_real_image_configuration_never_silently_falls_back(tmp_path):
    settings = Settings(_env_file=None, app_env="test", image_mode="real", data_dir=tmp_path, database_path=tmp_path / "app.db")
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        response = client.post("/api/v2/materials/generate", json={"prompt": "test"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "IMAGE_NOT_CONFIGURED"
