import pytest

from app.cli.check_providers import ProviderCheckError, check_providers


async def test_provider_check_accepts_1024_dimension_vectors(monkeypatch, capsys):
    class TextEmbedder:
        async def embed(self, _texts):
            return [[0.0] * 1024]

    class ImageEmbedder:
        async def embed_text(self, _text):
            return [0.0] * 1024

    monkeypatch.setattr(
        "app.cli.check_providers.get_text_embedder", lambda: TextEmbedder()
    )
    monkeypatch.setattr(
        "app.cli.check_providers.get_image_embedder", lambda: ImageEmbedder()
    )

    await check_providers()

    output = capsys.readouterr().out
    assert "text embedding ready" in output
    assert "image embedding ready" in output


async def test_provider_check_rejects_wrong_text_dimension(monkeypatch):
    class TextEmbedder:
        async def embed(self, _texts):
            return [[0.0] * 768]

    monkeypatch.setattr(
        "app.cli.check_providers.get_text_embedder", lambda: TextEmbedder()
    )

    with pytest.raises(ProviderCheckError, match="text embedding"):
        await check_providers()
