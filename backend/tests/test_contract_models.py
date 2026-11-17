"""Consistency tests between the OpenAPI contract and the Pydantic models."""

import json

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as ContractValidationError
from pydantic import ValidationError

from app.config import PROJECT_ROOT
from app.main import Health as MainHealth
from app.models import (
    Analysis,
    Asset,
    ConfirmRequest,
    Error,
    ErrorResponse,
    Fact,
    Fields,
    Health,
    Job,
    JobResult,
    Question,
    RevisionRequest,
    Session,
    Task,
    TaskCreate,
    TaskList,
    TaskPatch,
    TemplateList,
    TemplateSummary,
)

CONTRACT = json.loads((PROJECT_ROOT / "contracts/openapi.json").read_text(encoding="utf-8"))
SCHEMAS = CONTRACT["components"]["schemas"]

MODELS = {
    "Fields": Fields,
    "Fact": Fact,
    "Question": Question,
    "Analysis": Analysis,
    "Error": Error,
    "ErrorResponse": ErrorResponse,
    "Health": Health,
    "Session": Session,
    "Asset": Asset,
    "TemplateSummary": TemplateSummary,
    "TemplateList": TemplateList,
    "TaskCreate": TaskCreate,
    "TaskPatch": TaskPatch,
    "RevisionRequest": RevisionRequest,
    "ConfirmRequest": ConfirmRequest,
    "Task": Task,
    "TaskList": TaskList,
    "JobResult": JobResult,
    "Job": Job,
}

UUID_SAMPLE = "9b6f7a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
TIME_SAMPLE = "2026-01-01T08:00:00Z"
FIELDS_SAMPLE = {
    "title": "活动",
    "subtitle": "副标题",
    "body": "正文",
    "time": "周五 14:00",
    "location": "工坊",
    "organizer": "G15",
}
ERROR_SAMPLE = {"code": "GENERIC", "message": "失败", "details": {}}
ANALYSIS_SAMPLE = {
    "intentType": "event",
    "summary": "汇总",
    "recommendedTemplateIds": ["tpl-1"],
    "fields": FIELDS_SAMPLE,
    "missingFields": ["time"],
    "questions": [],
    "facts": [],
    "warnings": [],
}
TEMPLATE_SUMMARY_SAMPLE = {
    "id": "tpl-1",
    "version": 1,
    "name": "模板",
    "scenario": "poster",
    "style": "fresh",
    "thumbnailUrl": "u",
}
TASK_SAMPLE = {
    "id": UUID_SAMPLE,
    "revision": 1,
    "scenario": "poster",
    "style": None,
    "rawText": "做一张海报",
    "fields": FIELDS_SAMPLE,
    "assetIds": [],
    "templateId": None,
    "templateVersion": None,
    "analysis": None,
    "analysisMode": None,
    "confirmedRevision": None,
    "previewAssetId": None,
    "previewRevision": None,
    "phase": "draft",
    "lastError": None,
    "createdAt": TIME_SAMPLE,
    "updatedAt": TIME_SAMPLE,
    "userEditedFields": [],
}
SAMPLES = {
    "Fields": FIELDS_SAMPLE,
    "Fact": {"id": "fact-1", "kind": "time", "value": "周五 14:00"},
    "Question": {"field": "title", "question": "活动名称是什么？"},
    "Analysis": ANALYSIS_SAMPLE,
    "Error": ERROR_SAMPLE,
    "ErrorResponse": {"error": ERROR_SAMPLE, "requestId": UUID_SAMPLE},
    "Health": {"status": "ok", "mode": "mock", "version": "0.1.0"},
    "Session": {"expiresAt": TIME_SAMPLE},
    "Asset": {
        "id": UUID_SAMPLE,
        "kind": "upload",
        "mimeType": "image/png",
        "bytes": 1024,
        "width": 100,
        "height": 100,
        "fileUrl": "u",
        "createdAt": TIME_SAMPLE,
    },
    "TemplateSummary": TEMPLATE_SUMMARY_SAMPLE,
    "TemplateList": {"items": [TEMPLATE_SUMMARY_SAMPLE]},
    "TaskCreate": {"scenario": "poster", "rawText": "做一张海报", "assetIds": []},
    "TaskPatch": {"expectedRevision": 1, "scenario": "poster"},
    "RevisionRequest": {"expectedRevision": 1},
    "ConfirmRequest": {"expectedRevision": 1, "confirmedFactIds": []},
    "Task": TASK_SAMPLE,
    "TaskList": {"items": [TASK_SAMPLE]},
    "JobResult": {},
    "Job": {
        "id": UUID_SAMPLE,
        "taskId": UUID_SAMPLE,
        "kind": "analyze",
        "revision": 1,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "result": None,
        "error": None,
        "createdAt": TIME_SAMPLE,
        "startedAt": None,
        "finishedAt": None,
    },
}


def validate_contract(payload, name):
    schema = {**SCHEMAS[name], "components": CONTRACT["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


def camel_fields(model):
    return {info.alias or name: info for name, info in model.model_fields.items()}


@pytest.mark.parametrize("name", sorted(MODELS))
def test_property_and_required_sets_match_contract(name):
    schema = SCHEMAS[name]
    fields = camel_fields(MODELS[name])
    assert set(schema["properties"]) == set(fields)
    contract_required = set(schema.get("required", []))
    model_required = {alias for alias, info in fields.items() if info.is_required()}
    assert contract_required == model_required


@pytest.mark.parametrize("name", sorted(MODELS))
def test_sample_instances_round_trip_and_match_contract(name):
    model = MODELS[name].model_validate(SAMPLES[name])
    dumped = model.model_dump(mode="json", by_alias=True, exclude_unset=True)
    validate_contract(dumped, name)
    assert MODELS[name].model_validate(dumped) == model


def test_extra_keys_rejected_by_contract_and_model():
    payload = {**SAMPLES["Session"], "extra": "x"}
    with pytest.raises(ValidationError):
        Session.model_validate(payload)
    with pytest.raises(ContractValidationError):
        validate_contract(payload, "Session")


def test_invalid_enum_rejected_by_contract_and_model():
    payload = {**SAMPLES["Task"], "phase": "archived"}
    with pytest.raises(ValidationError):
        Task.model_validate(payload)
    with pytest.raises(ContractValidationError):
        validate_contract(payload, "Task")


def test_length_limits_rejected_by_contract_and_model():
    payload = {**SAMPLES["Fields"], "title": "t" * 201}
    with pytest.raises(ValidationError):
        Fields.model_validate(payload)
    with pytest.raises(ContractValidationError):
        validate_contract(payload, "Fields")


def test_task_patch_requires_one_updatable_field():
    payload = {"expectedRevision": 1}
    with pytest.raises(ValidationError):
        TaskPatch.model_validate(payload)
    with pytest.raises(ContractValidationError):
        validate_contract(payload, "TaskPatch")


@pytest.mark.parametrize(
    "controlled",
    [
        "analysis",
        "analysisMode",
        "confirmedRevision",
        "previewAssetId",
        "previewRevision",
        "phase",
        "lastError",
        "templateVersion",
        "userEditedFields",
    ],
)
def test_task_patch_rejects_server_controlled_fields(controlled):
    payload = {"expectedRevision": 1, "scenario": "poster", controlled: None}
    with pytest.raises(ValidationError):
        TaskPatch.model_validate(payload)
    with pytest.raises(ContractValidationError):
        validate_contract(payload, "TaskPatch")


def test_main_health_matches_contract_model():
    dumped = MainHealth(status="ok", mode="mock", version="0.1.0").model_dump(
        mode="json", by_alias=True
    )
    assert Health.model_validate(dumped) == Health(status="ok", mode="mock", version="0.1.0")
    validate_contract(dumped, "Health")


def test_frontend_api_types_cover_every_schema():
    generated = (PROJECT_ROOT / "frontend/src/types/api.d.ts").read_text(encoding="utf-8")
    for name in SCHEMAS:
        assert name in generated, f"api.d.ts 缺少契约类型 {name}"