import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.embeddings.image import (
    ApiImageEmbedder,
    ImageEmbeddingUnavailableError,
    QwenImageEmbedder,
    SensitiveImageDescriptionError,
)


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


async def test_qwen_image_embedding_uses_description_and_labels(monkeypatch):
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_api_key", "test-key"
    )
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_base_url",
        "https://qwen.test:8443",
    )
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_model", "qwen-vision.gguf"
    )
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": (
                            '```json\n{"description":"VERTU 发布会上的多人合影，联系 a@b.com",'
                            '"labels":["发布会","多人合影","VERTU"]}\n```'
                        )
                    }
                }]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return Response()

    class TextEmbedder:
        async def embed(self, texts):
            captured["text"] = texts[0]
            return [[0.25] * 1024]

    monkeypatch.setattr("app.embeddings.image.httpx.AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("app.embeddings.text.get_text_embedder", lambda: TextEmbedder())
    output = BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="PNG")

    analysis = await QwenImageEmbedder().analyze_image(output.getvalue())

    assert captured["url"] == "https://qwen.test:8443/v1/chat/completions"
    image_url = captured["json"]["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")
    assert analysis.description == "VERTU 发布会上的多人合影，联系 [REDACTED_EMAIL]"
    assert analysis.labels == ("发布会", "多人合影", "VERTU")
    assert "多人合影" in captured["text"]
    assert "a@b.com" not in captured["text"]
    assert analysis.redaction_count == 1
    assert analysis.vector == [0.25] * 1024


async def test_qwen_image_embedding_rejects_invalid_response(monkeypatch):
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_api_key", "test-key"
    )
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_base_url", "https://qwen.test/v1"
    )
    embedder = QwenImageEmbedder()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "not-json"}}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.embeddings.image.httpx.AsyncClient", lambda **_kwargs: Client())
    output = BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="PNG")

    with pytest.raises(ImageEmbeddingUnavailableError, match="Qwen vision"):
        await embedder.embed_image(output.getvalue())


async def test_qwen_image_embedding_retries_transient_status(monkeypatch):
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_api_key", "test-key"
    )
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_base_url",
        "https://qwen.test/v1",
    )
    request = httpx.Request("POST", "https://qwen.test/v1/chat/completions")
    responses = [
        httpx.Response(502, request=request),
        httpx.Response(
            200,
            request=request,
            json={
                "choices": [{
                    "message": {
                        "content": '{"description":"VERTU 发布会合影","labels":["发布会"]}'
                    }
                }]
            },
        ),
    ]
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            calls.append(1)
            return responses.pop(0)

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("app.embeddings.image.httpx.AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("app.embeddings.image.asyncio.sleep", no_delay)
    output = BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="PNG")

    description, labels = await QwenImageEmbedder()._describe(output.getvalue())

    assert description == "VERTU 发布会合影"
    assert labels == ("发布会",)
    assert len(calls) == 2


async def test_qwen_query_embedding_wraps_text_service_failure(monkeypatch):
    from app.embeddings.text import EmbeddingUnavailableError

    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_api_key", "test-key"
    )
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_base_url", "https://qwen.test/v1"
    )

    class TextEmbedder:
        async def embed(self, _texts):
            raise EmbeddingUnavailableError("offline")

    monkeypatch.setattr("app.embeddings.text.get_text_embedder", lambda: TextEmbedder())

    with pytest.raises(ImageEmbeddingUnavailableError, match="text embedding"):
        await QwenImageEmbedder().embed_text("发布会照片")


async def test_qwen_rejects_sensitive_description_before_embedding(monkeypatch):
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_api_key", "test-key"
    )
    monkeypatch.setattr(
        "app.embeddings.image.settings.image_embedding_base_url", "https://qwen.test/v1"
    )
    embedder = QwenImageEmbedder()

    async def describe(_data):
        return "后台截图显示 password: SuperSecret123", ("系统截图",)

    embedder._describe = describe

    with pytest.raises(SensitiveImageDescriptionError) as exc:
        await embedder.analyze_image(b"unused")

    assert exc.value.reasons == ["password"]
