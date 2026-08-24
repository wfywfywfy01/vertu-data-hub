from pathlib import Path

import pytest

from app import model_contract


ROOT = Path(__file__).resolve().parent.parent


def test_production_compose_requires_explicit_immutable_image():
    compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")

    assert "build:" not in compose
    assert "${DATA_HUB_IMAGE:?" in compose
    assert ":local" not in compose


def test_required_models_are_checked_offline(monkeypatch):
    checked = []
    monkeypatch.setattr(model_contract, "MODEL_CHECKS", (
        ("ocr", lambda: checked.append("ocr")),
        ("image", lambda: checked.append("image")),
        ("video", lambda: checked.append("video")),
    ))

    model_contract.require_models()

    assert checked == ["ocr", "image", "video"]


def test_api_only_checks_its_image_model(monkeypatch):
    checked = []
    monkeypatch.setattr(model_contract, "MODEL_CHECKS", (
        ("ocr", lambda: checked.append("ocr")),
        ("image", lambda: checked.append("image")),
        ("video", lambda: checked.append("video")),
    ))

    model_contract.require_api_models()

    assert checked == ["image"]


def test_missing_required_model_fails_startup(monkeypatch):
    def missing():
        raise FileNotFoundError("model cache empty")

    monkeypatch.setattr(model_contract, "MODEL_CHECKS", (("video", missing),))

    with pytest.raises(RuntimeError, match="required local model is unavailable: video"):
        model_contract.require_models()
