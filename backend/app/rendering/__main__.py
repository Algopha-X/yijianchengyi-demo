"""Generate real thumbnails for the local catalog: python -m app.rendering."""

import argparse
import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.config import PROJECT_ROOT
from app.rendering.renderer import render_template
from app.services.templates import load_templates


async def create_previews(
    directory: Path = PROJECT_ROOT / "data/template-previews", *, missing_only: bool = False
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fields = {
        "title": "秋日读书会",
        "subtitle": "把一本好书，读成一段相遇",
        "body": "带上一本喜欢的书，分享你的阅读故事。\n在文字之间，遇见新的朋友。",
        "time": "2026年11月20日 14:00",
        "location": "图书馆一楼交流区",
        "organizer": "校园阅读社",
    }
    for template in load_templates():
        if missing_only and any(
            (directory / f"{template['id']}{suffix}").is_file()
            for suffix in (".png", ".jpg", ".jpeg")
        ):
            print(f"{template['id']}: 已有缓存，保留")
            continue
        output = await render_template(template, fields)
        path = directory / f"{template['id']}.png"
        # A stopped first run must not leave a partial file that --missing skips.
        temporary = None
        try:
            with NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(output)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(
            f"{template['id']}: {template['canvas']['width']}x{template['canvas']['height']}, {len(output)} bytes"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="由服务端 Chromium 生成真实模板缩略图")
    parser.add_argument("--missing", action="store_true", help="只生成缺失缓存，保留已有 PNG/JPG")
    parser.add_argument("--directory", type=Path, default=PROJECT_ROOT / "data/template-previews",
                        help="模板预览缓存目录")
    arguments = parser.parse_args()
    asyncio.run(create_previews(arguments.directory, missing_only=arguments.missing))
