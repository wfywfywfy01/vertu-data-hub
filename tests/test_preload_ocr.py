from app.cli import preload_ocr


def test_preload_initializes_all_supported_models(monkeypatch):
    loaded = []
    monkeypatch.setattr(preload_ocr, "_get_ocr_engine", loaded.append)

    preload_ocr.main()

    assert loaded == ["default", "arabic", "cyrillic"]
