from app.canonical import CANONICAL_FIELDS, get_field
from app.profiling import ColumnStats
from app.scoring import ColumnMapping, bucket_for, name_similarity, score_column, type_format_score


def _stats(column_name: str = "col", **overrides) -> ColumnStats:
    defaults = dict(
        column_name=column_name,
        row_count=10,
        null_count=0,
        null_fraction=0.0,
        distinct_count=10,
        distinct_fraction=1.0,
        sample_values=["a", "b", "c"],
        date_parse_fraction=0.0,
        int_parse_fraction=0.0,
        float_parse_fraction=0.0,
        email_match_fraction=0.0,
        phone_match_fraction=0.0,
        currency_code_match_fraction=0.0,
    )
    defaults.update(overrides)
    return ColumnStats(**defaults)


def test_name_similarity_exact_alias_match_is_perfect():
    field = get_field("customer_email")
    assert name_similarity("email", field) == 1.0


def test_name_similarity_camel_case_alias_match_is_high():
    field = get_field("customer_natural_key")  # aliases include "customer_id"
    assert name_similarity("CustID", field) >= 0.7


def test_name_similarity_unrelated_name_is_low():
    field = get_field("customer_email")
    assert name_similarity("first_name", field) < 0.5


def test_type_format_score_email_uses_email_fraction():
    field = get_field("customer_email")
    stats = _stats(email_match_fraction=0.9)
    assert type_format_score(stats, field.type) == 0.9


def test_type_format_score_date_uses_date_fraction():
    field = get_field("invoice_issue_date")
    stats = _stats(date_parse_fraction=0.8)
    assert type_format_score(stats, field.type) == 0.8


def test_type_format_score_enum_rewards_low_cardinality():
    field = get_field("risk_tier")
    stats = _stats(distinct_fraction=0.03)
    assert type_format_score(stats, field.type) > 0.9


def test_type_format_score_string_is_neutral():
    field = get_field("legal_name")
    stats = _stats()
    assert type_format_score(stats, field.type) == 0.5


def test_bucket_for_thresholds():
    assert bucket_for(0.9) == "auto_accept"
    assert bucket_for(0.85) == "auto_accept"
    assert bucket_for(0.6) == "human_confirm"
    assert bucket_for(0.55) == "human_confirm"
    assert bucket_for(0.2) == "unmapped"


def test_score_column_ranks_exact_alias_match_highest_with_stubbed_embeddings(monkeypatch):
    """Stub the embedding calls so this test is deterministic and doesn't
    need the real fastembed model to be downloaded. Each canonical field
    gets a distinct one-hot vector; the column's embedding is stubbed to
    exactly match customer_email's vector, so embedding_score is 1.0 for
    customer_email and 0.0 for every other field."""
    import app.scoring as scoring_module

    def fake_field_embeddings():
        n = len(CANONICAL_FIELDS)
        vectors = {}
        for i, field in enumerate(CANONICAL_FIELDS):
            vec = [0.0] * n
            vec[i] = 1.0
            vectors[field.name] = vec
        return vectors

    fake_vectors = fake_field_embeddings()

    def fake_embed(texts):
        return [fake_vectors["customer_email"] for _ in texts]

    monkeypatch.setattr(scoring_module, "_field_embeddings", fake_field_embeddings)
    monkeypatch.setattr(scoring_module, "_embed", fake_embed)

    stats = _stats(column_name="email", email_match_fraction=1.0)
    mapping = score_column("email", stats, source_kind="crm")

    assert isinstance(mapping, ColumnMapping)
    assert mapping.best_field == "customer_email"
    assert mapping.bucket == "auto_accept"
    assert mapping.candidates == sorted(
        mapping.candidates, key=lambda c: c.confidence, reverse=True
    )


def test_name_similarity_handles_all_caps_column_name():
    """Regression test: the old camelCase-split regex (?<!^)(?=[A-Z]) split
    every uppercase letter, so "PHONE" became "p h o n e" (5 single-letter
    tokens) instead of staying one word — badly hurting the fuzzy match
    against legacy all-caps headers like crm_legacy.csv's PHONE, DISPNAME,
    DT_CREATE."""
    field = get_field("customer_phone")
    assert name_similarity("PHONE", field) == 1.0


def test_candidate_pool_includes_accounts_for_billing_and_support():
    """Regression test: account_number is an accounts-table field, but the
    billing/support candidate pools excluded the accounts table entirely —
    meaning billing.csv's and support.csv's account_number columns could
    never be mapped correctly by any signal, since the right field wasn't
    even a candidate."""
    from app.scoring import candidate_pool

    billing_names = {f.name for f in candidate_pool("billing")}
    support_names = {f.name for f in candidate_pool("support")}
    assert "account_number" in billing_names
    assert "account_number" in support_names
