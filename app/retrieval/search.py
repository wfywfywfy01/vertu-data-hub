"""Route human and agent searches to text or image retrieval."""
import logging

from app.config import settings
from app.embeddings.image import ImageEmbeddingUnavailableError
from app.retrieval.image_search import is_image_query, search_images
from app.retrieval.knowledge_search import search_knowledge


logger = logging.getLogger(__name__)


async def search_assets(query: str, **kwargs) -> list[dict]:
    if (
        settings.semantic_image_query_enabled
        and settings.image_embedding_provider == "api"
        and is_image_query(query, kwargs.get("category"))
    ):
        try:
            rows = await search_images(query, **kwargs)
            if rows:
                return rows
        except ImageEmbeddingUnavailableError:
            logger.warning("multimodal embedding unavailable; using OCR/text retrieval")
    return await search_knowledge(query, **kwargs)
