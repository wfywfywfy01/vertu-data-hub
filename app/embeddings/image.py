"""多模态（图片/文字）embedding 可替换接口（IMAGE_EMBEDDING_PROVIDER 走配置）：

- api  : 阿里云 multimodal-embedding 风格接口，图片与文字映射到同一向量空间，
         可做真正的"文字搜图""以图搜图"。需 IMAGE_EMBEDDING_API_KEY。
- hash : 图片按颜色网格提取粗粒度视觉特征（无需外部服务），文字仍走字符 n-gram
         哈希。两者不在同一语义空间——hash 模式下"以图搜图"对色调/构图相近的图片
         有效，但不具备真实跨模态语义相关性。正式环境须切回 api 并对存量图片重跑。
"""
import hashlib
import io
import math
from numbers import Real
from typing import Protocol

import httpx

from app.config import settings
from app.embeddings.text import HashTextEmbedder


class ImageEmbedder(Protocol):
    async def embed_image(self, data: bytes) -> list[float]: ...
    async def embed_text(self, text: str) -> list[float]: ...


class ImageEmbeddingUnavailableError(RuntimeError):
    pass


class ApiImageEmbedder:
    """阿里云 DashScope 多模态 embedding（图片以 URL 或 base64 传入，与文字同一向量空间）。"""

    def __init__(self) -> None:
        if not settings.image_embedding_api_key or not settings.image_embedding_base_url:
            raise RuntimeError(
                "IMAGE_EMBEDDING_BASE_URL and IMAGE_EMBEDDING_API_KEY are required"
            )
        self.base_url = settings.image_embedding_base_url.rstrip("/")
        self.model = settings.image_embedding_model

    async def _call(self, contents: list[dict]) -> list[float]:
        try:
            async with httpx.AsyncClient(
                timeout=settings.image_embedding_timeout_seconds, trust_env=False
            ) as client:
                resp = await client.post(
                    f"{self.base_url}/services/embeddings/multimodal-embedding/multimodal-embedding",
                    headers={"Authorization": f"Bearer {settings.image_embedding_api_key}"},
                    json={"model": self.model, "input": {"contents": contents}},
                )
                resp.raise_for_status()
                vector = resp.json()["output"]["embeddings"][0]["embedding"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ImageEmbeddingUnavailableError(
                "multimodal embedding service is unavailable"
            ) from exc
        if (
            not isinstance(vector, list)
            or len(vector) != settings.image_embedding_dim
            or not all(isinstance(value, Real) and math.isfinite(value) for value in vector)
        ):
            raise ImageEmbeddingUnavailableError(
                "multimodal embedding service returned an invalid vector"
            )
        return [float(value) for value in vector]

    async def embed_image(self, data: bytes) -> list[float]:
        import base64

        from app.processing.images import _open_image

        image, _image_format = _open_image(data)
        try:
            for edge, quality in ((1600, 82), (1280, 75), (1024, 70)):
                image.thumbnail((edge, edge))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                if output.tell() <= 3 * 1024 * 1024:
                    break
            else:
                raise ImageEmbeddingUnavailableError(
                    "image cannot be reduced to the multimodal API size limit"
                )
        finally:
            image.close()
        b64 = base64.b64encode(output.getvalue()).decode("ascii")
        return await self._call([{"image": f"data:image/jpeg;base64,{b64}"}])

    async def embed_text(self, text: str) -> list[float]:
        return await self._call([{"text": text}])


class HashImageEmbedder:
    """开发用后备方案：图片用颜色网格粗特征，文字用字符 n-gram 哈希。"""

    GRID = 8  # 8x8 网格 x 3 通道 = 192 维粗特征，投影进目标维度

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.image_embedding_dim
        self._text_embedder = HashTextEmbedder(dim=self.dim)

    async def embed_text(self, text: str) -> list[float]:
        return (await self._text_embedder.embed([text]))[0]

    async def embed_image(self, data: bytes) -> list[float]:
        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGB").resize((self.GRID, self.GRID))
        try:
            raw = img.tobytes()
        finally:
            img.close()

        v = [0.0] * self.dim
        for idx in range(self.GRID * self.GRID):
            r, g, b = raw[idx * 3 : idx * 3 + 3]
            for channel, value in enumerate((r, g, b)):
                key = f"{idx}:{channel}".encode("ascii")
                h = int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big")
                v[h % self.dim] += (value / 255.0) * (1.0 if (h >> 63) & 1 else -1.0)
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


_embedder: ImageEmbedder | None = None


def get_image_embedder() -> ImageEmbedder:
    global _embedder
    if _embedder is None:
        provider = settings.image_embedding_provider
        if provider == "api":
            _embedder = ApiImageEmbedder()
        elif provider == "hash":
            _embedder = HashImageEmbedder()
        else:
            raise ValueError(f"unknown IMAGE_EMBEDDING_PROVIDER '{provider}'")
    return _embedder
