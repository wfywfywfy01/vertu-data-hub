from io import BytesIO

from PIL import Image

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
