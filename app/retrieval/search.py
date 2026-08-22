"""Route human and agent searches to text or image retrieval."""
from app.retrieval.image_search import is_image_query, search_images
from app.retrieval.knowledge_search import search_knowledge


async def search_assets(query: str, **kwargs) -> list[dict]:
    if is_image_query(query, kwargs.get("category")):
        rows = await search_images(query, **kwargs)
        if rows:
            return rows
    return await search_knowledge(query, **kwargs)
