"""Image validation and local multilingual OCR."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO


MAX_IMAGE_PIXELS = 100_000_000


@dataclass(frozen=True)
class ImageExtraction:
    text: str
    line_count: int
    mean_confidence: float | None
    width: int
    height: int
    image_format: str
    ocr_language: str


def _ocr_language(language_code: str | None) -> str:
    language = (language_code or "").lower().split("-", 1)[0]
    if language in {"ar", "fa"}:
        return "arabic"
    if language == "ru":
        return "cyrillic"
    return "default"


@lru_cache(maxsize=3)
def _get_ocr_engine(language: str):
    from rapidocr import RapidOCR

    if language == "default":
        return RapidOCR()

    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

    params = {
        "Rec.lang_type": LangRec.ARABIC if language == "arabic" else LangRec.CYRILLIC,
        "Rec.ocr_version": OCRVersion.PPOCRV4,
        "Rec.model_type": ModelType.MOBILE,
    }
    return RapidOCR(params=params)


def _open_image(data: bytes):
    from PIL import Image, ImageOps

    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        pass

    try:
        with Image.open(BytesIO(data)) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise ValueError("image dimensions are invalid or too large")
            image_format = str(source.format or "unknown").lower()
            image = ImageOps.exif_transpose(source).convert("RGB")
        image.load()
        return image, image_format
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("image cannot be decoded") from exc


def extract_image(data: bytes, language_code: str | None = None) -> ImageExtraction:
    import numpy as np

    image, image_format = _open_image(data)
    width, height = image.size
    language = _ocr_language(language_code)
    try:
        output = _get_ocr_engine(language)(np.asarray(image))
    except Exception as exc:
        raise RuntimeError("OCR engine failed") from exc
    finally:
        image.close()

    lines = []
    scores = []
    for text, score in zip(output.txts or (), output.scores or ()):
        value = str(text).strip()
        if value:
            lines.append(value)
            scores.append(float(score))
    return ImageExtraction(
        text="\n".join(lines),
        line_count=len(lines),
        mean_confidence=sum(scores) / len(scores) if scores else None,
        width=width,
        height=height,
        image_format=image_format,
        ocr_language=language,
    )


def image_quality(data: bytes) -> float:
    """Cheap local quality signal for resolution, sharpness, and exposure."""
    from PIL import ImageFilter, ImageStat

    image, _image_format = _open_image(data)
    try:
        width, height = image.size
        grayscale = image.convert("L")
        resolution = min(1.0, min(width, height) / 1080.0)
        grayscale.thumbnail((512, 512))
        mean = ImageStat.Stat(grayscale).mean[0]
        exposure = max(0.0, 1.0 - abs(mean - 127.5) / 127.5)
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        sharpness = min(1.0, ImageStat.Stat(edges).stddev[0] / 45.0)
    finally:
        image.close()
    return round(0.35 * resolution + 0.35 * sharpness + 0.30 * exposure, 6)
