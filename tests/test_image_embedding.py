from io import BytesIO
import base64

from PIL import Image
import pytest

from app.embeddings.image import ApiImageEmbedder, ImageEmbeddingUnavailableError


async def test_api_image_embedding_normalizes_to_supported_jpeg(monkeypatch):
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_api_key", "test-key"
    )
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_base_url",
        "https://dashscope.test/api/v1",
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

    assert captured[0]["image"].startswith("data:image/jpeg;base64,")
    assert len(base64.b64decode(captured[0]["image"].split(",", 1)[1])) <= 3 * 1024 * 1024


async def test_api_image_embedding_rejects_invalid_vector(monkeypatch):
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_api_key", "test-key"
    )
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_base_url",
        "https://dashscope.test/api/v1",
    )
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_dim", 2
    )
    embedder = ApiImageEmbedder()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output": {"embeddings": [{"embedding": [0.0, float("nan")]}]}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.embeddings.image.httpx.AsyncClient", lambda **_kwargs: Client())

    with pytest.raises(ImageEmbeddingUnavailableError, match="invalid vector"):
        await embedder.embed_text("test")
