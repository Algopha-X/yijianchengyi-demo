"""Real SQLite, account boundaries, strict draft schemas and Chromium previews."""

import copy
import io
import sqlite3
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.template_workbench_v2 import router
from app.config import Settings
from app.main import create_app
from app.services import accounts
from app.services import template_workbench as workbench


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(_env_file=None, app_env="test", llm_mode="mock",
                              data_dir=tmp_path / "data", database_path=tmp_path / "app.db"))
    if not any(getattr(route, "path", "") == "/api/v2/template-drafts" for route in app.routes):
        app.include_router(router)
    with TestClient(app) as client:
        accounts.initialize(app.state.studio.db)
        workbench.initialize(app.state.studio.db)
        assert client.post("/api/session").status_code == 200
        yield client


def register(client, address="author@example.com", role="user"):
    client.cookies.clear()
    assert client.post("/api/session").status_code == 200
    challenge = client.post("/api/v2/auth/verification", json={"address": address, "purpose": "register"}).json()
    response = client.post("/api/v2/auth/register", json={
        "address": address, "password": "test-password-123", "nickname": "模板作者",
        "challengeId": challenge["challengeId"], "code": challenge["developmentCode"],
    })
    assert response.status_code == 201, response.text
    account = response.json()["account"]
    if role != "user":
        # Provision test reviewers through the fixture database, not a user API bypass.
        with client.app.state.studio.db.transaction() as connection:
            connection.execute("UPDATE accounts SET role=? WHERE id=?", (role, account["id"]))
    return client.cookies.get("ycy_session")


def use_token(client, token):
    client.cookies.clear()
    client.cookies.set("ycy_session", token)


def create(client):
    response = client.post("/api/v2/template-drafts", json={"sourceTemplateId": "poster-fresh-v1", "name": "我的模板草稿"})
    assert response.status_code == 201, response.text
    return response.json()


def patch(client, draft, document=None, **extra):
    return client.patch(f"/api/v2/template-drafts/{draft['id']}", json={
        "expectedVersion": draft["version"], "document": document or draft["document"], **extra,
    })


def submit(client, draft):
    response = client.post(f"/api/v2/template-drafts/{draft['id']}/submit", json={"expectedVersion": draft["version"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_private_draft_crud_and_compare_and_swap(client):
    first = create(client)
    assert first["version"] == 1 and first["status"] == "draft"
    assert client.get("/api/v2/template-drafts").json()["items"][0]["id"] == first["id"]
    document = copy.deepcopy(first["document"])
    document["canvas"]["background"] = "#FFEEDD"
    updated = patch(client, first, document)
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    stale = patch(client, first)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "TEMPLATE_VERSION_CONFLICT"
    assert client.get(f"/api/v2/template-drafts/{first['id']}").json()["document"]["canvas"]["background"] == "#FFEEDD"
    client.cookies.clear()
    assert client.post("/api/session").status_code == 200
    assert client.get(f"/api/v2/template-drafts/{first['id']}").status_code == 404
    assert client.get("/api/v2/template-drafts").json()["items"] == []


@pytest.mark.parametrize("change", ["html", "font", "box", "fraction", "url"])
def test_template_document_rejects_unsafe_or_out_of_contract_data(client, change):
    draft = create(client)
    document = copy.deepcopy(draft["document"])
    if change == "html":
        document["html"] = "<script>bad()</script>"
    elif change == "font":
        document["fields"]["title"]["fontFamily"] = "https://bad.example/font.woff"
    elif change == "box":
        document["fields"]["title"]["box"]["width"] = 9999
    elif change == "fraction":
        document["fields"]["title"]["box"]["x"] = 1.5
    else:
        document["imageSlot"]["url"] = "https://bad.example/image.png"
    response = patch(client, draft, document)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def grid():
    placements = [
        {"target": "title", "column": 0, "row": 0, "columnSpan": 2},
        {"target": "subtitle", "column": 0, "row": 1, "columnSpan": 2},
        {"target": "body", "column": 0, "row": 2, "columnSpan": 2, "rowSpan": 2},
        {"target": "image", "column": 0, "row": 4, "columnSpan": 2, "rowSpan": 2},
        {"target": "time", "column": 0, "row": 6},
        {"target": "location", "column": 1, "row": 6},
        {"target": "organizer", "column": 0, "row": 7, "columnSpan": 2},
    ]
    return {"columns": 2, "rows": 8, "margin": 60, "gap": 12, "placements": placements}


def test_grid_merges_generate_bounded_boxes_and_real_preview(client):
    draft = create(client)
    response = patch(client, draft, grid=grid())
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["document"]["fields"]["title"]["box"] == {"x": 60, "y": 60, "width": 960, "height": 154}
    assert draft["document"]["fields"]["time"]["box"]["width"] == 474
    preview = client.post(f"/api/v2/template-drafts/{draft['id']}/preview", json={
        "expectedVersion": draft["version"], "fields": workbench.SAMPLE_FIELDS,
    })
    assert preview.status_code == 200, preview.text
    with Image.open(io.BytesIO(preview.content)) as image:
        image.load()
        assert image.size == (1080, 1440)
    stale = client.post(f"/api/v2/template-drafts/{draft['id']}/preview", json={
        "expectedVersion": 1, "fields": workbench.SAMPLE_FIELDS,
    })
    assert stale.status_code == 409


@pytest.mark.parametrize("invalid", ["overlap", "bounds", "nested", "duplicate", "boolean"])
def test_grid_rejects_overlaps_out_of_bounds_and_unsupported_nested_schema(client, invalid):
    draft = create(client)
    value = grid()
    if invalid == "overlap":
        value["placements"][1]["row"] = 0
    elif invalid == "bounds":
        value["placements"][0]["columnSpan"] = 3
    elif invalid == "nested":
        value["placements"][0]["children"] = []
    elif invalid == "boolean":
        value["columns"] = True
    else:
        value["placements"][0]["target"] = "body"
    response = patch(client, draft, grid=value)
    assert response.status_code == 422, response.text


def test_submission_requires_login_and_locks_reviewed_revision(client):
    draft = create(client)
    assert client.post(f"/api/v2/template-drafts/{draft['id']}/submit", json={"expectedVersion": 1}).status_code == 401
    register(client)
    draft = create(client)
    submitted = submit(client, draft)
    assert submitted["status"] == "pending" and submitted["version"] == 2
    response = patch(client, submitted)
    assert response.status_code == 409 and response.json()["error"]["code"] == "TEMPLATE_LOCKED"
    assert client.get("/api/v2/template-reviews").status_code == 403


def test_only_other_reviewer_can_approve_immutable_publication(client):
    author_token = register(client, role="reviewer")
    draft = submit(client, create(client))
    url = f"/api/v2/template-drafts/{draft['id']}/review"
    request = {"expectedVersion": draft["version"], "decision": "approve", "comment": "布局可用"}
    response = client.post(url, json=request)
    assert response.status_code == 403 and response.json()["error"]["code"] == "SELF_REVIEW_FORBIDDEN"
    review_preview_url = f"/api/v2/template-drafts/{draft['id']}/review-preview"
    assert client.post(review_preview_url, json={"expectedVersion": draft["version"]}).status_code == 403
    register(client, "reviewer@example.com", "reviewer")
    assert client.get("/api/v2/template-reviews").json()["items"][0]["id"] == draft["id"]
    reviewer_preview = client.post(review_preview_url, json={"expectedVersion": draft["version"]})
    assert reviewer_preview.status_code == 200, reviewer_preview.text
    with Image.open(io.BytesIO(reviewer_preview.content)) as image:
        image.load()
        assert image.size == (1080, 1440)
    response = client.post(url, json=request)
    assert response.status_code == 200, response.text
    published = response.json()
    assert published["status"] == "approved" and published["version"] == 3
    publication_id = published["publicationId"]
    listing = client.get("/api/v2/published-templates").json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == publication_id
    detail = client.get(f"/api/v2/published-templates/{publication_id}").json()
    assert detail["document"]["id"] == "poster-fresh-v1"
    with Image.open(io.BytesIO(client.get(detail["thumbnailUrl"]).content)) as image:
        image.load()
        assert image.size == (1080, 1440)
    assert workbench.get_published(client.app.state.studio.db, publication_id) == detail["document"]
    # Studio reads published templates while PATCH/enqueue holds its own writer.
    with client.app.state.studio.db.transaction():
        assert workbench.get_published(client.app.state.studio.db, publication_id) == detail["document"]
    task_response = client.post("/api/tasks", json={
        "scenario": "poster", "style": "fresh", "assetIds": [],
        "rawText": "标题：校园读书会。正文：带上一本喜欢的书，分享故事。时间：周六下午。地点：图书馆。",
    })
    assert task_response.status_code == 201, task_response.text
    task = task_response.json()
    bound = client.patch(f"/api/tasks/{task['id']}", json={
        "expectedRevision": task["revision"], "templateId": publication_id,
    })
    assert bound.status_code == 200, bound.text
    task = bound.json()

    def run(kind):
        response = client.post(f"/api/tasks/{task['id']}/{kind}",
                               json={"expectedRevision": task["revision"]},
                               headers={"Idempotency-Key": str(uuid4())})
        assert response.status_code == 202, response.text
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            progress = client.get(f"/api/jobs/{response.json()['id']}").json()
            if progress["status"] not in {"queued", "running"}:
                assert progress["status"] == "succeeded", progress
                return client.get(f"/api/tasks/{task['id']}").json()
            time.sleep(0.02)
        raise AssertionError("发布模板绑定任务后生成超时")

    task = run("analyze")
    confirmation = client.post(f"/api/tasks/{task['id']}/confirm", json={
        "expectedRevision": task["revision"],
        "confirmedFactIds": [fact["id"] for fact in task["analysis"]["facts"]],
    })
    assert confirmation.status_code == 200, confirmation.text
    task = run("render")
    assert task["templateId"] == publication_id
    with Image.open(io.BytesIO(client.get(f"/api/assets/{task['previewAssetId']}/file").content)) as image:
        image.load()
        assert image.size == (1080, 1440)
    native = client.get(f"/api/assets/{task['previewAssetId']}/file").content
    for format, mime in (("png", "image/png"), ("jpg", "image/jpeg"), ("pdf", "application/pdf")):
        exported = client.get(f"/api/v2/tasks/{task['id']}/export", params={
            "expectedRevision": task["revision"], "format": format,
        })
        assert exported.status_code == 200, exported.text
        assert exported.headers["content-type"] == mime
        if format == "png":
            assert exported.content == native
        elif format == "jpg":
            with Image.open(io.BytesIO(exported.content)) as image:
                image.load()
                assert image.size == (1080, 1440)
        else:
            assert exported.content.startswith(b"%PDF-")
    changed = client.patch(f"/api/tasks/{task['id']}", json={
        "expectedRevision": task["revision"], "rawText": "修改后的新正文，必须重新分析和确认。",
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["previewAssetId"] is None
    stale = client.get(f"/api/v2/tasks/{task['id']}/export", params={
        "expectedRevision": changed.json()["revision"], "format": "pdf",
    })
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "PREVIEW_STALE"
    assert client.post(url, json=request).status_code == 409
    use_token(client, author_token)
    assert patch(client, published).status_code == 409
    with pytest.raises(sqlite3.IntegrityError), client.app.state.studio.db.transaction() as connection:
        connection.execute("UPDATE template_publications SET document_json='{}' WHERE id=?", (publication_id,))
    with pytest.raises(sqlite3.IntegrityError), client.app.state.studio.db.transaction() as connection:
        connection.execute("DELETE FROM template_publications WHERE id=?", (publication_id,))


def test_reject_with_reason_then_author_can_revise_and_resubmit(client):
    token = register(client)
    draft = submit(client, create(client))
    register(client, "reviewer@example.com", "admin")
    url = f"/api/v2/template-drafts/{draft['id']}/review"
    request = {"expectedVersion": draft["version"], "decision": "reject", "comment": ""}
    assert client.post(url, json=request).status_code == 422
    request["comment"] = "标题区域应更宽"
    response = client.post(url, json=request)
    assert response.status_code == 200
    rejected = response.json()
    assert rejected["status"] == "rejected" and rejected["reviewComment"] == "标题区域应更宽"
    assert client.get("/api/v2/published-templates").json()["total"] == 0
    use_token(client, token)
    revised = patch(client, rejected).json()
    assert revised["status"] == "draft" and revised["version"] == 4
    assert submit(client, revised)["status"] == "pending"
