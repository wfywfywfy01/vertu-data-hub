from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app import local_ui


def test_local_ui_serves_page_and_assets():
    client = TestClient(app)

    page = client.get("/ui")
    styles = client.get("/ui/query.css")
    script = client.get("/ui/query.js")

    assert page.status_code == 200
    assert "经销商知识库" in page.text
    assert styles.status_code == 200
    assert "--accent" in styles.text
    assert script.status_code == 200
    assert "loadDealers" in script.text
    assert "本地知识库服务已停止" in script.text


def test_local_ui_is_hidden_outside_development(monkeypatch):
    monkeypatch.setattr(local_ui.settings, "app_env", "production")

    response = TestClient(app).get("/ui")

    assert response.status_code == 404


async def test_local_dealer_list_includes_asset_count(monkeypatch):
    dealer_id = uuid4()

    async def fetch_all(*_args, **_kwargs):
        return [{
            "id": dealer_id,
            "official_name": "VMG Communication and Technology Joint Stock Company",
            "country_code": "VN",
            "city": None,
            "asset_count": 52,
        }]

    monkeypatch.setattr(local_ui.db, "fetch_all", fetch_all)
    response = TestClient(app).get("/ui/api/dealers")

    assert response.status_code == 200
    assert response.json()["items"][0]["asset_count"] == 52


async def test_local_search_is_dealer_scoped_and_cited(monkeypatch):
    dealer_id = uuid4()
    captured = {}

    async def list_dealers(ids):
        assert ids == [dealer_id]
        return [{
            "id": dealer_id,
            "official_name": "VMG Communication and Technology Joint Stock Company",
            "status": "active",
        }]

    async def search_knowledge(query, **kwargs):
        captured.update(query=query, **kwargs)
        return [{
            "text": "VERTU event",
            "category": "media",
            "sensitivity": "confidential",
            "score": 0.01,
            "semantic_similarity": 0.8,
            "lexical_score": None,
            "citation": {"original_name": "event.jpg", "version_number": 1},
        }]

    monkeypatch.setattr(local_ui.dealers, "list_dealers", list_dealers)
    monkeypatch.setattr(local_ui, "search_knowledge", search_knowledge)

    response = TestClient(app).post(
        "/ui/api/search",
        json={"query": "VMG 发布会", "dealer_id": str(dealer_id), "category": "media"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["citation"]["original_name"] == "event.jpg"
    assert captured["dealer_ids"] == [dealer_id]
    assert captured["dealer_id"] == dealer_id
    assert captured["actor_id"] == "local-pilot-ui"


def test_local_image_content_serves_managed_searchable_asset(monkeypatch, tmp_path):
    asset_id = uuid4()
    image = tmp_path / "event.jpg"
    image.write_bytes(b"test-image")

    async def fetch_one(*_args, **_kwargs):
        return {
            "status": "searchable",
            "bucket": "local-inbox",
            "object_key": "development/dealer/test/original/local/event.jpg",
            "content_type": "image/jpeg",
        }

    class Storage:
        def get_file_path(self, _key):
            return image

    monkeypatch.setattr(local_ui.db, "fetch_one", fetch_one)
    monkeypatch.setattr(local_ui, "LocalStorage", Storage)

    response = TestClient(app).get(f"/ui/api/assets/{asset_id}/content")

    assert response.status_code == 200
    assert response.content == b"test-image"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=300"


def test_local_image_content_rejects_non_image(monkeypatch):
    async def fetch_one(*_args, **_kwargs):
        return {
            "status": "searchable",
            "bucket": "local-inbox",
            "object_key": "development/dealer/test/original/local/report.pdf",
            "content_type": "application/pdf",
        }

    monkeypatch.setattr(local_ui.db, "fetch_one", fetch_one)

    response = TestClient(app).get(f"/ui/api/assets/{uuid4()}/content")

    assert response.status_code == 404
