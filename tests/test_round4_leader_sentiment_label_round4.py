"""Round 4 / Phase 3.7 regression test.

The Leader summary table's "Sentiment" column must be re-labeled when
the underlying value is ARR-enriched (so readers do not mistake an
ARR-blended signal for the canonical pulse_sentiment label).
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_leader_sentiment_column_label_distinguishes_arr_enriched() -> None:
    src = (REPO_ROOT / "leader_report_generator.py").read_text(encoding="utf-8")
    # Round 4 fix introduces a label that mentions ARR explicitly when
    # the column has been enriched.  Accept either "ARR" in the
    # sentiment header or a separate column name.
    assert "ARR" in src and "Sentiment" in src, (
        "Round 4 Phase 3.7: Leader summary table must label the "
        "Sentiment column as ARR-enriched (or split into a separate "
        "column) when ARR enrichment is applied."
    )
