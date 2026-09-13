"""Deterministic column -> canonical-field mapping scorer.

Combines three signals into a single confidence score per (source
column, canonical field) pair:

1. Name similarity (rapidfuzz) against the field's name + aliases.
2. Embedding cosine similarity (fastembed, local ONNX model) between the
   column's name+samples and the field's description.
3. A type/format signal read straight off the column's profile (app.profiling).

No LLM calls happen here and no MappingSpec is persisted — the LLM
tie-breaker and mapping-spec versioning are Day 3.
"""

import re
from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding
from pydantic import BaseModel
from rapidfuzz import fuzz

from app.canonical import CANONICAL_FIELDS, CanonicalField, FieldType
from app.profiling import ColumnStats

_NAME_WEIGHT = 0.45
_EMBEDDING_WEIGHT = 0.35
_TYPE_WEIGHT = 0.20

_AUTO_ACCEPT_THRESHOLD = 0.85
_HUMAN_CONFIRM_THRESHOLD = 0.55

_SOURCE_KIND_TO_TABLES: dict[str, list[str]] = {
    "crm": ["customers"],
    "billing": ["invoices"],
    "support": ["support_tickets"],
}

# account_number is a legitimately cross-cutting field — crm, billing, and
# support sources can all reference it. Including the *whole* accounts
# table per source_kind (an earlier version of this fix) was too broad: it
# also pulled in account_status/account_opened_at, which collide with
# support_tickets' near-identical status/opened_at fields and made the
# scorer worse on those columns, not better. Only the one field that's
# actually needed is added here.
_CROSS_CUTTING_FIELD_NAMES = {"account_number"}

_EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"


class MappingCandidate(BaseModel):
    canonical_field: str
    confidence: float
    name_score: float
    embedding_score: float
    type_score: float


class ColumnMapping(BaseModel):
    column_name: str
    candidates: list[MappingCandidate]
    best_field: str | None
    bucket: str


@lru_cache(maxsize=1)
def _embedding_model() -> TextEmbedding:
    return TextEmbedding(model_name=_EMBEDDING_MODEL_NAME)


def _embed(texts: list[str]) -> list[list[float]]:
    model = _embedding_model()
    return [list(vector) for vector in model.embed(texts)]


@lru_cache(maxsize=1)
def _field_embeddings() -> dict[str, list[float]]:
    """Canonical field description embeddings, computed once per process
    and cached — never recomputed per column or per request."""
    names = [field.name for field in CANONICAL_FIELDS]
    descriptions = [field.description for field in CANONICAL_FIELDS]
    vectors = _embed(descriptions)
    return dict(zip(names, vectors))


def _normalize_column_name(name: str) -> str:
    split_camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return split_camel.replace("_", " ").replace("-", " ").lower().strip()


def name_similarity(column_name: str, field: CanonicalField) -> float:
    normalized_column = _normalize_column_name(column_name)
    candidate_names = [field.name, *field.aliases]
    best_ratio = max(
        fuzz.token_set_ratio(normalized_column, _normalize_column_name(candidate))
        for candidate in candidate_names
    )
    return best_ratio / 100.0


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    denom = float(np.linalg.norm(a_arr) * np.linalg.norm(b_arr))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / denom)


def _describe_column(column_name: str, stats: ColumnStats) -> str:
    samples = ", ".join(stats.sample_values[:5])
    return f"{column_name}: sample values are {samples}"


def type_format_score(stats: ColumnStats, field_type: FieldType) -> float:
    if field_type == FieldType.EMAIL:
        return stats.email_match_fraction
    if field_type == FieldType.PHONE:
        return stats.phone_match_fraction
    if field_type in (FieldType.DATE, FieldType.DATETIME):
        return stats.date_parse_fraction
    if field_type == FieldType.INTEGER:
        return stats.int_parse_fraction
    if field_type == FieldType.MONEY_MINOR_UNITS:
        return stats.float_parse_fraction
    if field_type == FieldType.CURRENCY_CODE:
        return stats.currency_code_match_fraction
    if field_type == FieldType.ENUM:
        non_null = stats.row_count - stats.null_count
        if non_null == 0:
            return 0.5
        return max(0.0, 1.0 - min(stats.distinct_fraction, 1.0))
    return 0.5  # STRING: no strong type signal either way


def candidate_pool(source_kind: str | None) -> list[CanonicalField]:
    tables = _SOURCE_KIND_TO_TABLES.get(source_kind) if source_kind else None
    if tables is None:
        return CANONICAL_FIELDS
    return [
        field
        for field in CANONICAL_FIELDS
        if field.target_table in tables or field.name in _CROSS_CUTTING_FIELD_NAMES
    ]


def bucket_for(confidence: float) -> str:
    if confidence >= _AUTO_ACCEPT_THRESHOLD:
        return "auto_accept"
    if confidence >= _HUMAN_CONFIRM_THRESHOLD:
        return "human_confirm"
    return "unmapped"


def score_column(
    column_name: str, stats: ColumnStats, source_kind: str | None
) -> ColumnMapping:
    pool = candidate_pool(source_kind)
    field_vectors = _field_embeddings()
    column_embedding = _embed([_describe_column(column_name, stats)])[0]

    candidates: list[MappingCandidate] = []
    for field in pool:
        name_score = name_similarity(column_name, field)
        embedding_score = _cosine_similarity(column_embedding, field_vectors[field.name])
        type_score = type_format_score(stats, field.type)
        confidence = (
            _NAME_WEIGHT * name_score
            + _EMBEDDING_WEIGHT * embedding_score
            + _TYPE_WEIGHT * type_score
        )
        candidates.append(
            MappingCandidate(
                canonical_field=field.name,
                confidence=round(confidence, 4),
                name_score=round(name_score, 4),
                embedding_score=round(embedding_score, 4),
                type_score=round(type_score, 4),
            )
        )

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    best = candidates[0] if candidates else None
    return ColumnMapping(
        column_name=column_name,
        candidates=candidates,
        best_field=best.canonical_field if best else None,
        bucket=bucket_for(best.confidence) if best else "unmapped",
    )


def score_all_columns(
    stats_by_column: dict[str, ColumnStats], source_kind: str | None
) -> list[ColumnMapping]:
    return [
        score_column(name, stats, source_kind) for name, stats in stats_by_column.items()
    ]
