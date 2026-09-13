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
