# Day 2: Profiling + Deterministic Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given an already-uploaded batch (Day 1's `raw_records`), compute a per-column profile (nulls, cardinality, format signals, samples) with Polars, render a `ydata-profiling` HTML artifact, and score every source column against the canonical field registry using a deterministic hybrid of rapidfuzz name-similarity, local `fastembed` embedding similarity, and type/format signals — so `POST /profile` returns, for a batch, a ranked list of canonical-field candidates with confidence per column.

**Architecture:** Three independent, unit-testable modules (`app/profiling.py`, `app/report.py`, `app/scoring.py`) feed a thin `POST /profile` router that orchestrates them and persists results into the existing `column_profiles` table. Profiling and scoring have no I/O dependencies beyond Postgres reads done by the router — they take in-memory data and return Pydantic models, so they're tested without a DB or network call wherever possible.

**Tech Stack:** Polars (profiling/stats/type-inference engine — chosen over pandas because its expression API gives an honest, batched cast-success rate per column instead of pandas silently boxing everything as `object` dtype), pandas (used *only* as a `.to_pandas()` bridge into `ydata-profiling`, which requires a pandas DataFrame), `ydata-profiling` (HTML EDA artifact), `rapidfuzz` (name similarity), `fastembed` (local ONNX embeddings, `BAAI/bge-small-en-v1.5`), `numpy` (cosine similarity).

**Spec:** `compass_artifact_wf-530f5831-2136-5098-9562-95fd65678455_text_markdown.md` (repo root) — see §2 stage 2 (profiling), §3 "Hybrid mapping scorer" (the three-signal formula + confidence thresholds), §6 Day 2 (scope) and Day 3 (explicit boundary: LLM tie-breaker, mapping-spec versioning, and the benchmark eval script are **not** part of this plan). Builds directly on `docs/superpowers/plans/2026-09-13-day1-foundations-and-data.md`'s `app/canonical.py` registry and `app/models.py` schema (`ColumnProfile`, `OnboardingBatch`, `RawRecord`, `Source` — all already exist, no migration needed).

## Global Constraints

- **No LLM calls, no `MappingSpec` persistence, no benchmark eval script in this plan** — those are Day 3. This plan only *proposes* a mapping in the `POST /profile` response; it does not save it as a spec.
- **Polars, not DuckDB, for Day 2.** Raw rows are already-parsed JSONB sitting in Postgres — there's no SQL-over-files need yet. DuckDB is deferred to Day 6, where the mock-CRM's JSON responses and Excel-file uploads genuinely need `read_json`/`read_csv(AUTO_DETECT=TRUE)` sniffing and streaming.
- **Pandas appears in exactly one place**: `app/report.py`'s `.to_pandas()` bridge into `ydata-profiling`. No other module imports pandas.
- **Confidence formula** (tune-able but start here): `confidence = 0.45 * name_score + 0.35 * embedding_score + 0.20 * type_score`. **Thresholds:** `>= 0.85` → `auto_accept`, `0.55–0.85` → `human_confirm`, `< 0.55` → `unmapped`.
- **Candidate pool per column is restricted by `Source.kind`** (`crm → customers, accounts`; `billing → invoices`; `support → support_tickets`) to cut cross-table noise — a column is only scored against canonical fields whose `target_table` is relevant to the batch's source.
- **Canonical field embeddings are computed once and cached** (`functools.lru_cache`) — never recomputed per column or per request. Only the source column's own embedding is computed fresh each time (it depends on that column's sample values).
- `fastembed`'s model runs **locally** — no external embedding API, no per-token cost, no network calls at request time (only once, to download the ONNX model weights on first use — this needs network access the first time any test or request touches `app.scoring`, then it's cached on disk).
- `uv` remains the only dependency manager — every new library is added to `backend/pyproject.toml`, never installed ad hoc.
- Every DB-touching test uses the existing `unique_tenant_id` fixture (`backend/tests/conftest.py`) — no hardcoded tenant strings.
- `ydata-profiling` reports are generated with `minimal=True` — keeps report generation fast enough to run inside the test suite (the full report adds expensive correlation/interaction computations this project doesn't need for a demo artifact).

---

## Task 1: Column profiler (Polars)

**Files:**
- Create: `backend/app/profiling.py`
- Test: `backend/tests/test_profiling.py`
- Modify: `backend/pyproject.toml` (add `polars`)

**Interfaces:**
- Consumes: nothing (pure functions over plain Python data).
- Produces: `app.profiling.ColumnStats` (Pydantic `BaseModel`: `column_name: str`, `row_count: int`, `null_count: int`, `null_fraction: float`, `distinct_count: int`, `distinct_fraction: float`, `sample_values: list[str]`, `date_parse_fraction: float`, `int_parse_fraction: float`, `float_parse_fraction: float`, `email_match_fraction: float`, `phone_match_fraction: float`, `currency_code_match_fraction: float`), `app.profiling.build_dataframe(rows: list[dict]) -> pl.DataFrame`, `app.profiling.profile_column(df: pl.DataFrame, column_name: str) -> ColumnStats`, `app.profiling.profile_all_columns(df: pl.DataFrame) -> list[ColumnStats]`.

- [ ] **Step 1: Add `polars` to `backend/pyproject.toml`**

Add to the `dependencies` list (keep alphabetical-ish grouping consistent with the existing file):

```toml
    "polars>=1.9.0",
```

```bash
cd backend && uv sync
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_profiling.py
from app.profiling import ColumnStats, build_dataframe, profile_all_columns, profile_column


def test_build_dataframe_from_raw_rows():
    rows = [
        {"customer_id": "C-1", "email": "a@example.com"},
        {"customer_id": "C-2", "email": "b@example.com"},
    ]
    df = build_dataframe(rows)
    assert df.columns == ["customer_id", "email"]
    assert df.height == 2


def test_profile_column_computes_null_and_distinct_stats():
    rows = [{"col": "a"}, {"col": "a"}, {"col": "b"}, {"col": None}]
    df = build_dataframe(rows)
    stats = profile_column(df, "col")

    assert isinstance(stats, ColumnStats)
    assert stats.row_count == 4
    assert stats.null_count == 1
    assert stats.null_fraction == 0.25
    assert stats.distinct_count == 2  # "a", "b" among non-null values
    assert stats.distinct_fraction == 2 / 3


def test_profile_column_detects_email_format():
    rows = [
        {"contact": "a@example.com"},
        {"contact": "b@example.com"},
        {"contact": "not-an-email"},
    ]
    df = build_dataframe(rows)
    stats = profile_column(df, "contact")

    assert stats.email_match_fraction == 2 / 3


def test_profile_column_detects_date_format():
    rows = [{"d": "2026-01-01"}, {"d": "2026-02-15"}, {"d": "not-a-date"}]
    df = build_dataframe(rows)
    stats = profile_column(df, "d")

    assert stats.date_parse_fraction == 2 / 3


def test_profile_column_detects_integer_format():
    rows = [{"n": "10"}, {"n": "20"}, {"n": "abc"}]
    df = build_dataframe(rows)
    stats = profile_column(df, "n")

    assert stats.int_parse_fraction == 2 / 3


def test_profile_column_handles_all_null_column():
    rows = [{"n": None}, {"n": None}]
    df = build_dataframe(rows)
    stats = profile_column(df, "n")

    assert stats.null_fraction == 1.0
    assert stats.distinct_count == 0
    assert stats.distinct_fraction == 0.0
    assert stats.date_parse_fraction == 0.0


def test_profile_all_columns_covers_every_column():
    rows = [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]
    df = build_dataframe(rows)
    stats_list = profile_all_columns(df)

    assert {s.column_name for s in stats_list} == {"a", "b"}
```

- [ ] **Step 3: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_profiling.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.profiling'`.

- [ ] **Step 4: Write `backend/app/profiling.py`**

```python
"""Column profiler.

Given raw JSONB rows for a batch, computes per-column statistics using
Polars: null fraction, cardinality, sample values, and type/format
parse-success rates. Polars is used here (not pandas) because its
expression API gives an honest, explicit parse-success rate per column
(attempt a cast, count what didn't come back null) instead of pandas
silently boxing every string column as `object` dtype. These stats feed
both the deterministic mapping scorer (app.scoring) and the
ydata-profiling HTML report (app.report) — this module does no scoring
and no report rendering itself.
"""

import polars as pl
from pydantic import BaseModel

_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
_PHONE_PATTERN = r"^[\d\-\+\(\)\s]{7,20}$"
_CURRENCY_CODE_PATTERN = r"^[A-Za-z]{3}$"


class ColumnStats(BaseModel):
    column_name: str
    row_count: int
    null_count: int
    null_fraction: float
    distinct_count: int
    distinct_fraction: float
    sample_values: list[str]
    date_parse_fraction: float
    int_parse_fraction: float
    float_parse_fraction: float
    email_match_fraction: float
    phone_match_fraction: float
    currency_code_match_fraction: float


def build_dataframe(rows: list[dict]) -> pl.DataFrame:
    """`rows` are `RawRecord.raw_json` dicts — string keys to string-or-None
    values, since they came from `csv.DictReader`. `strict=False` tolerates
    rows with missing keys (a malformed source row with fewer fields than
    its header) by filling nulls instead of raising."""
    return pl.DataFrame(rows, infer_schema_length=None, strict=False)


def _match_fraction(series: pl.Series, pattern: str, non_null: int) -> float:
    if non_null == 0:
        return 0.0
    matches = series.drop_nulls().str.contains(pattern).sum()
    return matches / non_null


def _cast_fraction(series: pl.Series, dtype: pl.DataType, non_null: int) -> float:
    if non_null == 0:
        return 0.0
    non_null_series = series.drop_nulls()
    try:
        casted = non_null_series.cast(dtype, strict=False)
    except pl.exceptions.PolarsError:
        return 0.0
    return casted.drop_nulls().len() / non_null


def _date_parse_fraction(series: pl.Series, non_null: int) -> float:
    if non_null == 0:
        return 0.0
    non_null_series = series.drop_nulls()
    try:
        parsed = non_null_series.str.to_date(strict=False)
    except pl.exceptions.PolarsError:
        return 0.0
    return parsed.drop_nulls().len() / non_null


def profile_column(df: pl.DataFrame, column_name: str) -> ColumnStats:
    series = df[column_name]
    row_count = series.len()
    null_count = series.null_count()
    non_null = row_count - null_count
    non_null_series = series.drop_nulls()
    distinct_count = non_null_series.n_unique()
    samples = non_null_series.unique().head(5).to_list()

    return ColumnStats(
        column_name=column_name,
        row_count=row_count,
        null_count=null_count,
        null_fraction=null_count / row_count if row_count else 0.0,
        distinct_count=distinct_count,
        distinct_fraction=distinct_count / non_null if non_null else 0.0,
        sample_values=[str(v) for v in samples],
        date_parse_fraction=_date_parse_fraction(series, non_null),
        int_parse_fraction=_cast_fraction(series, pl.Int64, non_null),
        float_parse_fraction=_cast_fraction(series, pl.Float64, non_null),
        email_match_fraction=_match_fraction(series, _EMAIL_PATTERN, non_null),
        phone_match_fraction=_match_fraction(series, _PHONE_PATTERN, non_null),
        currency_code_match_fraction=_match_fraction(
            series, _CURRENCY_CODE_PATTERN, non_null
        ),
    )


def profile_all_columns(df: pl.DataFrame) -> list[ColumnStats]:
    return [profile_column(df, name) for name in df.columns]
```

Note: if `str.to_date(strict=False)` behaves differently on your installed Polars version (some versions raise on a column with zero parseable values instead of returning an all-null Series), the `try/except pl.exceptions.PolarsError` in `_date_parse_fraction` already covers it by falling back to `0.0` — you should not need to change the test expectations.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_profiling.py -v
```

Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/profiling.py backend/tests/test_profiling.py
git commit -m "feat: add Polars-based column profiler"
```

---

## Task 2: `ydata-profiling` HTML report artifact

**Files:**
- Create: `backend/app/report.py`
- Test: `backend/tests/test_report.py`
- Create: `backend/reports/.gitkeep`
- Modify: `backend/pyproject.toml` (add `pandas`, `ydata-profiling`)
- Modify: `docker-compose.yml` (mount `backend/reports`)
- Modify: `.gitignore` (repo root)

**Interfaces:**
- Consumes: `pl.DataFrame` (from `app.profiling.build_dataframe`, Task 1).
- Produces: `app.report.REPORTS_DIR: Path`, `app.report.generate_report(df: pl.DataFrame, batch_id: str) -> Path`.

- [ ] **Step 1: Add dependencies to `backend/pyproject.toml`**

```toml
    "pandas>=2.2.0",
    "ydata-profiling>=4.12.0,<5",
```

```bash
cd backend && uv sync
```

- [ ] **Step 2: Scaffold the reports directory**

```bash
mkdir -p backend/reports && touch backend/reports/.gitkeep
```

- [ ] **Step 3: Write the failing test**

```python
# backend/tests/test_report.py
import polars as pl

from app.report import generate_report


def test_generate_report_writes_html_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.report.REPORTS_DIR", tmp_path)
    df = pl.DataFrame(
        {
            "email": ["a@example.com", "b@example.com", None],
            "amount": ["100.00", "200.50", "300.00"],
        }
    )

    output_path = generate_report(df, batch_id="test-batch-123")

    assert output_path.exists()
    assert output_path.parent == tmp_path
    assert output_path.suffix == ".html"
    assert output_path.read_text(encoding="utf-8").strip() != ""
```

- [ ] **Step 4: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_report.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.report'`.

- [ ] **Step 5: Write `backend/app/report.py`**

```python
"""ydata-profiling HTML artifact generation.

Pandas appears in this module only — nowhere else in the project. This
is deliberate: ydata-profiling requires a pandas DataFrame, so this file
is a narrow `.to_pandas()` bridge at the edge of an otherwise all-Polars
profiling pipeline (see app.profiling's docstring for why Polars is the
engine of record).
"""

from pathlib import Path

import polars as pl
from ydata_profiling import ProfileReport

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def generate_report(df: pl.DataFrame, batch_id: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    pandas_df = df.to_pandas()
    profile = ProfileReport(
        pandas_df, title=f"Batch {batch_id} profile", minimal=True
    )
    output_path = REPORTS_DIR / f"{batch_id}.html"
    profile.to_file(output_path)
    return output_path
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/test_report.py -v
```

Expected: PASS. First run downloads no external model (ydata-profiling has no network dependency), but may take a few seconds to render even in `minimal` mode — this is normal.

- [ ] **Step 7: Mount the reports directory in Docker Compose**

Modify `docker-compose.yml` — add a volume line under the `api` service (alongside the existing `app` and `alembic` mounts):

```yaml
    volumes:
      - ./backend/app:/code/app
      - ./backend/alembic:/code/alembic
      - ./backend/reports:/code/reports
```

- [ ] **Step 8: Ignore generated reports**

Append to the repo-root `.gitignore`:

```
backend/reports/*.html
```

- [ ] **Step 9: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/report.py \
        backend/tests/test_report.py backend/reports/.gitkeep \
        docker-compose.yml .gitignore
git commit -m "feat: add ydata-profiling HTML report artifact"
```

---

## Task 3: Deterministic mapping scorer (rapidfuzz + fastembed + type signals)

**Files:**
- Create: `backend/app/scoring.py`
- Test: `backend/tests/test_scoring.py`
- Modify: `backend/pyproject.toml` (add `rapidfuzz`, `fastembed`, `numpy`)

**Interfaces:**
- Consumes: `app.canonical.CanonicalField`, `app.canonical.FieldType`, `app.canonical.CANONICAL_FIELDS` (existing), `app.profiling.ColumnStats` (Task 1).
- Produces: `app.scoring.MappingCandidate` (Pydantic `BaseModel`: `canonical_field: str`, `confidence: float`, `name_score: float`, `embedding_score: float`, `type_score: float`), `app.scoring.ColumnMapping` (Pydantic `BaseModel`: `column_name: str`, `candidates: list[MappingCandidate]`, `best_field: str | None`, `bucket: str`), `app.scoring.name_similarity(column_name: str, field: CanonicalField) -> float`, `app.scoring.type_format_score(stats: ColumnStats, field_type: FieldType) -> float`, `app.scoring.bucket_for(confidence: float) -> str`, `app.scoring.score_column(column_name: str, stats: ColumnStats, source_kind: str | None) -> ColumnMapping`, `app.scoring.score_all_columns(stats_by_column: dict[str, ColumnStats], source_kind: str | None) -> list[ColumnMapping]`.

- [ ] **Step 1: Add dependencies to `backend/pyproject.toml`**

```toml
    "rapidfuzz>=3.10.0",
    "fastembed>=0.4.0",
    "numpy>=1.26.0",
```

```bash
cd backend && uv sync
```

- [ ] **Step 2: Write the failing tests — pure scoring math (no embedding model needed)**

```python
# backend/tests/test_scoring.py
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
```

- [ ] **Step 3: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_scoring.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.scoring'`.

- [ ] **Step 4: Write `backend/app/scoring.py`**

```python
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
    "crm": ["customers", "accounts"],
    "billing": ["invoices"],
    "support": ["support_tickets"],
}

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
    split_camel = re.sub(r"(?<!^)(?=[A-Z])", " ", name)
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


def _candidate_pool(source_kind: str | None) -> list[CanonicalField]:
    tables = _SOURCE_KIND_TO_TABLES.get(source_kind) if source_kind else None
    if tables is None:
        return CANONICAL_FIELDS
    return [field for field in CANONICAL_FIELDS if field.target_table in tables]


def bucket_for(confidence: float) -> str:
    if confidence >= _AUTO_ACCEPT_THRESHOLD:
        return "auto_accept"
    if confidence >= _HUMAN_CONFIRM_THRESHOLD:
        return "human_confirm"
    return "unmapped"


def score_column(
    column_name: str, stats: ColumnStats, source_kind: str | None
) -> ColumnMapping:
    pool = _candidate_pool(source_kind)
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
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_scoring.py -v
```

Expected: PASS (9 tests). None of these need network access — the embedding calls are stubbed.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/scoring.py backend/tests/test_scoring.py
git commit -m "feat: add deterministic mapping scorer (rapidfuzz + fastembed + type signals)"
```

---

## Task 4: `POST /profile` endpoint

**Files:**
- Create: `backend/app/routers/profile.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_profile_endpoint.py`

**Interfaces:**
- Consumes: `app.db.get_session` (existing), `app.models.OnboardingBatch`, `app.models.RawRecord`, `app.models.Source`, `app.models.ColumnProfile` (existing, no migration needed), `app.profiling.build_dataframe`, `app.profiling.profile_all_columns` (Task 1), `app.report.generate_report` (Task 2), `app.scoring.score_all_columns` (Task 3).
- Produces: `POST /profile` (form: `tenant_id`, `batch_id`) → `200 {"batch_id": str, "columns": [{"column_name": str, "stats": {...}, "candidates": [...], "best_field": str | None, "bucket": str}], "report_path": str}`; `404` if the batch doesn't exist or belongs to a different tenant; `400` if `batch_id` isn't a valid UUID or the batch has no raw records.

This is the first request in the whole test suite to touch `app.scoring` for real (Task 3's tests stub the embedding model), so its first run downloads the `fastembed` ONNX model over the network — expect the *first* run of this file to be noticeably slower than subsequent runs, which use the on-disk cache.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_profile_endpoint.py
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import ColumnProfile
from app.report import REPORTS_DIR

client = TestClient(app)


def _upload_tiny_crm(tenant_id: str) -> str:
    with open("tests/fixtures/crm_tiny.csv", "rb") as f:
        content = f.read()
    response = client.post(
        "/upload",
        files={"file": ("crm_tiny.csv", content, "text/csv")},
        data={"tenant_id": tenant_id, "source_name": "crm", "source_kind": "crm"},
    )
    assert response.status_code == 201
    return response.json()["batch_id"]


def test_profile_returns_ranked_candidates_for_every_column(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)

    response = client.post(
        "/profile", data={"tenant_id": unique_tenant_id, "batch_id": batch_id}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["batch_id"] == batch_id

    columns_by_name = {c["column_name"]: c for c in body["columns"]}
    assert set(columns_by_name) == {"customer_id", "first_name", "last_name", "email"}

    for column in body["columns"]:
        assert column["candidates"], "every column gets at least one ranked candidate"
        confidences = [c["confidence"] for c in column["candidates"]]
        assert confidences == sorted(confidences, reverse=True)

    # "email" only appears as an alias on customer_email — unambiguous.
    assert columns_by_name["email"]["best_field"] == "customer_email"

    # "customer_id" is a shared alias across customer_natural_key AND
    # account_customer_natural_key (both in the crm candidate pool) — the
    # deterministic scorer can legitimately land on either; what matters
    # is it's confident, not which one it picked.
    assert columns_by_name["customer_id"]["best_field"] in {
        "customer_natural_key",
        "account_customer_natural_key",
    }
    assert columns_by_name["customer_id"]["candidates"][0]["confidence"] >= 0.55

    report_path = Path(body["report_path"])
    assert report_path.exists()
    assert report_path.parent == REPORTS_DIR


def test_profile_persists_column_profiles(unique_tenant_id):
    batch_id = _upload_tiny_crm(unique_tenant_id)
    client.post("/profile", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})

    with Session(engine) as session:
        profiles = session.exec(
            select(ColumnProfile).where(ColumnProfile.batch_id == UUID(batch_id))
        ).all()
        assert len(profiles) == 4
        assert {p.column_name for p in profiles} == {
            "customer_id",
            "first_name",
            "last_name",
            "email",
        }
        for p in profiles:
            assert "stats" in p.profile_json
            assert "mapping" in p.profile_json


def test_profile_is_idempotent_on_column_profiles(unique_tenant_id):
    """Re-profiling the same batch updates existing rows instead of
    duplicating them."""
    batch_id = _upload_tiny_crm(unique_tenant_id)
    client.post("/profile", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})
    client.post("/profile", data={"tenant_id": unique_tenant_id, "batch_id": batch_id})

    with Session(engine) as session:
        profiles = session.exec(
            select(ColumnProfile).where(ColumnProfile.batch_id == UUID(batch_id))
        ).all()
        assert len(profiles) == 4


def test_profile_unknown_batch_returns_404(unique_tenant_id):
    response = client.post(
        "/profile",
        data={
            "tenant_id": unique_tenant_id,
            "batch_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 404


def test_profile_invalid_batch_id_returns_400(unique_tenant_id):
    response = client.post(
        "/profile", data={"tenant_id": unique_tenant_id, "batch_id": "not-a-uuid"}
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_profile_endpoint.py -v
```

Expected: FAIL — `404` on `/profile` (route doesn't exist yet).

- [ ] **Step 3: Write `backend/app/routers/profile.py`**

```python
"""POST /profile — column profiling + deterministic mapping proposal.

Orchestrates app.profiling, app.report, and app.scoring against an
already-uploaded batch's raw_records. No LLM calls, no MappingSpec
persisted here — see app.scoring's module docstring for why.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.models import ColumnProfile, OnboardingBatch, RawRecord, Source
from app.profiling import build_dataframe, profile_all_columns
from app.report import generate_report
from app.scoring import score_all_columns

router = APIRouter()


@router.post("/profile")
def profile_batch(
    tenant_id: str = Form(...),
    batch_id: str = Form(...),
    session: Session = Depends(get_session),
):
    try:
        batch_uuid = UUID(batch_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="batch_id must be a valid UUID")

    batch = session.get(OnboardingBatch, batch_uuid)
    if batch is None or batch.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Batch not found")

    raw_rows = session.exec(
        select(RawRecord).where(RawRecord.batch_id == batch.id)
    ).all()
    if not raw_rows:
        raise HTTPException(status_code=400, detail="Batch has no raw records")

    source = session.get(Source, batch.source_id)
    source_kind = source.kind if source else None

    df = build_dataframe([row.raw_json for row in raw_rows])
    stats_list = profile_all_columns(df)
    stats_by_column = {stats.column_name: stats for stats in stats_list}

    mappings_by_column = {
        mapping.column_name: mapping
        for mapping in score_all_columns(stats_by_column, source_kind)
    }

    report_path = generate_report(df, str(batch.id))

    columns_response = []
    for stats in stats_list:
        mapping = mappings_by_column[stats.column_name]
        profile_json = {"stats": stats.model_dump(), "mapping": mapping.model_dump()}

        existing = session.exec(
            select(ColumnProfile).where(
                ColumnProfile.batch_id == batch.id,
                ColumnProfile.column_name == stats.column_name,
            )
        ).first()
        if existing is not None:
            existing.profile_json = profile_json
            session.add(existing)
        else:
            session.add(
                ColumnProfile(
                    batch_id=batch.id,
                    column_name=stats.column_name,
                    profile_json=profile_json,
                )
            )

        columns_response.append(
            {
                "column_name": stats.column_name,
                "stats": stats.model_dump(),
                "candidates": [c.model_dump() for c in mapping.candidates],
                "best_field": mapping.best_field,
                "bucket": mapping.bucket,
            }
        )

    session.commit()

    return {
        "batch_id": str(batch.id),
        "columns": columns_response,
        "report_path": str(report_path),
    }
```

- [ ] **Step 4: Wire the router into `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.routers.profile import router as profile_router
from app.routers.upload import router as upload_router

app = FastAPI(title="ConduitAI")

app.include_router(upload_router)
app.include_router(profile_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest tests/test_profile_endpoint.py -v
```

Expected: PASS (5 tests). Requires `db` reachable (`docker compose up -d db`), same as `test_upload.py` and `test_migrations.py`.

- [ ] **Step 6: Run the full backend test suite twice in a row**

```bash
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -v
cd backend && DATABASE_URL="postgresql+psycopg://conduit:conduit@localhost:5432/conduitai" uv run pytest -v
```

Expected: PASS both times — confirms nothing added in this plan breaks the suite's re-runnability (every DB-touching test uses `unique_tenant_id`).

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/profile.py backend/app/main.py backend/tests/test_profile_endpoint.py
git commit -m "feat: add POST /profile endpoint (profiler + deterministic scorer)"
```
