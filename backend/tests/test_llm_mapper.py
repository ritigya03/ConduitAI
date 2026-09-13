import pytest
from pydantic import ValidationError

from app.canonical import get_field
from app.llm_mapper import _build_decision_model, _build_user_prompt, tiebreak
from app.profiling import ColumnStats


def _stats(**overrides) -> ColumnStats:
    defaults = dict(
        column_name="col",
        row_count=5,
        null_count=0,
        null_fraction=0.0,
        distinct_count=5,
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


def test_build_decision_model_rejects_field_outside_candidate_list():
    model_cls = _build_decision_model(["customer_email", "customer_phone"])
    model_cls(canonical_field="customer_email", confidence=0.9, reasoning="matches")
    with pytest.raises(ValidationError):
        model_cls(canonical_field="not_a_real_field", confidence=0.9, reasoning="x")


def test_build_decision_model_allows_unknown():
    model_cls = _build_decision_model(["customer_email"])
    model_cls(canonical_field="UNKNOWN", confidence=0.1, reasoning="no match")


def test_build_user_prompt_includes_column_and_samples():
    stats = _stats(sample_values=["a@example.com", "b@example.com"])
    field = get_field("customer_email")
    prompt = _build_user_prompt("contact", stats, [field])
    assert "contact" in prompt
    assert "a@example.com" in prompt
    assert "customer_email" in prompt


def test_tiebreak_returns_none_with_no_candidates():
    assert tiebreak("col", _stats(), []) is None


def test_tiebreak_falls_back_to_ollama_when_groq_fails(monkeypatch):
    import app.llm_mapper as llm_mapper_module

    calls = []

    def fake_call_provider(*, base_url, api_key, model, response_model, user_prompt, mode=None):
        calls.append(base_url)
        if "groq" in base_url:
            raise RuntimeError("rate limited")
        return response_model(canonical_field="customer_email", confidence=0.8, reasoning="ok")

    monkeypatch.setattr(llm_mapper_module, "_call_provider", fake_call_provider)
    monkeypatch.setattr(llm_mapper_module.settings, "groq_api_key", "fake-key")

    field = get_field("customer_email")
    decision = tiebreak("contact", _stats(), [field])

    assert decision is not None
    assert decision.canonical_field == "customer_email"
    assert decision.model == f"ollama/{llm_mapper_module._OLLAMA_MODEL}"
    assert len(calls) == 2


def test_tiebreak_returns_none_when_all_providers_fail(monkeypatch):
    import app.llm_mapper as llm_mapper_module

    def fake_call_provider(**kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(llm_mapper_module, "_call_provider", fake_call_provider)
    monkeypatch.setattr(llm_mapper_module.settings, "groq_api_key", "fake-key")

    field = get_field("customer_email")
    assert tiebreak("contact", _stats(), [field]) is None


def test_tiebreak_skips_groq_when_no_api_key(monkeypatch):
    import app.llm_mapper as llm_mapper_module

    calls = []

    def fake_call_provider(*, base_url, api_key, model, response_model, user_prompt, mode=None):
        calls.append(base_url)
        return response_model(canonical_field="UNKNOWN", confidence=0.1, reasoning="none")

    monkeypatch.setattr(llm_mapper_module, "_call_provider", fake_call_provider)
    monkeypatch.setattr(llm_mapper_module.settings, "groq_api_key", None)

    field = get_field("customer_email")
    tiebreak("contact", _stats(), [field])

    assert len(calls) == 1
    assert "11434" in calls[0]
