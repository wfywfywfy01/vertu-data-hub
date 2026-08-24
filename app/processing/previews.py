"""Generate metadata-free, watermarked image previews."""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageOps


MAX_PREVIEW_EDGE = 1280


def image_preview(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE))
        draw = ImageDraw.Draw(image, "RGBA")
        label = "VERTU INTERNAL PREVIEW"
        box = draw.textbbox((0, 0), label)
        width = box[2] - box[0]
        height = box[3] - box[1]
        padding = 10
        left = max(0, image.width - width - padding * 2)
        top = max(0, image.height - height - padding * 2)
        draw.rectangle((left, top, image.width, image.height), fill=(0, 0, 0, 145))
        draw.text((left + padding, top + padding), label, fill=(255, 255, 255, 220))
        output = BytesIO()
        image.save(output, format="JPEG", quality=82, optimize=True)
        return output.getvalue()
