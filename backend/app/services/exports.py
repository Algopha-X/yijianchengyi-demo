"""Encode immutable rendered previews without re-running analysis or layout."""

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from app.errors import ApiError

ExportFormat = Literal["png", "jpg", "pdf"]


@dataclass(frozen=True)
class ExportedPreview:
    content: bytes
    media_type: str
    width: int
    height: int


def export_preview(
    path: Path,
    format: ExportFormat,
    *,
    scale: Literal[1, 2] = 1,
    background: str = "#FFFFFF",
) -> ExportedPreview:
    """Use native pixels by default; scale=2 is ordinary raster interpolation.

    PDF is a one-page raster document, not editable text or vector artwork.
    At 72 dpi its page size in PDF points equals its exported pixel dimensions.
    The caller must first authorize the preview and check its task revision.
    """
    if format not in {"png", "jpg", "pdf"} or type(scale) is not int or scale not in {1, 2}:
        raise ApiError(422, "VALIDATION_ERROR", "请选择 PNG、JPG 或 PDF，缩放倍数仅支持 1 或 2")
    try:
        original = path.read_bytes()
        with Image.open(io.BytesIO(original)) as source:
            source.load()
            if source.format != "PNG":
                raise OSError("Preview must be a server-rendered PNG")
            size = (source.width * scale, source.height * scale)
            if format == "png" and scale == 1:
                # Preserve the exact screenshot for the default PNG export.
                return ExportedPreview(original, "image/png", *size)
            image = source.convert("RGBA")
            if scale == 2:
                image = image.resize(size, Image.Resampling.LANCZOS)
            output = io.BytesIO()
            if format == "png":
                image.save(output, "PNG")
                media_type = "image/png"
            else:
                flattened = Image.new("RGB", size, background)
                flattened.paste(image, mask=image.getchannel("A"))
                if format == "jpg":
                    flattened.save(output, "JPEG", quality=92)
                    media_type = "image/jpeg"
                else:
                    flattened.save(output, "PDF", resolution=72.0, quality=95)
                    media_type = "application/pdf"
            return ExportedPreview(output.getvalue(), media_type, *size)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ApiError(
            503, "RENDER_FAILED", "导出暂时失败，请重试；已保存的预览和草稿不受影响"
        ) from None
