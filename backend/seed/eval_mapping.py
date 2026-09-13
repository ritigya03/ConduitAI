"""Benchmark eval script — CLI, on-demand.

Runs two benchmarks, because one alone is misleading:

1. **In-distribution** (`generate_seed_data.py`'s ground truth) — the
   legacy column names here (CustID, COMPNAME, CNTRY, ...) were written
   alongside `app/canonical.py`'s alias lists, which quietly include the
   exact same abbreviations. Fuzzy matching "solving" this proves
   nothing about generalization — the answer key and the test set share
   an author. Kept anyway, because it's still a useful regression guard
   for "does the deterministic layer still get the easy, well-aliased
   cases right."
2. **Held-out** (`held_out_benchmark.py`) — hand-labeled real column
   names from Salesforce/HubSpot/QuickBooks/Zendesk that were chosen
   without consulting the alias lists. This is the one that actually
   tests whether embeddings/LLM earn their cost on a source system the
   scorer has never seen the exact spelling of before.

Each benchmark runs three passes: baseline (pure rapidfuzz), hybrid
(rapidfuzz + fastembed + type/format, no LLM), and full (hybrid + LLM
tie-break for "unmapped" columns). The full pass makes real Groq/Ollama
calls — only run this on demand, not as part of the regular test suite
(see tests/test_mapping_benchmark.py for the fast, LLM-free regression
guard on the in-distribution set only). Run with:

    uv run python -m seed.eval_mapping
"""

import csv
import json

from app.llm_mapper import tiebreak
from app.profiling import build_dataframe, profile_all_columns, profile_column
from app.scoring import candidate_pool, name_similarity, score_all_columns, score_column
from seed.generate_seed_data import OUTPUT_DIR, generate
from seed.held_out_benchmark import HELD_OUT_COLUMNS

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


def _add(totals: dict, delta: dict) -> None:
    for key, value in delta.items():
        totals[key] += value


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


def _evaluate_column(
    column_name: str, source_kind: str, true_field: str | None, stats
) -> tuple[dict, dict, bool | None]:
    """Runs all three passes for one already-profiled column. Returns
    (baseline_score, hybrid_score, full_is_correct) — full_is_correct is
    None for columns with no ground truth (true_field is None), since
    "correct" isn't a meaningful concept for those."""
    pool = candidate_pool(source_kind)

    baseline_ranked = _baseline_ranked_fields(column_name, source_kind)
    baseline_top1 = baseline_ranked[0] if baseline_ranked else None
    baseline_score = _score(true_field, baseline_top1, baseline_ranked)

    hybrid_mapping = score_column(column_name, stats, source_kind)
    hybrid_ranked = [c.canonical_field for c in hybrid_mapping.candidates]
    hybrid_top1 = hybrid_mapping.best_field if hybrid_mapping.bucket != "unmapped" else None
    hybrid_score = _score(true_field, hybrid_top1, hybrid_ranked)

    if true_field is None:
        return baseline_score, hybrid_score, None

    if hybrid_mapping.bucket != "unmapped":
        full_field = hybrid_top1
    else:
        decision = tiebreak(column_name, stats, pool)
        if decision is not None and decision.canonical_field != "UNKNOWN":
            full_field = decision.canonical_field
        else:
            full_field = hybrid_mapping.best_field  # deterministic_fallback
    return baseline_score, hybrid_score, full_field == true_field


def _run_in_distribution_benchmark() -> tuple[dict, dict, int, int]:
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

        for column_name, true_field in mapping.items():
            stats = stats_by_column[column_name]
            baseline_score, hybrid_score, is_correct = _evaluate_column(
                column_name, source_kind, true_field, stats
            )
            _add(baseline, baseline_score)
            _add(hybrid, hybrid_score)
            if true_field is not None:
                mapped_total += 1
                if is_correct:
                    full_correct += 1

    return baseline, hybrid, full_correct, mapped_total


def _run_held_out_benchmark() -> tuple[dict, dict, int, int]:
    mapped_total = 0
    baseline = {"tp": 0, "fp": 0, "fn": 0, "correct_top1": 0, "correct_topk": 0}
    hybrid = {"tp": 0, "fp": 0, "fn": 0, "correct_top1": 0, "correct_topk": 0}
    full_correct = 0

    for source_kind, column_name, sample_values, true_field in HELD_OUT_COLUMNS:
        df = build_dataframe([{column_name: v} for v in sample_values])
        stats = profile_column(df, column_name)
        baseline_score, hybrid_score, is_correct = _evaluate_column(
            column_name, source_kind, true_field, stats
        )
        _add(baseline, baseline_score)
        _add(hybrid, hybrid_score)
        if true_field is not None:
            mapped_total += 1
            if is_correct:
                full_correct += 1

    return baseline, hybrid, full_correct, mapped_total


def _print_benchmark(title: str, baseline: dict, hybrid: dict, full_correct: int, mapped_total: int) -> None:
    print(f"\n=== {title} ({mapped_total} mapped columns) ===")
    print(f"{'pass':<10}{'acc@1':>10}{'acc@' + str(_TOP_K):>10}{'precision':>12}{'recall':>10}")
    _print_pass("baseline", mapped_total, **baseline)
    _print_pass("hybrid", mapped_total, **hybrid)
    print(f"{'full':<10}{full_correct / mapped_total:>10.2%}  (LLM tie-break adds no ranked list beyond top-1)")


def run() -> None:
    in_dist_baseline, in_dist_hybrid, in_dist_full, in_dist_total = _run_in_distribution_benchmark()
    _print_benchmark(
        "In-distribution (seed generator — alias lists co-designed with these names)",
        in_dist_baseline,
        in_dist_hybrid,
        in_dist_full,
        in_dist_total,
    )

    held_out_baseline, held_out_hybrid, held_out_full, held_out_total = _run_held_out_benchmark()
    _print_benchmark(
        "Held-out (Salesforce/HubSpot/QuickBooks/Zendesk — never seen by the alias lists)",
        held_out_baseline,
        held_out_hybrid,
        held_out_full,
        held_out_total,
    )


if __name__ == "__main__":
    run()
