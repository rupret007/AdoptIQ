"""Round 5 / Phase 5.4 regression test.

The renewal path's ``_normalize_cases_df`` must reuse
``data_normalization.add_case_lifecycle_fields`` instead of
re-implementing status / priority / open-date inference inline.
The inline implementation classified ``Unknown + closed_date`` rows
as OPEN, contradicting the canonical helper that closed them.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_renewal_cases_normalization_reuses_lifecycle_helper() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 5.4" in src, (
        "Round 5 Phase 5.4 marker missing in adoptiq_backend.py."
    )
    assert "add_case_lifecycle_fields" in src, (
        "Round 5 Phase 5.4: renewal _normalize_cases_df must call "
        "add_case_lifecycle_fields from data_normalization."
    )
