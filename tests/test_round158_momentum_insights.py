"""Round 158 — momentum insights (Brian item 6: "is it getting better?") and
the report ↔ Ask AI consistency lock.

* `canonical_metrics.window_momentum` / `pulse_score_momentum`: deterministic
  first-half vs second-half comparison inside the analysis window.  Undated
  rows excluded AND disclosed; no dated rows (or a one-sided pulse average)
  → ``None`` — refuse rather than fabricate a trend.
* The concise report renders a "Momentum within this window" paragraph only
  when the source state is trustworthy (offline fixtures are partial →
  renders nothing there → zero oracle churn; proven by the parity suite).
* Cross-surface lock: for the same facts, Ask AI's DECISION_CONTEXT ranking
  must equal the report risk table's ordering — the two surfaces can never
  name a different "top risk customer".
"""

from __future__ import annotations

import pandas as pd
import pytest

from canonical_metrics import (
    pulse_score_momentum,
    reporting_window_bounds,
    window_momentum,
)

AS_OF = pd.Timestamp("2026-08-03T12:00:00Z")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ADOPTIQ_RISK_SCORING_PROFILE", raising=False)
    yield


# ---------------------------------------------------------------------------
# window_momentum core semantics
# ---------------------------------------------------------------------------
def _dated(*dates, col="OPEN_DATE_C"):
    return pd.DataFrame([{col: d} for d in dates])


def test_momentum_rising_easing_steady():
    # 90-day window ending AS_OF: mid = AS_OF - 45d
    early = "2026-05-10"   # first half
    late = "2026-07-30"    # second half
    rising = window_momentum(_dated(early, late, late), date_columns=("OPEN_DATE_C",), as_of=AS_OF, days=90)
    assert rising["direction"] == "rising" and rising["first_half"] == 1 and rising["second_half"] == 2
    easing = window_momentum(_dated(early, early, late), date_columns=("OPEN_DATE_C",), as_of=AS_OF, days=90)
    assert easing["direction"] == "easing"
    steady = window_momentum(_dated(early, late), date_columns=("OPEN_DATE_C",), as_of=AS_OF, days=90)
    assert steady["direction"] == "steady"


def test_momentum_boundaries_are_deterministic():
    """Records exactly at the window start, midpoint, and end must land in a
    defined half: [start, mid) first, [mid, end] second."""
    start_at, end_at, _ = reporting_window_bounds(as_of=AS_OF, days=90)
    start = start_at.isoformat()
    mid = (start_at + pd.Timedelta(days=45)).isoformat()
    end = end_at.isoformat()
    mom = window_momentum(_dated(start, mid, end), date_columns=("OPEN_DATE_C",), as_of=AS_OF, days=90)
    assert mom["first_half"] == 1   # start only
    assert mom["second_half"] == 2  # mid + end (mid is inclusive-second)


def test_momentum_excludes_and_discloses_undated_and_out_of_window():
    df = _dated("2026-07-30", "not-a-date", None, "2020-01-01")
    mom = window_momentum(df, date_columns=("OPEN_DATE_C",), as_of=AS_OF, days=90)
    assert mom["second_half"] == 1 and mom["first_half"] == 0
    assert mom["undated"] == 2          # unparseable + None disclosed
    # 2020 row is dated but out of window: not counted, not "undated"


def test_momentum_refuses_without_dates_or_data():
    assert window_momentum(None, date_columns=("OPEN_DATE_C",), as_of=AS_OF, days=90) is None
    assert window_momentum(pd.DataFrame(), date_columns=("OPEN_DATE_C",), as_of=AS_OF, days=90) is None
    assert window_momentum(_dated("2020-01-01"), date_columns=("OPEN_DATE_C",), as_of=AS_OF, days=90) is None
    no_col = pd.DataFrame([{"OTHER": "x"}])
    assert window_momentum(no_col, date_columns=("OPEN_DATE_C",), as_of=AS_OF, days=90) is None
    assert window_momentum(_dated("2026-07-30"), date_columns=("OPEN_DATE_C",), as_of="garbage", days=90) is None


def test_momentum_uses_first_present_date_column():
    df = pd.DataFrame([{"Open Date": "2026-07-30"}])
    mom = window_momentum(df, date_columns=("OPEN_DATE_C", "Open Date"), as_of=AS_OF, days=90)
    assert mom is not None and mom["date_column"] == "Open Date"


def test_momentum_tz_aware_and_naive_dates_compare_cleanly():
    df = _dated("2026-07-30T10:00:00+02:00", "2026-05-10")
    mom = window_momentum(df, date_columns=("OPEN_DATE_C",), as_of=AS_OF, days=90)
    assert mom["first_half"] == 1 and mom["second_half"] == 1


# ---------------------------------------------------------------------------
# pulse_score_momentum
# ---------------------------------------------------------------------------
def _pulse(*rows):
    return pd.DataFrame(list(rows))


def test_pulse_momentum_improving_and_declining():
    improving = pulse_score_momentum(_pulse(
        {"PULSE_DATE_C": "2026-05-10", "SCORE__C": 4.0},
        {"PULSE_DATE_C": "2026-07-30", "SCORE__C": 8.0},
    ), as_of=AS_OF, days=90)
    assert improving["direction"] == "improving"
    assert improving["first_half_avg"] == 4.0 and improving["second_half_avg"] == 8.0
    declining = pulse_score_momentum(_pulse(
        {"PULSE_DATE_C": "2026-05-10", "SCORE__C": 8.0},
        {"PULSE_DATE_C": "2026-07-30", "SCORE__C": 3.0},
    ), as_of=AS_OF, days=90)
    assert declining["direction"] == "declining"


def test_pulse_momentum_refuses_one_sided_data():
    """An average on only one half is not a trend — must refuse."""
    one_sided = pulse_score_momentum(_pulse(
        {"PULSE_DATE_C": "2026-07-30", "SCORE__C": 8.0},
        {"PULSE_DATE_C": "2026-07-20", "SCORE__C": 6.0},
    ), as_of=AS_OF, days=90)
    assert one_sided is None
    assert pulse_score_momentum(_pulse({"PULSE_DATE_C": "2026-07-30"}), as_of=AS_OF, days=90) is None
    assert pulse_score_momentum(None, as_of=AS_OF, days=90) is None


# ---------------------------------------------------------------------------
# report rendering: momentum paragraph via the real pipeline
# ---------------------------------------------------------------------------
def _build_facts(tac_rows, pulse_rows):
    import decision_report_delivery as delivery

    team_data = {
        "Alex Rivera": {
            "subscriptions": pd.DataFrame([{"SUBSCRIPTION_ID": "S1", "BU_NAME": "Acme", "STATUS_C": "Active"}]),
            "action_plans": pd.DataFrame([{"ID": "AP1", "BU_NAME": "Acme", "SUBJECT_C": "t", "STATUS_C": "Open", "CREATED_DATE_C": "2026-07-25"}]),
            "adoption_barriers": pd.DataFrame([{"ID": "AB1", "BU_NAME": "Acme", "SEVERITY_C": "High", "AB_STATUS_C": "Open", "OPEN_DATE_C": "2026-05-15"}]),
            "customer_pulse": pd.DataFrame(pulse_rows),
            "tac_cases": pd.DataFrame(tac_rows),
            "success_priorities": pd.DataFrame(),
        }
    }
    facts = delivery.build_report_facts(
        team_data,
        report_type="Leader", scope_type="team", scope_value="Alex Rivera's Team",
        manager_name="Alex Rivera", days=90, as_of=AS_OF,
        data_as_of_utc=AS_OF.isoformat(), data_as_of_state="available",
    )
    return delivery, facts


def test_momentum_paragraph_renders_with_real_pipeline():
    delivery, facts = _build_facts(
        tac_rows=[
            {"SR Number": "1", "BU_NAME": "Acme", "Severity": "P1", "Case Status": "Open", "Date/Time Opened": "2026-07-30"},
            {"SR Number": "2", "BU_NAME": "Acme", "Severity": "P3", "Case Status": "Open", "Date/Time Opened": "2026-07-28"},
            {"SR Number": "3", "BU_NAME": "Acme", "Severity": "P3", "Case Status": "Open", "Date/Time Opened": "2026-05-10"},
        ],
        pulse_rows=[
            {"ID": "CP-001", "BU_NAME": "Acme", "SCORE__C": 4.0, "PULSE_DATE_C": "2026-05-10"},
            {"ID": "CP-002", "BU_NAME": "Acme", "SCORE__C": 8.0, "PULSE_DATE_C": "2026-07-30"},
        ],
    )
    doc = delivery.build_concise_word_document(facts)
    lines = [p.text for p in doc.paragraphs if p.text.startswith("Momentum within this window:")]
    assert len(lines) == 1
    line = lines[0]
    assert "TAC cases opened rising — 2 in the last 45 days vs 1 in the prior half" in line
    assert "Pulse improving — avg score 4 → 8" in line
    # contract still holds with the paragraph present
    sheets = delivery.build_source_data_sheets(facts)
    contract = delivery.validate_cross_artifact_contract(facts, sheets, doc)
    assert contract.get("ok"), contract.get("errors")


def test_round161_momentum_uses_evaluation_clock_when_public_as_of_differs():
    """R161: momentum must anchor on evaluation_as_of_utc, not blank public as_of_utc."""
    import decision_report_delivery as delivery

    team_data = {
        "Alex Rivera": {
            "subscriptions": pd.DataFrame([{"SUBSCRIPTION_ID": "S1", "BU_NAME": "Acme", "STATUS_C": "Active"}]),
            "action_plans": pd.DataFrame([{"ID": "AP1", "BU_NAME": "Acme", "SUBJECT_C": "t", "STATUS_C": "Open", "CREATED_DATE_C": "2026-07-25"}]),
            "adoption_barriers": pd.DataFrame([{"ID": "AB1", "BU_NAME": "Acme", "SEVERITY_C": "High", "AB_STATUS_C": "Open", "OPEN_DATE_C": "2026-05-15"}]),
            "customer_pulse": pd.DataFrame([
                {"ID": "CP-001", "BU_NAME": "Acme", "SCORE__C": 4.0, "PULSE_DATE_C": "2026-05-10"},
                {"ID": "CP-002", "BU_NAME": "Acme", "SCORE__C": 8.0, "PULSE_DATE_C": "2026-07-30"},
            ]),
            "tac_cases": pd.DataFrame([
                {"SR Number": "1", "BU_NAME": "Acme", "Severity": "P1", "Case Status": "Open", "Date/Time Opened": "2026-07-30"},
                {"SR Number": "2", "BU_NAME": "Acme", "Severity": "P3", "Case Status": "Open", "Date/Time Opened": "2026-05-10"},
            ]),
            "success_priorities": pd.DataFrame(),
        }
    }
    source_clock = "2026-08-10T12:00:00Z"
    facts = delivery.build_report_facts(
        team_data,
        report_type="Leader",
        scope_type="team",
        scope_value="Alex Rivera's Team",
        manager_name="Alex Rivera",
        days=90,
        as_of=AS_OF,
        data_as_of_utc=source_clock,
        data_as_of_state="available",
    )
    assert facts["as_of_utc"].startswith("2026-08-10")
    assert facts["evaluation_as_of_utc"].startswith("2026-08-03")
    doc = delivery.build_concise_word_document(facts)
    lines = [p.text for p in doc.paragraphs if p.text.startswith("Momentum within this window:")]
    assert len(lines) == 1
    assert "Pulse improving" in lines[0]


def test_momentum_paragraph_absent_without_dated_records():
    delivery, facts = _build_facts(
        tac_rows=[{"SR Number": "1", "BU_NAME": "Acme", "Severity": "P3", "Case Status": "Open"}],
        pulse_rows=[{"ID": "CP-001", "BU_NAME": "Acme", "PULSE_RATING__C": "Good"}],
    )
    doc = delivery.build_concise_word_document(facts)
    momentum = [p.text for p in doc.paragraphs if p.text.startswith("Momentum within this window:")]
    # AB (2026-05-15) and AP (2026-07-25) still carry dates in the shared
    # fixture — assert the TAC/pulse pieces specifically are absent.
    assert all("TAC cases opened" not in t and "Pulse " not in t for t in momentum)


# ---------------------------------------------------------------------------
# cross-surface consistency: DECISION_CONTEXT == report risk table ordering
# ---------------------------------------------------------------------------
def test_decision_context_ordering_matches_report_risk_table():
    from ask_ai_grounded import build_decision_context_block
    import decision_report_delivery as delivery
    import scripts.generate_offline_acceptance_artifacts as gen

    payload = dict(gen.load_sanitized_fixture(gen.DEFAULT_FIXTURE_PATH))
    team_data, labels = gen.build_scope_fixture(payload, "team", delivery)
    team_data = gen._stamp_offline_fixture_sources(team_data)
    facts = delivery.build_report_facts(
        team_data,
        report_type=labels["report_type"], scope_type=labels["scope_type"],
        scope_value=labels["scope_value"], manager_name=str(payload["manager_name"]),
        days=int(payload["days"]), as_of=AS_OF,
        data_as_of_utc=AS_OF.isoformat(), data_as_of_state="available",
    )
    rows = delivery._visible_risk_decision_rows(facts)
    block = build_decision_context_block(facts["risk_profiles"])
    report_order = [row[0] for row in rows]
    block_order = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped[:2] in {f"{i}." for i in range(1, 10)}:
            block_order.append(stripped.split("— ")[0].split(". ", 1)[1].strip())
    assert block_order[: len(report_order)] == report_order, (
        "Ask AI and the report must rank customers identically"
    )
