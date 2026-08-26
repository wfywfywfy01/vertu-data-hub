"""文本 embedding 可替换接口（EMBEDDING_PROVIDER 走配置）：

- api  : OpenAI 兼容 /embeddings 接口（如通义 text-embedding-v3），需 EMBEDDING_API_KEY
- hash : 字符 n-gram 哈希向量（确定性、免外部服务）。仅做词汇级相似，
         用于无 key 的开发/测试环境跑通流程；正式环境切 api 后需重跑入库

所有 provider 输出维度 = settings.embedding_dim，与 doc_chunk.embedding 列一致。
"""
import asyncio
import hashlib
import math
import re
from typing import Protocol

import httpx

from app.config import settings


class TextEmbedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingUnavailableError(RuntimeError):
    pass


class ApiTextEmbedder:
    """OpenAI 兼容 embeddings API。"""

    BATCH = 10  # DashScope text-embedding-v3 单次上限
    MAX_ATTEMPTS = 2
    RETRY_DELAY_SECONDS = 0.25

    def __init__(self) -> None:
        if not settings.embedding_api_key:
            raise RuntimeError("EMBEDDING_API_KEY is empty; set it in .env or use EMBEDDING_PROVIDER=hash")
        self.base_url = settings.embedding_base_url.rstrip("/")
        self.model = settings.embedding_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        try:
            async with httpx.AsyncClient(
                timeout=settings.embedding_timeout_seconds, trust_env=False
            ) as client:
                for i in range(0, len(texts), self.BATCH):
                    batch = texts[i : i + self.BATCH]
                    resp = await self._post(client, batch)
                    data = sorted(resp.json()["data"], key=lambda d: d["index"])
                    vectors.extend(d["embedding"] for d in data)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingUnavailableError("text embedding service is unavailable") from exc
        if len(vectors) != len(texts) or any(
            not isinstance(vector, list) or len(vector) != settings.embedding_dim
            for vector in vectors
        ):
            raise EmbeddingUnavailableError("text embedding service returned invalid vectors")
        return vectors

    async def _post(self, client: httpx.AsyncClient, batch: list[str]) -> httpx.Response:
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
                    json={"model": self.model, "input": batch},
                )
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                if attempt + 1 == self.MAX_ATTEMPTS or not _is_retryable(exc):
                    raise
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)
        raise AssertionError("unreachable")


class HashTextEmbedder:
    """字符 2/3-gram 特征哈希向量（signed hashing trick），L2 归一化后余弦相似度即
    n-gram 重合度。确定性、零依赖，适合开发环境与 pytest。"""

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        t = re.sub(r"\s+", " ", text.lower()).strip()
        for n in (2, 3):
            for i in range(max(len(t) - n + 1, 0)):
                h = int.from_bytes(
                    hashlib.blake2b(t[i : i + n].encode("utf-8"), digest_size=8).digest(), "big"
                )
                v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


_embedder: TextEmbedder | None = None


def get_text_embedder() -> TextEmbedder:
    global _embedder
    if _embedder is None:
        provider = settings.embedding_provider
        if provider == "api":
            _embedder = ApiTextEmbedder()
        elif provider == "hash":
            _embedder = HashTextEmbedder()
        else:
            raise ValueError(f"unknown EMBEDDING_PROVIDER '{provider}'")
    return _embedder


def vector_literal(vec: list[float]) -> str:
    """转为 pgvector 字面量，SQL 里用 %s::vector 传参。"""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def _is_retryable(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False
