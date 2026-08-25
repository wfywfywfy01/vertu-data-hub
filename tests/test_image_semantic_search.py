import hashlib
from uuid import uuid4

from app.retrieval import image_search
from app.retrieval import search as search_router
from app.embeddings.image import ImageEmbeddingUnavailableError


def test_image_intent_recognizes_human_queries():
    assert image_search.is_image_query("发布会照片，选几张发社媒")
    assert image_search.is_image_query("活动资料", "media")
    assert not image_search.is_image_query("上周库存是多少")
    assert image_search.visual_query("我想发个社媒，配个文字") == (
        "我想发个社媒，配个文字。图片要求：人物清晰，主体突出，品牌露出，适合社交媒体发布"
    )


async def test_image_search_is_scoped_cited_and_audited(monkeypatch):
    dealer_id = uuid4()
    asset_id = uuid4()
    version_id = uuid4()
    captured = {}

    class Embedder:
        async def embed_text(self, text):
            assert text == "发布会照片"
            return [1.0] + [0.0] * 1023

    async def fetch_all(query, params):
        captured["query"] = query
        captured["params"] = params
        return [{
            "asset_id": asset_id,
            "dealer_id": dealer_id,
            "scope_type": "dealer",
            "scope_key": str(dealer_id),
            "title": "发布会模特",
            "category": "media",
            "sensitivity": "confidential",
            "asset_version_id": version_id,
            "version_number": 1,
            "original_name": "image14.png",
            "quality_score": 0.86,
            "semantic_labels": [{"label": "模特展示手机", "score": 0.5}],
            "semantic_similarity": 0.48,
            "score": 0.518,
        }]

    async def execute(query, params):
        captured["audit_query"] = query
        captured["audit_params"] = params

    monkeypatch.setattr(image_search, "get_image_embedder", lambda: Embedder())
    monkeypatch.setattr(image_search.settings, "image_embedding_provider", "api")
    monkeypatch.setattr(image_search.settings, "image_embedding_model", "multimodal-embedding-v1")
    monkeypatch.setattr(image_search.settings, "image_embedding_dim", 1024)
    monkeypatch.setattr(image_search.db, "fetch_all", fetch_all)
    monkeypatch.setattr(image_search.db, "execute", execute)

    rows = await image_search.search_images(
        "发布会照片",
        dealer_ids=[dealer_id],
        dealer_id=dealer_id,
        actor_id="pytest-sales",
        request_id="semantic-search-test",
    )

    assert "a.dealer_id = ANY(%s)" in captured["query"]
    assert "a.dealer_id = %s" in captured["query"]
    assert "ie.embedding_provider = %s" in captured["query"]
    assert "ie.embedding <=> %s::vector" in captured["query"]
    assert rows[0]["retrieval_kind"] == "image_semantic"
    assert rows[0]["citation"]["original_name"] == "image14.png"
    assert "先锋设计" in rows[0]["suggested_caption"]
    payload = captured["audit_params"][2].obj
    assert payload["query_sha256"] == hashlib.sha256("发布会照片".encode()).hexdigest()
    assert "发布会照片" not in str(payload)


async def test_image_query_is_redacted_before_embedding(monkeypatch):
    captured = {}

    class Embedder:
        async def embed_text(self, text):
            captured["text"] = text
            return [0.0] * 1024

    async def fetch_all(_query, _params):
        return []

    async def execute(*_args, **_kwargs):
        return None

    monkeypatch.setattr(image_search, "get_image_embedder", lambda: Embedder())
    monkeypatch.setattr(image_search.settings, "image_embedding_provider", "api")
    monkeypatch.setattr(image_search.db, "fetch_all", fetch_all)
    monkeypatch.setattr(image_search.db, "execute", execute)

    await image_search.search_images(
        "把 frank.fu@vertu.cn 的发布会照片找出来",
        dealer_ids=[],
        actor_id="pytest",
    )

    assert "frank.fu@vertu.cn" not in captured["text"]
    assert "[REDACTED_EMAIL]" in captured["text"]


async def test_image_metadata_fallback_keeps_authorization_scope(monkeypatch):
    dealer_id = uuid4()
    asset_id = uuid4()
    version_id = uuid4()
    captured = {}

    async def fetch_all(query, params):
        captured["query"] = query
        captured["params"] = params
        return [{
            "asset_id": asset_id,
            "dealer_id": dealer_id,
            "scope_type": "dealer",
            "scope_key": str(dealer_id),
            "title": "VMG launch image14",
            "category": "media",
            "sensitivity": "internal",
            "asset_version_id": version_id,
            "version_number": 1,
            "original_name": "image14.png",
            "quality_score": 0.8,
            "semantic_labels": [],
        }]

    async def execute(*_args, **_kwargs):
        return None

    monkeypatch.setattr(image_search.db, "fetch_all", fetch_all)
    monkeypatch.setattr(image_search.db, "execute", execute)

    rows = await image_search.search_image_metadata(
        "找 image14 照片",
        dealer_ids=[dealer_id],
        dealer_id=dealer_id,
        actor_id="pytest",
    )

    assert "a.dealer_id = ANY(%s)" in captured["query"]
    assert "a.dealer_id = %s" in captured["query"]
    assert any("%image14%" in value for value in captured["params"] if isinstance(value, list))
    assert rows[0]["retrieval_kind"] == "image_metadata"


async def test_image_router_falls_back_when_semantic_index_is_empty(monkeypatch):
    calls = []
    monkeypatch.setattr(search_router.settings, "semantic_image_query_enabled", True)
    monkeypatch.setattr(search_router.settings, "image_embedding_provider", "api")

    async def images(query, **kwargs):
        calls.append("images")
        return []

    async def knowledge(query, **kwargs):
        calls.append("knowledge")
        return [{"asset_id": uuid4()}]

    async def metadata(query, **kwargs):
        calls.append("metadata")
        return []

    monkeypatch.setattr(search_router, "search_images", images)
    monkeypatch.setattr(search_router, "search_image_metadata", metadata)
    monkeypatch.setattr(search_router, "search_knowledge", knowledge)

    rows = await search_router.search_assets(
        "找发布会照片", dealer_ids=[], actor_id="pytest"
    )

    assert len(rows) == 1
    assert calls == ["images", "metadata", "knowledge"]


async def test_image_router_skips_local_model_when_disabled(monkeypatch):
    calls = []

    async def images(query, **kwargs):
        calls.append("images")
        return []

    async def knowledge(query, **kwargs):
        calls.append("knowledge")
        return [{"asset_id": uuid4()}]

    monkeypatch.setattr(search_router.settings, "semantic_image_query_enabled", False)
    monkeypatch.setattr(search_router, "search_images", images)
    monkeypatch.setattr(search_router, "search_knowledge", knowledge)

    rows = await search_router.search_assets(
        "找发布会照片", dealer_ids=[], actor_id="pytest"
    )

    assert len(rows) == 1
    assert calls == ["knowledge"]


async def test_image_router_skips_hash_cross_modal_search(monkeypatch):
    calls = []

    async def images(query, **kwargs):
        calls.append("images")
        return []

    async def knowledge(query, **kwargs):
        calls.append("knowledge")
        return [{"asset_id": uuid4()}]

    monkeypatch.setattr(search_router.settings, "semantic_image_query_enabled", True)
    monkeypatch.setattr(search_router.settings, "image_embedding_provider", "hash")
    monkeypatch.setattr(search_router, "search_images", images)
    monkeypatch.setattr(search_router, "search_knowledge", knowledge)
    await search_router.search_assets("找发布会照片", dealer_ids=[], actor_id="pytest")

    assert calls == ["knowledge"]


async def test_image_router_falls_back_when_multimodal_api_is_unavailable(monkeypatch):
    calls = []

    async def images(query, **kwargs):
        calls.append("images")
        raise ImageEmbeddingUnavailableError("offline")

    async def knowledge(query, **kwargs):
        calls.append("knowledge")
        return [{"asset_id": uuid4()}]

    async def metadata(query, **kwargs):
        calls.append("metadata")
        return []

    monkeypatch.setattr(search_router.settings, "semantic_image_query_enabled", True)
    monkeypatch.setattr(search_router.settings, "image_embedding_provider", "api")
    monkeypatch.setattr(search_router, "search_images", images)
    monkeypatch.setattr(search_router, "search_image_metadata", metadata)
    monkeypatch.setattr(search_router, "search_knowledge", knowledge)

    rows = await search_router.search_assets(
        "找发布会照片", dealer_ids=[], actor_id="pytest"
    )

    assert len(rows) == 1
    assert calls == ["images", "metadata", "knowledge"]
