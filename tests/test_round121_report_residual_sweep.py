"""Round 121 / Build 90 — report-accuracy residual sweep (G1-G4).

The Build 89 live-re-acceptance audit confirmed Round 120 mostly held, but
three fixes landed on one render path and missed a sibling, plus one real
status-display accuracy bug surfaced.  This file pins each residual fix
(source-shape + behavior).

G1 — Renewal BEMS escalation bracket still rendered ``(Type: unknown)``
     unconditionally (77x).  Suppress when ``case_type`` is a sentinel
     (reuses ``_R120_UNKNOWN_TOKENS``; mirrors the F4 TAC-bracket contract).
G2 — Leader follow-on narrative sentences ("requires immediate attention",
     "elevated activity", mixed-health, "high-priority adoption barriers")
     hard-coded plural nouns ("1 adoption barriers").  Route through
     ``_r120_pluralize`` (Build 89 only fixed the "Portfolio shows N…" line).
G3 — BE Focus Areas bare ``Unknown`` theme/technology relabeled to
     "Other / Unclassified" at the display layer (row + counts preserved).
G4a — Support-case Status showed ``Unknown`` for demonstrably-closed cases
     (close date present).  The source-of-truth normalizer now relabels the
     displayed status to "Closed".
G4b — Support-case ``Type: unknown`` table cells relabeled to "Unclassified"
     at the display layer (underlying ``case_type_class`` untouched).

Round 121.  Made-with: Cursor.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
_APP_SIMPLE = _ROOT / "app_simple.py"
_LEADER = _ROOT / "leader_report_generator.py"
_SCORER = _ROOT / "be_priority_scorer.py"
_PIPELINE = _ROOT / "be_priority_pipeline.py"
_DATANORM = _ROOT / "data_normalization.py"
_EI = _ROOT / "executive_intelligence_formatter.py"


# ---------------------------------------------------------------------------
# G1 — Renewal BEMS bracket suppressed for sentinel case_type
# ---------------------------------------------------------------------------


def test_g1_bems_bracket_guarded_by_unknown_tokens_source_shape():
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    # The BEMS-escalation bracket must be guarded by the same frozenset that
    # F4 used for the TAC bracket.
    assert_in_source(src, "Round 121 / G1", label='src')
    assert (
        'if str(case_type).strip().lower() not in _R120_UNKNOWN_TOKENS:' in src
    ), "G1: BEMS '(Type: ...)' bracket not guarded by _R120_UNKNOWN_TOKENS"


def test_g1_unknown_tokens_cover_the_sentinel():
    # The literal default the BEMS row falls back to ('unknown') must be in
    # the suppression set so the bracket is omitted.
    import app_simple

    assert "unknown" in app_simple._R120_UNKNOWN_TOKENS
    assert "" in app_simple._R120_UNKNOWN_TOKENS
    # A genuine type is NOT in the set, so the bracket would still render.
    assert "break_fix_technical" not in app_simple._R120_UNKNOWN_TOKENS


# ---------------------------------------------------------------------------
# G2 — Leader follow-on narrative pluralization
# ---------------------------------------------------------------------------


def test_g2_pluralize_singular_and_plural():
    from leader_report_generator import _r120_pluralize

    assert _r120_pluralize(1, "adoption barrier") == "1 adoption barrier"
    assert _r120_pluralize(2, "adoption barrier") == "2 adoption barriers"
    assert _r120_pluralize(1, "TAC case") == "1 TAC case"
    assert _r120_pluralize(3, "TAC case") == "3 TAC cases"
    assert (
        _r120_pluralize(1, "high-priority adoption barrier")
        == "1 high-priority adoption barrier"
    )
    assert _r120_pluralize(0, "account") == "0 accounts"


def test_g2_followon_sentences_use_pluralize_source_shape():
    src = _LEADER.read_text(encoding="utf-8")
    assert src.count("Round 121 / G2") >= 3
    # The three follow-on branches must no longer hard-code the plural nouns.
    assert "adoption barriers ({ab_per_customer" not in src
    assert "TAC cases ({tac_per_customer" not in src
    assert "{high_priority_barriers} high-priority adoption barriers that" not in src
    # And they must route the counts through the helper.
    assert_in_source(src, "_r120_pluralize(total_barriers, 'adoption barrier')", label='src')
    assert_in_source(src, "_r120_pluralize(total_tac_cases, 'TAC case')", label='src')
    assert (
        "_r120_pluralize(high_priority_barriers, 'high-priority adoption barrier')"
        in src
    )


# ---------------------------------------------------------------------------
# G3 — BE Focus Areas bare Unknown -> "Other / Unclassified"
# ---------------------------------------------------------------------------


def test_g3_relabel_unclassified_helper():
    from be_priority_scorer import relabel_unclassified

    for sentinel in ["Unknown", "unknown", "", "  ", "nan", "None", "Other/Unknown",
                     "Other / Unknown", "N/A"]:
        assert relabel_unclassified(sentinel) == "Other / Unclassified", sentinel
    # Genuine labels containing 'unknown' as a substring are preserved.
    assert relabel_unclassified("Unknown Protocol") == "Unknown Protocol"
    assert relabel_unclassified("Webex Calling") == "Webex Calling"
    assert relabel_unclassified(None) == "Other / Unclassified"


def test_g3_focus_areas_no_bare_unknown_row_counts_preserved():
    from be_priority_scorer import compute_be_focus_areas

    # Build a cluster whose sub_technology / ab_category_final are blank so the
    # scorer normalizes them to the "Unknown" key.  Give it enough signal to
    # clear the default min_cluster_score=30.
    rows = []
    for i in range(8):
        rows.append({
            "ID": f"AB-{i}",
            "sub_technology": "",
            "ab_category_final": "",
            "be_priority_score": 80.0,
            "customer_name": f"Cust {i % 4}",
            "be_llm_class": "TRUE_BLOCKER" if i < 2 else "OBSERVATION_ONLY",
            "title": f"Issue {i}",
        })
    df = pd.DataFrame(rows)
    out = compute_be_focus_areas(df)
    # Not a provenance frame.
    assert "_adoptiq_provenance_row" not in out.columns or not bool(
        out.get("_adoptiq_provenance_row", pd.Series([False])).any()
    )
    techs = set(out["Technology"].astype(str))
    themes = set(out["Theme"].astype(str))
    assert "Unknown" not in techs and "Unknown" not in themes
    assert "Other / Unclassified" in techs
    assert "Other / Unclassified" in themes
    # Row + counts preserved: one cluster, 8 open barriers, 4 customers, 2 blockers.
    assert len(out) == 1
    assert int(out.iloc[0]["Open_Barriers"]) == 8
    assert int(out.iloc[0]["Customers_Affected"]) == 4
    assert int(out.iloc[0]["True_Blocker_Count"]) == 2


def test_g3_pipeline_barriers_df_uses_relabel_source_shape():
    src = _PIPELINE.read_text(encoding="utf-8")
    assert_in_source(src, "Round 121 / G3", label='src')
    assert_in_source(src, "bes.relabel_unclassified(", label='src')


def test_g3_scorer_focus_rollup_uses_relabel_source_shape():
    src = _SCORER.read_text(encoding="utf-8")
    assert_in_source(src, "Round 121 / G3", label='src')
    assert_in_source(src, '"Technology": relabel_unclassified(tech)', label='src')
    assert_in_source(src, '"Theme": relabel_unclassified(theme)', label='src')


# ---------------------------------------------------------------------------
# G4a — Unknown status + close date -> displayed "Closed"
# ---------------------------------------------------------------------------


def _utc_iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_g4a_unknown_with_close_date_becomes_closed():
    from data_normalization import add_case_lifecycle_fields

    now = datetime.now(timezone.utc)
    df = pd.DataFrame([{
        "BU_NAME": "Acme",
        "Case Status": "weird-bucket-not-in-patterns",
        "Date/Time Opened": _utc_iso(now - timedelta(days=10)),
        "Date/Time Closed": _utc_iso(now - timedelta(days=2)),
    }])
    out = add_case_lifecycle_fields(df)
    assert out.loc[0, "case_status_norm"] == "Closed"
    assert bool(out.loc[0, "is_closed"]) is True
    assert bool(out.loc[0, "is_open"]) is False


def test_g4a_unknown_without_close_date_stays_unknown():
    from data_normalization import add_case_lifecycle_fields

    now = datetime.now(timezone.utc)
    df = pd.DataFrame([{
        "BU_NAME": "Acme",
        "Case Status": "weird-bucket-not-in-patterns",
        "Date/Time Opened": _utc_iso(now - timedelta(days=10)),
    }])
    out = add_case_lifecycle_fields(df)
    assert out.loc[0, "case_status_norm"] == "Unknown"
    assert bool(out.loc[0, "is_closed"]) is False


def test_g4a_genuine_open_and_closed_untouched():
    from data_normalization import add_case_lifecycle_fields

    now = datetime.now(timezone.utc)
    df = pd.DataFrame([
        {
            "BU_NAME": "Acme",
            "Case Status": "Open",
            "Date/Time Opened": _utc_iso(now - timedelta(days=5)),
        },
        {
            "BU_NAME": "Acme",
            "Case Status": "Closed",
            "Date/Time Opened": _utc_iso(now - timedelta(days=9)),
            "Date/Time Closed": _utc_iso(now - timedelta(days=1)),
        },
    ])
    out = add_case_lifecycle_fields(df)
    assert list(out["case_status_norm"]) == ["Open", "Closed"]
    assert list(out["is_open"]) == [True, False]
    assert list(out["is_closed"]) == [False, True]


def test_g4a_source_shape_marker():
    src = _DATANORM.read_text(encoding="utf-8")
    assert_in_source(src, "Round 121 / G4a", label='src')
    assert_in_source(src, 'use.loc[_r121_closed_unknown, "case_status_norm"] = "Closed"', label='src')


# ---------------------------------------------------------------------------
# G4b — support-case Type "unknown" -> "Unclassified" at display
# ---------------------------------------------------------------------------


def test_g4b_display_case_type_relabels_only_sentinels():
    from executive_intelligence_formatter import _r121_display_case_type

    assert _r121_display_case_type("unknown") == "Unclassified"
    assert _r121_display_case_type("Unknown") == "Unclassified"
    assert _r121_display_case_type("") == "Unclassified"
    assert _r121_display_case_type(None) == "Unclassified"
    assert _r121_display_case_type(float("nan")) == "Unclassified"
    # Genuine classified types pass through unchanged.
    assert _r121_display_case_type("break_fix_technical") == "break_fix_technical"
    assert _r121_display_case_type("provisioning_request") == "provisioning_request"


def test_g4b_lifecycle_table_uses_display_helper_source_shape():
    src = _EI.read_text(encoding="utf-8")
    assert_in_source(src, "Round 121 / G4b", label='src')
    assert_in_source(src, "_r121_display_case_type(", label='src')
    # The bare str(...case_type_class...'unknown')) cell assignment is gone.
    assert "cells[6].text = str(row.get('case_type_class', 'unknown'))" not in src
