import csv
import json
import random

from faker import Faker

from seed.generate_seed_data import OUTPUT_DIR, generate

EXPECTED_FILES = {"crm_snake.csv", "crm_legacy.csv", "billing.csv", "support.csv"}


def test_generation_is_deterministic_across_runs():
    generate()
    first_ground_truth = (OUTPUT_DIR / "ground_truth.json").read_text()
    first_crm = (OUTPUT_DIR / "crm_snake.csv").read_text()

    random.seed(42)
    Faker.seed(42)
    generate()

    assert (OUTPUT_DIR / "ground_truth.json").read_text() == first_ground_truth
    assert (OUTPUT_DIR / "crm_snake.csv").read_text() == first_crm


def test_all_expected_files_are_written():
    generate()
    for filename in EXPECTED_FILES:
        assert (OUTPUT_DIR / filename).exists()
    assert (OUTPUT_DIR / "ground_truth.json").exists()


def test_ground_truth_has_both_sections():
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())
    assert set(ground_truth["column_mappings"].keys()) == EXPECTED_FILES
    assert set(ground_truth["expected_defects"].keys()) == {
        "crm_snake.csv", "billing.csv", "support.csv",
    }


def test_benchmark_has_at_least_40_mapped_columns():
    # Total column count is a weaker number than it looks — a chunk of every
    # file is deliberately unmapped (None) to test that the mapper abstains
    # correctly. What Day 3's accuracy@1 is actually computed over is the
    # *mapped* columns, so that's what must clear the 40-column floor.
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())
    mapped_columns = sum(
        1
        for mapping in ground_truth["column_mappings"].values()
        for canonical_name in mapping.values()
        if canonical_name is not None
    )
    assert mapped_columns >= 40


def test_all_mapped_canonical_names_are_real_fields():
    from app.canonical import CANONICAL_FIELDS

    valid_names = {f.name for f in CANONICAL_FIELDS}
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())
    for mapping in ground_truth["column_mappings"].values():
        for canonical_name in mapping.values():
            if canonical_name is not None:
                assert canonical_name in valid_names


def test_crm_snake_and_crm_legacy_share_row_values_under_different_headers():
    generate()
    with (OUTPUT_DIR / "crm_snake.csv").open() as f:
        snake_rows = list(csv.DictReader(f))
    with (OUTPUT_DIR / "crm_legacy.csv").open() as f:
        legacy_rows = list(csv.DictReader(f))

    assert len(snake_rows) == len(legacy_rows)
    assert snake_rows[0]["company_name"] == legacy_rows[0]["COMPNAME"]


def test_expected_defects_reference_valid_row_indices():
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())
    for filename, defects in ground_truth["expected_defects"].items():
        with (OUTPUT_DIR / filename).open() as f:
            row_count = sum(1 for _ in csv.DictReader(f))
        for row_index_str in defects:
            assert int(row_index_str) < row_count
