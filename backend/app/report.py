"""ydata-profiling HTML artifact generation.

Pandas appears in this module only — nowhere else in the project. This
is deliberate: ydata-profiling requires a pandas DataFrame, so this file
is a narrow `.to_pandas()` bridge at the edge of an otherwise all-Polars
profiling pipeline (see app.profiling's docstring for why Polars is the
engine of record).
"""

from pathlib import Path

import polars as pl

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def generate_report(df: pl.DataFrame, batch_id: str) -> Path:
    # Imported here, not at module level: ydata-profiling pulls in its
    # full dependency chain (pandas, matplotlib, scipy...) on import,
    # which costs real memory on every process that so much as imports
    # this module (e.g. app.main, for REPORTS_DIR) even when /profile is
    # never called. Deferred so that cost is only paid by a request that
    # actually needs a report.
    from ydata_profiling import ProfileReport

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    pandas_df = df.to_pandas()
    profile = ProfileReport(
        pandas_df, title=f"Batch {batch_id} profile", minimal=True
    )
    output_path = REPORTS_DIR / f"{batch_id}.html"
    profile.to_file(output_path)
    return output_path
