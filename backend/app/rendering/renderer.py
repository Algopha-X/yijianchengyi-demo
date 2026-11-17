"""Measure and screenshot trusted template documents with isolated Chromium."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from app.errors import ApiError
from app.rendering.html_builder import build_html
from app.rendering.images import fit_image
from app.services.templates import TemplateValidationError, validate_template_payload

# This script is maintained application code, never output from a model or user input.
_FIT_TEXT = """() => {
  const errors = [];
  for (const field of document.querySelectorAll('.field')) {
    const text = field.querySelector('.text');
    const minimum = Number(field.dataset.minFont);
    const maximumLines = Number(field.dataset.maxLines);
    const original = Number.parseFloat(getComputedStyle(field).fontSize);
    let fits = false;
    for (let size = original; size >= minimum; size -= 1) {
      field.style.fontSize = size + 'px';
      const lineHeight = Number.parseFloat(getComputedStyle(field).lineHeight);
      const lines = Math.max(1, Math.round(text.getBoundingClientRect().height / lineHeight));
      fits = text.scrollWidth <= field.clientWidth + 1
          && text.getBoundingClientRect().height <= field.clientHeight + 1
          && lines <= maximumLines;
      if (fits) break;
    }
    if (!fits) errors.push({field:field.dataset.field,
      message:'在最小字号下仍超出文字区域或行数上限',minFontSize:minimum,maxLines:maximumLines});
  }
  return errors;
}"""


async def _render(template: dict[str, Any], document: str) -> bytes:
    canvas = template["canvas"]
    # Optional explicit local-demo configuration; never a silent placeholder image fallback.
    channel = os.getenv("YCY_BROWSER_CHANNEL", "").strip()
    if channel and channel not in {"chrome", "msedge"}:
        raise ApiError(503, "RENDER_FAILED", "YCY_BROWSER_CHANNEL 只能指定 chrome 或 msedge")
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, channel=channel or None)
            try:
                context = await browser.new_context(
                    viewport={"width": canvas["width"], "height": canvas["height"]},
                    device_scale_factor=1,
                    service_workers="block",
                    accept_downloads=False,
                    java_script_enabled=False,
                    locale="zh-CN",
                )
                # Data URLs contain all resources. Even a future accidental external URL is denied.
                await context.route("**/*", lambda route: route.abort())
                page = await context.new_page()
                await page.set_content(document, wait_until="load", timeout=30_000)
                await page.evaluate(
                    "async () => { await document.fonts.ready; "
                    "await Promise.all([...document.images].map(i => i.decode())); }"
                )
                loaded = await page.evaluate("document.fonts.check('16px \"Noto Sans SC\"')")
                if not loaded:
                    raise ApiError(503, "RENDER_FAILED", "中文字体加载失败，请重试")
                errors = await page.evaluate(_FIT_TEXT)
                if errors:
                    raise ApiError(
                        422,
                        "TEXT_OVERFLOW",
                        "文字无法在模板内完整排版，请精简内容或更换模板",
                        {"fields": errors},
                    )
                return await page.screenshot(type="png", animations="disabled", timeout=30_000)
            finally:
                await browser.close()
    except ApiError:
        raise
    except PlaywrightError as exc:
        raise ApiError(
            503, "RENDER_FAILED", "图片渲染失败，请检查 Chromium 是否已安装后重试"
        ) from exc


def _render_in_thread(
    template: dict[str, Any], fields: dict[str, str], image_path: Path | None
) -> bytes:
    # Windows ASGI/AnyIO may use a Selector loop, which cannot start browser subprocesses.
    # A private Proactor loop in a worker thread keeps browser execution independent.
    try:
        template = validate_template_payload(template)
    except TemplateValidationError as exc:
        raise ApiError(422, "VALIDATION_ERROR", "模板数据无效，请选择已发布的模板") from exc
    image = None
    if image_path is not None:
        box = template["imageSlot"]["box"]
        image = fit_image(
            Path(image_path),
            box["width"],
            box["height"],
            template["imageSlot"]["fit"],
            template["canvas"]["background"],
        )
    document = build_html(template, fields, image)
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
            return runner.run(_render(template, document))
    return asyncio.run(_render(template, document))


async def render_template(
    template: dict[str, Any], fields: dict[str, str], image_path: Path | None = None
) -> bytes:
    """Return a real PNG at the template's native size, or a public ApiError."""
    return await asyncio.to_thread(_render_in_thread, template, fields, image_path)
