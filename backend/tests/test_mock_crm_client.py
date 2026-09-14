import httpx
import pytest

from app.mock_crm_client import MockCrmError, fetch_all_customers


def test_fetch_all_customers_paginates_until_exhausted(monkeypatch):
    monkeypatch.setattr("app.mock_crm_client.settings.mock_crm_api_token", "test-token")

    pages = {
        1: {"items": [{"CustID": "A"}, {"CustID": "B"}], "page": 1, "page_size": 2, "total": 3, "has_next": True},
        2: {"items": [{"CustID": "C"}], "page": 2, "page_size": 2, "total": 3, "has_next": False},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json=pages[page])

    rows = fetch_all_customers(page_size=2, transport=httpx.MockTransport(handler))
    assert [r["CustID"] for r in rows] == ["A", "B", "C"]


def test_fetch_all_customers_retries_transient_503_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.mock_crm_client.settings.mock_crm_api_token", "test-token")

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] < 3:
            return httpx.Response(503, json={"detail": "overloaded"})
        return httpx.Response(
            200, json={"items": [{"CustID": "A"}], "page": 1, "page_size": 20, "total": 1, "has_next": False}
        )

    rows = fetch_all_customers(transport=httpx.MockTransport(handler))
    assert rows == [{"CustID": "A"}]
    assert call_count["n"] == 3


def test_fetch_all_customers_401_raises_immediately_without_retry(monkeypatch):
    monkeypatch.setattr("app.mock_crm_client.settings.mock_crm_api_token", "wrong-token")

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(401, json={"detail": "unauthorized"})

    with pytest.raises(MockCrmError):
        fetch_all_customers(transport=httpx.MockTransport(handler))
    assert call_count["n"] == 1  # not retried


def test_fetch_all_customers_gives_up_after_max_retries_on_persistent_503(monkeypatch):
    monkeypatch.setattr("app.mock_crm_client.settings.mock_crm_api_token", "test-token")

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(503, json={"detail": "overloaded"})

    with pytest.raises(MockCrmError):
        fetch_all_customers(transport=httpx.MockTransport(handler))
    assert call_count["n"] == 4  # stop_after_attempt(4)
