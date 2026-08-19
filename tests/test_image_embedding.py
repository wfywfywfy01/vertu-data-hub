from io import BytesIO

from PIL import Image

from app.embeddings.image import ApiImageEmbedder


async def test_api_image_embedding_uses_actual_content_type(monkeypatch):
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_api_key", "test-key"
    )
    embedder = ApiImageEmbedder()
    captured = []

    async def fake_call(contents):
        captured.extend(contents)
        return [0.0] * 1024

    embedder._call = fake_call
    output = BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="PNG")

    await embedder.embed_image(output.getvalue())

    assert captured[0]["image"].startswith("data:image/png;base64,")
