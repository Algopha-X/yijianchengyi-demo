"""Deterministic tagged-text extraction without synthetic facts or hidden truncation."""

from typing import Any

from app.config import Settings
from app.providers.facts import infer_intent
from app.providers.validation import candidates, extract_fields, require_capacity, validate_analysis
from app.services.templates import field_capacity_errors


class MockProvider:
    async def analyze(
        self,
        task: dict[str, Any],
        templates: list[dict[str, Any]],
        selected_template: dict[str, Any] | None,
        settings: Settings,
    ) -> dict[str, Any]:
        fields = extract_fields(task)
        eligible = candidates(task, templates)
        fitting = [template for template in eligible if not field_capacity_errors(template, fields)]
        if selected_template is not None:
            require_capacity(selected_template, fields)
        elif eligible and not fitting:
            require_capacity(eligible[0], fields)
        warnings = [
            "当前为 Mock 模式：仅按标签和保守关键词规则提取、校验内容，未调用真实大模型或自动润色。"
        ]
        if fields["body"] == task["rawText"].strip() and task["rawText"].strip():
            warnings.append(
                "未识别到字段标签，原文已完整保留到正文；请补充或调整标题、时间、地点等字段。"
            )
        if selected_template is None:
            warnings.append("请先选择模板，再检查必填信息并确认事实。")
        if not eligible:
            warnings.append("当前类型和风格没有可用模板，请调整选择。")
        return validate_analysis(
            {
                "intentType": infer_intent(task["rawText"]),
                "summary": "已提取用户提供的字段，请核对内容与关键事实。",
                "recommendedTemplateIds": [template["id"] for template in fitting[:3]],
                "fields": fields,
                "missingFields": [],
                "questions": [],
                "facts": [],
                "warnings": warnings,
            },
            task,
            templates,
            selected_template,
        )
