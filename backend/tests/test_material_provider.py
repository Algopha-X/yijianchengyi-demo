"""Provider simulations verify adapter behavior, never count as a real model call."""

import asyncio
import base64
import io
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.services import materials
from app.services.studio import Studio


def picture(fmt="PNG"):
    output = io.BytesIO()
    Image.new("RGB", (64, 48), "green").save(output, format=fmt)
    return output.getvalue()


def app_settings(tmp_path, **extra):
    return Settings(
        _env_file=None,
        app_env="test",
        data_dir=tmp_path,
        database_path=tmp_path / "app.db",
        **extra,
    )


def mock_transport(monkeypatch, handler):
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        materials.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_real_adapter_transport_mock_accepts_decoded_images(tmp_path, monkeypatch, fmt, mime):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(picture(fmt)).decode()}]}
        )

    mock_transport(monkeypatch, handler)
    settings = app_settings(
        tmp_path,
        image_mode="real",
        image_base_url="https://image.test/v1",
        image_api_key="test-only-placeholder",
        image_model="test-image",
    )
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        response = client.post("/api/v2/materials/generate", json={"prompt": "测试图片"})
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["mode"] == "real" and result["asset"]["mimeType"] == mime
        assert len(calls) == 1 and calls[0].url.path == "/v1/images/generations"
        assert client.get(result["asset"]["fileUrl"]).content == picture(fmt)


@pytest.mark.parametrize(
    "kind,expected_calls",
    [("server", 2), ("url", 1), ("bad-image", 1), ("duplicate", 2), ("nan", 2), ("denied", 1)],
)
def test_provider_failures_never_return_mock_or_store_assets(
    tmp_path, monkeypatch, kind, expected_calls
):
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "server":
            return httpx.Response(503)
        if kind == "denied":
            return httpx.Response(401)
        if kind == "url":
            return httpx.Response(200, json={"data": [{"url": "https://image.test/image.png"}]})
        if kind == "duplicate":
            return httpx.Response(200, content=b'{"data":[],"data":[]}')
        if kind == "nan":
            return httpx.Response(200, content=b'{"data":NaN}')
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(b"not-an-image").decode()}]}
        )

    mock_transport(monkeypatch, handler)
    settings = app_settings(
        tmp_path,
        image_mode="real",
        image_base_url="https://image.test/v1",
        image_api_key="test-only-placeholder",
        image_model="test-image",
    )
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        response = client.post("/api/v2/materials/generate", json={"prompt": "测试"})
        assert response.status_code == 502, response.text
        assert len(calls) == expected_calls
        assert client.get("/api/v2/materials").json()["items"] == []


def test_generation_slots_bound_waiting_and_release_cancelled_requests(tmp_path):
    service = Studio(app_settings(tmp_path, job_concurrency=1, job_queue_limit=1))

    async def run():
        entered = asyncio.Event()

        async def waiting():
            entered.set()
            async with materials.generation_slot(service, "second"):
                pass

        async with materials.generation_slot(service, "first"):
            queued = asyncio.create_task(waiting())
            await entered.wait()
            await asyncio.sleep(0)
            with pytest.raises(ApiError) as error:
                async with materials.generation_slot(service, "third"):
                    pass
            assert error.value.status_code == 429
            queued.cancel()
            await asyncio.gather(queued, return_exceptions=True)
            assert service._image_generation_state["count"] == 1
        assert service._image_generation_state["count"] == 0
        assert not service._image_generation_state["owners"]

    asyncio.run(run())


def account(client, address, role="user"):
    client.cookies.clear()
    client.post("/api/session")
    challenge = client.post(
        "/api/v2/auth/verification", json={"address": address, "purpose": "register"}
    ).json()
    response = client.post(
        "/api/v2/auth/register",
        json={
            "address": address,
            "password": "test-password-123",
            "nickname": "素材测试",
            "challengeId": challenge["challengeId"],
            "code": challenge["developmentCode"],
        },
    )
    assert response.status_code == 201, response.text
    if role != "user":
        with client.app.state.studio.db.transaction() as connection:
            connection.execute(
                "UPDATE accounts SET role=? WHERE id=?", (role, response.json()["account"]["id"])
            )
    return client.cookies.get("ycy_session")


def use(client, token):
    client.cookies.clear()
    client.cookies.set("ycy_session", token)


def test_review_actual_roles_self_rejection_cas_thumbnail_and_public_import(tmp_path):
    with TestClient(create_app(app_settings(tmp_path))) as client:
        author = account(client, "author@materials.test", "reviewer")
        asset = client.post(
            "/api/assets", files={"file": ("photo.png", picture(), "image/png")}
        ).json()
        path = f"/api/v2/materials/{asset['id']}"
        material = client.patch(path, json={"name": "待审原名", "requestPublic": True}).json()
        review = {"expectedRevision": material["revision"], "decision": "approve"}
        assert client.post(path + "/review", json=review).status_code == 403
        outsider = account(client, "other@materials.test")
        assert client.get(path + "/thumbnail").status_code == 404
        assert client.post(path + "/review", json=review).status_code == 403
        assert (
            client.post(
                "/api/v2/materials/generate", json={"prompt": "变体", "sourceAssetId": asset["id"]}
            ).status_code
            == 404
        )
        use(client, author)
        revised = client.patch(path, json={"name": "修改后的名称", "requestPublic": True}).json()
        account(client, "reviewer@materials.test", "reviewer")
        assert client.get(path + "/thumbnail").status_code == 200
        assert client.post(path + "/review", json=review).status_code == 409
        approved = client.post(
            path + "/review", json={**review, "expectedRevision": revised["revision"]}
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["visibility"] == "public"
        use(client, outsider)
        assert client.get(path + "/thumbnail").status_code == 200
        assert client.get(f"/api/assets/{asset['id']}/file").status_code == 404
        imported = client.post(path + "/import")
        assert imported.status_code == 201 and imported.json()["id"] != asset["id"]


@pytest.mark.parametrize("kind", ["stream-bytes", "decoded-bytes", "pixels"])
def test_real_provider_rejects_oversized_outputs_without_storing(tmp_path, monkeypatch, kind):
    calls = []
    if kind == "pixels":
        output = io.BytesIO()
        Image.new("RGB", (6400, 6300), "green").save(output, format="PNG")
        content = output.getvalue()
    else:
        content = b"x" * (materials.MAX_UPLOAD + 1)

    def handler(request):
        calls.append(request)
        if kind == "stream-bytes":
            return httpx.Response(200, content=b"x" * (materials.MAX_UPLOAD * 4 // 3 + 65537))
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(content).decode()}]}
        )

    mock_transport(monkeypatch, handler)
    settings = app_settings(
        tmp_path,
        image_mode="real",
        image_base_url="https://image.test/v1",
        image_api_key="test-only-placeholder",
        image_model="test-image",
    )
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        response = client.post("/api/v2/materials/generate", json={"prompt": "测试边界"})
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == "IMAGE_OUTPUT_INVALID"
        assert len(calls) == 1
        assert client.get("/api/v2/materials").json()["items"] == []


def test_cancelled_cpu_image_work_keeps_slot_until_thread_finishes(tmp_path):
    service = Studio(app_settings(tmp_path, job_concurrency=1, job_queue_limit=1))
    entered, release = threading.Event(), threading.Event()

    def worker():
        entered.set()
        release.wait(3)

    async def run():
        async def generate():
            async with materials.generation_slot(service, "owner"):
                await materials.image_work(worker)

        task = asyncio.create_task(generate())
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert materials.generation_busy(service, "owner")
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not materials.generation_busy(service, "owner")
        assert service._image_generation_state["count"] == 0

    asyncio.run(run())
