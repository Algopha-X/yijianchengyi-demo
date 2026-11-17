"""Compare the actual application's generated operations against the frozen contract."""

import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.studio import router
from app.config import Settings
from app.main import create_app

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts/openapi.json").read_text(encoding="utf-8")
)


@pytest.fixture
def application(tmp_path):
    return create_app(Settings(
        _env_file=None, app_env="test", llm_mode="mock",
        data_dir=tmp_path / "data", database_path=tmp_path / "data/app.db",
    ))


def operations(document):
    return {
        (path, method): operation
        for path, path_item in document["paths"].items()
        for method, operation in path_item.items()
        if method in HTTP_METHODS
    }


def success_codes(operation):
    return {status for status in operation["responses"] if status.isdigit() and 200 <= int(status) < 300}


def test_v1_openapi_keeps_exact_sixteen_operations_with_separate_v2_extensions(application):
    expected = operations(CONTRACT)
    all_operations = operations(application.openapi())
    actual = {key: value for key, value in all_operations.items() if not key[0].startswith("/api/v2/")}
    assert len(expected) == len(actual) == 16
    assert all(key in expected or key[0].startswith("/api/v2/") for key in all_operations)
    assert set(actual) == set(expected)
    for operation_key, expected_operation in expected.items():
        actual_operation = actual[operation_key]
        assert actual_operation["operationId"] == expected_operation["operationId"], operation_key
        assert success_codes(actual_operation) == success_codes(expected_operation), operation_key
    identifiers = [operation["operationId"] for operation in all_operations.values()]
    assert len(identifiers) == len(set(identifiers))


def test_actual_openapi_path_parameters_keep_public_camel_case_and_uuid_types(application):
    expected = operations(CONTRACT)
    actual = operations(application.openapi())
    for operation_key, expected_operation in expected.items():
        expected_paths = {parameter["name"]: parameter for parameter in expected_operation.get("parameters", [])
                          if parameter["in"] == "path"}
        actual_paths = {parameter["name"]: parameter for parameter in actual[operation_key].get("parameters", [])
                        if parameter["in"] == "path"}
        assert set(actual_paths) == set(expected_paths), operation_key
        for name, parameter in expected_paths.items():
            assert actual_paths[name]["required"] is True
            assert actual_paths[name]["schema"]["type"] == parameter["schema"]["type"]
            assert actual_paths[name]["schema"].get("format") == parameter["schema"].get("format")


def test_export_query_constraints_and_binary_content_match_contract(application):
    operation_key = ("/api/tasks/{taskId}/export", "get")
    expected = operations(CONTRACT)[operation_key]
    actual_operations = operations(application.openapi())
    actual = actual_operations[operation_key]
    expected_queries = {item["name"]: item for item in expected["parameters"] if item["in"] == "query"}
    actual_queries = {item["name"]: item for item in actual["parameters"] if item["in"] == "query"}
    assert set(actual_queries) == set(expected_queries)
    for name, parameter in expected_queries.items():
        assert actual_queries[name]["required"] == parameter["required"]
        for keyword in ("type", "enum", "minimum"):
            if keyword in parameter["schema"]:
                assert actual_queries[name]["schema"][keyword] == parameter["schema"][keyword]
    for path in ("/api/tasks/{taskId}/export", "/api/assets/{assetId}/file"):
        expected_content = CONTRACT["paths"][path]["get"]["responses"]["200"]["content"]
        actual_content = actual_operations[(path, "get")]["responses"]["200"]["content"]
        assert actual_content == expected_content


def test_idempotency_header_is_required_uuid_in_actual_openapi(application):
    for action in ("analyze", "render"):
        operation = application.openapi()["paths"][f"/api/tasks/{{taskId}}/{action}"]["post"]
        header = next(item for item in operation["parameters"] if item["in"] == "header")
        assert header["name"] == "Idempotency-Key"
        assert header["required"] is True
        assert header["schema"]["format"] == "uuid"


def test_camel_case_path_aliases_resolve_and_bad_query_or_header_is_422(application):
    with TestClient(application) as client:
        assert client.post("/api/session").status_code == 200
        created = client.post("/api/tasks", json={
            "scenario": "poster", "style": "fresh", "rawText": "测试导出校验", "assetIds": [],
        })
        assert created.status_code == 201
        task = created.json()
        assert client.get(f"/api/tasks/{task['id']}").json() == task
        assert client.get("/api/tasks/not-a-uuid").status_code == 422
        assert client.get(f"/api/tasks/{task['id']}/export",
                          params={"format": "png", "expectedRevision": 0}).status_code == 422
        assert client.get(f"/api/tasks/{task['id']}/export",
                          params={"expectedRevision": 1}).status_code == 422
        assert client.post(f"/api/tasks/{task['id']}/analyze",
                           headers={"Idempotency-Key": "not-a-uuid"},
                           json={"expectedRevision": 1}).status_code == 422


def test_job_routes_keep_optional_result_fields_unset():
    expected = {
        ("/api/tasks/{taskId}/analyze", "POST"),
        ("/api/tasks/{taskId}/render", "POST"),
        ("/api/jobs/{jobId}", "GET"),
    }
    found = set()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if (route.path, method) in expected:
                assert route.response_model_exclude_unset is True
                found.add((route.path, method))
    assert found == expected


def test_actual_analysis_result_omits_unset_preview_asset(application):
    with TestClient(application) as client:
        assert client.post("/api/session").status_code == 200
        task = client.post("/api/tasks", json={
            "scenario": "poster", "style": "fresh", "rawText": "标题：契约验证。正文：测试内容",
            "assetIds": [],
        }).json()
        response = client.post(f"/api/tasks/{task['id']}/analyze",
                               headers={"Idempotency-Key": str(uuid4())},
                               json={"expectedRevision": task["revision"]})
        assert response.status_code == 202
        job = response.json()
        for _ in range(100):
            result = client.get(f"/api/jobs/{job['id']}").json()
            if result["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert result["status"] == "succeeded"
        assert result["result"] == {"appliedRevision": task["revision"] + 1}
