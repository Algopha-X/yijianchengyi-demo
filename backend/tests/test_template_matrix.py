"""All twelve layouts with short/capacity text and with/without a local image."""

import asyncio
import io
import json

import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from app.config import PROJECT_ROOT
from app.rendering.renderer import render_template
from app.services.templates import latest_templates, load_templates

TEMPLATES = latest_templates(load_templates())
SHORT = {
    "title": "秋日读书会",
    "subtitle": "把一本好书，读成一段相遇",
    "body": "带上一本喜欢的书，分享你的阅读故事。\n在文字之间，遇见新的朋友。",
    "time": "2026年11月20日 14:00",
    "location": "图书馆一楼交流区",
    "organizer": "校园阅读社",
}


def test_twelve_templates_have_six_styles_per_scenario_and_distinct_layouts():
    schema = json.loads((PROJECT_ROOT / "contracts/template.schema.json").read_text("utf-8"))
    validator = Draft202012Validator(schema)
    assert len(TEMPLATES) == 12
    for scenario in ("poster", "article_image"):
        group = [template for template in TEMPLATES if template["scenario"] == scenario]
        assert {template["style"] for template in group} == {
            "fresh", "asymmetric", "lively", "elegant", "modern", "retro"
        }
        layouts = set()
        for template in group:
            validator.validate(template)
            layouts.add(json.dumps({
                "text": {key: value["box"] for key, value in template["fields"].items()},
                "image": template["imageSlot"]["box"],
                "withoutImage": template["withoutImage"],
            }, sort_keys=True))
        assert len(layouts) == 6, "不同风格必须具有实际布局区别，不能仅换色"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda item: item["id"])
@pytest.mark.parametrize("boundary", [False, True], ids=["short", "boundary"])
@pytest.mark.parametrize("with_image", [False, True], ids=["no-image", "image"])
def test_real_render_matrix_is_complete_or_reports_known_overflow(template, boundary, with_image, tmp_path):
    fields = {key: "艺" * rule["maxChars"] for key, rule in template["fields"].items()} if boundary else SHORT
    source = None
    if with_image:
        source = tmp_path / "local-image.png"
        Image.new("RGB", (900, 300), "#CE985A").save(source)
    output = asyncio.run(render_template(template, fields, source))
    with Image.open(io.BytesIO(output)) as image:
        image.load()
        assert image.format == "PNG"
        assert image.size == (template["canvas"]["width"], template["canvas"]["height"])
        assert len(image.convert("RGB").getcolors(image.width * image.height)) > 100
    (tmp_path / f"{template['id']}.png").write_bytes(output)
