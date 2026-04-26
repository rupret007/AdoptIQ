"""Round 4 / Phase 1.6 regression test.

When ``risk_components`` is empty, the Renewal Excel writer must NOT
fabricate Financial / Adoption / Support rows with hard-coded scores
and "Stable" labels.  Instead, emit a single ``INSUFFICIENT DATA`` row
(or equivalent) so readers do not mistake placeholder data for analysis.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_renewal_excel_does_not_synthesize_components() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Pre-Round-4 the writer hard-coded a "Stable" sentiment label
    # next to fabricated Financial/Adoption/Support rows.  After the
    # fix, those literals should not appear together as fabricated
    # rows.  We pin the absence of the offending pattern.
    forbidden = re.compile(
        r"['\"]Financial['\"]\s*,.*?['\"]Stable['\"]",
        re.DOTALL,
    )
    assert not forbidden.search(src), (
        "Round 4 Phase 1.6: Renewal Excel must not fabricate "
        "Financial / 'Stable' Risk_Components rows when real "
        "components are missing."
    )


def test_renewal_excel_emits_insufficient_data_marker_when_empty() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Marker may be 'INSUFFICIENT DATA' or 'Data_Unavailable' - either
    # is acceptable as a fail-honest signal.
    assert (
        "INSUFFICIENT DATA" in src
        or "Data_Unavailable" in src
        or "insufficient data" in src.lower()
    ), (
        "Round 4 Phase 1.6: Renewal Excel must emit an honest "
        "'INSUFFICIENT DATA' marker (or Data_Unavailable row) when "
        "risk_components is empty."
    )
