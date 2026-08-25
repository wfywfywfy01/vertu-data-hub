from app.workers import image


def test_restricted_image_never_uses_configured_cloud_embedder(monkeypatch):
    monkeypatch.setattr(image.settings, "image_embedding_provider", "api")
    monkeypatch.setattr(image.settings, "allow_external_image_processing", True)
    monkeypatch.setattr(
        image,
        "get_image_embedder",
        lambda: (_ for _ in ()).throw(AssertionError("cloud embedder must not be selected")),
    )

    embedder, provider, model, dimension = image._select_image_embedder(
        {"sensitivity": "restricted"}
    )

    assert isinstance(embedder, image.HashImageEmbedder)
    assert (provider, model, dimension) == ("hash", "hash-color-grid-v1", 1024)


def test_confidential_image_uses_cloud_when_explicitly_enabled(monkeypatch):
    configured = object()
    monkeypatch.setattr(image.settings, "image_embedding_provider", "api")
    monkeypatch.setattr(image.settings, "allow_external_image_processing", True)
    monkeypatch.setattr(image, "get_image_embedder", lambda: configured)

    embedder, provider, model, dimension = image._select_image_embedder(
        {"sensitivity": "confidential"}
    )

    assert embedder is configured
    assert (provider, model, dimension) == (
        "api", image.settings.image_embedding_model, 1024
    )


def test_confidential_image_uses_qwen_when_explicitly_enabled(monkeypatch):
    configured = object()
    monkeypatch.setattr(image.settings, "image_embedding_provider", "qwen")
    monkeypatch.setattr(image.settings, "allow_external_image_processing", True)
    monkeypatch.setattr(image, "get_image_embedder", lambda: configured)

    embedder, provider, model, dimension = image._select_image_embedder(
        {"sensitivity": "confidential"}
    )

    assert embedder is configured
    assert provider == "qwen"
    assert model.startswith("qwen-image-v1:")
    assert dimension == 1024
