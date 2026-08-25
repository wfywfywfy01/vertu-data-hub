from app import semantic_images


async def test_cloud_backfill_updates_primary_vector(monkeypatch):
    captured = {}

    class Storage:
        def download_bytes(self, key):
            assert key == "images/event.webp"
            return b"image"

    class Embedder:
        async def embed_image(self, data):
            assert data == b"image"
            return [0.0] * 1024

    async def fetch_all(query, params):
        captured["select"] = query
        captured["select_params"] = params
        return [{
            "id": "image-row",
            "asset_version_id": "version-row",
            "bucket": "local-inbox",
            "object_key": "images/event.webp",
        }]

    async def execute(query, params):
        captured["update"] = query
        captured["update_params"] = params

    monkeypatch.setattr(semantic_images.settings, "image_embedding_provider", "api")
    monkeypatch.setattr(semantic_images.settings, "image_embedding_model", "multimodal-embedding-v1")
    monkeypatch.setattr(semantic_images.settings, "image_embedding_dim", 1024)
    monkeypatch.setattr(semantic_images.settings, "allow_external_image_processing", True)
    monkeypatch.setattr(semantic_images, "LocalStorage", Storage)
    monkeypatch.setattr(semantic_images, "get_image_embedder", lambda: Embedder())
    monkeypatch.setattr(semantic_images, "image_quality", lambda _data: 0.75)
    monkeypatch.setattr(semantic_images.db, "fetch_all", fetch_all)
    monkeypatch.setattr(semantic_images.db, "execute", execute)

    count = await semantic_images.index_semantic_images()

    assert count == 1
    assert "IS DISTINCT FROM" in captured["select"]
    assert "a.sensitivity IN ('internal', 'confidential')" in captured["select"]
    assert captured["update_params"][1:4] == (
        "api", "multimodal-embedding-v1", 1024
    )
    assert "semantic_embedding = NULL" in captured["update"]


async def test_cloud_backfill_requires_explicit_external_permission(monkeypatch):
    monkeypatch.setattr(semantic_images.settings, "image_embedding_provider", "api")
    monkeypatch.setattr(semantic_images.settings, "allow_external_image_processing", False)

    try:
        await semantic_images.index_semantic_images()
    except RuntimeError as exc:
        assert str(exc) == "ALLOW_EXTERNAL_IMAGE_PROCESSING=true is required"
    else:
        raise AssertionError("backfill must fail closed")
