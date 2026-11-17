"""Decode local uploads and adapt a copy to the template's image rectangle."""

import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.errors import ApiError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000


def fit_image(image_path: Path, width: int, height: int, fit: str, background: str) -> bytes:
    """Return an RGB PNG, preserving aspect ratio and the untouched source file."""
    try:
        if image_path.stat().st_size > MAX_IMAGE_BYTES:
            raise ApiError(413, "FILE_TOO_LARGE", "单张图片不能超过 10 MiB")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(image_path) as original:
                if original.format not in {"JPEG", "PNG"}:
                    raise ApiError(415, "UNSUPPORTED_IMAGE", "仅支持实际可解码的 JPG 或 PNG 图片")
                if original.width * original.height > MAX_IMAGE_PIXELS:
                    raise ApiError(415, "UNSUPPORTED_IMAGE", "图片解码后不能超过 4000 万像素")
                original.load()
                image = ImageOps.exif_transpose(original).convert("RGBA")
        target = Image.new("RGB", (width, height), background)
        if fit == "cover":
            adapted = ImageOps.fit(image, (width, height), Image.Resampling.LANCZOS)
            target.paste(adapted, (0, 0), adapted)
        elif fit == "contain":
            adapted = ImageOps.contain(image, (width, height), Image.Resampling.LANCZOS)
            target.paste(
                adapted,
                ((width - adapted.width) // 2, (height - adapted.height) // 2),
                adapted,
            )
        else:
            raise ApiError(422, "VALIDATION_ERROR", "模板图片适配规则无效")
        output = io.BytesIO()
        target.save(output, format="PNG")
        return output.getvalue()
    except ApiError:
        raise
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise ApiError(
            415, "UNSUPPORTED_IMAGE", "图片损坏或无法安全解码，请重新上传 JPG/PNG"
        ) from exc
