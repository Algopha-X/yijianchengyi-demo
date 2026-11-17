"""Generate a fixed document from validated template data and escaped text."""

import base64
import html
from typing import Any

from app.errors import ApiError
from app.rendering.fonts import embedded_font_css
from app.services.templates import FIELD_KEYS, field_capacity_errors, validate_template_payload


def _box_css(box: dict[str, int]) -> str:
    return f"left:{box['x']}px;top:{box['y']}px;width:{box['width']}px;height:{box['height']}px;"


def build_html(
    template: dict[str, Any], fields: dict[str, str], image_png: bytes | None = None
) -> str:
    """Only server-generated tags/CSS are emitted; users supply plain field values."""
    template = validate_template_payload(template)
    if set(fields) - set(FIELD_KEYS) or any(not isinstance(v, str) for v in fields.values()):
        raise ApiError(422, "VALIDATION_ERROR", "排版内容只能包含六个纯文本字段")
    errors = field_capacity_errors(template, fields)
    if errors:
        raise ApiError(
            422, "TEXT_OVERFLOW", "文字超过模板容量，请精简内容或更换模板", {"fields": list(errors)}
        )
    canvas = template["canvas"]
    style = (
        embedded_font_css()
        + "*{box-sizing:border-box}html,body{margin:0;padding:0;}"
        + f"body{{width:{canvas['width']}px;height:{canvas['height']}px;"
        + f"background:{canvas['background']};}}"
        + ".decoration,.field,.image{position:absolute;}"
        + '.field{font-family:"Noto Sans SC";font-synthesis:none;}'
        + ".text{display:block;white-space:pre-wrap;overflow-wrap:anywhere;word-break:normal;}"
        + ".image{display:block;}"
    )
    elements = []
    for decoration in template["decorations"]:
        radius = "50%" if decoration["shape"] == "ellipse" else f"{decoration.get('radius', 0)}px"
        css = (
            _box_css(decoration["box"]) + f"background:{decoration['fill']};"
            f"opacity:{decoration['opacity']};border-radius:{radius};"
        )
        elements.append(f'<div class="decoration" aria-hidden="true" style="{css}"></div>')
    if image_png is not None:
        encoded = base64.b64encode(image_png).decode("ascii")
        elements.append(
            f'<img class="image" alt="" style="{_box_css(template["imageSlot"]["box"])}" '
            f'src="data:image/png;base64,{encoded}">'
        )
    for key in FIELD_KEYS:
        text = fields.get(key, "").strip()
        if not text:
            continue
        field = template["fields"][key]
        box = field["box"] if image_png else template["withoutImage"].get(key, field["box"])
        css = (
            _box_css(box) + f"color:{field['color']};font-size:{field['fontSize']}px;"
            f"font-weight:{field['fontWeight']};line-height:{field['lineHeight']};"
            f"text-align:{field['align']};"
        )
        elements.append(
            f'<div class="field" data-field="{key}" data-min-font="{field["minFontSize"]}" '
            f'data-max-lines="{field["maxLines"]}" style="{css}">'
            f'<span class="text">{html.escape(text, quote=True)}</span></div>'
        )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        'img-src data:; font-src data:; style-src &#39;unsafe-inline&#39;;">'
        f"<style>{style}</style></head><body>{''.join(elements)}</body></html>"
    )
