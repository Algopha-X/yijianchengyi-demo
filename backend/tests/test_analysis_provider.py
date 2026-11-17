"""Actual provider behavior and bounded transport tests; no external model is called."""

import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.errors import ApiError
from app.providers.chat_completions import ChatCompletionsProvider
from app.providers.mock import MockProvider
from app.services.analysis import analyze
from app.services.templates import load_templates

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def template():
    return next(
        template
        for template in load_templates(ROOT / "templates")
        if template["id"] == "poster-fresh-v1"
    )


@pytest.fixture
def task():
    data = json.loads((ROOT / "examples/campus-event-complete.json").read_text(encoding="utf-8"))
    return {
        **data,
        "fields": {
            key: ""
            for key in (
                "title",
                "subtitle",
                "body",
                "time",
                "location",
                "organizer",
            )
        },
        "userEditedFields": [],
    }


@pytest.fixture
def mock_settings():
    return Settings(_env_file=None, llm_mode="mock", app_env="test")


@pytest.fixture
def real_settings():
    return Settings(
        _env_file=None,
        llm_mode="real",
        app_env="test",
        llm_base_url="https://model.invalid/v1/",
        llm_api_key="test-only-key",
        llm_model="transport-test-model",
    )


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    async def immediate(_seconds):
        return None

    monkeypatch.setattr("app.providers.chat_completions.asyncio.sleep", immediate)


def run_mock(task, template, settings):
    return asyncio.run(MockProvider().analyze(task, [template], template, settings))


def output_envelope(payload):
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


def run_real(task, template, settings, handler):
    return asyncio.run(
        ChatCompletionsProvider(httpx.MockTransport(handler)).analyze(
            task,
            [template],
            template,
            settings,
        )
    )


def test_mock_complete_extracts_every_tag_and_preserves_final_body(task, template, mock_settings):
    first = run_mock(task, template, mock_settings)
    assert first == run_mock(task, template, mock_settings)
    assert first["fields"] == {
        "title": "秋日读书会",
        "subtitle": "",
        "body": "带上一本喜欢的书，分享你的阅读故事。",
        "time": "2026年11月7日14:00",
        "location": "图书馆一楼交流区",
        "organizer": "校园阅读社",
    }
    assert first["missingFields"] == []
    assert {fact["id"] for fact in first["facts"]} == {"time", "location", "organizer"}
    assert "Mock" in first["warnings"][0]


def test_mock_missing_example_asks_real_metadata_questions(task, template, mock_settings):
    missing = json.loads((ROOT / "examples/campus-event-missing.json").read_text(encoding="utf-8"))
    task.update(missing)
    result = run_mock(task, template, mock_settings)
    assert result["missingFields"] == ["time", "location"]
    assert result["questions"] == [
        {"field": key, "question": template["fields"][key]["question"]}
        for key in ("time", "location")
    ]
    assert result["facts"] == []
    assert result["fields"]["time"] == result["fields"]["location"] == ""


def test_mock_body_splits_at_next_label_not_first_sentence(task, template, mock_settings):
    task["rawText"] = "标题：分段测试。正文：第一句。第二句！\n第三句。时间：14:00。地点：图书馆"
    result = run_mock(task, template, mock_settings)
    assert result["fields"]["body"] == "第一句。第二句！\n第三句"
    assert result["fields"]["time"] == "14:00"
    assert result["fields"]["location"] == "图书馆"


def test_explicit_fields_override_original_including_clear(task, template, mock_settings):
    task["fields"].update(title="我的标题", time="", location="用户确认的新地点")
    task["userEditedFields"] = ["title", "time", "location"]
    original = copy.deepcopy(task)
    result = run_mock(task, template, mock_settings)
    assert result["fields"]["title"] == "我的标题"
    assert result["fields"]["time"] == ""
    assert result["fields"]["location"] == "用户确认的新地点"
    assert result["missingFields"] == ["time"]
    assert task == original


def test_unlabelled_text_is_preserved_without_fabricating_facts(task, template, mock_settings):
    task["rawText"] = "请帮我做一张海报"
    result = run_mock(task, template, mock_settings)
    assert result["fields"]["body"] == task["rawText"]
    assert all(value == "" for key, value in result["fields"].items() if key != "body")
    assert result["missingFields"] == ["title"]
    assert result["intentType"] == "announcement"
    assert any("完整保留" in warning for warning in result["warnings"])


def test_mock_overflow_reports_field_without_truncating(task, template, mock_settings):
    body = "长" * 161
    task["rawText"] = "标题：边界。正文：" + body
    with pytest.raises(ApiError) as caught:
        run_mock(task, template, mock_settings)
    assert caught.value.status_code == 422
    assert caught.value.code == "TEXT_OVERFLOW"
    assert caught.value.details["fields"][0]["field"] == "body"
    assert caught.value.details["fields"][0]["actualChars"] == 161
    assert task["rawText"].endswith(body)


def test_capacity_uses_unicode_code_points(task, template, mock_settings):
    template["fields"]["body"]["maxChars"] = 2
    task["rawText"] = "标题：边界。正文：🙂🙂"
    assert run_mock(task, template, mock_settings)["fields"]["body"] == "🙂🙂"
    task["rawText"] += "🙂"
    with pytest.raises(ApiError, match="文字超过"):
        run_mock(task, template, mock_settings)


def test_unselected_template_stays_unconfirmed_and_does_not_guess_missing(
    task, template, mock_settings
):
    result = asyncio.run(analyze(task, [template], None, mock_settings))
    assert result["recommendedTemplateIds"] == [template["id"]]
    assert result["missingFields"] == result["questions"] == []
    assert any("选择模板" in warning for warning in result["warnings"])


def test_candidates_filter_scenario_style_latest_and_limit(task, template, mock_settings):
    templates = []
    for style in ("retro", "modern", "fresh", "lively"):
        candidate = copy.deepcopy(template)
        candidate.update(id=f"poster-{style}-v1", style=style)
        templates.append(candidate)
    newest = copy.deepcopy(template)
    newest.update(id="poster-fresh-v2", version=2)
    templates.append(newest)
    task["style"] = None
    result = asyncio.run(MockProvider().analyze(task, templates, None, mock_settings))
    assert result["recommendedTemplateIds"] == [
        "poster-fresh-v2",
        "poster-lively-v1",
        "poster-modern-v1",
    ]
    task["style"] = "asymmetric"
    result = asyncio.run(MockProvider().analyze(task, templates, None, mock_settings))
    assert result["recommendedTemplateIds"] == []


def test_facts_include_literal_amount_and_contacts_with_chinese_adjacent(
    task, template, mock_settings
):
    task["rawText"] = (
        "标题：活动。正文：报名费200元，请联系13800138000或hello@example.com咨询。"
        "时间：14:00。地点：图书馆"
    )
    result = run_mock(task, template, mock_settings)
    values = {(fact["kind"], fact["value"]) for fact in result["facts"]}
    assert ("amount", "200元") in values
    assert ("contact", "13800138000") in values
    assert ("contact", "hello@example.com") in values
    assert len({fact["id"] for fact in result["facts"]}) == len(result["facts"])


def test_real_transport_returns_validated_json_and_safe_url(
    task, template, mock_settings, real_settings
):
    result = run_mock(task, template, mock_settings)
    requests = []

    def handler(request):
        requests.append(request)
        data = json.loads(request.content)
        assert data["model"] == "transport-test-model"
        assert data["response_format"] == {"type": "json_object"}
        assert request.headers["Authorization"] == "Bearer test-only-key"
        return httpx.Response(200, json=output_envelope(result))

    assert run_real(task, template, real_settings, handler)["fields"] == result["fields"]
    assert len(requests) == 1
    assert str(requests[0].url) == "https://model.invalid/v1/chat/completions"


def test_real_does_not_duplicate_completion_path(task, template, mock_settings, real_settings):
    result = run_mock(task, template, mock_settings)
    settings = real_settings.model_copy(
        update={"llm_base_url": "https://model.invalid/v1/chat/completions/"}
    )

    def handler(request):
        assert str(request.url) == "https://model.invalid/v1/chat/completions"
        return httpx.Response(200, json=output_envelope(result))

    run_real(task, template, settings, handler)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_status_consumes_only_one_retry(
    task, template, mock_settings, real_settings, status
):
    result = run_mock(task, template, mock_settings)
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(status)
            if len(calls) == 1
            else httpx.Response(200, json=output_envelope(result))
        )

    assert run_real(task, template, real_settings, handler)["fields"] == result["fields"]
    assert len(calls) == 2


@pytest.mark.parametrize(
    "failure", ["json_then_429", "timeout_then_json", "json_twice", "duplicate_keys"]
)
def test_network_and_json_repair_share_total_budget(task, template, real_settings, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout_then_json" and len(calls) == 1:
            raise httpx.ReadTimeout("private upstream detail", request=request)
        if failure == "json_then_429" and len(calls) == 2:
            return httpx.Response(429, text="private upstream detail")
        content = (
            '{"intentType":"event","intentType":"record"}'
            if failure == "duplicate_keys"
            else "not JSON"
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    with pytest.raises(ApiError) as caught:
        run_real(task, template, real_settings, handler)
    assert len(calls) == 2
    assert caught.value.status_code == 503
    assert caught.value.code in {"MODEL_OUTPUT_INVALID", "MODEL_UNAVAILABLE"}
    assert "private" not in str(caught.value)


def test_invalid_json_can_be_repaired_once(task, template, mock_settings, real_settings):
    result = run_mock(task, template, mock_settings)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "```json\n{}\n```"}}]}
            )
        assert len(json.loads(request.content)["messages"]) == 3
        return httpx.Response(200, json=output_envelope(result))

    assert run_real(task, template, real_settings, handler)["fields"] == result["fields"]
    assert len(calls) == 2


@pytest.mark.parametrize(
    "mutation", ["extra", "enum", "template", "time", "person", "body_type", "explicit_clear"]
)
def test_invalid_or_untrusted_model_content_is_rejected(
    task, template, mock_settings, real_settings, mutation
):
    result = run_mock(task, template, mock_settings)
    if mutation == "extra":
        result["html"] = "<script>not executed</script>"
    elif mutation == "enum":
        result["intentType"] = "unknown"
    elif mutation == "template":
        result["recommendedTemplateIds"] = ["poster-retro-v1"]
    elif mutation == "time":
        result["fields"]["time"] = "2099年1月1日"
    elif mutation == "person":
        result["facts"].append({"id": "invented", "kind": "person", "value": "虚构人物"})
    elif mutation == "body_type":
        result["fields"]["body"] = ["not a string"]
    else:
        task["fields"]["time"] = ""
        task["userEditedFields"] = ["time"]
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=output_envelope(result))

    with pytest.raises(ApiError) as caught:
        run_real(task, template, real_settings, handler)
    assert len(calls) == 2
    assert caught.value.code == "MODEL_OUTPUT_INVALID"


def test_real_rechecks_required_fields_instead_of_trusting_model(
    task, template, mock_settings, real_settings
):
    task["rawText"] = "标题：活动。正文：介绍"
    result = run_mock(task, template, mock_settings)
    result["missingFields"] = []
    result["questions"] = []
    response = run_real(
        task,
        template,
        real_settings,
        lambda request: httpx.Response(200, json=output_envelope(result)),
    )
    assert response["missingFields"] == ["time", "location"]


def test_real_does_not_retry_auth_errors_or_disclose_upstream_body(task, template, real_settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, text="secret-upstream-response test-only-key")

    with pytest.raises(ApiError) as caught:
        run_real(task, template, real_settings, handler)
    assert len(calls) == 1
    assert caught.value.code == "MODEL_UNAVAILABLE"
    assert "secret-upstream" not in str(caught.value)
    assert "test-only-key" not in str(caught.value)


def test_real_honors_zero_retries(task, template, real_settings):
    calls = []
    settings = real_settings.model_copy(update={"llm_max_retries": 0})

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(ApiError) as caught:
        run_real(task, template, settings, handler)
    assert len(calls) == 1
    assert caught.value.code == "MODEL_UNAVAILABLE"


def test_real_capacity_failure_is_field_error_after_repair_budget(
    task, template, mock_settings, real_settings
):
    result = run_mock(task, template, mock_settings)
    result["fields"]["body"] = "文" * 161
    with pytest.raises(ApiError) as caught:
        run_real(
            task,
            template,
            real_settings,
            lambda request: httpx.Response(200, json=output_envelope(result)),
        )
    assert caught.value.code == "TEXT_OVERFLOW"
    assert caught.value.details["fields"][0]["field"] == "body"
