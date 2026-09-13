"""Benchmark eval script — CLI, on-demand.

Compares three passes on the labeled seed benchmark:
1. baseline: pure rapidfuzz name-matching only.
2. hybrid: rapidfuzz + fastembed + type/format signal (Day 2, no LLM).
3. full: hybrid, plus an LLM tie-break for every column Day 2 buckets
   "unmapped" (Day 3).

Pass 3 makes real Groq/Ollama calls — only run this on demand, not as
part of the regular test suite (see tests/test_mapping_benchmark.py for
the fast, LLM-free regression guard). Run with:

    uv run python -m seed.eval_mapping
"""

import csv
import json

from app.llm_mapper import tiebreak
from app.profiling import build_dataframe, profile_all_columns
from app.scoring import candidate_pool, name_similarity, score_all_columns
from seed.generate_seed_data import OUTPUT_DIR, generate

FILE_SOURCE_KIND = {
    "crm_snake.csv": "crm",
    "crm_legacy.csv": "crm",
    "billing.csv": "billing",
    "support.csv": "support",
}

_TOP_K = 3


def _baseline_ranked_fields(column_name: str, source_kind: str) -> list[str]:
    pool = candidate_pool(source_kind)
    ranked = sorted(pool, key=lambda f: name_similarity(column_name, f), reverse=True)
    return [f.name for f in ranked]


def _score(true_field: str | None, predicted_top1: str | None, ranked: list[str]) -> dict:
    is_positive_prediction = predicted_top1 is not None
    is_correct_top1 = true_field is not None and predicted_top1 == true_field
    is_correct_topk = true_field is not None and true_field in ranked[:_TOP_K]
    return {
        "tp": 1 if is_correct_top1 else 0,
        "fp": 1 if (is_positive_prediction and not is_correct_top1) else 0,
        "fn": 1 if (true_field is not None and not is_correct_top1) else 0,
        "correct_top1": 1 if is_correct_top1 else 0,
        "correct_topk": 1 if is_correct_topk else 0,
    }


def _print_pass(
    name: str, mapped_total: int, tp: int, fp: int, fn: int, correct_top1: int, correct_topk: int
) -> None:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    print(
        f"{name:<10}"
        f"{correct_top1 / mapped_total:>10.2%}"
        f"{correct_topk / mapped_total:>10.2%}"
        f"{precision:>12.2%}"
        f"{recall:>10.2%}"
    )


def run() -> None:
    generate()
    ground_truth = json.loads((OUTPUT_DIR / "ground_truth.json").read_text())[
        "column_mappings"
    ]

    mapped_total = 0
    baseline = {"tp": 0, "fp": 0, "fn": 0, "correct_top1": 0, "correct_topk": 0}
    hybrid = {"tp": 0, "fp": 0, "fn": 0, "correct_top1": 0, "correct_topk": 0}
    full_correct = 0

    for filename, mapping in ground_truth.items():
        source_kind = FILE_SOURCE_KIND[filename]
        with (OUTPUT_DIR / filename).open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        df = build_dataframe(rows)
        stats_by_column = {s.column_name: s for s in profile_all_columns(df)}
        hybrid_mappings = {
            m.column_name: m for m in score_all_columns(stats_by_column, source_kind)
        }
        pool = candidate_pool(source_kind)

        for column_name, true_field in mapping.items():
            baseline_ranked = _baseline_ranked_fields(column_name, source_kind)
            baseline_top1 = baseline_ranked[0] if baseline_ranked else None
            for key, value in _score(true_field, baseline_top1, baseline_ranked).items():
                baseline[key] += value

            hybrid_mapping = hybrid_mappings[column_name]
            hybrid_ranked = [c.canonical_field for c in hybrid_mapping.candidates]
            hybrid_top1 = (
                hybrid_mapping.best_field if hybrid_mapping.bucket != "unmapped" else None
            )
            for key, value in _score(true_field, hybrid_top1, hybrid_ranked).items():
                hybrid[key] += value

            if true_field is None:
                continue
            mapped_total += 1

            if hybrid_mapping.bucket != "unmapped":
                full_field = hybrid_top1
            else:
                decision = tiebreak(column_name, stats_by_column[column_name], pool)
                if decision is not None and decision.canonical_field != "UNKNOWN":
                    full_field = decision.canonical_field
                else:
                    full_field = hybrid_mapping.best_field  # deterministic_fallback
            if full_field == true_field:
                full_correct += 1

    print(f"Labeled benchmark: {mapped_total} mapped columns\n")
    print(f"{'pass':<10}{'acc@1':>10}{'acc@' + str(_TOP_K):>10}{'precision':>12}{'recall':>10}")
    _print_pass("baseline", mapped_total, **baseline)
    _print_pass("hybrid", mapped_total, **hybrid)
    print(f"{'full':<10}{full_correct / mapped_total:>10.2%}  (LLM tie-break adds no ranked list beyond top-1)")


if __name__ == "__main__":
    run()
