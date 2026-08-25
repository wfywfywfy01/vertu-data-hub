import uuid

from app.retrieval import knowledge_search


def test_fallback_terms_extract_names_and_chinese_bigrams():
    terms = knowledge_search._fallback_terms(
        "Safiran Hamrah 的库存由谁录入，多久更新一次，哪天更新？"
    )

    assert {"safiran", "hamrah", "库存", "录入", "更新", "哪天"} <= set(terms)
    assert len(terms) == len(set(terms))


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


async def test_embedding_failure_falls_back_to_lexical_search(monkeypatch):
    dealer_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    version_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    queries = []

    class Embedder:
        async def embed(self, _texts):
            raise knowledge_search.EmbeddingUnavailableError("offline")

    async def fetch_all(query, _params):
        queries.append(query)
        return [{
            "chunk_id": chunk_id,
            "dealer_id": dealer_id,
            "scope_type": "dealer",
            "scope_key": str(dealer_id),
            "asset_version_id": version_id,
            "text": "当前库存为 12 台。",
            "section": None,
            "page_start": 1,
            "page_end": 1,
            "citation": {},
            "asset_id": asset_id,
            "title": "库存周报",
            "category": "sales_inventory",
            "sensitivity": "internal",
            "version_number": 1,
            "original_name": "inventory.pdf",
            "lexical_score": 0.5,
        }]

    async def no_audit(_query, _params):
        return None

    monkeypatch.setattr(knowledge_search, "get_text_embedder", lambda: Embedder())
    monkeypatch.setattr(knowledge_search.db, "fetch_all", fetch_all)
    monkeypatch.setattr(knowledge_search.db, "execute", no_audit)

    results = await knowledge_search.search_knowledge(
        "库存",
        dealer_ids=None,
        actor_id="pytest-admin",
    )

    assert len(queries) == 1
    assert "ts_rank_cd" in queries[0]
    assert results[0]["text"] == "当前库存为 12 台。"
    assert results[0]["semantic_similarity"] is None


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
