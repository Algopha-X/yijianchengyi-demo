import logging
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, Field, ValidationError

from app.config import PROJECT_ROOT, ConfigurationError, Settings
from app.errors import ApiError
from app.main import VERSION, create_app


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    # Do not read a developer's real .env or credentials in automated tests.
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture
def app():
    return create_app(Settings(app_env="test"))


def assert_contract(payload, name):
    import json

    contract = json.loads((PROJECT_ROOT / "contracts/openapi.json").read_text(encoding="utf-8"))
    schema = {**contract["components"]["schemas"][name], "components": contract["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


def test_default_startup_and_health_need_no_key():
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "mock", "version": VERSION}
    assert_contract(response.json(), "Health")


def test_real_health_is_mode_information_not_a_provider_probe(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "real")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-only-private-marker")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
    assert response.json() == {"status": "ok", "mode": "real", "version": VERSION}
    assert "private-marker" not in response.text
    assert "provider" not in response.text
    assert "test-model" not in response.text
    assert_contract(response.json(), "Health")


@pytest.mark.parametrize("missing", ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"])
def test_real_mode_fails_startup_when_any_required_setting_is_missing(monkeypatch, missing):
    for name, value in {
        "LLM_MODE": "real",
        "LLM_BASE_URL": "https://provider.example.invalid/v1",
        "LLM_API_KEY": "test-only-private-marker",
        "LLM_MODEL": "test-model",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(missing, "   ")
    with pytest.raises(ConfigurationError, match=missing) as error, TestClient(create_app()):
        pytest.fail("invalid real configuration must not start")
    assert "private-marker" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://provider.example.invalid/v1",
        "https://test-only-private-marker@provider.example.invalid/v1",
        "https://provider.example.invalid/v1?key=test-only-private-marker",
        "https://provider.example.invalid/v1#test-only-private-marker",
        "https://provider.example.invalid:invalid/v1",
        "https://",
    ],
)
def test_real_mode_rejects_non_https_or_credential_bearing_base_urls(monkeypatch, url):
    monkeypatch.setenv("LLM_MODE", "real")
    monkeypatch.setenv("LLM_BASE_URL", url)
    monkeypatch.setenv("LLM_API_KEY", "test-only-private-marker")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    with pytest.raises(ConfigurationError, match="LLM_BASE_URL") as error, TestClient(create_app()):
        pytest.fail("invalid provider base URL must not start")
    assert "test-only-private-marker" not in str(error.value)


def test_bad_enum_has_a_safe_startup_error(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "test-only-private-marker")
    with pytest.raises(ConfigurationError, match="LLM_MODE") as error, TestClient(create_app()):
        pytest.fail("invalid mode must not start")
    assert "private-marker" not in str(error.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_PORT", "65536"),
        ("LLM_TIMEOUT_SECONDS", "0"),
        ("LLM_MAX_RETRIES", "2"),
        ("JOB_CONCURRENCY", "3"),
        ("JOB_QUEUE_LIMIT", "0"),
        ("SESSION_TTL_DAYS", "0"),
    ],
)
def test_out_of_range_configuration_fails_startup(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError, match=name), TestClient(create_app()):
        pytest.fail("invalid numeric setting must not start")


def test_relative_paths_use_the_repository_root_from_any_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings(data_dir="./custom-data", database_path="./custom-data/tasks.db")
    assert settings.data_dir == PROJECT_ROOT / "custom-data"
    assert settings.database_path == PROJECT_ROOT / "custom-data/tasks.db"
    assert not settings.data_dir.exists()


def test_absolute_paths_are_preserved(tmp_path):
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "app.db")
    assert settings.data_dir == tmp_path
    assert settings.database_path == tmp_path / "app.db"


def test_dotenv_loading_and_environment_precedence(monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("APP_PORT=8123\nDATA_DIR=./dotenv-data\nLLM_MODE=mock\n", encoding="utf-8")
    monkeypatch.setenv("APP_PORT", "8124")
    settings = Settings(_env_file=dotenv)
    assert settings.app_port == 8124
    assert settings.data_dir == PROJECT_ROOT / "dotenv-data"


def test_example_env_is_usable_without_credentials():
    settings = Settings(_env_file=PROJECT_ROOT / ".env.example")
    assert settings.llm_mode == "mock"
    assert settings.llm_api_key is None
    assert settings.data_dir == PROJECT_ROOT / "data"


def test_secret_settings_repr_hides_key():
    settings = Settings(llm_api_key="test-only-private-marker")
    assert "private-marker" not in repr(settings)
    assert settings.llm_api_key.get_secret_value() == "test-only-private-marker"


def test_settings_are_immutable():
    with pytest.raises(ValidationError):
        Settings().llm_mode = "real"


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [("get", "/api/missing", 404, "NOT_FOUND"), ("post", "/api/health", 405, "METHOD_NOT_ALLOWED")],
)
def test_http_errors_follow_contract_and_have_matching_request_ids(app, method, path, status, code):
    with TestClient(app) as client:
        response = getattr(client, method)(path)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["requestId"] == response.headers["X-Request-ID"]
    assert UUID(response.json()["requestId"]).version == 4
    assert_contract(response.json(), "ErrorResponse")
    if status == 405:
        assert "GET" in response.headers["Allow"]


def test_public_application_errors_preserve_fields(app):
    @app.get("/test/application-error")
    async def fail():
        raise ApiError(409, "REVISION_CONFLICT", "版本已更新", {"currentRevision": 2})

    with TestClient(app) as client:
        response = client.get("/test/application-error")
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "REVISION_CONFLICT",
        "message": "版本已更新",
        "details": {"currentRevision": 2},
    }
    assert_contract(response.json(), "ErrorResponse")


def test_http_exception_does_not_echo_private_detail(app):
    @app.get("/test/http-error")
    async def fail():
        raise HTTPException(503, detail="test-only-private-marker")

    with TestClient(app) as client:
        response = client.get("/test/http-error")
    assert response.status_code == 503
    assert "private-marker" not in response.text
    assert_contract(response.json(), "ErrorResponse")


def test_validation_error_reports_fields_without_input_values(app):
    class Input(BaseModel):
        count: int = Field(gt=0)

    @app.post("/test/validation-error")
    async def validate(payload: Input):
        return payload

    with TestClient(app) as client:
        response = client.post("/test/validation-error", json={"count": "test-only-private-marker"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]["fields"][0]["field"] == "body.count"
    assert "private-marker" not in response.text
    assert_contract(response.json(), "ErrorResponse")


def test_unexpected_error_is_safe_in_response_and_logs(app, caplog):
    @app.get("/test/unexpected-error")
    async def fail():
        raise RuntimeError("test-only-private-marker")

    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        response = client.get("/test/unexpected-error")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["requestId"] == response.headers["X-Request-ID"]
    assert "private-marker" not in response.text + caplog.text
    assert_contract(response.json(), "ErrorResponse")


def test_broken_response_is_internal_error_not_request_validation(app):
    @app.get("/test/broken-response", response_model=int)
    async def invalid_response():
        return "test-only-private-marker"

    with TestClient(app) as client:
        response = client.get("/test/broken-response")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "private-marker" not in response.text
    assert_contract(response.json(), "ErrorResponse")


def test_requests_get_new_server_generated_ids(app):
    with TestClient(app) as client:
        first = client.get("/api/health", headers={"X-Request-ID": "untrusted-client-value"})
        second = client.get("/api/health")
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    assert UUID(first.headers["X-Request-ID"]).version == 4
