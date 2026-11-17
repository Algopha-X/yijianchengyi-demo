"""Real Chromium PNG output, text fitting and API-loop integration regressions."""

import asyncio
import copy
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from app.config import PROJECT_ROOT
from app.errors import ApiError
from app.rendering.renderer import render_template

FIELDS = {
    "title": "秋日读书会",
    "subtitle": "把一本好书，读成一段相遇",
    "body": "带上一本喜欢的书，分享你的阅读故事。\n在文字之间，遇见新的朋友。",
    "time": "2026年11月20日 14:00",
    "location": "图书馆一楼交流区",
    "organizer": "校园阅读社",
}


@pytest.mark.parametrize(
    "name,size",
    [
        ("poster-fresh-v1", (1080, 1440)),
        ("poster-modern-v1", (1080, 1440)),
        ("article-image-fresh-v1", (1200, 675)),
        ("article-image-modern-v1", (1200, 675)),
    ],
)
def test_real_chromium_renders_native_sized_chinese_png(name, size, tmp_path):
    template = json.loads((PROJECT_ROOT / "templates" / f"{name}.json").read_text("utf-8"))
    output = asyncio.run(render_template(template, FIELDS))
    image = Image.open(io.BytesIO(output))
    assert image.format == "PNG"
    assert image.size == size
    # Text and decorations must actually change pixels, not produce a blank success image.
    assert len(image.convert("RGB").getcolors(size[0] * size[1])) > 100
    (tmp_path / f"{name}.png").write_bytes(output)


def test_real_measuring_rejects_text_that_cannot_fit_at_minimum():
    template = json.loads((PROJECT_ROOT / "templates/poster-fresh-v1.json").read_text("utf-8"))
    template = copy.deepcopy(template)
    template["fields"]["title"]["box"] = {"x": 100, "y": 90, "width": 50, "height": 50}
    with pytest.raises(ApiError) as error:
        asyncio.run(render_template(template, FIELDS))
    assert error.value.code == "TEXT_OVERFLOW"
    assert error.value.details["fields"][0]["field"] == "title"


def test_render_with_real_local_image_and_literal_script_text(tmp_path):
    template = json.loads((PROJECT_ROOT / "templates/poster-fresh-v1.json").read_text("utf-8"))
    source = tmp_path / "image.png"
    Image.new("RGBA", (800, 200), (226, 169, 90, 180)).save(source)
    fields = {**FIELDS, "body": "<script>alert('仅显示文本')</script>"}
    image = Image.open(io.BytesIO(asyncio.run(render_template(template, fields, source))))
    assert image.size == (1080, 1440)
    assert image.convert("RGB").getpixel((400, 800)) != (243, 247, 239)


def test_accidental_remote_resource_is_blocked_before_network_request():
    from app.rendering.html_builder import build_html
    from app.rendering.renderer import _render

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        template = json.loads((PROJECT_ROOT / "templates/poster-fresh-v1.json").read_text("utf-8"))
        # Simulate an application markup bug: CSP and browser routing must still stop I/O.
        document = build_html(template, FIELDS).replace(
            "</body>", f'<img src="http://127.0.0.1:{server.server_port}/must-not-load"></body>'
        )
        with pytest.raises(ApiError):
            asyncio.run(_render(template, document))
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
