from io import BytesIO

from PIL import Image
import pytest

from app.processing.previews import MAX_PREVIEW_EDGE, image_preview


def test_image_preview_is_resized_watermarked_jpeg_without_metadata():
    source = BytesIO()
    image = Image.new("RGB", (2400, 1600), "white")
    exif = Image.Exif()
    exif[0x010E] = "confidential source metadata"
    image.save(source, format="JPEG", exif=exif)

    result = image_preview(source.getvalue())

    with Image.open(BytesIO(result)) as preview:
        assert preview.format == "JPEG"
        assert max(preview.size) == MAX_PREVIEW_EDGE
        assert not preview.getexif()
        assert preview.getpixel((preview.width - 5, preview.height - 5))[0] < 160


def test_preview_masks_ocr_text_and_restricted_image():
    source = BytesIO()
    Image.new("RGB", (600, 400), "red").save(source, format="PNG")
    for boxes, restricted in [(None, False), ((), True), (((0.1, 0.1, 0.5, 0.5),), False)]:
        with Image.open(BytesIO(image_preview(source.getvalue(), text_boxes=boxes, restricted=restricted))) as preview:
            red, green, blue = preview.getpixel((150, 120))
            assert abs(red - green) < 15
    with Image.open(BytesIO(image_preview(source.getvalue(), text_boxes=()))) as preview:
        assert preview.getpixel((150, 120))[0] > 240


def test_preview_rejects_invalid_mask_coordinates():
    source = BytesIO()
    Image.new("RGB", (100, 100)).save(source, format="PNG")
    with pytest.raises(ValueError):
        image_preview(source.getvalue(), text_boxes=((0, 0, float("nan"), 1),))
