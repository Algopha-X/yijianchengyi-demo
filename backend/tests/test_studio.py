"""Integration checks use isolated SQLite files and exercise actual HTTP contracts."""

import asyncio
import io
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image

from app.config import PROJECT_ROOT, Settings
from app.main import create_app
from app.services.studio import now


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        app_env="test",
        llm_mode="mock",
        data_dir=tmp_path / "data",
        database_path=tmp_path / "studio.db",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as session:
        assert session.post("/api/session").status_code == 200
        yield session


def contract(payload, name):
    definition = json.loads((PROJECT_ROOT / "contracts/openapi.json").read_text(encoding="utf-8"))
    schema = {**definition["components"]["schemas"][name], "components": definition["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


def make_task(client, *, complete=True):
    sample = "campus-event-complete.json" if complete else "campus-event-missing.json"
    payload = json.loads((PROJECT_ROOT / "examples" / sample).read_text(encoding="utf-8"))
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    task = response.json()
    contract(task, "Task")
    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"expectedRevision": task["revision"], "templateId": "poster-fresh-v1"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def wait_job(client, job):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job['id']}")
        assert response.status_code == 200, response.text
        job = response.json()
        contract(job, "Job")
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"Job did not finish: {job}")


def submit(client, task, kind="analyze", key=None):
    response = client.post(
        f"/api/tasks/{task['id']}/{kind}",
        json={"expectedRevision": task["revision"]},
        headers={"Idempotency-Key": key or str(uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()


def analyzed(client, task):
    job = wait_job(client, submit(client, task))
    assert job["status"] == "succeeded", job
    return client.get(f"/api/tasks/{task['id']}").json()


def confirmed(client, task):
    response = client.post(
        f"/api/tasks/{task['id']}/confirm",
        json={
            "expectedRevision": task["revision"],
            "confirmedFactIds": [fact["id"] for fact in task["analysis"]["facts"]],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_session_cookie_and_origin_boundaries(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/tasks").status_code == 401
        for origin in ["null", "https://untrusted.example", "http://127.0.0.1:5173.evil.invalid"]:
            response = client.post("/api/session", headers={"Origin": origin})
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
        response = client.post("/api/session", headers={"Origin": "http://127.0.0.1:8000"})
        contract(response.json(), "Session")
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=604800" in cookie
        token = client.cookies.get("ycy_session")
        client.post("/api/session")
        assert client.cookies.get("ycy_session") == token
        with client.app.state.studio.db.transaction() as connection:
            row = connection.execute("SELECT token_hash FROM sessions").fetchone()
        assert row["token_hash"] != token and len(row["token_hash"]) == 64


def test_missing_fields_cannot_confirm_or_render_then_can_be_completed(client):
    task = analyzed(client, make_task(client, complete=False))
    assert task["phase"] == "needs_input"
    assert set(task["analysis"]["missingFields"]) == {"time", "location"}
    contract(task, "Task")
    response = client.post(
        f"/api/tasks/{task['id']}/confirm",
        json={"expectedRevision": task["revision"], "confirmedFactIds": []},
    )
    assert response.status_code == 422
    response = client.post(
        f"/api/tasks/{task['id']}/render",
        json={"expectedRevision": task["revision"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 409
    fields = dict(task["fields"], time="2026年11月20日14:00", location="图书馆一楼交流区")
    task = client.patch(
        f"/api/tasks/{task['id']}", json={"expectedRevision": task["revision"], "fields": fields}
    ).json()
    task = analyzed(client, task)
    assert task["fields"]["time"] == fields["time"]
    assert task["phase"] == "needs_confirmation"
    task = confirmed(client, task)
    assert task["confirmedRevision"] == task["revision"]


def test_idempotency_before_revision_and_request_conflict(client):
    task = make_task(client)
    key = str(uuid4())
    first = submit(client, task, key=key)
    assert wait_job(client, first)["status"] == "succeeded"
    assert submit(client, task, key=key)["id"] == first["id"]
    response = client.post(
        f"/api/tasks/{task['id']}/analyze",
        json={"expectedRevision": task["revision"] + 1},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_old_revision_conflict_and_controlled_fields_rejected(client):
    task = make_task(client)
    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"expectedRevision": task["revision"] - 1, "rawText": "新草稿"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["details"]["currentRevision"] == task["revision"]
    for key, value in {
        "revision": 99,
        "phase": "preview_ready",
        "ownerSessionId": str(uuid4()),
        "confirmedRevision": 2,
    }.items():
        response = client.patch(
            f"/api/tasks/{task['id']}", json={"expectedRevision": task["revision"], key: value}
        )
        assert response.status_code == 422
    assert (
        client.patch(
            f"/api/tasks/{task['id']}", json={"expectedRevision": task["revision"], "rawText": None}
        ).status_code
        == 422
    )


def test_old_running_analysis_never_overwrites_new_draft(client, monkeypatch):
    from app.services import analysis

    original = analysis.analyze

    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.15)
        return await original(*args, **kwargs)

    monkeypatch.setattr(analysis, "analyze", delayed)
    task = make_task(client)
    job = submit(client, task)
    updated = client.patch(
        f"/api/tasks/{task['id']}",
        json={
            "expectedRevision": task["revision"],
            "rawText": "标题：新草稿。正文：保留刚刚修改的内容。",
        },
    ).json()
    assert wait_job(client, job)["status"] == "superseded"
    result = client.get(f"/api/tasks/{task['id']}").json()
    assert result["rawText"] == updated["rawText"] and result["revision"] == updated["revision"]
    assert result["analysis"] is None


def test_queue_limit_is_atomic_and_busy_returns_conflict(client):
    client.portal.call(client.app.state.studio.stop)
    client.app.state.studio.settings = client.app.state.studio.settings.model_copy(
        update={"job_queue_limit": 1}
    )
    first, second = make_task(client), make_task(client)
    submit(client, first)
    for task, code in [(first, "TASK_BUSY"), (second, "QUEUE_FULL")]:
        response = client.post(
            f"/api/tasks/{task['id']}/analyze",
            json={"expectedRevision": task["revision"]},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == (429 if code == "QUEUE_FULL" else 409)
        assert response.json()["error"]["code"] == code
    with client.app.state.studio.db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def image_bytes(mode="RGBA", size=(100, 80), fmt="PNG"):
    buffer = io.BytesIO()
    Image.new(mode, size, (120, 160, 100, 128) if mode == "RGBA" else (120, 160, 100)).save(
        buffer, fmt
    )
    return buffer.getvalue()


def test_upload_decode_and_cross_session_resources(client):
    task = make_task(client)
    job = submit(client, task)
    response = client.post(
        "/api/assets", files={"file": ("../user-controlled.png", image_bytes(), "image/png")}
    )
    assert response.status_code == 201, response.text
    asset = response.json()
    contract(asset, "Asset")
    assert asset["width"] == 100
    assert client.get(asset["fileUrl"]).content == image_bytes()
    for name, content in [
        ("bad.png", b"<svg/>"),
        ("bad.svg", image_bytes()),
        ("bad.jpg", image_bytes()),
        ("big.png", b"x" * (10 * 1024 * 1024 + 1)),
    ]:
        response = client.post("/api/assets", files={"file": (name, content, "image/png")})
        assert response.status_code in {413, 415}
    client.cookies.clear()
    client.post("/api/session")
    for path in [f"/api/tasks/{task['id']}", f"/api/jobs/{job['id']}", asset["fileUrl"]]:
        assert client.get(path).status_code == 404
    assert client.get("/api/tasks").json() == {"items": []}


def test_real_render_png_jpg_and_edit_invalidates_old_preview(client):
    task = confirmed(client, analyzed(client, make_task(client)))
    job = wait_job(client, submit(client, task, "render"))
    assert job["status"] == "succeeded", job
    task = client.get(f"/api/tasks/{task['id']}").json()
    contract(task, "Task")
    assert task["phase"] == "preview_ready"
    for fmt in ["png", "jpg"]:
        response = client.get(
            f"/api/tasks/{task['id']}/export",
            params={"format": fmt, "expectedRevision": task["revision"]},
        )
        assert response.status_code == 200
        assert "attachment" in response.headers["Content-Disposition"]
        with Image.open(io.BytesIO(response.content)) as image:
            image.load()
            assert image.size == (1080, 1440)
            if fmt == "jpg":
                assert image.mode == "RGB"
    updated = client.patch(
        f"/api/tasks/{task['id']}",
        json={
            "expectedRevision": task["revision"],
            "fields": {**task["fields"], "title": "修改后的标题"},
        },
    ).json()
    assert updated["previewAssetId"] is None and updated["confirmedRevision"] is None
    assert (
        client.get(
            f"/api/tasks/{task['id']}/export",
            params={"expectedRevision": updated["revision"], "format": "png"},
        ).status_code
        == 409
    )


def test_persistence_and_running_recovery(settings):
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        task = make_task(client)
        cookies = dict(client.cookies)
        client.portal.call(client.app.state.studio.stop)
        job = submit(client, task)
        with client.app.state.studio.db.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET status='running',stage='classifying',started_at=? WHERE id=?",
                (now(), job["id"]),
            )
    with TestClient(create_app(settings)) as client:
        client.cookies.update(cookies)
        recovered = client.get(f"/api/tasks/{task['id']}").json()
        assert recovered["rawText"] == task["rawText"]
        assert recovered["lastError"]["code"] == "WORKER_RESTARTED"
        assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "failed"
        assert len(client.get("/api/tasks").json()["items"]) == 1


def test_two_worker_slots_process_jobs_concurrently(client, monkeypatch):
    from app.services import analysis

    original = analysis.analyze
    gate = asyncio.Event()
    started = []

    async def barrier(task, *args):
        started.append(task["id"])
        if len(started) == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), 2)
        return await original(task, *args)

    monkeypatch.setattr(analysis, "analyze", barrier)
    tasks = [make_task(client), make_task(client)]
    jobs = [submit(client, task) for task in tasks]
    assert all(wait_job(client, job)["status"] == "succeeded" for job in jobs)
    assert len(started) == 2


def test_queued_job_resumes_after_service_restart(settings):
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        task = make_task(client)
        cookies = dict(client.cookies)
        client.portal.call(client.app.state.studio.stop)
        job = submit(client, task)
        assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "queued"
    with TestClient(create_app(settings)) as client:
        client.cookies.update(cookies)
        assert wait_job(client, job)["status"] == "succeeded"
        assert client.get(f"/api/tasks/{task['id']}").json()["analysis"] is not None
