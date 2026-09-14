"""Splink-based fuzzy customer dedupe -- "same customer, different
spelling" (Day 6). Runs with hand-specified match/non-match
probabilities rather than Splink's statistical EM training: this
project's per-tenant customer counts (tens to low hundreds) are too
small for EM to converge reliably (confirmed during planning: EM left
"legal_name"/"country" only partially trained on a 5-row prototype, and
produced zero predictions above threshold on data an expert would call
an obvious match). Expert-specified priors are a legitimate, documented
Splink usage mode, not a workaround. The email comparison carries the
most weight: two records sharing an exact email are strong evidence of
the same underlying customer regardless of how differently the name is
spelled, which is exactly the "same customer, different spelling" case
this is built to catch.
"""

import pandas as pd
from pydantic import BaseModel
from splink import DuckDBAPI, Linker, SettingsCreator, block_on
import splink.comparison_library as cl

from app.models import Customer


class DuplicateCandidate(BaseModel):
    customer_id_a: str
    customer_id_b: str
    match_probability: float


def _build_settings() -> SettingsCreator:
    name_comparison = cl.JaroWinklerAtThresholds("legal_name", [0.9, 0.7]).configure(
        m_probabilities=[0.85, 0.5, 0.3, 0.02],
        u_probabilities=[0.001, 0.02, 0.05, 0.9],
    )
    email_comparison = cl.ExactMatch("email").configure(
        m_probabilities=[0.95, 0.05],
        u_probabilities=[0.001, 0.999],
    )
    country_comparison = cl.ExactMatch("country").configure(
        m_probabilities=[0.6, 0.4],
        u_probabilities=[0.3, 0.7],
    )
    return SettingsCreator(
        link_type="dedupe_only",
        probability_two_random_records_match=0.01,
        comparisons=[name_comparison, email_comparison, country_comparison],
        blocking_rules_to_generate_predictions=[
            block_on("substr(legal_name, 1, 3)"),
            block_on("email"),
        ],
        retain_intermediate_calculation_columns=False,
    )


def find_duplicate_candidates(customers: list[Customer], threshold: float = 0.5) -> list[DuplicateCandidate]:
    if len(customers) < 2:
        return []

    df = pd.DataFrame(
        [
            {
                "unique_id": str(c.id),
                "legal_name": c.legal_name,
                "email": c.email or "",
                "country": c.country or "",
            }
            for c in customers
        ]
    )

    db_api = DuckDBAPI()
    linker = Linker(df, _build_settings(), db_api, set_up_basic_logging=False)
    result = linker.inference.predict(threshold_match_probability=threshold)
    result_df = result.as_pandas_dataframe()

    return [
        DuplicateCandidate(
            customer_id_a=row["unique_id_l"],
            customer_id_b=row["unique_id_r"],
            match_probability=round(float(row["match_probability"]), 4),
        )
        for _, row in result_df.iterrows()
    ]
