"""Fail closed when models required by offline production are absent."""
from app.embeddings.chinese_clip import prepare_model
from app.processing.images import _get_ocr_engine
from app.processing.media import get_transcriber


def _check_ocr() -> None:
    for language in ("default", "arabic", "cyrillic"):
        _get_ocr_engine(language)


MODEL_CHECKS = (
    ("ocr", _check_ocr),
    ("image", lambda: prepare_model(download=False)),
    ("video", lambda: get_transcriber().load(download=False)),
)


def require_api_models() -> None:
    _run_checks((MODEL_CHECKS[1],))


def require_models() -> None:
    _run_checks(MODEL_CHECKS)


def _run_checks(checks) -> None:
    for name, check in checks:
        try:
            check()
        except Exception as exc:
            raise RuntimeError(f"required local model is unavailable: {name}") from exc
