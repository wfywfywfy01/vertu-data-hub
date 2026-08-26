import httpx
import pytest

from app.embeddings.text import ApiTextEmbedder, EmbeddingUnavailableError


def _response(status: int, *, vector: list[float] | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://openrouter.test/api/v1/embeddings")
    payload = {"data": [{"index": 0, "embedding": vector}]} if vector is not None else {}
    return httpx.Response(status, request=request, json=payload)


async def test_api_text_embedding_retries_transient_status(monkeypatch):
    monkeypatch.setattr("app.embeddings.text.settings.embedding_api_key", "test-key")
    monkeypatch.setattr(
        "app.embeddings.text.settings.embedding_base_url",
        "https://openrouter.test/api/v1",
    )
    monkeypatch.setattr("app.embeddings.text.settings.embedding_dim", 2)
    responses = [_response(502), _response(200, vector=[0.25, 0.75])]
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

    monkeypatch.setattr("app.embeddings.text.httpx.AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("app.embeddings.text.asyncio.sleep", no_delay)

    vectors = await ApiTextEmbedder().embed(["发布会照片"])

    assert vectors == [[0.25, 0.75]]
    assert len(calls) == 2


async def test_api_text_embedding_does_not_retry_auth_failure(monkeypatch):
    monkeypatch.setattr("app.embeddings.text.settings.embedding_api_key", "test-key")
    monkeypatch.setattr(
        "app.embeddings.text.settings.embedding_base_url",
        "https://openrouter.test/api/v1",
    )
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            calls.append(1)
            return _response(401)

    monkeypatch.setattr("app.embeddings.text.httpx.AsyncClient", lambda **_kwargs: Client())

    with pytest.raises(EmbeddingUnavailableError, match="unavailable"):
        await ApiTextEmbedder().embed(["发布会照片"])

    assert len(calls) == 1
