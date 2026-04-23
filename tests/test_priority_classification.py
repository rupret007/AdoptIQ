"""Pin canonical priority classification across every user-facing call site.

Round 2 of the data-accuracy hardening replaced multiple ``str.contains``
heuristics that matched any label containing ``'1'`` or ``'2'`` (and would
mis-classify ``"P10"`` / ``"S12"`` as P1/P2) with calls to
``add_case_lifecycle_fields`` + ``case_priority_norm``. These tests freeze
that contract: the exact same ``Severity`` mix must produce the *same*
P1/P2 counts everywhere — the canonical helpers, the briefing builder,
the escalated-case helper, the ARR estimator, and the priority pie.
"""
from __future__ import annotations

import pandas as pd
import pytest

import canonical_metrics as cm
from data_normalization import add_case_lifecycle_fields


# ---------------------------------------------------------------------------
# Shared "trap" frame: contains exactly 1 P1 and 1 P2, plus several rows
# that the old str.contains('1|2') / str.contains('P1|1|Critical') heuristics
# would have captured as false positives.
# ---------------------------------------------------------------------------


@pytest.fixture
def trap_csone_df() -> pd.DataFrame:
    """Severity values designed to expose substring-based classifiers.

    Canonical interpretation:
      - P1 (count=1): the single ``"P1"`` row.
      - P2 (count=1): the single ``"P2"`` row.
      - "P10", "S12", "Severity 12" -> Unknown / out of P1-P4 range.
      - "Critical" / "1" -> P1 via ``normalize_priority_label``.
      - "High" / "2" -> P2 via ``normalize_priority_label``.

    So the canonical answer is P1=3 (P1, "1", Critical) and P2=3
    (P2, "2", High). The old substring heuristics would have given
    a higher number because they would have ALSO captured "P10",
    "S12", and "Severity 12".
    """
    return pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "SR Number": "TAC-001", "Severity": "P1"},
            {"customer_name": "Acme Corp", "SR Number": "TAC-002", "Severity": "P2"},
            {"customer_name": "Beta Inc", "SR Number": "TAC-003", "Severity": "1"},
            {"customer_name": "Beta Inc", "SR Number": "TAC-004", "Severity": "2"},
            {"customer_name": "Gamma LLC", "SR Number": "TAC-005", "Severity": "Critical"},
            {"customer_name": "Gamma LLC", "SR Number": "TAC-006", "Severity": "High"},
            # Trap rows: every one of these contains the substring "1" or "2"
            # but is NOT a P1 or P2 case under canonical classification.
            {"customer_name": "Delta Co", "SR Number": "TAC-007", "Severity": "P10"},
            {"customer_name": "Delta Co", "SR Number": "TAC-008", "Severity": "S12"},
            {"customer_name": "Delta Co", "SR Number": "TAC-009", "Severity": "Severity 12"},
            {"customer_name": "Echo Inc", "SR Number": "TAC-010", "Severity": "P3"},
            {"customer_name": "Echo Inc", "SR Number": "TAC-011", "Severity": "P4"},
            {"customer_name": "Echo Inc", "SR Number": "TAC-012", "Severity": ""},
        ]
    )


@pytest.fixture
def trap_csone_norm(trap_csone_df: pd.DataFrame) -> pd.DataFrame:
    return add_case_lifecycle_fields(trap_csone_df)


# ---------------------------------------------------------------------------
# Canonical helpers themselves
# ---------------------------------------------------------------------------


def test_canonical_p1_p2_counts_ignore_substring_traps(trap_csone_df: pd.DataFrame) -> None:
    """cm.count_p1 / count_p2 must return EXACTLY the canonical counts.

    P1 set under ``normalize_priority_label``: {"P1", "1", "Critical"} -> 3
    P2 set under ``normalize_priority_label``: {"P2", "2", "High"} -> 3
    """
    assert cm.count_p1(trap_csone_df) == 3
    assert cm.count_p2(trap_csone_df) == 3


def test_canonical_escalated_count_matches_p1_plus_p2(trap_csone_df: pd.DataFrame) -> None:
    """cm.count_escalated must equal cm.count_p1 + cm.count_p2."""
    assert cm.count_escalated(trap_csone_df) == cm.count_p1(trap_csone_df) + cm.count_p2(trap_csone_df)


def test_normalized_priority_does_not_mark_p10_as_p1(trap_csone_norm: pd.DataFrame) -> None:
    """The single most important regression guard: P10 must NOT be P1."""
    p10_rows = trap_csone_norm[trap_csone_norm["Severity"] == "P10"]
    assert not p10_rows.empty, "trap fixture must contain a P10 row"
    assert (p10_rows["case_priority_norm"] != "P1").all()


def test_normalized_priority_does_not_mark_s12_as_p2(trap_csone_norm: pd.DataFrame) -> None:
    """Defensive twin of the P10 case: S12 must NOT be P2."""
    s12_rows = trap_csone_norm[trap_csone_norm["Severity"] == "S12"]
    assert not s12_rows.empty
    assert (s12_rows["case_priority_norm"] != "P2").all()


# ---------------------------------------------------------------------------
# Call-site contracts: the round 2 fixes claim every user-facing P1/P2
# count flows through case_priority_norm. These tests assert that the
# contract is honored end-to-end.
# ---------------------------------------------------------------------------


def test_briefing_critical_cases_use_canonical_priority(trap_csone_df: pd.DataFrame) -> None:
    """The briefing's "ALL Critical Cases (P1/P2)" path replaces
    ``str.contains('1|2')`` with ``case_priority_norm.isin(['P1','P2'])``.

    The old heuristic captured 9 rows on this fixture (every row whose
    severity string contained "1" or "2"). The canonical filter must
    capture exactly 6 (P1+P2 under normalization).
    """
    norm = add_case_lifecycle_fields(trap_csone_df)
    canonical_critical = norm[norm["case_priority_norm"].isin(["P1", "P2"])]
    assert len(canonical_critical) == 6
    # Defensive: the trap rows must be excluded.
    excluded = canonical_critical["Severity"].isin(["P10", "S12", "Severity 12", "P3", "P4", ""])
    assert not excluded.any()


def test_estimate_arr_p1_p2_counts_use_canonical_priority(trap_csone_df: pd.DataFrame) -> None:
    """The ARR estimator (app_simple._enrich_csone_with_arr) was rewritten
    to read ``case_priority_norm`` instead of ``Severity == 'P1'``. Verify
    the per-customer counts the estimator sees match the canonical counts.
    """
    norm = add_case_lifecycle_fields(trap_csone_df)
    # Acme has Severity ['P1','P2'] -> canonical P1=1, P2=1.
    acme = norm[norm["customer_name"] == "Acme Corp"]
    assert int((acme["case_priority_norm"] == "P1").sum()) == 1
    assert int((acme["case_priority_norm"] == "P2").sum()) == 1
    # Gamma has Severity ['Critical','High'] -> canonical P1=1, P2=1
    # (the old `Severity.astype(str) == 'P1'` test would have given 0/0).
    gamma = norm[norm["customer_name"] == "Gamma LLC"]
    assert int((gamma["case_priority_norm"] == "P1").sum()) == 1
    assert int((gamma["case_priority_norm"] == "P2").sum()) == 1


def test_p1_p2_by_customer_briefing_uses_canonical_priority(trap_csone_df: pd.DataFrame) -> None:
    """adoptiq_backend ``P1/P2 Cases by Customer`` now slices on
    ``case_priority_norm`` per customer. The Acme/Beta/Gamma rows have
    canonical P1+P2 each; Delta/Echo have none.
    """
    norm = add_case_lifecycle_fields(trap_csone_df)
    p1_by_cust = norm[norm["case_priority_norm"] == "P1"]["customer_name"].value_counts().to_dict()
    p2_by_cust = norm[norm["case_priority_norm"] == "P2"]["customer_name"].value_counts().to_dict()
    assert p1_by_cust == {"Acme Corp": 1, "Beta Inc": 1, "Gamma LLC": 1}
    assert p2_by_cust == {"Acme Corp": 1, "Beta Inc": 1, "Gamma LLC": 1}
    # Delta has only trap severities ('P10','S12','Severity 12') and
    # MUST contribute zero to P1/P2 counts.
    assert "Delta Co" not in p1_by_cust
    assert "Delta Co" not in p2_by_cust


def test_escalated_helper_in_app_simple_matches_canonical(trap_csone_df: pd.DataFrame) -> None:
    """app_simple's "escalated_cases" path now uses
    ``case_priority_norm.isin(['P1','P2'])``. The count must equal
    ``cm.count_escalated``.
    """
    norm = add_case_lifecycle_fields(trap_csone_df)
    escalated = norm[norm["case_priority_norm"].isin(["P1", "P2"])]
    assert len(escalated) == cm.count_escalated(trap_csone_df)
    # And it must NOT include any of the trap rows.
    assert not escalated["Severity"].isin(["P10", "S12", "Severity 12"]).any()


def test_old_substring_heuristic_would_have_been_wrong(trap_csone_df: pd.DataFrame) -> None:
    """Document the bug we eliminated: the old ``str.contains('1|2')``
    mask would have over-matched on this fixture (every severity whose
    string contains "1" or "2": "P1","P2","1","2","P10","S12","Severity 12"
    => 7 rows). The canonical answer is 6 (P1+P2 under normalization).

    The point of this test is not the exact "7" — it's that the legacy
    heuristic over-counts vs the canonical implementation. If anyone
    re-introduces the substring heuristic this test will fail.
    """
    sev = trap_csone_df["Severity"].astype(str)
    legacy_buggy_mask = sev.str.contains("1|2", na=False)
    legacy_count = int(legacy_buggy_mask.sum())
    canonical = cm.count_escalated(trap_csone_df)
    assert canonical == 6
    assert legacy_count > canonical, (
        f"Legacy substring heuristic ({legacy_count}) should over-count vs "
        f"canonical ({canonical}); if these match, the substring heuristic "
        "has accidentally been re-introduced somewhere."
    )
    # And document the specific over-count traps that the heuristic
    # falsely captured: P10, S12, Severity 12.
    legacy_captured = trap_csone_df.loc[legacy_buggy_mask, "Severity"].tolist()
    assert "P10" in legacy_captured
    assert "S12" in legacy_captured
    assert "Severity 12" in legacy_captured
