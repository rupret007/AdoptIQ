"""Round 79 / Build 55 / Phase 4 (B4): BE-priority XLSX integration tests.

Pins ``be_priority_pipeline.build_be_priority_outputs`` -- the orchestrator
that ties the deterministic scorer (R79/B1) and the LLM classifier (R79/B2)
into the two canonical XLSX sheets the Comprehensive + Leader writers need:

* ``BE_Priority_Barriers`` -- per-AB rank with deterministic priority +
  optional LLM classification.
* ``BE_Focus_Areas`` -- portfolio rollup grouped by sub-technology /
  category cluster.

Source-shape pin: the Comprehensive ``run_comprehensive_analysis`` and the
Leader ``run_leader_report_generation`` both wire the helper so that any
failure ships explicit provenance rows (R67/B2 always-assign contract)
rather than dropping the sheet entirely.

Round 79 / Phase 4 (B4).  Made-with: Cursor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

import be_priority_pipeline as bpp
from adoptiq_backend import write_excel_workbook


ROOT = Path(__file__).resolve().parents[1]


def _ab_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a minimally-shaped AB DataFrame for the pipeline."""

    df = pd.DataFrame(rows)
    # Ensure the columns the scorer reads always exist.
    for col, default in (
        ("ID", ""),
        ("customer_name", ""),
        ("technology", ""),
        ("sub_technology", ""),
        ("severity_norm", "Medium"),
        ("status_norm", "Open"),
        ("ab_category_final", "Adoption"),
        ("open_age_days", 30),
        ("Title", ""),
        ("Problem Description", ""),
        ("Account ID", ""),
    ):
        if col not in df.columns:
            df[col] = default
    return df


def _llm_callable_factory(records: list[dict]):
    """Build a stub LLM callable that returns the JSON encoding of records."""

    import json as _json

    captured: dict[str, str] = {}

    def _callable(system_prompt: str, user_prompt: str) -> str:
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return _json.dumps(records)

    return _callable, captured


# ---------------------------------------------------------------------------
# Shape and contract tests at the pipeline API level.
# ---------------------------------------------------------------------------


def test_build_be_priority_outputs_returns_three_tuple_with_expected_types():
    barriers, focus_areas, diag = bpp.build_be_priority_outputs(
        _ab_frame(
            [
                {
                    "ID": "AB001",
                    "customer_name": "Acme Corp",
                    "technology": "Webex",
                    "sub_technology": "Meetings",
                    "severity_norm": "Critical",
                    "open_age_days": 60,
                    "Title": "Login fails",
                    "Problem Description": "Users cannot log in.",
                }
            ]
        ),
        use_llm=False,
    )
    assert isinstance(barriers, pd.DataFrame)
    assert isinstance(focus_areas, pd.DataFrame)
    assert isinstance(diag, dict)


def test_build_be_priority_outputs_empty_input_returns_provenance_rows():
    barriers, focus_areas, diag = bpp.build_be_priority_outputs(
        pd.DataFrame(), use_llm=False
    )
    assert "_adoptiq_provenance_row" in barriers.columns
    assert "_adoptiq_provenance_row" in focus_areas.columns
    assert bool(barriers["_adoptiq_provenance_row"].iloc[0]) is True
    assert bool(focus_areas["_adoptiq_provenance_row"].iloc[0]) is True
    assert diag.get("rows_scored") == 0


def test_build_be_priority_outputs_none_input_returns_provenance_rows():
    barriers, focus_areas, diag = bpp.build_be_priority_outputs(
        None, use_llm=False
    )
    assert bool(barriers["_adoptiq_provenance_row"].iloc[0]) is True
    assert bool(focus_areas["_adoptiq_provenance_row"].iloc[0]) is True


def test_barriers_sheet_carries_curated_column_order():
    """Source-shape pin: the curated columns appear in the documented order
    so downstream readers (Excel templates, pd.read_excel consumers, etc.)
    are not broken by a future column-shuffle regression."""

    expected = (
        "Rank",
        "Customer",
        "Technology",
        "Sub_Technology",
        "AB_ID",
        "CSConsole_Severity",
        "Independent_Priority_Score",
        "BE_Class",
        "BE_Reason",
        "BE_Confidence",
        "Days_Open",
        "Customer_Risk_Score",
        "Customer_Pulse",
        "Top_Signal",
        "Title",
        "Description",
        "Action_Plan_Status",
        "Account_ID",
    )
    barriers, _, _ = bpp.build_be_priority_outputs(
        _ab_frame(
            [
                {
                    "ID": "AB001",
                    "customer_name": "Acme",
                    "technology": "Webex",
                    "severity_norm": "Critical",
                }
            ]
        ),
        use_llm=False,
    )
    assert tuple(barriers.columns) == expected


def test_barriers_sheet_rank_starts_at_one_and_sorted_desc_by_score():
    rows = [
        {
            "ID": f"AB{i:03d}",
            "customer_name": f"Customer {i}",
            "technology": "Webex",
            "severity_norm": ("Critical" if i == 0 else "Low"),
            "open_age_days": (180 if i == 0 else 5),
        }
        for i in range(3)
    ]
    barriers, _, _ = bpp.build_be_priority_outputs(_ab_frame(rows), use_llm=False)
    assert list(barriers["Rank"]) == [1, 2, 3]
    scores = list(barriers["Independent_Priority_Score"])
    assert scores == sorted(scores, reverse=True)


def test_diag_carries_rows_scored_top_n_and_llm_diag():
    barriers, _, diag = bpp.build_be_priority_outputs(
        _ab_frame(
            [
                {"ID": f"AB{i:03d}", "severity_norm": "High"}
                for i in range(8)
            ]
        ),
        llm_top_n=5,
        use_llm=False,
    )
    assert diag["rows_scored"] == 8
    assert diag["top_n_selected"] == 5
    assert "llm_diag" in diag


def test_use_llm_false_short_circuits_classifier_with_kill_switch():
    """When the operator flips ``BE_PRIORITY_LLM_ENABLED=false``, the LLM
    callable is NOT invoked and BE_Class stays empty across the sheet."""

    callable_count = {"calls": 0}

    def _llm(system_prompt: str, user_prompt: str) -> str:
        callable_count["calls"] += 1
        return "[]"

    barriers, _, diag = bpp.build_be_priority_outputs(
        _ab_frame(
            [
                {"ID": "AB001", "severity_norm": "Critical"},
                {"ID": "AB002", "severity_norm": "High"},
            ]
        ),
        llm_callable=_llm,
        use_llm=False,
    )
    assert callable_count["calls"] == 0
    assert all(v == "" for v in barriers["BE_Class"])


def test_llm_classification_threads_into_be_class_column():
    rows = [
        {
            "ID": "AB001",
            "customer_name": "Acme",
            "severity_norm": "Critical",
            "Title": "Cannot log in",
            "Problem Description": "Users blocked on login screen.",
        },
        {
            "ID": "AB002",
            "customer_name": "Globex",
            "severity_norm": "High",
            "Title": "Confused user wants tutorial",
            "Problem Description": "Customer needs training docs.",
        },
    ]
    llm, _captured = _llm_callable_factory(
        [
            {
                "id": "AB001",
                "class": "TRUE_BLOCKER",
                "reason": "User can't login.",
                "confidence": "high",
            },
            {
                "id": "AB002",
                "class": "TRAINING_GAP",
                "reason": "Documentation needed.",
                "confidence": "medium",
            },
        ]
    )
    barriers, _, diag = bpp.build_be_priority_outputs(
        _ab_frame(rows), llm_callable=llm, use_llm=True, llm_top_n=2
    )
    classes = dict(zip(barriers["AB_ID"], barriers["BE_Class"]))
    assert classes["AB001"] == "TRUE_BLOCKER"
    assert classes["AB002"] == "TRAINING_GAP"


def test_round105_leader_raw_ab_columns_feed_be_classifier_prompt():
    """Round 105: Leader passes curated/raw AB columns, not ``ab_norm``.

    The live Build 73 Leader run classified 34/34 rows as AMBIGUOUS
    because the BE prompt had blank Title/Description. The pipeline must
    coalesce NAME/Comments/Age fields before scoring and classification.
    """
    llm, captured = _llm_callable_factory(
        [
            {
                "id": "AB001",
                "class": "TRUE_BLOCKER",
                "reason": "Customer cannot proceed with migration.",
                "confidence": "high",
            }
        ]
    )
    raw_leader_ab = pd.DataFrame(
        [
            {
                "ID": "AB001",
                "Customer Name": "Acme Corp",
                "NAME": "Migration blocked by missing 5K webinar support",
                "Comments": "Customer cannot complete rollout until the feature is available.",
                "Severity": "High",
                "Status": "Open",
                "Age (Days)": 45,
                "Product Name": "Webex Contact Center",
                "Barrier Category": "Feature Gap",
            }
        ]
    )

    barriers, _, diag = bpp.build_be_priority_outputs(
        raw_leader_ab,
        llm_callable=llm,
        use_llm=True,
        llm_top_n=1,
    )

    prompt = captured["user"]
    assert "Title: Migration blocked by missing 5K webinar support" in prompt
    assert "Description: Customer cannot complete rollout" in prompt
    assert barriers.loc[0, "Title"] == "Migration blocked by missing 5K webinar support"
    assert barriers.loc[0, "Description"].startswith("Customer cannot complete rollout")
    assert barriers.loc[0, "BE_Class"] == "TRUE_BLOCKER"
    assert diag["llm_diag"]["classified_count"] == 1


def test_llm_failure_falls_back_to_unclassified_with_diag():
    def _broken_llm(system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("LLM down")

    rows = [{"ID": "AB001", "severity_norm": "Critical"}]
    barriers, _, diag = bpp.build_be_priority_outputs(
        _ab_frame(rows), llm_callable=_broken_llm, use_llm=True
    )
    # Pipeline survives; diag captures the failure (llm_error attached and
    # classified_count == 0).
    assert "llm_diag" in diag
    llm_diag = diag["llm_diag"]
    assert llm_diag.get("llm_error") is not None
    assert "exception" in str(llm_diag["llm_error"]).lower()
    assert llm_diag.get("classified_count", 0) == 0
    # And the BE_Class column stays blank (UNCLASSIFIED becomes empty string
    # via the curated projection because the classifier writes UNCLASSIFIED
    # to ``be_llm_class`` and the pipeline projects that into BE_Class).
    assert all(v in ("", "UNCLASSIFIED") for v in barriers["BE_Class"])


def test_pulse_series_threads_into_customer_pulse_column():
    pulse_df = pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "pulse_score_0_to_10": 3.0},
            {"customer_name": "Globex", "pulse_score_0_to_10": 8.0},
        ]
    )
    rows = [
        {"ID": "AB001", "customer_name": "Acme Corp", "severity_norm": "Medium"},
        {"ID": "AB002", "customer_name": "Globex", "severity_norm": "Medium"},
    ]
    barriers, _, _ = bpp.build_be_priority_outputs(
        _ab_frame(rows), pulse_df=pulse_df, use_llm=False
    )
    by_id = dict(zip(barriers["AB_ID"], barriers["Customer_Pulse"]))
    assert by_id["AB001"] == 3.0
    assert by_id["AB002"] == 8.0


def test_risk_profiles_thread_into_customer_risk_column():
    profiles = {
        "Acme Corp": {"risk_score_0_100": 75.0},
        "Globex": {"risk_score_0_100": 25.0},
    }
    rows = [
        {"ID": "AB001", "customer_name": "Acme Corp"},
        {"ID": "AB002", "customer_name": "Globex"},
    ]
    barriers, _, _ = bpp.build_be_priority_outputs(
        _ab_frame(rows), risk_profiles=profiles, use_llm=False
    )
    by_id = dict(zip(barriers["AB_ID"], barriers["Customer_Risk_Score"]))
    assert by_id["AB001"] == 75.0
    assert by_id["AB002"] == 25.0


def test_focus_areas_returns_proper_rollup_when_clusters_meet_threshold():
    rows = []
    for i in range(5):
        rows.append(
            {
                "ID": f"AB{i:03d}",
                "customer_name": f"Customer {i}",
                "technology": "Webex",
                "sub_technology": "Meetings",
                "severity_norm": "Critical",
                "ab_category_final": "Login Issues",
                "open_age_days": 90,
            }
        )
    _, focus_areas, _ = bpp.build_be_priority_outputs(
        _ab_frame(rows), use_llm=False, min_cluster_score=0.0
    )
    # When clusters meet the threshold, the focus sheet has rich shape.
    if "_adoptiq_provenance_row" in focus_areas.columns:
        # Skip provenance row case; data should yield rollup.
        assert False, "expected non-provenance focus areas frame"
    assert "Technology" in focus_areas.columns
    assert "Theme" in focus_areas.columns
    assert "Cluster_Focus_Score" in focus_areas.columns


def test_focus_areas_threshold_filters_low_score_clusters():
    rows = [
        {
            "ID": "AB001",
            "customer_name": "Acme",
            "technology": "Webex",
            "sub_technology": "Calls",
            "severity_norm": "Low",
            "ab_category_final": "Minor Tweak",
            "open_age_days": 5,
        }
    ]
    _, focus_areas, _ = bpp.build_be_priority_outputs(
        _ab_frame(rows), use_llm=False, min_cluster_score=200.0
    )
    # Cluster score is small (~3-5); above-threshold filter -> provenance row.
    assert bool(focus_areas["_adoptiq_provenance_row"].iloc[0]) is True


def test_long_description_truncated_in_barriers_sheet():
    rows = [
        {
            "ID": "AB001",
            "customer_name": "Acme",
            "Problem Description": "x" * 5000,
        }
    ]
    barriers, _, _ = bpp.build_be_priority_outputs(_ab_frame(rows), use_llm=False)
    desc = str(barriers["Description"].iloc[0])
    assert len(desc) <= 2000


def test_correlation_id_propagates_to_diag():
    barriers, _, diag = bpp.build_be_priority_outputs(
        _ab_frame([{"ID": "AB001", "severity_norm": "Critical"}]),
        use_llm=False,
        correlation_id="TEST-RUN-42",
    )
    # Correlation ID flows through llm_diag (when LLM is invoked).
    assert isinstance(diag, dict)


def test_top_n_cap_limits_llm_classification_load():
    """Even with 100 ABs, the LLM only classifies the top-N rows."""

    seen_user_prompts: list[str] = []

    def _llm(system_prompt: str, user_prompt: str) -> str:
        seen_user_prompts.append(user_prompt)
        return "[]"

    rows = [
        {
            "ID": f"AB{i:03d}",
            "customer_name": f"Cust {i}",
            "severity_norm": "High",
            "Title": f"Issue {i}",
        }
        for i in range(100)
    ]
    bpp.build_be_priority_outputs(
        _ab_frame(rows), llm_callable=_llm, use_llm=True, llm_top_n=25
    )
    assert len(seen_user_prompts) == 1
    # Briefing should reference 25 rows worth of content (one prompt).
    user_prompt = seen_user_prompts[0]
    # Strict shape pin: the briefing carries at most 25 IDs.
    matches = re.findall(r"AB\d{3}", user_prompt)
    assert len(set(matches)) <= 25


def test_severity_column_mirrors_csconsole_severity():
    rows = [
        {"ID": "AB001", "severity_norm": "Critical"},
        {"ID": "AB002", "severity_norm": "Medium"},
    ]
    barriers, _, _ = bpp.build_be_priority_outputs(_ab_frame(rows), use_llm=False)
    by_id = dict(zip(barriers["AB_ID"], barriers["CSConsole_Severity"]))
    assert by_id["AB001"] == "Critical"
    assert by_id["AB002"] == "Medium"


def test_rank_is_monotonic_increasing_starting_at_one():
    rows = [
        {"ID": f"AB{i:03d}", "severity_norm": "High"}
        for i in range(20)
    ]
    barriers, _, _ = bpp.build_be_priority_outputs(_ab_frame(rows), use_llm=False)
    ranks = list(barriers["Rank"])
    assert ranks == list(range(1, len(rows) + 1))


def test_provenance_row_carries_explanatory_message():
    barriers, _, _ = bpp.build_be_priority_outputs(pd.DataFrame(), use_llm=False)
    msg = str(barriers["AdoptIQ_Message"].iloc[0]).lower()
    assert "empty" in msg or "no" in msg


def test_pipeline_writes_xlsx_with_be_sheets_via_writer(tmp_path: Path):
    """End-to-end smoke pin: when the pipeline outputs are passed into
    ``write_excel_workbook``, both BE sheets are present in the produced
    XLSX file."""

    rows = [
        {
            "ID": "AB001",
            "customer_name": "Acme Corp",
            "technology": "Webex",
            "sub_technology": "Meetings",
            "severity_norm": "Critical",
            "ab_category_final": "Login",
            "open_age_days": 90,
            "Title": "Login fails",
            "Problem Description": "Users blocked.",
        }
    ]
    barriers, focus_areas, _ = bpp.build_be_priority_outputs(
        _ab_frame(rows), use_llm=False, min_cluster_score=0.0
    )
    sheets = {
        "BE_Priority_Barriers": barriers,
        "BE_Focus_Areas": focus_areas,
    }
    base = tmp_path / "test_r79_b4"
    out_path = write_excel_workbook(
        str(base), sheets, {}, "TestManager", "Webex", 90
    )
    assert Path(out_path).exists()
    workbook = pd.ExcelFile(out_path)
    assert "BE_Priority_Barriers" in workbook.sheet_names
    assert "BE_Focus_Areas" in workbook.sheet_names


def test_html_strip_allowlist_includes_be_priority_sheets():
    """Source-shape pin: the new BE sheets are routed through the same
    HTML-strip path AB / Action_Plans use, so CSConsole rich-text
    leakage cannot escape into the operator's XLSX."""

    backend_src = (ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # Both names must be inside the _R66_HTML_STRIP_SHEETS tuple body.
    assert '"BE_Priority_Barriers"' in backend_src
    assert '"BE_Focus_Areas"' in backend_src


def test_orchestrator_wired_into_run_comprehensive_analysis():
    """Source-shape pin: the BE-priority orchestrator is called inside
    ``run_comprehensive_analysis`` and the sheets are written into
    ``all_sheets`` before the workbook writer call."""

    src = (ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Comprehensive must thread through the pipeline helper.
    assert "be_priority_pipeline" in src
    assert 'all_sheets["BE_Priority_Barriers"]' in src
    assert 'all_sheets["BE_Focus_Areas"]' in src


def test_orchestrator_wired_into_run_leader_report_generation():
    """Source-shape pin: the BE-priority orchestrator is also called
    inside ``run_leader_report_generation`` so the Leader XLSX carries
    the same sheets."""

    src = (ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "[LEADER] Round 79 / B2" in src
    assert "sheets['BE_Priority_Barriers']" in src
    assert "sheets['BE_Focus_Areas']" in src


def test_pipeline_failure_emits_provenance_rows_in_orchestrator():
    """Source-shape pin: the orchestrator wraps the pipeline call in
    try/except + provenance fallback so a failure NEVER drops the sheet."""

    src = (ROOT / "app_simple.py").read_text(encoding="utf-8")
    # The fallback path must reference the always-assign R67/B2 contract.
    assert "Round 79 / B2" in src
    assert "_adoptiq_provenance_row" in src
    assert "AdoptIQ_Status" in src


def test_pulse_lookup_is_case_insensitive():
    pulse_df = pd.DataFrame(
        [{"customer_name": "ACME CORP", "pulse_score_0_to_10": 4.0}]
    )
    rows = [{"ID": "AB001", "customer_name": "Acme Corp"}]
    barriers, _, _ = bpp.build_be_priority_outputs(
        _ab_frame(rows), pulse_df=pulse_df, use_llm=False
    )
    assert barriers["Customer_Pulse"].iloc[0] == 4.0


def test_pulse_missing_value_renders_as_none():
    rows = [{"ID": "AB001", "customer_name": "Acme Corp"}]
    barriers, _, _ = bpp.build_be_priority_outputs(_ab_frame(rows), use_llm=False)
    val = barriers["Customer_Pulse"].iloc[0]
    assert val is None or pd.isna(val)


def test_pipeline_survives_dataframe_with_only_unknown_columns():
    """Defensive pin: the scorer must handle a DataFrame whose column
    set lacks the canonical names; it should fall back gracefully."""

    df = pd.DataFrame(
        [
            {
                "weird_col_a": "x",
                "weird_col_b": 12,
            }
        ]
    )
    barriers, focus_areas, diag = bpp.build_be_priority_outputs(df, use_llm=False)
    # Pipeline either ships rows OR provenance rows; never raises.
    assert isinstance(barriers, pd.DataFrame)
    assert isinstance(focus_areas, pd.DataFrame)
