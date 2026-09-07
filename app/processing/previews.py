"""Generate fail-closed, metadata-free image previews with text regions hidden."""
from __future__ import annotations

from io import BytesIO
import math

from PIL import ImageDraw
from app.processing.images import _open_image


MAX_PREVIEW_EDGE = 1280


def image_preview(data: bytes, *, text_boxes=None, restricted: bool = False) -> bytes:
    decoded, _ = _open_image(data)
    with decoded as image:
        image.thumbnail((MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE))
        draw = ImageDraw.Draw(image, "RGBA")
        if restricted or text_boxes is None:
            draw.rectangle((0, 0, image.width, image.height), fill=(232, 235, 237, 255))
            draw.text((20, 20), "Protected image. Original requires administrator export.", fill=(35, 40, 45, 255))
        else:
            for box in text_boxes:
                if len(box) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for v in box):
                    raise ValueError("invalid preview redaction coordinates")
                x0, y0, x1, y1 = box
                if x0 > x1 or y0 > y1:
                    raise ValueError("invalid preview redaction bounds")
                draw.rectangle((max(0, x0 * image.width - 5), max(0, y0 * image.height - 5),
                                min(image.width, x1 * image.width + 5), min(image.height, y1 * image.height + 5)),
                               fill=(35, 40, 45, 255))
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
