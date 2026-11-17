"""Native and scaled raster exports, PDF structure and access/version boundaries."""

import io
import re
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.exports_v2 import router
from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.services.exports import export_preview


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    directory = tmp_path_factory.mktemp("exports-v2")
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            llm_mode="mock",
            data_dir=directory / "data",
            database_path=directory / "studio.db",
        )
    )
    if not any(getattr(route, "path", "") == "/api/v2/tasks/{taskId}/export" for route in app.routes):
        app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/api/session").status_code == 200
        task = client.post(
            "/api/tasks",
            json={
                "scenario": "poster",
                "style": "fresh",
                "rawText": "标题：秋日读书会。正文：带上一本喜欢的书。时间：周六下午。地点：校园图书馆。",
                "assetIds": [],
            },
        ).json()
        task = client.patch(
            f"/api/tasks/{task['id']}",
            json={"expectedRevision": task["revision"], "templateId": "poster-fresh-v1"},
        ).json()

        def job(kind):
            response = client.post(
                f"/api/tasks/{task['id']}/{kind}",
                json={"expectedRevision": task["revision"]},
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert response.status_code == 202, response.text
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                progress = client.get(f"/api/jobs/{response.json()['id']}").json()
                if progress["status"] not in {"queued", "running"}:
                    assert progress["status"] == "succeeded", progress
                    return client.get(f"/api/tasks/{task['id']}").json()
                time.sleep(0.02)
            raise AssertionError("导出测试的实际预览生成超时")

        task = job("analyze")
        response = client.post(
            f"/api/tasks/{task['id']}/confirm",
            json={
                "expectedRevision": task["revision"],
                "confirmedFactIds": [fact["id"] for fact in task["analysis"]["facts"]],
            },
        )
        assert response.status_code == 200, response.text
        task = job("render")
        yield client, task


def download(client, task, format="png", scale=None, revision=None):
    params = {
        "expectedRevision": task["revision"] if revision is None else revision,
        "format": format,
    }
    if scale is not None:
        params["scale"] = scale
    return client.get(f"/api/v2/tasks/{task['id']}/export", params=params)


@pytest.mark.parametrize("format,mime", [("png", "image/png"), ("jpg", "image/jpeg")])
@pytest.mark.parametrize("scale", [1, 2])
def test_raster_formats_and_actual_dimensions(rendered, format, mime, scale):
    client, task = rendered
    response = download(client, task, format, scale)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == mime
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-export-resize"] == ("native" if scale == 1 else "raster-upscale")
    with Image.open(io.BytesIO(response.content)) as image:
        image.load()
        assert image.size == (1080 * scale, 1440 * scale)
        if format == "jpg":
            assert image.mode == "RGB"


def test_default_png_is_exact_immutable_preview_and_does_not_call_ai(rendered, monkeypatch):
    from app.services import analysis

    def unexpected(*_args, **_kwargs):
        raise AssertionError("导出不得重新调用模型")

    monkeypatch.setattr(analysis, "analyze", unexpected)
    client, task = rendered
    response = download(client, task)
    assert response.status_code == 200
    preview = client.get(f"/api/assets/{task['previewAssetId']}/file")
    assert response.content == preview.content


@pytest.mark.parametrize("scale", [1, 2])
def test_pdf_is_one_page_raster_with_matching_page_dimensions(rendered, scale):
    client, task = rendered
    response = download(client, task, "pdf", scale)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-") and response.content.rstrip().endswith(b"%%EOF")
    assert len(re.findall(rb"/Type\s*/Page\b", response.content)) == 1
    dimensions = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", response.content)
    assert dimensions is not None
    assert tuple(float(value) for value in dimensions.groups()) == (1080 * scale, 1440 * scale)
    assert b"/Subtype /Image" in response.content


@pytest.mark.parametrize("format,scale", [("svg", 1), ("png", 0), ("png", 3), ("png", "2.5")])
def test_invalid_export_options_are_public_validation_errors(rendered, format, scale):
    client, task = rendered
    response = download(client, task, format, scale)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_revision_and_foreign_or_missing_session_cannot_download(rendered):
    client, task = rendered
    response = download(client, task, revision=task["revision"] + 1)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_CONFLICT"
    cookie = client.cookies.get("ycy_session")
    try:
        client.cookies.clear()
        assert download(client, task).status_code == 401
        assert client.post("/api/session").status_code == 200
        assert download(client, task).status_code == 404
    finally:
        client.cookies.clear()
        client.cookies.set("ycy_session", cookie)


def test_no_preview_requires_confirmed_current_preview(rendered):
    client, _ = rendered
    response = client.post(
        "/api/tasks", json={"scenario": "poster", "style": "fresh", "rawText": "新草稿", "assetIds": []}
    )
    task = response.json()
    response = download(client, task, "pdf")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PREVIEW_STALE"


def test_transparent_source_flattens_with_declared_background_and_failure_is_safe(tmp_path):
    source = tmp_path / "preview.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 0)).save(source)
    result = export_preview(source, "jpg", background="#125634")
    with Image.open(io.BytesIO(result.content)) as image:
        assert all(abs(actual - expected) <= 2 for actual, expected in zip(image.getpixel((0, 0)), (18, 86, 52), strict=True))
    with pytest.raises(ApiError) as error:
        export_preview(tmp_path / "missing.png", "pdf")
    assert error.value.code == "RENDER_FAILED"
    assert str(tmp_path) not in error.value.message
