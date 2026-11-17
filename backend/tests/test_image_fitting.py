"""Image fitting preserves source bytes, aspect ratio and transparent backgrounds."""

import hashlib
import io

import pytest
from PIL import Image

from app.errors import ApiError
from app.rendering.images import fit_image


def test_contain_adds_letterboxing_without_stretch_or_source_mutation(tmp_path):
    source = tmp_path / "wide.png"
    Image.new("RGB", (400, 100), "#cc0000").save(source)
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    output = Image.open(io.BytesIO(fit_image(source, 200, 200, "contain", "#ffffff")))
    assert output.size == (200, 200)
    assert output.getpixel((100, 0)) == (255, 255, 255)
    assert output.getpixel((100, 100)) == (204, 0, 0)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum


def test_cover_crops_center_and_transparency_is_composited(tmp_path):
    source = tmp_path / "transparent.png"
    image = Image.new("RGBA", (100, 400), (255, 0, 0, 0))
    image.paste((0, 100, 0, 255), (0, 150, 100, 250))
    image.save(source)
    output = Image.open(io.BytesIO(fit_image(source, 200, 200, "cover", "#ffffff")))
    assert output.getpixel((100, 100)) == (0, 100, 0)
    assert output.mode == "RGB"


def test_fake_image_is_rejected(tmp_path):
    source = tmp_path / "pretend.png"
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    with pytest.raises(ApiError) as error:
        fit_image(source, 200, 200, "contain", "#ffffff")
    assert error.value.status_code == 415
