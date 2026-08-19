from io import BytesIO

from PIL import Image
import pytest

from app.processing import images


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (320, 120), "white").save(output, format="PNG")
    return output.getvalue()


def test_persian_image_uses_arabic_ocr_and_keeps_metadata(monkeypatch):
    requested = []

    class Output:
        txts = ("Safiran Hamrah", "Inventory 12")
        scores = (0.95, 0.85)
        img = None

    class Engine:
        def __call__(self, _image):
            return Output()

    monkeypatch.setattr(
        images,
        "_get_ocr_engine",
        lambda language: requested.append(language) or Engine(),
    )

    result = images.extract_image(_png_bytes(), "fa-IR")

    assert requested == ["arabic"]
    assert result.text == "Safiran Hamrah\nInventory 12"
    assert result.line_count == 2
    assert result.mean_confidence == pytest.approx(0.9)
    assert result.image_format == "png"
    assert (result.width, result.height) == (320, 120)


def test_corrupt_image_is_rejected():
    with pytest.raises(ValueError, match="cannot be decoded"):
        images.extract_image(b"not-an-image")
