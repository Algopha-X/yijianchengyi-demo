"""Regression tests for raw-text facts and strictly typed JSON revisions."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.models.tasks import ConfirmRequest, RevisionRequest, TaskPatch
from app.providers.chat_completions import ChatCompletionsProvider
from app.providers.mock import MockProvider
from app.providers.validation import validate_analysis
from app.services.templates import load_templates

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def template():
    return next(value for value in load_templates() if value["id"] == "poster-fresh-v1")


def task_for(text):
    return {
        "scenario": "poster",
        "style": "fresh",
        "rawText": text,
        "fields": {
            key: "" for key in ("title", "subtitle", "body", "time", "location", "organizer")
        },
        "userEditedFields": [],
    }


def mock(task, template):
    return asyncio.run(
        MockProvider().analyze(
            task, [template], template, Settings(_env_file=None, llm_mode="mock")
        )
    )


def real(task, template, payload, monkeypatch):
    calls = []

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("app.providers.chat_completions.asyncio.sleep", no_wait)

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(payload, ensure_ascii=False),
                        }
                    }
                ]
            },
        )

    config = Settings(
        _env_file=None,
        llm_mode="real",
        llm_base_url="https://fact.invalid/v1",
        llm_api_key="test-key-not-real",
        llm_model="test-model",
    )
    try:
        result = asyncio.run(
            ChatCompletionsProvider(httpx.MockTransport(handler)).analyze(
                task, [template], template, config
            )
        )
        return result, calls
    except ApiError as error:
        assert len(calls) == 2
        raise error


def sample_payload():
    return {
        "intentType": "event",
        "summary": "学术分享活动",
        "recommendedTemplateIds": ["poster-fresh-v1"],
        "fields": {
            "title": "学术分享",
            "subtitle": "",
            "body": "嘉宾张三将于11月20日在图书馆交流。报名费50元，请联系13812345678。",
            "time": "2026年11月20日14:00",
            "location": "图书馆一楼",
            "organizer": "校园阅读社",
        },
        "missingFields": [],
        "questions": [],
        "facts": [],
        "warnings": [],
    }


@pytest.mark.parametrize(
    "old,new",
    [
        ("张三", "李四"),
        ("11月20日", "11月21日"),
        ("图书馆交流", "教学楼交流"),
        ("报名费50元，", ""),
        ("请联系13812345678。", ""),
    ],
)
def test_real_rejects_modified_or_omitted_body_facts(template, monkeypatch, old, new):
    payload = sample_payload()
    source = (
        "标题：学术分享。时间：2026年11月20日14:00。地点：图书馆一楼。"
        "主办方：校园阅读社。正文：" + payload["fields"]["body"]
    )
    task = task_for(source)
    payload["fields"]["body"] = payload["fields"]["body"].replace(old, new)
    with pytest.raises(ApiError) as caught:
        real(task, template, payload, monkeypatch)
    assert caught.value.code == "MODEL_OUTPUT_INVALID"


def test_unlabelled_raw_amount_and_contact_cannot_disappear(template, monkeypatch):
    task = task_for(
        "请为讲座做一张海报，嘉宾张三将在11月20日14:00来到图书馆一楼，报名费50元，联系13812345678。"
    )
    payload = sample_payload()
    payload["fields"].update(body="欢迎参加讲座。", time="11月20日14:00", organizer="")
    with pytest.raises(ApiError) as caught:
        real(task, template, payload, monkeypatch)
    assert caught.value.code == "MODEL_OUTPUT_INVALID"


def test_bare_chinese_name_cannot_be_replaced(template, monkeypatch):
    task = task_for("标题：阅读邀请。正文：张三。时间：14:00。地点：图书馆")
    payload = mock(task, template)
    payload["fields"]["body"] = "李四。"
    with pytest.raises(ApiError) as caught:
        real(task, template, payload, monkeypatch)
    assert caught.value.code == "MODEL_OUTPUT_INVALID"


def test_fact_inventory_is_built_when_model_supplies_empty_facts(template):
    payload = sample_payload()
    task = task_for(
        "标题：学术分享。正文："
        + payload["fields"]["body"]
        + "时间：2026年11月20日14:00。地点：图书馆一楼。主办方：校园阅读社"
    )
    result = validate_analysis(payload, task, [template], template)
    facts = {(fact["kind"], fact["value"]) for fact in result["facts"]}
    assert ("person", "张三") in facts
    assert ("time", "11月20日") in facts
    assert ("location", "图书馆") in facts
    assert ("amount", "50元") in facts
    assert ("contact", "13812345678") in facts


def test_plain_descriptive_copy_can_still_be_polished(template, monkeypatch):
    task = task_for(
        "标题：阅读邀请。正文：带上一本喜欢的书。分享你的阅读故事。时间：14:00。地点：图书馆"
    )
    payload = mock(task, template)
    payload["fields"]["title"] = "共赴书香时光"
    payload["fields"]["body"] = "携一本爱书，让阅读故事在此相遇。"
    result, calls = real(task, template, payload, monkeypatch)
    assert result["fields"]["body"] == payload["fields"]["body"]
    assert len(calls) == 1


def test_unknown_person_sentence_is_preserved_and_explained(template, monkeypatch):
    sentence = "嘉宾阿卜杜拉汗分享旅行心得。"
    task = task_for(
        "标题：阅读邀请。正文：" + sentence + "欢迎大家一起阅读。时间：14:00。地点：图书馆"
    )
    payload = mock(task, template)
    payload["fields"]["body"] = sentence + "期待与你共读。"
    result, calls = real(task, template, payload, monkeypatch)
    assert sentence in result["fields"]["body"] and len(calls) == 1
    assert any("无法由规则可靠核验" in warning for warning in result["warnings"])
    assert any(fact["value"] == sentence for fact in result["facts"])
    payload["fields"]["body"] = "嘉宾阿卜杜拉汗介绍旅行感受。期待与你共读。"
    with pytest.raises(ApiError) as caught:
        real(task, template, payload, monkeypatch)
    assert caught.value.code == "MODEL_OUTPUT_INVALID"


def test_explicitly_replaced_body_does_not_restore_old_raw_facts(template, monkeypatch):
    task = task_for(
        "标题：讲座。正文：嘉宾张三介绍计划，报名费50元，联系13812345678。时间：14:00。地点：图书馆"
    )
    task["fields"]["body"] = "嘉宾李四介绍新计划，免费参加。"
    task["userEditedFields"] = ["body"]
    payload = mock(task, template)
    result, calls = real(task, template, payload, monkeypatch)
    assert result["fields"]["body"] == task["fields"]["body"]
    assert not any(
        "张三" in fact["value"] or "50元" in fact["value"] or "13812345678" in fact["value"]
        for fact in result["facts"]
    )
    assert len(calls) == 1


def test_explicit_time_correction_wins_over_old_body_and_warns(template, monkeypatch):
    task = task_for("标题：活动。正文：活动将于11月20日在图书馆举行。时间：11月20日。地点：图书馆")
    task["fields"]["time"] = "11月21日"
    task["userEditedFields"] = ["time"]
    payload = mock(task, template)
    payload["fields"]["body"] = "活动将于11月21日在图书馆举行。"
    result, calls = real(task, template, payload, monkeypatch)
    assert result["fields"]["time"] == "11月21日"
    assert not any(fact["value"] == "11月20日" for fact in result["facts"])
    assert any("优先保留修改值" in warning for warning in result["warnings"])
    assert len(calls) == 1


@pytest.mark.parametrize(
    "text,intent",
    [
        ("标题：闭馆通知。正文：请提前归还图书。", "announcement"),
        ("标题：活动回顾。正文：大家一起交流了阅读心得。", "record"),
        ("标题：周末读书会。正文：欢迎报名参加。", "event"),
    ],
)
def test_mock_conservative_intent_routing(template, text, intent):
    result = mock(task_for(text), template)
    assert result["intentType"] == intent
    assert (set(result["missingFields"]) == {"time", "location"}) == (intent == "event")


@pytest.mark.parametrize(
    "model,extra",
    [
        (TaskPatch, {"rawText": "new"}),
        (RevisionRequest, {}),
        (ConfirmRequest, {"confirmedFactIds": []}),
    ],
)
@pytest.mark.parametrize("invalid", [True, False, "1", 1.0])
def test_json_revision_models_reject_coerced_values(model, extra, invalid):
    with pytest.raises(ValidationError):
        model.model_validate({"expectedRevision": invalid, **extra})
    assert model.model_validate({"expectedRevision": 1, **extra}).expected_revision == 1


def test_http_revision_bool_and_string_are_rejected_without_mutation(tmp_path):
    config = Settings(
        _env_file=None,
        app_env="test",
        data_dir=tmp_path / "data",
        database_path=tmp_path / "db.sqlite",
    )
    with TestClient(create_app(config)) as client:
        client.post("/api/session")
        task = client.post(
            "/api/tasks",
            json={"scenario": "poster", "rawText": "标题：活动。正文：介绍", "assetIds": []},
        ).json()
        for value in [True, "1"]:
            response = client.patch(
                f"/api/tasks/{task['id']}", json={"expectedRevision": value, "rawText": "修改"}
            )
            assert response.status_code == 422
            assert client.get(f"/api/tasks/{task['id']}").json()["revision"] == 1
