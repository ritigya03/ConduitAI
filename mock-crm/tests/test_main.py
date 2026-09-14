from fastapi.testclient import TestClient

from app.main import API_TOKEN, app

client = TestClient(app)


def test_customers_requires_auth():
    response = client.get("/customers")
    assert response.status_code == 401


def test_customers_rejects_wrong_token():
    response = client.get("/customers", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_customers_returns_first_page():
    response = client.get(
        "/customers", params={"page": 1, "page_size": 10}, headers={"Authorization": f"Bearer {API_TOKEN}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 10
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert body["total"] == 25
    assert body["has_next"] is True
    assert "CustID" in body["items"][0]


def test_customers_last_page_has_no_next():
    response = client.get(
        "/customers", params={"page": 3, "page_size": 10}, headers={"Authorization": f"Bearer {API_TOKEN}"}
    )
    body = response.json()
    assert len(body["items"]) == 5  # 25 total, page 3 of size 10
    assert body["has_next"] is False


def test_customers_page_past_the_end_returns_empty_items():
    response = client.get(
        "/customers", params={"page": 99, "page_size": 10}, headers={"Authorization": f"Bearer {API_TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["has_next"] is False


def test_health():
    assert client.get("/health").json() == {"status": "ok"}
