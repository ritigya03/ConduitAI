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
