"""Real browser checks against a running local server; no API response mocks on main flows."""

import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(os.getenv("DEMO_ARTIFACTS_DIR", str(ROOT / "data/browser-validation")))
BASE_URL = os.getenv("DEMO_BASE_URL", "http://127.0.0.1:8000")


@pytest.fixture
def browser():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(channel=os.getenv("YCY_BROWSER_CHANNEL") or None, headless=True)
        yield instance
        instance.close()


@pytest.fixture
def context(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN", accept_downloads=True)
    yield context
    context.close()


def begin(page, *, complete=True):
    page.goto(BASE_URL + "/create", wait_until="networkidle")
    page.get_by_role("button", name="完整活动文案" if complete else "缺失信息文案").click()
    page.get_by_role("button", name="清新校园活动海报", exact=False).click()
    page.get_by_role("button", name="处理内容并检查信息", exact=False).click()
    expect(page.locator("#field-title")).to_have_value("秋日读书会", timeout=30_000)


def confirm_render(page):
    for checkbox in page.locator(".fact-item input").all():
        checkbox.check()
    page.get_by_role("button", name="确认并生成作品", exact=False).click()
    expect(page.get_by_role("button", name="下载 PNG", exact=True)).to_be_enabled(timeout=60_000)
    expect(page.locator("img.generated-preview")).to_be_visible()


def test_complete_browser_download_upload_and_refresh(context):
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE_URL, wait_until="networkidle")
    expect(page.get_by_text("Mock 演示模式", exact=True)).to_be_visible()
    page.screenshot(path=str(ARTIFACTS / "01-home.png"), full_page=True)
    page.goto(BASE_URL + "/create", wait_until="networkidle")
    page.get_by_role("button", name="完整活动文案").click()
    page.get_by_role("button", name="清新校园活动海报", exact=False).click()
    picture = io.BytesIO()
    Image.new("RGB", (900, 500), "#6a9a78").save(picture, "PNG")
    page.locator("#image-upload").set_input_files({"name": "demo-original.png", "mimeType": "image/png", "buffer": picture.getvalue()})
    expect(page.get_by_text("图片已上传", exact=True)).to_be_visible()
    page.get_by_role("button", name="处理内容并检查信息", exact=False).click()
    expect(page.locator("#field-time")).to_have_value("2026年11月20日14:00", timeout=30_000)
    page.screenshot(path=str(ARTIFACTS / "02-confirm-facts.png"), full_page=True)
    confirm_render(page)
    task_url = page.url
    page.screenshot(path=str(ARTIFACTS / "03-generated.png"), full_page=True)
    for format in ["PNG", "JPG"]:
        with page.expect_download() as event:
            page.get_by_role("button", name="下载 " + format, exact=True).click()
        path = ARTIFACTS / ("browser-export." + format.lower())
        event.value.save_as(str(path))
        with Image.open(path) as image:
            image.load()
            assert image.size == (1080, 1440)
            assert image.format == ("PNG" if format == "PNG" else "JPEG")
    page.reload(wait_until="networkidle")
    expect(page.get_by_role("button", name="下载 PNG", exact=True)).to_be_enabled(timeout=15_000)
    assert page.url == task_url
    page.get_by_role("button", name="返回修改内容", exact=False).click()
    page.locator("#field-title").fill("更新后的读书会")
    expect(page.locator("img.generated-preview")).to_have_count(0)
    expect(page.get_by_text("已保存到本机服务", exact=False)).to_be_visible(timeout=10_000)
    page.goto(BASE_URL + "/history", wait_until="networkidle")
    expect(page.get_by_role("heading", name="更新后的读书会", exact=True)).to_be_visible()
    page.screenshot(path=str(ARTIFACTS / "04-history.png"), full_page=True)
    assert errors == []
    (ARTIFACTS / "complete-flow.json").write_text(json.dumps({"status": "passed", "taskUrl": task_url, "downloads": ["browser-export.png", "browser-export.jpg"], "browserErrors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_missing_information_and_autosave_survive_refresh(context):
    page = context.new_page()
    begin(page, complete=False)
    expect(page.get_by_text("还有 2 项信息需要补充", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="确认并生成作品", exact=False)).to_be_disabled()
    page.screenshot(path=str(ARTIFACTS / "05-missing-information.png"), full_page=True)
    page.locator("#field-time").fill("2026年11月20日14:00")
    page.locator("#field-location").fill("图书馆一楼交流区")
    expect(page.get_by_text("已保存到本机服务", exact=False)).to_be_visible(timeout=10_000)
    page.reload(wait_until="networkidle")
    # Changed drafts return to the input step because the old analysis has expired.
    page.get_by_role("button", name="处理内容并检查信息", exact=False).click()
    expect(page.locator("#field-time")).to_have_value("2026年11月20日14:00", timeout=15_000)
    expect(page.locator("#field-location")).to_have_value("图书馆一楼交流区")
    expect(page.get_by_text("必填信息已完整", exact=False)).to_be_visible()
    confirm_render(page)


def test_conflicting_tabs_preserve_local_text(context):
    first = context.new_page()
    begin(first)
    second = context.new_page()
    second.goto(first.url, wait_until="networkidle")
    expect(second.locator("#field-title")).to_have_value("秋日读书会")
    first.locator("#field-title").fill("第一页面保存的标题")
    expect(first.get_by_text("已保存到本机服务", exact=False)).to_be_visible(timeout=10_000)
    second.locator("#field-title").fill("第二页面保留的标题")
    expect(second.get_by_text("版本冲突，请处理", exact=False)).to_be_visible(timeout=10_000)
    expect(second.locator("#field-title")).to_have_value("第二页面保留的标题")
    expect(second.get_by_text("已保留本地修改", exact=True)).to_be_visible()
    second.screenshot(path=str(ARTIFACTS / "06-version-conflict.png"), full_page=True)


def test_mobile_and_connection_error_retry(browser):
    context = browser.new_context(viewport={"width": 400, "height": 900}, locale="zh-CN")
    page = context.new_page()
    page.goto(BASE_URL + "/create", wait_until="networkidle")
    expect(page.locator("#raw-text")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(ARTIFACTS / "07-mobile.png"), full_page=True)
    page.route("**/api/v2/tasks", lambda route: route.abort())
    page.goto(BASE_URL + "/history", wait_until="networkidle")
    expect(page.get_by_role("alert")).to_be_visible()
    page.unroute("**/api/v2/tasks")
    page.get_by_role("button", name="重新加载", exact=True).click()
    expect(page.get_by_role("alert")).to_have_count(0)
    context.close()


def test_article_image_type_selection_and_actual_export(context):
    page = context.new_page()
    page.goto(BASE_URL + "/create", wait_until="networkidle")
    page.get_by_role("button", name="完整活动文案").click()
    page.get_by_role("button", name="公众号配图", exact=False).click()
    page.get_by_role("button", name="清新读书会横版配图", exact=False).click()
    page.get_by_role("button", name="处理内容并检查信息", exact=False).click()
    expect(page.locator("#field-title")).to_have_value("秋日读书会", timeout=30_000)
    confirm_render(page)
    with page.expect_download() as event:
        page.get_by_role("button", name="下载 PNG", exact=True).click()
    path = ARTIFACTS / "browser-article.png"
    event.value.save_as(str(path))
    with Image.open(path) as image:
        assert image.size == (1200, 675)
    page.screenshot(path=str(ARTIFACTS / "08-article.png"), full_page=True)


def test_failed_preview_load_recovers_after_generating_a_new_version(context):
    page = context.new_page()
    begin(page)
    page.route("**/api/assets/*/file", lambda route: route.abort())
    for checkbox in page.locator(".fact-item input").all():
        checkbox.check()
    page.get_by_role("button", name="确认并生成作品", exact=False).click()
    expect(page.get_by_role("button", name="下载 PNG", exact=True)).to_be_enabled(timeout=60_000)
    expect(page.get_by_role("heading", name="预览图片加载失败", exact=True)).to_be_visible()
    page.unroute("**/api/assets/*/file")
    page.get_by_role("button", name="返回修改内容", exact=False).click()
    page.locator("#field-title").fill("预览恢复后的读书会")
    expect(page.get_by_text("已保存到本机服务", exact=False)).to_be_visible(timeout=10_000)
    page.get_by_role("button", name="重新分析并检查", exact=False).click()
    expect(page.get_by_role("button", name="重新分析并检查", exact=False)).to_be_enabled(timeout=30_000)
    confirm_render(page)
    expect(page.get_by_role("heading", name="预览图片加载失败", exact=True)).to_have_count(0)
    page.locator(".preview-image-button").click()
    expect(page.get_by_role("dialog", name="放大查看作品")).to_be_visible()
    page.screenshot(path=str(ARTIFACTS / "09-preview-recovery.png"), full_page=True)
