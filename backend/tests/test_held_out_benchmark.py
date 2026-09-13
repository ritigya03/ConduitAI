from app.canonical import CANONICAL_FIELDS
from seed.held_out_benchmark import HELD_OUT_COLUMNS

_VALID_SOURCE_KINDS = {"crm", "billing", "support"}


def test_all_true_fields_are_real_canonical_fields_or_none():
    valid_names = {f.name for f in CANONICAL_FIELDS}
    for source_kind, column_name, _samples, true_field in HELD_OUT_COLUMNS:
        if true_field is not None:
            assert true_field in valid_names, f"{column_name} -> unknown field {true_field}"


def test_all_source_kinds_are_valid():
    for source_kind, column_name, _samples, _true_field in HELD_OUT_COLUMNS:
        assert source_kind in _VALID_SOURCE_KINDS, f"{column_name} has bad source_kind {source_kind}"


def test_has_a_meaningful_number_of_labeled_columns():
    assert len(HELD_OUT_COLUMNS) >= 25


def test_includes_genuinely_unmappable_columns():
    """The benchmark must test correct abstention, not just correct mapping."""
    unmapped = [c for c in HELD_OUT_COLUMNS if c[3] is None]
    assert len(unmapped) >= 3


def test_every_column_has_sample_values():
    for _source_kind, column_name, samples, _true_field in HELD_OUT_COLUMNS:
        assert len(samples) >= 1, f"{column_name} has no sample values"
