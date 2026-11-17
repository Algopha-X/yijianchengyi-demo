"""Rendering accepts data, never executable markup or a second template language."""

import copy
import json

import pytest

from app.config import PROJECT_ROOT
from app.errors import ApiError
from app.rendering.html_builder import build_html


def template():
    return json.loads((PROJECT_ROOT / "templates/poster-fresh-v1.json").read_text("utf-8"))


def test_markup_is_escaped_and_all_resources_are_embedded():
    document = build_html(
        template(),
        {
            "title": "安全排版",
            "body": '<script>alert(1)</script><img src="https://example.invalid/x">',
        },
    )
    assert "<script>" not in document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document
    assert "&lt;img src=&quot;https://example.invalid/x&quot;&gt;" in document
    assert "Content-Security-Policy" in document
    assert "data:font/ttf;base64," in document
    assert 'class="image"' not in document
    assert "top:420px;width:880px;height:650px" in document


def test_excess_codepoints_raise_public_field_error_without_truncation():
    with pytest.raises(ApiError) as error:
        build_html(template(), {"title": "中" * 25, "body": "正文"})
    assert error.value.code == "TEXT_OVERFLOW"
    assert error.value.details["fields"][0]["actualChars"] == 25


def test_unknown_fields_and_unsafe_template_css_are_rejected():
    with pytest.raises(ApiError):
        build_html(template(), {"title": "标题", "body": "正文", "html": "<h1>hello</h1>"})
    unsafe = copy.deepcopy(template())
    unsafe["fields"]["body"]["color"] = 'url("https://example.invalid")'
    with pytest.raises(ValueError):
        build_html(unsafe, {"title": "标题", "body": "正文"})
