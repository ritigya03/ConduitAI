"""LLM tie-breaker for low-confidence column mappings.

Called ONLY for columns the Day 2 deterministic scorer bucketed
"unmapped" — this keeps LLM usage to a handful of calls per file, well
under free-tier rate limits (Groq: 30 RPM / 6,000 TPM). Falls back
Groq -> local Ollama -> None (caller then keeps the deterministic best
guess) so a missing key, a rate limit, or no internet never blocks the
pipeline. Catching broad `Exception` around each provider call is
deliberate here, not sloppy — any failure mode (auth, rate limit,
timeout, malformed response) must fall through to the next provider.
"""

from typing import Literal

import instructor
from instructor import from_openai
from openai import OpenAI
from pydantic import BaseModel, create_model

from app.canonical import CanonicalField
from app.config import settings
from app.profiling import ColumnStats

# Groq's model roster changes over time — verify with
# `curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"`
# before assuming this is still live.
_GROQ_MODEL = "openai/gpt-oss-120b"
_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# llama3 (already pulled locally) doesn't support tool-calling, which is
# Instructor's default structured-output mode — Ollama calls use
# Mode.JSON instead (prompt + response_format=json), which any chat
# model can do. Groq's model does support tools, so it uses Instructor's
# default mode.
_OLLAMA_MODEL = "llama3"

_SYSTEM_PROMPT = """You map messy source-data column names to a canonical field schema. You will be given a source column's name, a few sample values, and a list of candidate canonical fields with descriptions. Pick the single best-matching canonical field, or "UNKNOWN" if none genuinely fit. Never invent a field name that isn't in the candidate list.

Example:
Column: "amt_usd". Samples: 100.00, 250.50, 99.99
Candidates:
- invoice_amount_minor_units: The invoice amount, stored as an integer count of the currency's minor units.
- invoice_tax_amount_minor_units: The tax amount charged on this invoice.
Answer: canonical_field="invoice_amount_minor_units", confidence=0.9, reasoning="amt_usd reads as the primary charge, not the tax line."
"""


class LLMDecision(BaseModel):
    canonical_field: str
    confidence: float
    reasoning: str
    model: str


def _build_decision_model(candidate_names: list[str]) -> type[BaseModel]:
    allowed = tuple(candidate_names) + ("UNKNOWN",)
    return create_model(
        "MappingDecision",
        canonical_field=(Literal[allowed], ...),
        confidence=(float, ...),
        reasoning=(str, ...),
    )


def _build_user_prompt(
    column_name: str, stats: ColumnStats, candidates: list[CanonicalField]
) -> str:
    samples = ", ".join(stats.sample_values[:5]) or "(no non-null samples)"
    candidate_lines = "\n".join(
        f"- {field.name}: {field.description}" for field in candidates
    )
    return (
        f'Column: "{column_name}". Samples: {samples}\n'
        f"Candidates:\n{candidate_lines}\n"
        "Which canonical field does this column map to?"
    )


def _call_provider(
    *,
    base_url: str,
    api_key: str,
    model: str,
    response_model: type[BaseModel],
    user_prompt: str,
    mode: instructor.Mode | None = None,
) -> BaseModel:
    client = from_openai(
        OpenAI(base_url=base_url, api_key=api_key), **({"mode": mode} if mode else {})
    )
    return client.chat.completions.create(
        model=model,
        response_model=response_model,
        max_retries=2,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )


def tiebreak(
    column_name: str, stats: ColumnStats, candidates: list[CanonicalField]
) -> LLMDecision | None:
    if not candidates:
        return None

    response_model = _build_decision_model([f.name for f in candidates])
    user_prompt = _build_user_prompt(column_name, stats, candidates)

    if settings.groq_api_key:
        try:
            result = _call_provider(
                base_url=_GROQ_BASE_URL,
                api_key=settings.groq_api_key,
                model=_GROQ_MODEL,
                response_model=response_model,
                user_prompt=user_prompt,
            )
            return LLMDecision(
                canonical_field=result.canonical_field,
                confidence=result.confidence,
                reasoning=result.reasoning,
                model=f"groq/{_GROQ_MODEL}",
            )
        except Exception:
            pass  # fall through to Ollama

    try:
        result = _call_provider(
            base_url=settings.ollama_base_url,
            api_key="ollama",  # Ollama ignores the key but the OpenAI client requires one
            model=_OLLAMA_MODEL,
            response_model=response_model,
            user_prompt=user_prompt,
            mode=instructor.Mode.JSON,
        )
        return LLMDecision(
            canonical_field=result.canonical_field,
            confidence=result.confidence,
            reasoning=result.reasoning,
            model=f"ollama/{_OLLAMA_MODEL}",
        )
    except Exception:
        return None
