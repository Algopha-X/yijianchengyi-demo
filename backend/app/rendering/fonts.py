"""Embed the bundled, licensed Noto Sans SC font in every render document."""

import base64
from functools import lru_cache

from app.config import PROJECT_ROOT
from app.errors import ApiError


@lru_cache(maxsize=1)
def embedded_font_css() -> str:
    font = PROJECT_ROOT / "assets/fonts/NotoSansSC-VF.ttf"
    if not font.is_file():
        raise ApiError(503, "RENDER_FAILED", "缺少随项目分发的中文字体，请按运行说明安装")
    encoded = base64.b64encode(font.read_bytes()).decode("ascii")
    return (
        '@font-face{font-family:"Noto Sans SC";font-style:normal;'
        f'font-weight:100 900;src:url("data:font/ttf;base64,{encoded}") format("truetype");'
        "font-display:block;}"
    )
