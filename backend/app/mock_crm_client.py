"""Paginated client for the mock-CRM service, with exponential-backoff
retry on transient failures (503, timeout, connection error) -- the
mock service is deliberately flaky (see mock-crm/app/main.py) so this
retry logic is exercised for real on most runs, not just in tests.

`transport` is accepted as an explicit, optional parameter (rather than
monkeypatching `httpx.Client` in tests) so tests can inject an
`httpx.MockTransport` directly -- a cleaner seam than patching a
third-party class.
"""

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings


class MockCrmError(Exception):
    """The mock CRM couldn't be reached or returned an unexpected response."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 503)
    return False


# retry_if_exception (a predicate), not retry_if_exception_type -- a
# plain type check would also retry a permanent 4xx that
# raise_for_status() turns into the same HTTPStatusError class, wasting
# attempts on something that will never succeed.
@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=0.3, min=0.3, max=3),
    reraise=True,
)
def _get_page(client: httpx.Client, page: int, page_size: int) -> dict:
    response = client.get(
        "/customers",
        params={"page": page, "page_size": page_size},
        headers={"Authorization": f"Bearer {settings.mock_crm_api_token}"},
    )
    if response.status_code == 401:
        # Not retryable -- a bad token will never succeed on retry.
        raise MockCrmError(f"Mock CRM rejected the request: 401 {response.text}")
    response.raise_for_status()  # raises HTTPStatusError for 5xx -- retried by the decorator above
    return response.json()


def fetch_all_customers(page_size: int = 20, transport: httpx.BaseTransport | None = None) -> list[dict]:
    """Pages through /customers until has_next is False, returning every
    row. Raises MockCrmError (not retried further) if the service is
    unreachable or rejects auth outright."""
    rows: list[dict] = []
    try:
        with httpx.Client(base_url=settings.mock_crm_base_url, timeout=5.0, transport=transport) as client:
            page = 1
            while True:
                body = _get_page(client, page, page_size)
                rows.extend(body["items"])
                if not body["has_next"]:
                    break
                page += 1
    except httpx.TransportError as exc:
        raise MockCrmError(f"Could not reach mock CRM at {settings.mock_crm_base_url}: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise MockCrmError(f"Mock CRM returned {exc.response.status_code} after retries: {exc}") from exc
    return rows
