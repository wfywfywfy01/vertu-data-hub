"""多模态（图片/文字）embedding 可替换接口（IMAGE_EMBEDDING_PROVIDER 走配置）：

- api  : 阿里云 multimodal-embedding 风格接口，图片与文字映射到同一向量空间，
         可做真正的"文字搜图""以图搜图"。需 IMAGE_EMBEDDING_API_KEY。
- hash : 图片按颜色网格提取粗粒度视觉特征（无需外部服务），文字仍走字符 n-gram
         哈希。两者不在同一语义空间——hash 模式下"以图搜图"对色调/构图相近的图片
         有效，但不具备真实跨模态语义相关性。正式环境须切回 api 并对存量图片重跑。
"""
import asyncio
import hashlib
import io
import json
import math
from dataclasses import dataclass
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


class SensitiveImageDescriptionError(RuntimeError):
    def __init__(self, reasons: list[str]):
        super().__init__("Qwen image description requires review")
        self.reasons = reasons


@dataclass(frozen=True)
class ImageAnalysis:
    vector: list[float]
    description: str = ""
    labels: tuple[str, ...] = ()
    redaction_count: int = 0


def image_model_identity() -> str:
    if settings.image_embedding_provider == "qwen":
        value = "|".join((
            "qwen-image-v1",
            settings.image_embedding_model,
            settings.embedding_provider,
            settings.embedding_model,
        ))
        return f"qwen-image-v1:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"
    return settings.image_embedding_model


def _jpeg_data_url(data: bytes) -> str:
    import base64

    from app.processing.images import _open_image

    image, _image_format = _open_image(data)
    try:
        for edge, quality in ((768, 70), (512, 65), (384, 60)):
            image.thumbnail((edge, edge))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            if output.tell() <= 512 * 1024:
                break
        else:
            raise ImageEmbeddingUnavailableError(
                "image cannot be reduced to the multimodal API size limit"
            )
    finally:
        image.close()
    return f"data:image/jpeg;base64,{base64.b64encode(output.getvalue()).decode('ascii')}"


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
        return await self._call([{"image": _jpeg_data_url(data)}])

    async def embed_text(self, text: str) -> list[float]:
        return await self._call([{"text": text}])


class QwenImageEmbedder:
    """Use an OpenAI-compatible Qwen vision endpoint, then embed its description."""

    MAX_ATTEMPTS = 3
    RETRY_DELAY_SECONDS = 2.0

    PROMPT = (
        "分析这张经销商业务图片。只输出 JSON，不要 Markdown："
        '{"description":"用中文客观描述人物、产品、场景、活动、品牌和可见文字",'
        '"labels":["最多12个简短标签"]}。'
        "不要猜测身份，不要输出电话、邮箱、证件号等个人敏感信息。"
    )

    def __init__(self) -> None:
        if not settings.image_embedding_api_key or not settings.image_embedding_base_url:
            raise RuntimeError(
                "IMAGE_EMBEDDING_BASE_URL and IMAGE_EMBEDDING_API_KEY are required"
            )
        base_url = settings.image_embedding_base_url.rstrip("/")
        self.chat_url = (
            f"{base_url}/chat/completions"
            if base_url.endswith("/v1")
            else f"{base_url}/v1/chat/completions"
        )
        self.model = settings.image_embedding_model

    async def _describe(self, data: bytes) -> tuple[str, tuple[str, ...]]:
        try:
            async with httpx.AsyncClient(
                timeout=settings.image_embedding_timeout_seconds, trust_env=False
            ) as client:
                response = await self._post_description(client, data)
                content = response.json()["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise TypeError("vision response content must be text")
                start, end = content.find("{"), content.rfind("}")
                payload = json.loads(content[start : end + 1])
                if not isinstance(payload, dict):
                    raise TypeError("vision response must be a JSON object")
                description = str(payload.get("description", "")).strip()[:2000]
                raw_labels = payload.get("labels", [])
                if not isinstance(raw_labels, list):
                    raise TypeError("vision labels must be a JSON array")
                labels = tuple(
                    str(label).strip()[:80]
                    for label in raw_labels[:12]
                    if str(label).strip()
                )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ImageEmbeddingUnavailableError("Qwen vision service is unavailable") from exc
        if not description:
            raise ImageEmbeddingUnavailableError("Qwen vision service returned no description")
        return description, labels

    async def _post_description(
        self, client: httpx.AsyncClient, data: bytes
    ) -> httpx.Response:
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": self.PROMPT},
                    {"type": "image_url", "image_url": {"url": _jpeg_data_url(data)}},
                ],
            }],
            "temperature": 0,
            "max_tokens": 500,
        }
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                response = await client.post(
                    self.chat_url,
                    headers={"Authorization": f"Bearer {settings.image_embedding_api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                if attempt + 1 == self.MAX_ATTEMPTS or not _is_retryable(exc):
                    raise
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)
        raise AssertionError("unreachable")

    async def _embed_text(self, text: str) -> list[float]:
        from app.embeddings.text import EmbeddingUnavailableError, get_text_embedder

        try:
            vector = (await get_text_embedder().embed([text]))[0]
        except (EmbeddingUnavailableError, IndexError, RuntimeError) as exc:
            raise ImageEmbeddingUnavailableError("text embedding service is unavailable") from exc
        if (
            not isinstance(vector, list)
            or len(vector) != settings.image_embedding_dim
            or not all(isinstance(value, Real) and math.isfinite(value) for value in vector)
        ):
            raise ImageEmbeddingUnavailableError("text embedding service returned an invalid vector")
        return [float(value) for value in vector]

    async def analyze_image(self, data: bytes) -> ImageAnalysis:
        from app.processing.redaction import redact_text
        from app.processing.sensitivity import high_sensitivity_reasons

        description, labels = await self._describe(data)
        reasons = high_sensitivity_reasons("\n".join((description, *labels)))
        if reasons:
            raise SensitiveImageDescriptionError(reasons)
        safe_description = redact_text(description)
        safe_labels = tuple(redact_text(label) for label in labels)
        description = safe_description.text
        labels = tuple(label.text for label in safe_labels)
        text = "。".join((description, *labels))
        vector = await self._embed_text(text)
        return ImageAnalysis(
            vector=vector,
            description=description,
            labels=labels,
            redaction_count=safe_description.count + sum(label.count for label in safe_labels),
        )

    async def embed_image(self, data: bytes) -> list[float]:
        return (await self.analyze_image(data)).vector

    async def embed_text(self, text: str) -> list[float]:
        return await self._embed_text(text)


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
        elif provider == "qwen":
            _embedder = QwenImageEmbedder()
        elif provider == "hash":
            _embedder = HashImageEmbedder()
        else:
            raise ValueError(f"unknown IMAGE_EMBEDDING_PROVIDER '{provider}'")
    return _embedder


async def analyze_image(embedder: ImageEmbedder, data: bytes) -> ImageAnalysis:
    analyzer = getattr(embedder, "analyze_image", None)
    if analyzer is not None:
        return await analyzer(data)
    return ImageAnalysis(vector=await embedder.embed_image(data))


def _is_retryable(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False
