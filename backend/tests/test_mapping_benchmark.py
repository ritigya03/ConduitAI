"""Fast, deterministic regression guard: the full deterministic hybrid
scorer (fuzzy + embedding + type) must not score worse than a pure-fuzzy
baseline on the labeled seed benchmark. No LLM calls, no network — this
runs on every `pytest` invocation. The three-way comparison that adds
the LLM tie-break and prints the full metrics table is
`backend/seed/eval_mapping.py`, run on demand.
"""

import csv
import json

from app.profiling import build_dataframe, profile_all_columns
from app.scoring import candidate_pool, name_similarity, score_all_columns
from seed.generate_seed_data import OUTPUT_DIR, generate

FILE_SOURCE_KIND = {
    "crm_snake.csv": "crm",
    "crm_legacy.csv": "crm",
    "billing.csv": "billing",
    "support.csv": "support",
}


def _baseline_best_field(column_name: str, source_kind: str) -> str | None:
    pool = candidate_pool(source_kind)
    if not pool:
        return None
    best = max(pool, key=lambda f: name_similarity(column_name, f))
    return best.name


def test_hybrid_scorer_is_at_least_as_accurate_as_fuzzy_baseline():
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())[
        "column_mappings"
    ]

    baseline_correct = 0
    hybrid_correct = 0
    total_mapped = 0

    for filename, mapping in ground_truth.items():
        source_kind = FILE_SOURCE_KIND[filename]
        with (OUTPUT_DIR / filename).open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        df = build_dataframe(rows)
        stats_by_column = {s.column_name: s for s in profile_all_columns(df)}
        hybrid_mappings = {
            m.column_name: m for m in score_all_columns(stats_by_column, source_kind)
        }

        for column_name, true_field in mapping.items():
            if true_field is None:
                continue
            total_mapped += 1

            if _baseline_best_field(column_name, source_kind) == true_field:
                baseline_correct += 1

            hybrid_mapping = hybrid_mappings[column_name]
            hybrid_field = (
                hybrid_mapping.best_field if hybrid_mapping.bucket != "unmapped" else None
            )
            if hybrid_field == true_field:
                hybrid_correct += 1

    assert total_mapped >= 40
    baseline_accuracy = baseline_correct / total_mapped
    hybrid_accuracy = hybrid_correct / total_mapped
    print(f"\nfuzzy-only baseline accuracy@1: {baseline_accuracy:.2%}")
    print(f"deterministic hybrid accuracy@1: {hybrid_accuracy:.2%}")

    assert hybrid_accuracy >= baseline_accuracy
