from uuid import UUID, uuid4

from app.dedupe import find_duplicate_candidates
from app.models import Customer


def _customer(id_int: int, legal_name: str, email: str | None, country: str | None) -> Customer:
    return Customer(
        id=UUID(int=id_int),
        tenant_id="t",
        natural_key=str(id_int),
        legal_name=legal_name,
        email=email,
        country=country,
        content_hash="h",
        source_batch_id=uuid4(),
    )


def test_find_duplicate_candidates_catches_same_email_near_duplicate_name():
    customers = [
        _customer(1, "Whitfield & Sons", "contact@whitfield.example", "US"),
        _customer(2, "Whitfield and Sons Ltd", "contact@whitfield.example", "US"),
        _customer(3, "Totally Unrelated Corp", "other@unrelated.example", "FR"),
    ]

    candidates = find_duplicate_candidates(customers, threshold=0.5)

    pairs = {frozenset({c.customer_id_a, c.customer_id_b}) for c in candidates}
    assert frozenset({str(customers[0].id), str(customers[1].id)}) in pairs
    assert not any(str(customers[2].id) in pair for pair in pairs)


def test_find_duplicate_candidates_returns_nothing_for_fewer_than_two_customers():
    assert find_duplicate_candidates([], threshold=0.5) == []
    assert find_duplicate_candidates([_customer(1, "Solo Corp", None, None)], threshold=0.5) == []


def test_find_duplicate_candidates_handles_null_email_and_country_gracefully():
    """Splink comparisons must not blow up on the Customer model's
    genuinely-nullable email/country fields."""
    customers = [
        _customer(1, "Acme Corp", None, None),
        _customer(2, "Acme Corporation", None, None),
    ]
    candidates = find_duplicate_candidates(customers, threshold=0.0)
    assert isinstance(candidates, list)  # doesn't raise
