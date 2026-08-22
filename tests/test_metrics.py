from fastapi.testclient import TestClient

from app.api.main import app


def test_metrics_use_route_templates_not_asset_ids():
    client = TestClient(app)
    client.get("/health/live")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert 'route="/health/live",status="200"' in response.text
    assert "data_hub_http_requests_in_flight" in response.text
