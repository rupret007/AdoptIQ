"""Round 157 — reporting depth + Ask AI decision-grade grounding.

Three deterministic additions, all zero-oracle-churn:

* **B1 support themes** — `canonical_metrics.tac_theme_summary` + a "Support
  themes (TAC)" paragraph in the concise report (paragraph, not a table, so
  the visible-table contract is untouched; the offline fixture has no
  technology column, so offline artifacts are byte-identical).
* **B4 concentration driver** — when a customer's open TAC cases cluster in
  one technology, the risk drivers name it deterministically.
* **Ask AI decision context** — the canonical risk loop now slices pulse /
  action-plan frames PER CUSTOMER (previously every customer's profile was
  computed from the whole team's pulse/AP rows), and a DECISION_CONTEXT
  prompt block hands the LLM the engine's own top-risk ranking, drivers,
  compound-risk lines, and next-best actions so "who do I call first" is
  answered from the deterministic engine, never model inference.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ask_ai_grounded import (
    _r157_slice_frame_for_customer,
    build_decision_context_block,
)
from canonical_metrics import tac_theme_summary
from risk_scoring import compute_customer_risk_profile

AS_OF = pd.Timestamp("2026-08-03T12:00:00Z")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ADOPTIQ_RISK_SCORING_PROFILE", raising=False)
    yield


# ---------------------------------------------------------------------------
# B1 — canonical support themes
# ---------------------------------------------------------------------------
def _tac(*rows):
    return pd.DataFrame(list(rows))


def test_theme_summary_groups_and_orders_deterministically():
    themes = tac_theme_summary(_tac(
        {"sub_technology": "Webex Calling", "Severity": "P1"},
        {"sub_technology": "webex calling", "Severity": "P3"},
        {"sub_technology": "Control Hub", "Severity": "P2"},
        {"sub_technology": "Meetings", "Severity": "P3"},
    ))
    assert [t["label"] for t in themes] == ["Webex Calling", "Control Hub", "Meetings"]
    assert themes[0]["case_count"] == 2  # case-insensitive merge
    assert themes[0]["escalated_count"] == 1  # P1 only
    assert themes[1]["escalated_count"] == 1  # P2 counts as escalated


def test_theme_summary_skips_unspecific_and_missing_column():
    assert tac_theme_summary(_tac({"sub_technology": "Other / Unclassified", "Severity": "P1"})) == []
    assert tac_theme_summary(_tac({"Severity": "P1"})) == []  # fixture shape -> no themes
    assert tac_theme_summary(None) == []
    assert tac_theme_summary(pd.DataFrame()) == []


def test_theme_summary_caps_at_top_n():
    rows = [{"sub_technology": f"Tech{i}", "Severity": "P3"} for i in range(6)]
    assert len(tac_theme_summary(pd.DataFrame(rows), top_n=3)) == 3


def test_theme_ties_break_alphabetically():
    themes = tac_theme_summary(_tac(
        {"sub_technology": "Zulu", "Severity": "P3"},
        {"sub_technology": "Alpha", "Severity": "P3"},
    ))
    assert [t["label"] for t in themes] == ["Alpha", "Zulu"]


# ---------------------------------------------------------------------------
# B1 — the report paragraph renders live, stays absent offline
# ---------------------------------------------------------------------------
def _facts_with_tac(tac_df):
    """Minimal facts render via the real pipeline: build from a tiny team."""
    import decision_report_delivery as delivery
    import scripts.generate_offline_acceptance_artifacts as gen

    team_data = {
        "Alex Rivera": {
            "subscriptions": pd.DataFrame([{"SUBSCRIPTION_ID": "S1", "BU_NAME": "Acme", "STATUS_C": "Active"}]),
            "action_plans": pd.DataFrame([{"ID": "AP1", "BU_NAME": "Acme", "SUBJECT_C": "t", "STATUS_C": "Open"}]),
            "adoption_barriers": pd.DataFrame([{"ID": "AB1", "BU_NAME": "Acme", "SEVERITY_C": "High", "AB_STATUS_C": "Open", "OPEN_DATE_C": "2026-07-01"}]),
            "customer_pulse": pd.DataFrame([{"ID": "CP-001", "BU_NAME": "Acme", "PULSE_RATING__C": "Good", "SCORE__C": 8}]),
            "tac_cases": pd.DataFrame(tac_df),
            "success_priorities": pd.DataFrame(),
        }
    }
    _ = gen  # stamping helper not needed once frames are real DataFrames
    facts = delivery.build_report_facts(
        team_data,
        report_type="Leader", scope_type="team", scope_value="Alex Rivera's Team",
        manager_name="Alex Rivera", days=90, as_of=AS_OF,
        data_as_of_utc=AS_OF.isoformat(), data_as_of_state="available",
    )
    return delivery, facts


def _doc_paragraph_texts(document):
    return [p.text for p in document.paragraphs if p.text.strip()]


def test_support_themes_paragraph_renders_when_technology_present():
    delivery, facts = _facts_with_tac([
        {"SR Number": "1", "BU_NAME": "Acme", "Severity": "P1", "Case Status": "Open",
         "Date/Time Opened": "2026-07-30", "Tech.": "Webex Calling"},
        {"SR Number": "2", "BU_NAME": "Acme", "Severity": "P3", "Case Status": "Open",
         "Date/Time Opened": "2026-07-28", "Tech.": "Webex Calling"},
    ])
    doc = delivery.build_concise_word_document(facts)
    themes_lines = [t for t in _doc_paragraph_texts(doc) if t.startswith("Support themes (TAC):")]
    assert len(themes_lines) == 1
    assert "Webex Calling — 2 case(s) (1 escalated)" in themes_lines[0]
    assert any(
        "Metric_Lineage / insight.support_themes" in text
        for text in _doc_paragraph_texts(doc)
    )
    # contract must still hold with the paragraph present
    sheets = delivery.build_source_data_sheets(facts)
    contract = delivery.validate_cross_artifact_contract(facts, sheets, doc)
    assert contract.get("ok"), contract.get("errors")


def test_support_themes_absent_for_fixture_shape():
    """The offline fixture's TAC rows carry no technology column — the
    paragraph must be absent, keeping offline artifacts byte-identical."""
    delivery, facts = _facts_with_tac([
        {"SR Number": "1", "BU_NAME": "Acme", "Severity": "P1", "Case Status": "Open",
         "Date/Time Opened": "2026-07-30"},
    ])
    doc = delivery.build_concise_word_document(facts)
    assert not [t for t in _doc_paragraph_texts(doc) if t.startswith("Support themes")]


# ---------------------------------------------------------------------------
# B4 — technology-concentration driver
# ---------------------------------------------------------------------------
def test_concentration_driver_names_dominant_technology():
    csone = pd.DataFrame([
        {"sub_technology": "Webex Calling", "Severity": "P2", "Status": "Open"},
        {"sub_technology": "Webex Calling", "Severity": "P3", "Status": "Open"},
        {"sub_technology": "Meetings", "Severity": "P3", "Status": "Open"},
    ])
    prof = compute_customer_risk_profile("Acme", customer_csone=csone, as_of=AS_OF)
    hits = [f for f in prof["risk_factors"] if "concentrated in Webex Calling" in f]
    assert len(hits) == 1
    assert "2 of 3 open cases" in hits[0]


def test_concentration_driver_requires_majority_and_two_cases():
    # 50/50 split -> no majority -> no driver
    even = pd.DataFrame([
        {"sub_technology": "Webex Calling", "Severity": "P3", "Status": "Open"},
        {"sub_technology": "Meetings", "Severity": "P3", "Status": "Open"},
    ])
    prof = compute_customer_risk_profile("Acme", customer_csone=even, as_of=AS_OF)
    assert not [f for f in prof["risk_factors"] if "concentrated" in f]
    # single case -> no driver
    one = pd.DataFrame([{"sub_technology": "Webex Calling", "Severity": "P3", "Status": "Open"}])
    prof = compute_customer_risk_profile("Acme", customer_csone=one, as_of=AS_OF)
    assert not [f for f in prof["risk_factors"] if "concentrated" in f]


def test_concentration_driver_defers_to_compound_risk():
    """When the compound factor already names the tech overlap, the
    concentration line would be redundant — compound wins."""
    ab = pd.DataFrame([{"sub_technology": "Webex Calling", "SEVERITY_C": "Critical", "AB_STATUS_C": "Open"}])
    csone = pd.DataFrame([
        {"sub_technology": "Webex Calling", "Severity": "P2", "Status": "Open"},
        {"sub_technology": "Webex Calling", "Severity": "P3", "Status": "Open"},
    ])
    prof = compute_customer_risk_profile("Acme", customer_ab=ab, customer_csone=csone, as_of=AS_OF)
    assert any(f.startswith("Compound risk") for f in prof["risk_factors"])
    assert not [f for f in prof["risk_factors"] if "concentrated" in f]


# ---------------------------------------------------------------------------
# Ask AI — per-customer slicing fix
# ---------------------------------------------------------------------------
def test_slice_by_customer_name_column():
    df = pd.DataFrame([
        {"BU_NAME": "Acme", "SCORE__C": 1.0},
        {"BU_NAME": "Beta", "SCORE__C": 9.0},
    ])
    sliced = _r157_slice_frame_for_customer(df, "Acme")
    assert len(sliced) == 1 and sliced.iloc[0]["SCORE__C"] == 1.0


def test_slice_by_account_id_mapping():
    df = pd.DataFrame([
        {"ACCOUNT__C": "A-1", "SCORE__C": 2.0},
        {"ACCOUNT__C": "A-2", "SCORE__C": 8.0},
    ])
    sliced = _r157_slice_frame_for_customer(df, "Acme", {"A-1": "Acme", "A-2": "Beta"})
    assert len(sliced) == 1 and sliced.iloc[0]["SCORE__C"] == 2.0


def test_slice_returns_none_when_unattributable():
    df = pd.DataFrame([{"SCORE__C": 2.0}])  # no name or account column
    assert _r157_slice_frame_for_customer(df, "Acme") is None
    assert _r157_slice_frame_for_customer(pd.DataFrame(), "Acme") is None
    assert _r157_slice_frame_for_customer(None, "Acme") is None


def test_slicing_prevents_cross_customer_pulse_pollution():
    """The bug this fixes: Beta's poor pulse must not raise Acme's risk."""
    team_pulse = pd.DataFrame([
        {"BU_NAME": "Acme", "SCORE__C": 9.0},   # healthy
        {"BU_NAME": "Beta", "SCORE__C": 0.5},   # poor
    ])
    polluted = compute_customer_risk_profile(
        "Acme", customer_pulse=team_pulse, as_of=AS_OF
    )
    clean = compute_customer_risk_profile(
        "Acme",
        customer_pulse=_r157_slice_frame_for_customer(team_pulse, "Acme"),
        as_of=AS_OF,
    )
    assert polluted["components"]["customer_pulse"]["details"]["poor_bad_count"] == 1
    assert clean["components"]["customer_pulse"]["details"]["poor_bad_count"] == 0
    assert clean["risk_score_0_100"] <= polluted["risk_score_0_100"]


# ---------------------------------------------------------------------------
# Ask AI — DECISION_CONTEXT block
# ---------------------------------------------------------------------------
def _profiles():
    high = compute_customer_risk_profile(
        "Acme",
        customer_ab=pd.DataFrame([
            {"sub_technology": "Webex Calling", "SEVERITY_C": "Critical", "AB_STATUS_C": "Open"},
        ]),
        customer_csone=pd.DataFrame([
            {"sub_technology": "Webex Calling", "Severity": "P1", "Status": "Open",
             "Case Status": "Open", "Date/Time Opened": "2026-07-30"},
        ]),
        as_of=AS_OF,
    )
    low = compute_customer_risk_profile("Beta", as_of=AS_OF)
    return {"Beta": low, "Acme": high}


def test_decision_context_ranks_by_score_and_includes_actions():
    block = build_decision_context_block(_profiles())
    assert block.startswith("DECISION_CONTEXT")
    # Acme (higher score) must come first despite dict insertion order
    assert block.index("1. Acme") < block.index("2. Beta")
    assert "next_best_action:" in block
    assert "compound_risk: Compound risk in Webex Calling" in block
    assert "scoring_profile: legacy" in block
    assert "MUST come from DECISION_CONTEXT" in block


def test_decision_context_strips_source_chrome():
    block = build_decision_context_block(_profiles())
    assert "[Source:" not in block


def test_decision_context_empty_inputs_produce_no_block():
    assert build_decision_context_block(None) == ""
    assert build_decision_context_block({}) == ""


def test_decision_context_caps_at_five():
    profiles = {f"C{i}": compute_customer_risk_profile(f"C{i}", as_of=AS_OF) for i in range(9)}
    block = build_decision_context_block(profiles)
    assert "5." in block and "6." not in block


def test_decision_context_discloses_active_scoring_profile(monkeypatch):
    monkeypatch.setenv("ADOPTIQ_RISK_SCORING_PROFILE", "magnitude_first")
    block = build_decision_context_block(_profiles())
    assert "scoring_profile: magnitude_first" in block
