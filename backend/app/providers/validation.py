"""Authoritative validation shared by deterministic and remote providers."""

import re
from typing import Any

from app.errors import ApiError
from app.models import Analysis
from app.providers.facts import SourceFact, entity_tokens, uncertain_kind, uncertain_sentences
from app.services.templates import (
    FIELD_KEYS,
    field_capacity_errors,
    latest_templates,
    missing_required_fields,
)

LABELS = {
    "标题": "title",
    "副标题": "subtitle",
    "正文": "body",
    "时间": "time",
    "地点": "location",
    "主办方": "organizer",
}
LABEL_PATTERN = re.compile(r"(?<!副)(副标题|主办方|标题|正文|时间|地点)\s*[：:]")
AMOUNT_PATTERN = re.compile(
    r"(?:[¥￥$]\s*\d+(?:[.,]\d+)*|\d+(?:\.\d+)?\s*(?:万元|千元|亿元|元|美元|人民币)|"
    r"[一二三四五六七八九十百千万两零]+(?:万元|千元|亿元|元))"
)
CONTACT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"(?:\+?86[- ]?)?1[3-9]\d[- ]?\d{4}[- ]?\d{4}|"
    r"0\d{2,3}[- ]\d{7,8})(?![A-Za-z0-9_])"
)
ANALYSIS_KEYS = {
    "intentType",
    "summary",
    "recommendedTemplateIds",
    "fields",
    "missingFields",
    "questions",
    "facts",
    "warnings",
}


def extract_fields(task: dict[str, Any]) -> dict[str, str]:
    """Split on the next label, retaining all sentences in the body."""
    result = {key: "" for key in FIELD_KEYS}
    raw = task.get("rawText", "")
    matches = list(LABEL_PATTERN.finditer(raw))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        value = raw[match.end() : end].strip()
        # A full stop immediately before the following label separates tagged fields.
        # The final body punctuation remains part of the user's text.
        if index + 1 < len(matches):
            value = re.sub(r"[。；;]\s*$", "", value).rstrip()
        result[LABELS[match.group(1)]] = value
    # A model-free route must not throw away ordinary natural-language input.
    # Keep it verbatim as an editable body; do not invent a title/time/location.
    if not matches and raw.strip():
        result["body"] = raw.strip()
    current = task.get("fields", {})
    edited = set(task.get("userEditedFields", []))
    for key in FIELD_KEYS:
        if key in edited:
            result[key] = current.get(key, "").strip()
        elif not result[key] and current.get(key):
            result[key] = current[key].strip()
    return result


def candidates(task: dict[str, Any], templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            template
            for template in latest_templates(templates)
            if template["scenario"] == task["scenario"]
            and (task.get("style") is None or template["style"] == task["style"])
        ],
        key=lambda template: template["id"],
    )


def require_capacity(template: dict[str, Any], fields: dict[str, str]) -> None:
    errors = field_capacity_errors(template, fields)
    if errors:
        raise ApiError(
            422,
            "TEXT_OVERFLOW",
            "文字超过模板容量，请精简内容或更换模板",
            {
                "fields": [{**error, "message": "此字段超过模板字数上限"} for error in errors],
            },
        )


def literal_tokens(text: str) -> list[tuple[str, str]]:
    return [("amount", match.group()) for match in AMOUNT_PATTERN.finditer(text)] + [
        ("contact", match.group()) for match in CONTACT_PATTERN.finditer(text)
    ]


def source_inventory(task: dict[str, Any], original: dict[str, str]):
    """Build an immutable fact list from authoritative input, not model facts.

    Explicit edits replace the corresponding original field. If an edited time
    or location conflicts with an older body sentence, that old value is no
    longer mandatory; a warning requests human reconciliation instead of
    restoring it over the user's correction.
    """
    authority = "\n".join(original.values())
    edited = set(task.get("userEditedFields", []))
    tokens = list(
        dict.fromkeys(
            [
                *entity_tokens(authority),
                *(SourceFact(kind, value) for kind, value in literal_tokens(authority)),
            ]
        )
    )
    required = []
    conflicts = []
    replacements = {"time": "time", "location": "location"}
    for token in tokens:
        field = replacements.get(token.kind)
        if field in edited and token.value not in original[field]:
            conflicts.append(field)
            continue
        required.append(token)
    guards = []
    for key in ("title", "subtitle", "body"):
        if key not in edited:
            for sentence in uncertain_sentences(original[key]):
                # A conflicting old sentence must not force the old fact back.
                if any(
                    token.value in sentence
                    and token.kind in replacements
                    and replacements[token.kind] in conflicts
                    for token in tokens
                ):
                    continue
                guards.append(sentence)
    return authority, required, list(dict.fromkeys(guards)), bool(conflicts)


def validate_analysis(
    payload: object,
    task: dict[str, Any],
    templates: list[dict[str, Any]],
    selected_template: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != ANALYSIS_KEYS:
        raise ValueError("分析必须且只能包含契约声明的字段")
    if not isinstance(payload.get("fields"), dict) or set(payload["fields"]) != set(FIELD_KEYS):
        raise ValueError("分析字段必须是全部六个公共字段")
    parsed = Analysis.model_validate(payload, strict=True)
    result = parsed.model_dump(mode="json", by_alias=True)
    fields = {key: value.strip() for key, value in result["fields"].items()}
    original = extract_fields(task)
    edited = set(task.get("userEditedFields", []))
    source_text, source_facts, guarded_sentences, has_conflict = source_inventory(task, original)
    for key in FIELD_KEYS:
        if key in edited and fields[key] != original[key]:
            raise ValueError("不能改写用户显式编辑的字段")
    for key in ("time", "location", "organizer"):
        value = fields[key]
        if original[key] and value != original[key]:
            raise ValueError("不能改写已提供的时间、地点或主办方")
        if value and value not in source_text:
            raise ValueError("关键事实必须来自原文或已有字段")
    eligible = candidates(task, templates)
    allowed_ids = {template["id"] for template in eligible}
    if any(template_id not in allowed_ids for template_id in result["recommendedTemplateIds"]):
        raise ValueError("推荐模板不在当前候选集合中")
    if any(
        field_capacity_errors(template, fields)
        for template in eligible
        if template["id"] in result["recommendedTemplateIds"]
    ):
        if selected_template is not None:
            require_capacity(selected_template, fields)
        raise ValueError("推荐模板容量不适合当前内容")
    displayed_text = "\n".join(fields.values())
    for fact in source_facts:
        if fact.value not in displayed_text:
            raise ValueError("不能丢失源事实：姓名、时间、地点、金额或联系方式")
    for token in [
        *entity_tokens(displayed_text + "\n" + result["summary"]),
        *(
            SourceFact(kind, value)
            for kind, value in literal_tokens(displayed_text + "\n" + result["summary"])
        ),
    ]:
        if token.value not in source_text:
            raise ValueError("不能编造或变更姓名、时间、地点、金额或联系方式")
    for sentence in guarded_sentences:
        if sentence not in displayed_text:
            raise ValueError("无法可靠核验的事实句必须保留原句，请由用户显式修改")
    for sentence in uncertain_sentences(displayed_text + "\n" + result["summary"]):
        if sentence not in source_text:
            raise ValueError("不能新增无法可靠核验的人名、地点或时间事实句")
    facts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_fact(fact_id: str, kind: str, value: str) -> None:
        if (kind, value) not in seen:
            facts.append({"id": fact_id, "kind": kind, "value": value})
            seen.add((kind, value))

    for key, kind in (("time", "time"), ("location", "location"), ("organizer", "person")):
        if fields[key]:
            add_fact(key, kind, fields[key])
    for index, (kind, value) in enumerate(literal_tokens(displayed_text), 1):
        add_fact(f"{kind}-{index}", kind, value)
    for index, fact in enumerate(source_facts, 1):
        add_fact(f"input-{index}", fact.kind, fact.value)
    for index, sentence in enumerate(guarded_sentences, 1):
        if len(sentence) > 500:
            raise ApiError(422, "VALIDATION_ERROR", "待人工核对的事实句过长，请拆分或精简后再分析")
        add_fact(f"sentence-{index}", uncertain_kind(sentence), sentence)
    for index, fact in enumerate(result["facts"], 1):
        value = fact["value"].strip()
        if not value or value not in source_text or value not in displayed_text:
            raise ValueError("事实必须可定位到原文或字段，并与当前展示内容一致")
        add_fact(f"source-{index}", fact["kind"], value)
    if selected_template is not None:
        require_capacity(selected_template, fields)
        missing = list(missing_required_fields(selected_template, result["intentType"], fields))
        questions = [
            {"field": key, "question": selected_template["fields"][key]["question"]}
            for key in missing
        ]
    else:
        missing, questions = [], []
    warnings = list(result["warnings"])
    if guarded_sentences:
        warnings.append(
            "部分人名或地点无法由规则可靠核验，相关原句已原样保留并列入事实清单，请人工核对。"
        )
    if has_conflict:
        warnings.append(
            "原文中的时间或地点与显式修改的字段存在差异，已优先保留修改值；请检查正文中的旧信息。"
        )
    if len(facts) > 50:
        raise ApiError(422, "VALIDATION_ERROR", "关键事实超过50项，请分成多个作品后再分析")
    result.update(
        fields=fields,
        facts=facts,
        missingFields=missing,
        questions=questions,
        warnings=list(dict.fromkeys(warnings))[:20],
    )
    return Analysis.model_validate(result, strict=True).model_dump(mode="json", by_alias=True)
