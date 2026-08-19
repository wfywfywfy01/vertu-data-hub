"""Download and validate OCR models during an online build stage."""
from app.processing.images import _get_ocr_engine


def main() -> None:
    for language in ("default", "arabic", "cyrillic"):
        _get_ocr_engine(language)
        print(f"OCR model ready: {language}")


if __name__ == "__main__":
    main()
