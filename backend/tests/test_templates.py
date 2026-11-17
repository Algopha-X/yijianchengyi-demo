"""Tests for the template catalog, version selection and metadata rules."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.api.templates import catalog_dependency
from app.config import PROJECT_ROOT, Settings
from app.main import create_app
from app.services.templates import (
    TemplateCatalog,
    TemplateNotFoundError,
    TemplateValidationError,
    ThumbnailNotFoundError,
    field_capacity_errors,
    field_is_required,
    latest_templates,
    load_templates,
    missing_required_fields,
    required_field_keys,
    validate_template_payload,
)


@pytest.fixture
def base_template() -> dict:
    return json.loads(
        (PROJECT_ROOT / "templates/poster-fresh-v1.json").read_text(encoding="utf-8")
    )


def write_template(directory: Path, template: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{template['id']}.json"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    return path


def make_version(template: dict, version: int) -> dict:
    result = copy.deepcopy(template)
    result["version"] = version
    result["id"] = f"poster-fresh-v{version}"
    return result


@pytest.fixture
def catalog(tmp_path: Path, base_template: dict) -> TemplateCatalog:
    template_dir = tmp_path / "templates"
    write_template(template_dir, base_template)
    return TemplateCatalog(template_dir, tmp_path / "previews")


@pytest.fixture
def client(catalog: TemplateCatalog):
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[catalog_dependency] = lambda: catalog
    with TestClient(app) as test_client:
        yield test_client


def validate_openapi_payload(payload: object, name: str) -> None:
    contract = json.loads((PROJECT_ROOT / "contracts/openapi.json").read_text(encoding="utf-8"))
    schema = {**contract["components"]["schemas"][name], "components": contract["components"]}
    Draft202012Validator(schema).validate(payload)


def validate_template_schema(payload: object) -> None:
    schema = json.loads(
        (PROJECT_ROOT / "contracts/template.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(payload)


def test_repository_template_is_schema_valid():
    templates = load_templates()
    for template in templates:
        validate_template_schema(template)
    assert "poster-fresh-v1" in {template["id"] for template in templates}


def test_unknown_top_level_and_nested_fields_are_rejected(base_template):
    unknown_top = copy.deepcopy(base_template)
    unknown_top["unexpected"] = True
    with pytest.raises(TemplateValidationError, match="未知字段"):
        validate_template_payload(unknown_top)

    unknown_nested = copy.deepcopy(base_template)
    unknown_nested["fields"]["title"]["unexpected"] = True
    with pytest.raises(TemplateValidationError, match="未知字段"):
        validate_template_payload(unknown_nested)


def test_box_must_stay_inside_canvas(base_template):
    invalid = copy.deepcopy(base_template)
    invalid["fields"]["title"]["box"]["x"] = 1000
    with pytest.raises(TemplateValidationError, match="超出画布边界"):
        validate_template_payload(invalid)


def test_font_range_and_filename_must_be_consistent(tmp_path: Path, base_template):
    invalid_font = copy.deepcopy(base_template)
    invalid_font["fields"]["title"]["minFontSize"] = 100
    with pytest.raises(TemplateValidationError, match="不能大于"):
        validate_template_payload(invalid_font)

    source = tmp_path / "wrong-name.json"
    source.write_text(json.dumps(base_template), encoding="utf-8")
    with pytest.raises(TemplateValidationError, match="文件名"):
        load_templates(tmp_path)


def test_title_and_body_must_always_be_required(base_template):
    invalid = copy.deepcopy(base_template)
    invalid["fields"]["body"]["required"] = False
    with pytest.raises(TemplateValidationError, match="标题和正文"):
        validate_template_payload(invalid)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("schemaVersion",), "2.0", "允许范围"),
        (("canvas", "width"), 1079, "画布尺寸"),
        (("id",), "article-image-fresh-v1", "必须一致"),
    ],
)
def test_invalid_schema_fields_and_cross_field_values_are_rejected(
    base_template, path, value, message
):
    invalid = copy.deepcopy(base_template)
    target = invalid
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(TemplateValidationError, match=message):
        validate_template_payload(invalid)


def test_latest_version_wins_per_scenario_and_style(tmp_path: Path, base_template):
    template_dir = tmp_path / "templates"
    write_template(template_dir, make_version(base_template, 1))
    write_template(template_dir, make_version(base_template, 3))

    article = copy.deepcopy(base_template)
    article.update(
        id="article-image-fresh-v1",
        version=1,
        scenario="article_image",
        style="fresh",
    )
    article["canvas"] = {"width": 1200, "height": 675, "background": "#F3F7EF"}
    for key, field in article["fields"].items():
        field["box"] = {"x": 10, "y": 10 + len(key) * 20, "width": 300, "height": 18}
    article["imageSlot"]["box"] = {"x": 400, "y": 10, "width": 400, "height": 300}
    article["withoutImage"] = {"body": {"x": 10, "y": 200, "width": 1100, "height": 300}}
    article["decorations"] = []
    write_template(template_dir, article)

    latest = latest_templates(load_templates(template_dir))
    assert [template["id"] for template in latest] == [
        "poster-fresh-v3",
        "article-image-fresh-v1",
    ]


def test_list_returns_latest_contract_items_and_filters(client, base_template):
    response = client.get("/api/templates", params={"scenario": "poster", "style": "fresh"})
    assert response.status_code == 200
    payload = response.json()
    validate_openapi_payload(payload, "TemplateList")
    assert payload == {
        "items": [
            {
                "id": base_template["id"],
                "version": 1,
                "name": base_template["name"],
                "scenario": "poster",
                "style": "fresh",
                "thumbnailUrl": "/api/templates/poster-fresh-v1/thumbnail",
            }
        ]
    }
    assert client.get("/api/templates?scenario=article_image").json() == {"items": []}


def test_detail_returns_full_contract_template(client, base_template):
    response = client.get(f"/api/templates/{base_template['id']}")
    assert response.status_code == 200
    assert response.json() == base_template
    validate_template_schema(response.json())


def test_unknown_template_and_missing_thumbnail_are_explicit_404(client):
    missing_template = client.get("/api/templates/not-a-template")
    assert missing_template.status_code == 404
    assert missing_template.json()["error"]["code"] == "NOT_FOUND"
    assert missing_template.json()["error"]["message"] == "未找到模板"

    missing_thumbnail = client.get("/api/templates/poster-fresh-v1/thumbnail")
    assert missing_thumbnail.status_code == 404
    assert missing_thumbnail.json()["error"]["code"] == "NOT_FOUND"
    assert "缩略图尚未生成" in missing_thumbnail.json()["error"]["message"]


def test_invalid_filter_is_contract_validation_error(client):
    response = client.get("/api/templates", params={"scenario": "unknown"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]["fields"][0]["field"] == "query.scenario"


def test_router_openapi_operation_ids_and_parameters_match_contract(client):
    paths = client.app.openapi()["paths"]
    assert paths["/api/templates"]["get"]["operationId"] == "listTemplates"
    assert {
        parameter["name"] for parameter in paths["/api/templates"]["get"]["parameters"]
    } == {"scenario", "style"}
    assert paths["/api/templates/{templateId}"]["get"]["operationId"] == "getTemplate"
    assert {
        parameter["name"]
        for parameter in paths["/api/templates/{templateId}"]["get"]["parameters"]
    } == {"templateId"}
    assert (
        paths["/api/templates/{templateId}/thumbnail"]["get"]["operationId"]
        == "getTemplateThumbnail"
    )
    assert {
        parameter["name"]
        for parameter in paths["/api/templates/{templateId}/thumbnail"]["get"]["parameters"]
    } == {"templateId"}


def test_existing_thumbnail_is_streamed(tmp_path: Path, base_template):
    template_dir = tmp_path / "templates"
    preview_dir = tmp_path / "previews"
    write_template(template_dir, base_template)
    preview_dir.mkdir()
    thumbnail = preview_dir / "poster-fresh-v1.png"
    thumbnail.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    catalog = TemplateCatalog(template_dir, preview_dir)

    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[catalog_dependency] = lambda: catalog
    with TestClient(app) as test_client:
        response = test_client.get("/api/templates/poster-fresh-v1/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == thumbnail.read_bytes()


def test_required_and_required_for_rules(base_template):
    assert required_field_keys(base_template, "event") == (
        "title",
        "body",
        "time",
        "location",
    )
    assert required_field_keys(base_template, "announcement") == ("title", "body")
    assert field_is_required(base_template, "location", "event")
    assert not field_is_required(base_template, "location", "record")
    assert missing_required_fields(
        base_template,
        "event",
        {"title": "活动", "body": "正文", "time": " ", "location": "\n"},
    ) == ("time", "location")


def test_capacity_uses_trimmed_unicode_code_points(base_template):
    maximum = base_template["fields"]["title"]["maxChars"]
    fields = {key: "" for key in base_template["fields"]}
    fields["title"] = "😀" * maximum
    assert field_capacity_errors(base_template, fields) == ()

    fields["title"] = "😀" * (maximum + 1)
    assert field_capacity_errors(base_template, fields) == (
        {"field": "title", "actualChars": maximum + 1, "maxChars": maximum},
    )


def test_direct_lookup_helpers_raise_clear_errors(catalog):
    with pytest.raises(TemplateNotFoundError):
        catalog.get_template("missing")
    with pytest.raises(ThumbnailNotFoundError):
        catalog.get_thumbnail("poster-fresh-v1")


def test_invalid_template_catalog_is_a_public_503():
    catalog = Mock(spec=TemplateCatalog)
    catalog.list_templates.side_effect = TemplateValidationError("fields.title 超出画布边界")
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[catalog_dependency] = lambda: catalog
    with TestClient(app) as test_client:
        response = test_client.get("/api/templates")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "fields.title" in response.json()["error"]["details"]["reason"]


def test_catalog_construction_failure_is_a_public_503(monkeypatch):
    def fail():
        raise TemplateValidationError("模板目录中没有任何模板")

    monkeypatch.setattr("app.api.templates.get_template_catalog", fail)
    app = create_app(Settings(app_env="test"))
    with TestClient(app) as test_client:
        response = test_client.get("/api/templates")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "没有任何模板" in response.json()["error"]["details"]["reason"]
