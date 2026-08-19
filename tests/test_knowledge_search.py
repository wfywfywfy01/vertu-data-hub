import uuid

from app.retrieval import knowledge_search


async def test_query_is_redacted_before_embedding(monkeypatch):
    embedded = []

    class Embedder:
        async def embed(self, texts):
            embedded.extend(texts)
            return [[0.0] * 1024]

    async def no_hits(_query, _params):
        return []

    async def no_audit(_query, _params):
        return None

    monkeypatch.setattr(knowledge_search, "get_text_embedder", lambda: Embedder())
    monkeypatch.setattr(knowledge_search.db, "fetch_all", no_hits)
    monkeypatch.setattr(knowledge_search.db, "execute", no_audit)

    await knowledge_search.search_knowledge(
        "Contact frank.fu@vertu.cn.",
        dealer_ids=None,
        actor_id="pytest-admin",
    )

    assert embedded == ["Contact [REDACTED_EMAIL]."]


def test_rrf_rewards_chunk_found_by_both_retrievers():
    shared = {
        "chunk_id": uuid.uuid4(),
        "dealer_id": uuid.uuid4(),
        "asset_id": uuid.uuid4(),
        "asset_version_id": uuid.uuid4(),
        "text": "shared",
        "section": None,
        "category": "sales_inventory",
        "sensitivity": "internal",
        "citation": {},
        "version_number": 1,
        "title": "Inventory",
        "original_name": "inventory.md",
        "page_start": 1,
        "page_end": 1,
        "semantic_similarity": 0.8,
    }
    vector_only = {
        **shared,
        "chunk_id": uuid.uuid4(),
        "text": "vector only",
        "semantic_similarity": 0.9,
    }
    lexical_shared = {**shared, "lexical_score": 0.5}

    results = knowledge_search._fuse([vector_only, shared], [lexical_shared], 2)

    assert results[0]["text"] == "shared"
    assert results[0]["semantic_similarity"] == 0.8
    assert results[0]["lexical_score"] == 0.5
