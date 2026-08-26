import asyncio
import io

from PIL import Image

from app.config import settings
from app.embeddings.image import QwenImageEmbedder, get_image_embedder
from app.embeddings.text import get_text_embedder


class ProviderCheckError(RuntimeError):
    pass


def _probe_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="JPEG")
    return output.getvalue()


async def check_providers() -> None:
    try:
        text_vectors = await get_text_embedder().embed(["经销商知识库连通性检查"])
        if len(text_vectors) != 1 or len(text_vectors[0]) != 1024:
            raise ValueError("text embedding dimension must be 1024")
    except Exception as exc:
        raise ProviderCheckError("text embedding") from exc
    print(
        f"text embedding ready provider={settings.embedding_provider} "
        f"dim={len(text_vectors[0])}"
    )

    try:
        image_embedder = get_image_embedder()
        if isinstance(image_embedder, QwenImageEmbedder):
            image_vector = (await image_embedder.analyze_image(_probe_image())).vector
        else:
            image_vector = await image_embedder.embed_text("经销商发布会照片")
        if len(image_vector) != 1024:
            raise ValueError("image embedding dimension must be 1024")
    except Exception as exc:
        raise ProviderCheckError("image embedding") from exc
    print(
        f"image embedding ready provider={settings.image_embedding_provider} "
        f"dim={len(image_vector)}"
    )


def main() -> None:
    try:
        asyncio.run(check_providers())
    except ProviderCheckError as exc:
        raise SystemExit(f"external provider check failed: {exc}") from None


if __name__ == "__main__":
    main()
