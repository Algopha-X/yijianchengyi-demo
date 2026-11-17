"""Real database account, verification, token and workspace boundary checks."""

import importlib.util
import io
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.services import accounts, materials


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None, app_env="test", data_dir=tmp_path, database_path=tmp_path / "app.db"
    )
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        yield client


def code(client, address="artist@example.com", purpose="register"):
    response = client.post(
        "/api/v2/auth/verification", json={"address": address, "purpose": purpose}
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "development"
    return response.json()


def register(client):
    verification = code(client)
    response = client.post(
        "/api/v2/auth/register",
        json={
            "address": "artist@example.com",
            "password": "test-password-123",
            "nickname": "创作者",
            "challengeId": verification["challengeId"],
            "code": verification["developmentCode"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def task(client, text="我的私人作品"):
    response = client.post(
        "/api/tasks", json={"scenario": "poster", "rawText": text, "assetIds": []}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_registration_profile_and_workspace_survive_logout_login(client):
    draft = task(client)
    old_token = client.cookies.get("ycy_session")
    account = register(client)
    assert account["role"] == "user"
    assert client.cookies.get("ycy_session") != old_token
    assert client.get("/api/v2/auth/me").json()["account"]["id"] == account["id"]
    assert (
        client.patch("/api/v2/auth/profile", json={"nickname": "新昵称"}).json()["account"][
            "nickname"
        ]
        == "新昵称"
    )
    assert client.post("/api/v2/auth/logout").status_code == 200
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 404
    assert (
        client.post(
            "/api/v2/auth/login", json={"address": account["address"], "password": "incorrect"}
        ).status_code
        == 401
    )
    response = client.post(
        "/api/v2/auth/login", json={"address": account["address"], "password": "test-password-123"}
    )
    assert response.status_code == 200, response.text
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 200
    with client.app.state.studio.db.transaction() as connection:
        stored = connection.execute("SELECT password_hash FROM accounts").fetchone()[0]
    assert "test-password" not in stored and stored.startswith("pbkdf2_sha256$")


def test_import_requires_explicit_choice_and_preserves_private_assets(client):
    register(client)
    original = task(client)
    client.post("/api/v2/auth/logout")
    anonymous = task(client, "匿名草稿")
    login = {
        "address": "artist@example.com",
        "password": "test-password-123",
        "importAnonymous": True,
    }
    assert client.post("/api/v2/auth/login", json=login).status_code == 200
    ids = {item["id"] for item in client.get("/api/tasks").json()["items"]}
    assert ids == {original["id"], anonymous["id"]}


def test_verification_attempt_limit_rate_limit_and_reuse(client):
    verification = code(client)
    assert (
        client.post(
            "/api/v2/auth/verification",
            json={"address": "artist@example.com", "purpose": "register"},
        ).status_code
        == 429
    )
    request = {
        "address": "artist@example.com",
        "password": "test-password-123",
        "nickname": "测试",
        "challengeId": verification["challengeId"],
        "code": "000000" if verification["developmentCode"] != "000000" else "111111",
    }
    for _ in range(5):
        assert client.post("/api/v2/auth/register", json=request).status_code == 422
    request["code"] = verification["developmentCode"]
    assert client.post("/api/v2/auth/register", json=request).status_code == 422
    other = code(client, "other@example.com")
    request.update(
        address="other@example.com", challengeId=other["challengeId"], code=other["developmentCode"]
    )
    assert client.post("/api/v2/auth/register", json=request).status_code == 201
    assert client.post("/api/v2/auth/register", json=request).status_code == 422


def test_reset_revokes_sessions_without_exposing_account_as_anonymous(client):
    register(client)
    draft = task(client)
    with client.app.state.studio.db.transaction() as connection:
        connection.execute("UPDATE verification_codes SET created_at='2020-01-01T00:00:00+00:00'")
    verification = code(client, purpose="reset")
    response = client.post(
        "/api/v2/auth/reset-password",
        json={
            "address": "artist@example.com",
            "password": "new-password-456",
            "challengeId": verification["challengeId"],
            "code": verification["developmentCode"],
        },
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/tasks").status_code == 401
    client.post("/api/session")
    assert client.get("/api/tasks").json()["items"] == []
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 404
    assert (
        client.post(
            "/api/v2/auth/login",
            json={"address": "artist@example.com", "password": "new-password-456"},
        ).status_code
        == 200
    )
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 200


def test_development_verification_disabled_in_production(tmp_path):
    settings = Settings(
        _env_file=None, app_env="production", data_dir=tmp_path, database_path=tmp_path / "app.db"
    )
    with TestClient(create_app(settings)) as client:
        headers = {"Origin": "http://127.0.0.1:8000"}
        client.post("/api/session", headers=headers)
        response = client.post(
            "/api/v2/auth/verification",
            headers=headers,
            json={"address": "a@example.com", "purpose": "register"},
        )
        assert response.status_code == 503
        assert "developmentCode" not in response.text


def test_login_without_import_returns_to_preserved_anonymous_workspace_on_logout(client):
    register(client)
    private = task(client, "账号私有作品")
    client.post("/api/v2/auth/logout")
    anonymous = task(client, "不导入的匿名作品")
    old_scope = client.post("/api/session").headers["X-Session-Scope"]
    old_cookie = client.cookies.get("ycy_session")
    response = client.post(
        "/api/v2/auth/login",
        json={
            "address": "artist@example.com",
            "password": "test-password-123",
            "importAnonymous": False,
        },
    )
    assert response.status_code == 200
    assert "_sessionId" not in response.text
    assert client.get(f"/api/tasks/{anonymous['id']}").status_code == 404
    assert client.get(f"/api/tasks/{private['id']}").status_code == 200
    response = client.post("/api/v2/auth/logout")
    assert response.headers["X-Session-Scope"] == old_scope
    assert client.cookies.get("ycy_session") != old_cookie
    assert client.get(f"/api/tasks/{anonymous['id']}").status_code == 200
    assert client.get(f"/api/tasks/{private['id']}").status_code == 404
    assert client.get("/api/v2/auth/me").json()["account"] is None


def login(client, address="artist@example.com", password="test-password-123", **extra):
    return client.post(
        "/api/v2/auth/login", json={"address": address, "password": password, **extra}
    )


def set_token(client, token):
    client.cookies.clear()
    client.cookies.set("ycy_session", token)


def second_login(client):
    first = client.cookies.get("ycy_session")
    client.cookies.clear()
    client.post("/api/session")
    assert login(client).status_code == 200
    return first, client.cookies.get("ycy_session")


def age_codes(client):
    with client.app.state.studio.db.transaction() as connection:
        connection.execute("UPDATE verification_codes SET created_at='2020-01-01T00:00:00+00:00'")


def test_logout_invalidates_captured_token_and_new_anonymous_workspace(client):
    register(client)
    draft = task(client)
    captured = client.cookies.get("ycy_session")
    assert client.post("/api/v2/auth/logout").status_code == 200
    fresh = client.cookies.get("ycy_session")
    assert fresh != captured
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 404
    set_token(client, captured)
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/v2/auth/me").status_code == 401
    client.post("/api/session")
    assert client.get("/api/tasks").json()["items"] == []


def test_reset_revokes_every_login_and_keeps_private_workspace(client):
    register(client)
    draft = task(client)
    first, second = second_login(client)
    age_codes(client)
    verification = code(client, purpose="reset")
    response = client.post(
        "/api/v2/auth/reset-password",
        json={
            "address": "artist@example.com",
            "password": "new-password-456",
            "challengeId": verification["challengeId"],
            "code": verification["developmentCode"],
        },
    )
    assert response.status_code == 200
    assert client.cookies.get("ycy_session") not in (first, second)
    assert response.headers["X-Session-Scope"]
    assert client.get("/api/v2/auth/me").json()["account"] is None
    assert client.get("/api/tasks").json()["items"] == []
    for token in (first, second):
        set_token(client, token)
        assert client.get(f"/api/tasks/{draft['id']}").status_code == 401
    client.post("/api/session")
    assert client.get("/api/tasks").json()["items"] == []
    assert login(client).status_code == 401
    assert login(client, password="new-password-456").status_code == 200
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 200


def test_change_password_rotates_current_token_and_revokes_other_sessions(client):
    register(client)
    draft = task(client)
    first, second = second_login(client)
    request = {"oldPassword": "wrong-password", "newPassword": "replacement-password"}
    assert client.post("/api/v2/auth/change-password", json=request).status_code == 401
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 200
    request["oldPassword"] = "test-password-123"
    response = client.post("/api/v2/auth/change-password", json=request)
    assert response.status_code == 200, response.text
    current = client.cookies.get("ycy_session")
    assert current not in (first, second)
    assert response.headers["X-Session-Scope"]
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 200
    for token in (first, second):
        set_token(client, token)
        assert client.get("/api/tasks").status_code == 401
    set_token(client, current)
    client.post("/api/v2/auth/logout")
    assert login(client).status_code == 401
    assert login(client, password="replacement-password").status_code == 200
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 200


@pytest.mark.parametrize("known", [True, False])
def test_login_rate_limit_persists_failures_and_expires(client, known):
    if known:
        register(client)
        client.post("/api/v2/auth/logout")
    address = "artist@example.com" if known else "unknown@example.com"
    for _ in range(5):
        response = login(client, address, "incorrect-password")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "LOGIN_FAILED"
    assert login(client, address).status_code == 429
    with client.app.state.studio.db.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM login_attempts WHERE address=?", (address,)
        ).fetchone()
        assert row["attempts"] == 5 and row["blocked_until"]
        connection.execute(
            "UPDATE login_attempts SET window_started='2020-01-01T00:00:00+00:00', blocked_until='2020-01-01T00:00:00+00:00'"
        )
    assert login(client, address).status_code == (200 if known else 401)


def test_user_cannot_promote_self_or_call_admin_routes(client):
    user = register(client)
    assert client.get("/api/v2/auth/admin/users").status_code == 403
    assert (
        client.patch(f"/api/v2/auth/admin/users/{user['id']}", json={"role": "admin"}).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/v2/auth/profile", json={"nickname": "创作者", "role": "admin"}
        ).status_code
        == 422
    )
    assert client.get("/api/v2/auth/me").json()["account"]["role"] == "user"
    assert login(client, importAnonymous="true").status_code == 422
    client.post("/api/v2/auth/logout")
    verification = code(client, "new@example.com")
    response = client.post(
        "/api/v2/auth/register",
        json={
            "address": "new@example.com",
            "password": "other-password-123",
            "nickname": "新用户",
            "role": "admin",
            "challengeId": verification["challengeId"],
            "code": verification["developmentCode"],
        },
    )
    assert response.status_code == 422


def test_admin_management_has_boundaries_and_revokes_banned_user(client):
    user = register(client)
    draft = task(client)
    user_token = client.cookies.get("ycy_session")
    service = client.app.state.studio
    admin = accounts.create_admin(
        service, "operator@example.com", "operator-password-123", "管理员"
    )
    client.cookies.clear()
    client.post("/api/session")
    assert login(client, admin["address"], "operator-password-123").status_code == 200
    admin_token = client.cookies.get("ycy_session")
    response = client.get("/api/v2/auth/admin/users")
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert {entry["role"] for entry in response.json()["items"]} == {"user", "admin"}
    assert "password_hash" not in response.text and "workspace_id" not in response.text
    assert client.get("/api/v2/auth/admin/users?limit=101").status_code == 422
    for changes in ({"role": "user"}, {"banned": True}):
        result = client.patch(f"/api/v2/auth/admin/users/{admin['id']}", json=changes)
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "LAST_ADMIN"
    for changes in ({}, {"role": None}, {"banned": "true"}, {"password": "not-allowed"}):
        assert (
            client.patch(f"/api/v2/auth/admin/users/{user['id']}", json=changes).status_code == 422
        )
    assert (
        client.patch(f"/api/v2/auth/admin/users/{uuid4()}", json={"role": "user"}).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v2/auth/admin/users/{user['id']}", json={"role": "reviewer", "banned": True}
        ).status_code
        == 200
    )
    set_token(client, user_token)
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 401
    client.post("/api/session")
    banned = login(client)
    assert banned.status_code == 403 and banned.json()["error"]["code"] == "ACCOUNT_BANNED"
    set_token(client, admin_token)
    assert (
        client.patch(f"/api/v2/auth/admin/users/{user['id']}", json={"banned": False}).status_code
        == 200
    )
    client.post("/api/v2/auth/logout")
    assert login(client).status_code == 200
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 200
    assert client.get("/api/v2/auth/admin/users").status_code == 403


def test_second_admin_allows_demotion_but_removes_admin_authority(client):
    service = client.app.state.studio
    first = accounts.create_admin(service, "first@example.com", "operator-password-123", "管理员甲")
    accounts.create_admin(service, "second@example.com", "operator-password-123", "管理员乙")
    assert login(client, "first@example.com", "operator-password-123").status_code == 200
    assert (
        client.patch(
            f"/api/v2/auth/admin/users/{first['id']}", json={"role": "reviewer"}
        ).status_code
        == 200
    )
    assert client.get("/api/v2/auth/admin/users").status_code == 403


def enqueue_stopped(client, draft, key=None):
    response = client.post(
        f"/api/tasks/{draft['id']}/analyze",
        json={"expectedRevision": draft["revision"]},
        headers={"Idempotency-Key": key or str(uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_anonymous_import_waits_for_jobs_and_does_not_partially_move_data(client):
    register(client)
    client.post("/api/v2/auth/logout")
    draft = task(client)
    client.portal.call(client.app.state.studio.stop)
    job = enqueue_stopped(client, draft)
    response = login(client, importAnonymous=True)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IMPORT_BUSY"
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").status_code == 200
    assert client.get("/api/v2/auth/me").json()["account"] is None
    assert login(client, importAnonymous=False).status_code == 200
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 404


def test_import_idempotency_collision_is_recoverable_without_partial_transfer(client):
    register(client)
    original = task(client)
    client.portal.call(client.app.state.studio.stop)
    key = str(uuid4())
    first = enqueue_stopped(client, original, key)
    with client.app.state.studio.db.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET status='failed',stage='failed' WHERE id=?", (first["id"],)
        )
    client.post("/api/v2/auth/logout")
    draft = task(client, "匿名草稿")
    second = enqueue_stopped(client, draft, key)
    with client.app.state.studio.db.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET status='failed',stage='failed' WHERE id=?", (second["id"],)
        )
    response = login(client, importAnonymous=True)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IMPORT_CONFLICT"
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 200
    assert client.get(f"/api/tasks/{original['id']}").status_code == 404
    assert login(client).status_code == 200
    assert client.get(f"/api/tasks/{original['id']}").status_code == 200
    assert client.get(f"/api/tasks/{draft['id']}").status_code == 404


def test_import_transfers_material_favorites_and_notification_read_state(client):
    account = register(client)
    with client.app.state.studio.db.transaction() as connection:
        target = connection.execute(
            "SELECT workspace_id FROM accounts WHERE id=?", (account["id"],)
        ).fetchone()[0]
    client.post("/api/v2/auth/logout")
    source = accounts.raw_session(client.app.state.studio, client.cookies.get("ycy_session"))
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(image, format="PNG")
    uploaded = client.post(
        "/api/assets", files={"file": ("sample.png", image.getvalue(), "image/png")}
    )
    assert uploaded.status_code == 201, uploaded.text
    asset_id = uploaded.json()["id"]
    draft = task(client)
    client.portal.call(client.app.state.studio.stop)
    job = enqueue_stopped(client, draft)
    with client.app.state.studio.db.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET status='failed',stage='failed' WHERE id=?", (job["id"],)
        )
        connection.execute("INSERT INTO material_favorites VALUES (?,?)", (source, asset_id))
        connection.execute("INSERT INTO notification_reads VALUES (?,?)", (source, job["id"]))
        # Existing duplicate metadata must be merged without a UNIQUE error.
        connection.execute("INSERT INTO material_favorites VALUES (?,?)", (target, asset_id))
    response = login(client, importAnonymous=True)
    assert response.status_code == 200, response.text
    assert client.get(f"/api/assets/{asset_id}/file").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").status_code == 200
    with client.app.state.studio.db.transaction() as connection:
        assert (
            connection.execute(
                "SELECT owner_session_id FROM assets WHERE id=?", (asset_id,)
            ).fetchone()[0]
            == target
        )
        assert (
            connection.execute(
                "SELECT owner_id FROM material_favorites WHERE asset_id=?", (asset_id,)
            ).fetchall()[0][0]
            == target
        )
        assert (
            connection.execute(
                "SELECT owner_id FROM notification_reads WHERE job_id=?", (job["id"],)
            ).fetchone()[0]
            == target
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM material_favorites WHERE owner_id=?", (source,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM notification_reads WHERE owner_id=?", (source,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("case", ["expired", "wrong-purpose", "superseded"])
def test_verification_rejects_expired_wrong_purpose_and_superseded_codes(client, case):
    verification = code(client, purpose="reset" if case == "wrong-purpose" else "register")
    if case == "expired":
        with client.app.state.studio.db.transaction() as connection:
            connection.execute(
                "UPDATE verification_codes SET expires_at='2020-01-01T00:00:00+00:00'"
            )
    if case == "superseded":
        age_codes(client)
        code(client)
    response = client.post(
        "/api/v2/auth/register",
        json={
            "address": "artist@example.com",
            "password": "test-password-123",
            "nickname": "创作者",
            "challengeId": verification["challengeId"],
            "code": verification["developmentCode"],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VERIFICATION_INVALID"
    with client.app.state.studio.db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["missing-config", "sms", "transport"])
def test_smtp_unavailable_never_exposes_development_code(tmp_path, monkeypatch, kind):
    configured = (
        {
            "smtp_host": "smtp.example.invalid",
            "smtp_user": "sender@example.com",
            "smtp_password": "private-smtp-password",
            "smtp_from": "sender@example.com",
        }
        if kind == "transport"
        else {}
    )
    settings = Settings(
        _env_file=None,
        app_env="test",
        data_dir=tmp_path,
        database_path=tmp_path / "app.db",
        verification_mode="smtp",
        **configured,
    )
    if kind == "transport":

        def unavailable(*args, **kwargs):
            raise OSError("private transport detail")

        monkeypatch.setattr(accounts.smtplib, "SMTP_SSL", unavailable)
    with TestClient(create_app(settings)) as client:
        client.post("/api/session")
        response = client.post(
            "/api/v2/auth/verification",
            json={
                "address": "13812345678" if kind == "sms" else "artist@example.com",
                "purpose": "register",
            },
        )
        assert response.status_code == 503, response.text
        assert "developmentCode" not in response.text
        assert "private" not in response.text
        with client.app.state.studio.db.transaction() as connection:
            assert connection.execute("SELECT count(*) FROM verification_codes").fetchone()[0] == 0


def test_admin_cli_getpass_creates_no_default_or_plaintext_password(client, monkeypatch, capsys):
    script = Path(__file__).resolve().parents[2] / "scripts" / "create_admin.py"
    spec = importlib.util.spec_from_file_location("create_admin_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with client.app.state.studio.db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
    monkeypatch.setattr("app.config.load_settings", lambda: client.app.state.studio.settings)
    monkeypatch.setattr(module.sys, "argv", [str(script), "--address", "operator@example.com"])
    passwords = iter(["operator-password-123", "operator-password-123"])
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: next(passwords))
    assert module.main() == 0
    output = capsys.readouterr()
    assert "operator@example.com" in output.out
    assert "operator-password-123" not in output.out + output.err
    assert login(client, "operator@example.com", "operator-password-123").status_code == 200
    assert client.get("/api/v2/auth/admin/users").status_code == 200


def test_admin_cli_rejects_mismatch_and_existing_user_elevation(client, monkeypatch, capsys):
    user = register(client)
    with pytest.raises(ApiError) as error:
        accounts.create_admin(
            client.app.state.studio, user["address"], "operator-password-123", "管理员"
        )
    assert error.value.code == "ACCOUNT_EXISTS"
    assert client.get("/api/v2/auth/me").json()["account"]["role"] == "user"
    script = Path(__file__).resolve().parents[2] / "scripts" / "create_admin.py"
    spec = importlib.util.spec_from_file_location("create_admin_mismatch", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sys, "argv", [str(script), "--address", "operator@example.com"])
    passwords = iter(["operator-password-123", "different-password"])
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: next(passwords))
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert "operator-password-123" not in str(capsys.readouterr())
    with client.app.state.studio.db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1


@pytest.mark.parametrize("operation", ["change", "reset"])
def test_credentials_change_preserves_unimported_anonymous_return(client, operation):
    register(client)
    private = task(client, "账号私有作品")
    client.post("/api/v2/auth/logout")
    anonymous = task(client, "未导入的匿名作品")
    anonymous_token = client.cookies.get("ycy_session")
    assert login(client, importAnonymous=False).status_code == 200
    old_login = client.cookies.get("ycy_session")
    if operation == "change":
        response = client.post(
            "/api/v2/auth/change-password",
            json={
                "oldPassword": "test-password-123",
                "newPassword": "new-password-456",
            },
        )
        assert response.status_code == 200, response.text
        assert client.get(f"/api/tasks/{private['id']}").status_code == 200
        assert client.get(f"/api/tasks/{anonymous['id']}").status_code == 404
        assert client.post("/api/v2/auth/logout").status_code == 200
    else:
        age_codes(client)
        verification = code(client, purpose="reset")
        response = client.post(
            "/api/v2/auth/reset-password",
            json={
                "address": "artist@example.com",
                "password": "new-password-456",
                "challengeId": verification["challengeId"],
                "code": verification["developmentCode"],
            },
        )
        assert response.status_code == 200, response.text
    restored_token = client.cookies.get("ycy_session")
    assert restored_token not in {old_login, anonymous_token}
    assert client.get("/api/v2/auth/me").json()["account"] is None
    assert client.get(f"/api/tasks/{anonymous['id']}").status_code == 200
    assert client.get(f"/api/tasks/{private['id']}").status_code == 404
    for captured in (old_login, anonymous_token):
        set_token(client, captured)
        assert client.get("/api/tasks").status_code == 401


@pytest.mark.parametrize("pointer_state", ["valid", "expired", "account-workspace"])
def test_remote_reset_expired_cookie_can_recover_only_bounded_anonymous_workspace(
    client, pointer_state
):
    user = register(client)
    private = task(client, "账号作品")
    client.post("/api/v2/auth/logout")
    anonymous = task(client, "浏览器原匿名作品")
    assert login(client).status_code == 200
    captured = client.cookies.get("ycy_session")
    with client.app.state.studio.db.transaction() as connection:
        if pointer_state == "expired":
            connection.execute(
                "UPDATE anonymous_returns SET expires_at='2020-01-01T00:00:00+00:00'"
            )
        elif pointer_state == "account-workspace":
            workspace = connection.execute(
                "SELECT workspace_id FROM accounts WHERE id=?", (user["id"],)
            ).fetchone()[0]
            connection.execute("UPDATE anonymous_returns SET anonymous_session_id=?", (workspace,))
    # Simulate another browser proving control of the reset address.
    client.cookies.clear()
    client.post("/api/session")
    age_codes(client)
    verification = code(client, purpose="reset")
    response = client.post(
        "/api/v2/auth/reset-password",
        json={
            "address": "artist@example.com",
            "password": "new-password-456",
            "challengeId": verification["challengeId"],
            "code": verification["developmentCode"],
        },
    )
    assert response.status_code == 200, response.text
    set_token(client, captured)
    assert client.get(f"/api/tasks/{private['id']}").status_code == 401
    response = client.post("/api/session")
    assert response.status_code == 200, response.text
    assert client.get("/api/v2/auth/me").json()["account"] is None
    assert client.get(f"/api/tasks/{private['id']}").status_code == 404
    assert client.get(f"/api/tasks/{anonymous['id']}").status_code == (
        200 if pointer_state == "valid" else 404
    )
    set_token(client, captured)
    assert accounts.recover_anonymous_after_revocation(client.app.state.studio, captured) is None
    assert client.get("/api/tasks").status_code == 401


def test_import_during_image_generation_is_rejected_without_owner_change(client):
    register(client)
    client.post("/api/v2/auth/logout")
    anonymous = task(client)
    owner = accounts.raw_session(client.app.state.studio, client.cookies.get("ycy_session"))
    entered, release = threading.Event(), threading.Event()

    async def pending_generation():
        import asyncio

        async with materials.generation_slot(client.app.state.studio, owner):
            entered.set()
            await asyncio.to_thread(release.wait, 5)

    future = client.portal.start_task_soon(pending_generation)
    try:
        assert entered.wait(2)
        response = login(client, importAnonymous=True)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "IMPORT_BUSY"
        assert client.get(f"/api/tasks/{anonymous['id']}").status_code == 200
        assert client.get("/api/v2/auth/me").json()["account"] is None
    finally:
        release.set()
        future.result(3)
    assert login(client, importAnonymous=True).status_code == 200
    assert client.get(f"/api/tasks/{anonymous['id']}").status_code == 200


@pytest.mark.parametrize("status", ["draft", "pending"])
def test_anonymous_template_drafts_import_with_pending_guard(client, status):
    user = register(client)
    client.post("/api/v2/auth/logout")
    source_id = client.get("/api/templates").json()["items"][0]["id"]
    response = client.post(
        "/api/v2/template-drafts", json={"sourceTemplateId": source_id, "name": "匿名模板草稿"}
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    if status == "pending":
        # Anonymous submit is disallowed; exercise the import guard against a
        # pending legacy record without bypassing the real submission API.
        with client.app.state.studio.db.transaction() as connection:
            connection.execute(
                "UPDATE template_drafts SET status='pending' WHERE id=?", (draft["id"],)
            )
    response = login(client, importAnonymous=True)
    assert response.status_code == (200 if status == "draft" else 409), response.text
    assert client.get(f"/api/v2/template-drafts/{draft['id']}").status_code == 200
    with client.app.state.studio.db.transaction() as connection:
        owner = connection.execute(
            "SELECT owner_id FROM template_drafts WHERE id=?", (draft["id"],)
        ).fetchone()[0]
        workspace = connection.execute(
            "SELECT workspace_id FROM accounts WHERE id=?", (user["id"],)
        ).fetchone()[0]
    assert (owner == workspace) is (status == "draft")
