"""Load, validate and query the immutable template catalog."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic.alias_generators import to_camel

from app.config import PROJECT_ROOT
from app.models.templates import Scenario, Style, TemplateList, TemplateSummary

FIELD_KEYS = ("title", "subtitle", "body", "time", "location", "organizer")
SCENARIO_ORDER = {"poster": 0, "article_image": 1}
STYLE_ORDER = {
    "fresh": 0,
    "asymmetric": 1,
    "lively": 2,
    "elegant": 3,
    "modern": 4,
    "retro": 5,
}
CANVAS_SIZES = {"poster": (1080, 1440), "article_image": (1200, 675)}
THUMBNAIL_SUFFIXES = ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"))

IntentType = Literal["event", "announcement", "record"]
FieldKey = Literal["title", "subtitle", "body", "time", "location", "organizer"]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class _TemplateModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        hide_input_in_errors=True,
        populate_by_name=True,
        strict=True,
    )


class BoxModel(_TemplateModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class CanvasModel(_TemplateModel):
    width: int = Field(ge=320, le=4000)
    height: int = Field(ge=320, le=4000)
    background: Color


class TextFieldModel(_TemplateModel):
    label: str = Field(min_length=1)
    question: str = Field(min_length=1)
    required: bool
    required_for: Annotated[list[IntentType], Field(max_length=3)]
    max_chars: int = Field(ge=1, le=5000)
    max_lines: int = Field(ge=1, le=50)
    font_family: Literal["Noto Sans SC"]
    font_weight: Literal[400, 700]
    font_size: int = Field(ge=12, le=200)
    min_font_size: int = Field(ge=12, le=200)
    line_height: float = Field(ge=1, le=2.5)
    color: Color
    align: Literal["left", "center", "right"]
    box: BoxModel

    @field_validator("required_for")
    @classmethod
    def required_for_unique(cls, value: list[IntentType]) -> list[IntentType]:
        _require(len(value) == len(set(value)), "requiredFor 数组项必须唯一")
        return value


class FieldsModel(_TemplateModel):
    title: TextFieldModel
    subtitle: TextFieldModel
    body: TextFieldModel
    time: TextFieldModel
    location: TextFieldModel
    organizer: TextFieldModel


class ImageSlotModel(_TemplateModel):
    required: Literal[False]
    fit: Literal["cover", "contain"]
    box: BoxModel


class WithoutImageModel(_TemplateModel):
    title: BoxModel | None = None
    subtitle: BoxModel | None = None
    body: BoxModel
    time: BoxModel | None = None
    location: BoxModel | None = None
    organizer: BoxModel | None = None


class DecorationModel(_TemplateModel):
    shape: Literal["rectangle", "ellipse"]
    box: BoxModel
    fill: Color
    opacity: float = Field(ge=0, le=1)
    radius: int | None = Field(default=None, ge=0, le=300)


class TemplateDocumentModel(_TemplateModel):
    schema_version: Literal["1.0"]
    id: Annotated[
        str,
        Field(
            pattern=(
                r"^(poster|article-image)-(fresh|asymmetric|lively|elegant|modern|retro)"
                r"-v[1-9][0-9]*$"
            )
        ),
    ]
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=60)
    scenario: Scenario
    style: Style
    intent_types: Annotated[list[IntentType], Field(min_length=1, max_length=3)]
    canvas: CanvasModel
    fields: FieldsModel
    image_slot: ImageSlotModel
    without_image: WithoutImageModel
    decorations: Annotated[list[DecorationModel], Field(max_length=30)]

    @field_validator("intent_types")
    @classmethod
    def intent_types_unique(cls, value: list[IntentType]) -> list[IntentType]:
        _require(len(value) == len(set(value)), "intentTypes 数组项必须唯一")
        return value


class TemplateValidationError(ValueError):
    """A template is invalid or the catalog cannot be used safely."""


class TemplateNotFoundError(LookupError):
    """No valid template has the requested ID."""


class ThumbnailNotFoundError(FileNotFoundError):
    """The template exists, but its pre-generated thumbnail is absent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TemplateValidationError(message)


def _path_label(path: Iterable[object]) -> str:
    parts = list(path)
    return "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in parts
    )


def _validation_message(error: dict[str, Any]) -> str:
    messages = {
        "extra_forbidden": "包含未知字段",
        "greater_than_equal": "数值低于下限",
        "less_than_equal": "数值超过上限",
        "literal_error": "值不在允许范围内",
        "missing": "缺少必填字段",
        "string_pattern_mismatch": "格式不符合约定",
        "too_long": "内容过长",
        "too_short": "内容过短",
        "type": "数据类型不符合约定",
    }
    detail = messages.get(error["type"], error["msg"].removeprefix("Value error, "))
    return f"模板字段 {_path_label(error['loc'])} {detail}"


def _check_box(box: Mapping[str, Any], canvas: Mapping[str, Any], label: str) -> None:
    _require(
        all(type(box.get(key)) is int for key in ("x", "y", "width", "height")),
        f"{label} 必须使用整数坐标",
    )
    _require(box["width"] > 0 and box["height"] > 0, f"{label} 的宽高必须为正数")
    _require(
        box["x"] >= 0
        and box["y"] >= 0
        and box["x"] + box["width"] <= canvas["width"]
        and box["y"] + box["height"] <= canvas["height"],
        f"{label} 超出画布边界",
    )


def _check_template_semantics(template: dict[str, Any], source: Path | None) -> None:
    if source is not None:
        _require(template["id"] == source.stem, "模板文件名必须与模板 ID 一致")

    scenario = template["scenario"]
    style = template["style"]
    version = template["version"]
    prefix = "poster" if scenario == "poster" else "article-image"
    _require(
        template["id"] == f"{prefix}-{style}-v{version}",
        "模板 ID、类型、风格和版本必须一致",
    )
    expected_size = CANVAS_SIZES[scenario]
    actual_size = (template["canvas"]["width"], template["canvas"]["height"])
    _require(actual_size == expected_size, f"{scenario} 画布尺寸必须为 {expected_size}")
    _require(set(template["fields"]) == set(FIELD_KEYS), "模板必须包含全部六个公共字段")
    _require(
        template["fields"]["title"]["required"] and template["fields"]["body"]["required"],
        "标题和正文必须始终必填",
    )

    for key, field in template["fields"].items():
        _require(field["question"].strip() != "", f"{key}.question 不能为空")
        _require(
            field["minFontSize"] <= field["fontSize"],
            f"{key}.minFontSize 不能大于 fontSize",
        )
        _check_box(field["box"], template["canvas"], f"fields.{key}.box")

    _check_box(template["imageSlot"]["box"], template["canvas"], "imageSlot.box")
    for key, box in template["withoutImage"].items():
        _require(key in FIELD_KEYS, f"withoutImage 包含未知字段：{key}")
        _check_box(box, template["canvas"], f"withoutImage.{key}")
    for index, decoration in enumerate(template["decorations"]):
        _check_box(decoration["box"], template["canvas"], f"decorations[{index}].box")


def validate_template_payload(
    payload: object,
    source: Path | None = None,
) -> dict[str, Any]:
    """Validate one template against its strict document model and semantic rules."""

    try:
        document = TemplateDocumentModel.model_validate(payload)
    except ValidationError as exc:
        error = exc.errors(include_input=False, include_url=False)[0]
        raise TemplateValidationError(_validation_message(error)) from None
    template = document.model_dump(mode="json", by_alias=True, exclude_none=True)
    _check_template_semantics(template, source)
    return template


def _read_template(path: Path) -> dict[str, Any]:
    _require(path.suffix == ".json", f"模板文件必须使用 JSON：{path.name}")
    _require(path.is_file(), f"模板文件不存在：{path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateValidationError(f"模板 JSON 无法解析：{path.name}") from exc
    return validate_template_payload(payload, source=path)


def load_templates(
    directory: Path = PROJECT_ROOT / "templates",
) -> list[dict[str, Any]]:
    """Load and validate every published template in a deterministic order."""

    _require(directory.is_dir(), "模板目录不存在")
    paths = sorted(directory.glob("*.json"))
    _require(bool(paths), "模板目录中没有任何模板")
    templates = [_read_template(path) for path in paths]
    ids = [template["id"] for template in templates]
    _require(len(ids) == len(set(ids)), "模板 ID 不能重复")
    return sorted(templates, key=lambda template: template["id"])


def latest_templates(templates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the highest valid version for every scenario/style pair."""

    winners: dict[tuple[str, str], dict[str, Any]] = {}
    versions: dict[tuple[str, str], int] = {}
    for template in templates:
        key = (template["scenario"], template["style"])
        version = template["version"]
        previous = versions.get(key)
        _require(previous != version, f"{key[0]}/{key[1]} 存在重复版本 {version}")
        if previous is None or version > previous:
            winners[key] = template
            versions[key] = version
    return sorted(
        winners.values(),
        key=lambda template: (
            SCENARIO_ORDER[template["scenario"]],
            STYLE_ORDER[template["style"]],
            template["id"],
        ),
    )


def field_is_required(
    template: Mapping[str, Any],
    field_key: str,
    intent_type: Literal["event", "announcement", "record"],
) -> bool:
    """Apply required=true and requiredFor to one shared metadata field."""

    _require(intent_type in {"event", "announcement", "record"}, "未知 intentType")
    _require(field_key in FIELD_KEYS, f"未知字段：{field_key}")
    field = template["fields"][field_key]
    return bool(field["required"] or intent_type in field["requiredFor"])


def required_field_keys(
    template: Mapping[str, Any],
    intent_type: Literal["event", "announcement", "record"],
) -> tuple[str, ...]:
    return tuple(
        field_key
        for field_key in FIELD_KEYS
        if field_is_required(template, field_key, intent_type)
    )


def missing_required_fields(
    template: Mapping[str, Any],
    intent_type: Literal["event", "announcement", "record"],
    fields: Mapping[str, str],
) -> tuple[str, ...]:
    """Return required keys whose trimmed value is empty."""

    return tuple(
        field_key
        for field_key in required_field_keys(template, intent_type)
        if not fields.get(field_key, "").strip()
    )


def field_capacity_errors(
    template: Mapping[str, Any],
    fields: Mapping[str, str],
) -> tuple[dict[str, object], ...]:
    """Check maxChars using trimmed Unicode code-point count for every field."""

    errors: list[dict[str, object]] = []
    for field_key in FIELD_KEYS:
        value = fields.get(field_key, "")
        _require(isinstance(value, str), f"{field_key} 必须是文本")
        length = len(value.strip())
        maximum = template["fields"][field_key]["maxChars"]
        if length > maximum:
            errors.append(
                {
                    "field": field_key,
                    "actualChars": length,
                    "maxChars": maximum,
                }
            )
    return tuple(errors)


class TemplateCatalog:
    """Immutable view of validated templates and their runtime thumbnails."""

    def __init__(
        self,
        template_dir: Path = PROJECT_ROOT / "templates",
        thumbnail_dir: Path = PROJECT_ROOT / "data/template-previews",
    ) -> None:
        self.template_dir = template_dir
        self.thumbnail_dir = thumbnail_dir
        loaded = load_templates(template_dir)
        self._templates = {template["id"]: template for template in loaded}
        self._latest = latest_templates(loaded)

    def list_templates(
        self,
        scenario: Scenario | None = None,
        style: Style | None = None,
    ) -> TemplateList:
        selected = [
            template
            for template in self._latest
            if (scenario is None or template["scenario"] == scenario)
            and (style is None or template["style"] == style)
        ]
        _require(len(selected) <= 12, "首版模板列表最多包含 12 个最新模板")
        return TemplateList(
            items=[
                TemplateSummary(
                    id=template["id"],
                    version=template["version"],
                    name=template["name"],
                    scenario=template["scenario"],
                    style=template["style"],
                    thumbnail_url=f"/api/templates/{template['id']}/thumbnail",
                )
                for template in selected
            ]
        )

    def get_template(self, template_id: str) -> dict[str, Any]:
        template = self._templates.get(template_id)
        if template is None:
            raise TemplateNotFoundError(template_id)
        return template

    def get_thumbnail(self, template_id: str) -> tuple[Path, str]:
        self.get_template(template_id)
        thumbnail_root = self.thumbnail_dir.resolve()
        for suffix, media_type in THUMBNAIL_SUFFIXES:
            candidate = (thumbnail_root / f"{template_id}{suffix}").resolve()
            _require(candidate.is_relative_to(thumbnail_root), "模板缩略图路径不安全")
            if candidate.is_file():
                return candidate, media_type
        raise ThumbnailNotFoundError(template_id)


@lru_cache(maxsize=1)
def get_template_catalog() -> TemplateCatalog:
    return TemplateCatalog()
