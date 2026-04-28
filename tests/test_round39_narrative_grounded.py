"""Round 39 / Phase 1.3 — narrative health branches grounded in real numbers.

Pre-Round-39 ``_add_individual_summary_paragraph`` rendered "requires
immediate attention with high volumes of adoption barriers and
technical issues" whenever
``total_barriers > total_customers OR total_tac_cases > total_customers * 0.5``.

In the Brian Frazier 90d audit William Phillips's portfolio (0
adoption barriers, 5 TAC cases, 2 customers) tripped the second clause
(``5 > 1``) and printed the boilerplate -- which is a lie since
"adoption barriers" was zero.  The phrase appeared 10 of 11 times in
the rendered Word document, eroding executive trust.

Round 39 / Phase 1.3 replaces the coarse comparators with per-customer
rates AND absolute-volume floors:

  * Healthy:      AB == 0 AND TAC == 0
  * Manageable:   AB + TAC <= 5
  * Generally healthy:  AB rate <= 0.5/customer AND TAC rate <= 0.3/customer
  * High volume:  (AB >= 10 AND AB rate > 1/customer)
                  OR (TAC >= 10 AND TAC rate > 0.5/customer)
  * Mixed:        catch-all (never claims "high volumes")

This file pins each branch with a portfolio shaped to land on it.
"""
from __future__ import annotations

import pandas as pd
import pytest
from unittest import mock


@pytest.fixture
def generator():
    from leader_report_generator import LeaderReportGenerator
    gen = LeaderReportGenerator(mock.MagicMock(), [
        ("manager@example.com", "Test User", "Manager"),
    ])
    # Stub out the sentiment derivation so the test doesn't need a
    # real customer_pulse score column.
    gen._derive_sentiment_summary = mock.MagicMock(return_value="Neutral")
    # Stub out arr_sentiment_analyzer to None (already is None per __init__).
    return gen


def _data(*, customers, abs_count=0, aps_count=0, cps_count=0, tacs_count=0):
    """Build a per-CSSM data dict with controlled counts.  Each
    DataFrame has ``BU_NAME`` so downstream column-existence guards
    don't short-circuit the row past where Phase 1.3 logic runs."""
    def _df(n):
        return pd.DataFrame({"BU_NAME": ["Customer X"] * n}) if n else pd.DataFrame()
    return {
        "customers": list(customers),
        "subscriptions": pd.DataFrame(),
        "adoption_barriers": _df(abs_count),
        "action_plans": _df(aps_count),
        "customer_pulse": _df(cps_count),
        "tac_cases": _df(tacs_count),
    }


def _last_paragraph_text(generator):
    """Extract concatenated text from all paragraphs the renderer
    added (the renderer can add multiple paragraphs per call).
    Strips trailing empty paragraphs introduced by spacing logic."""
    paras = [p.text for p in generator.doc.paragraphs if p.text]
    return "\n".join(paras) if paras else ""


# ---------------------------------------------------------------------------
# The audit's named regression: William Phillips portfolio shape.
# ---------------------------------------------------------------------------


def test_william_phillips_shape_does_not_say_high_volumes(generator):
    """0 adoption barriers, 5 TAC, 2 customers MUST NOT render
    'high volumes' or 'requires immediate attention' -- those phrases
    were the audit's named false-alarm pattern."""
    data = _data(customers=["Cust 1", "Cust 2"], abs_count=0, tacs_count=5)
    generator._add_individual_summary_paragraph("William Phillips", data, days=90)
    text = _last_paragraph_text(generator).lower()
    assert "high volumes" not in text, (
        "Round 39 / Phase 1.3: the William-Phillips-shaped portfolio "
        "(0 ABs, 5 TAC, 2 customers) hit the pre-Round-39 'high volumes' "
        "branch on the TAC * 0.5 comparator alone.  The new thresholds "
        "must NOT trip 'high volumes' for this shape."
    )
    assert "immediate attention" not in text, (
        "Round 39 / Phase 1.3: same portfolio must not say "
        "'requires immediate attention' -- the absolute floor of 10 "
        "TAC cases prevents this."
    )
    # Sanity: SOME health language did get rendered (we exercised the path).
    assert text, "renderer produced an empty paragraph for William Phillips"


# ---------------------------------------------------------------------------
# Branch matrix.
# ---------------------------------------------------------------------------


def test_zero_signals_renders_excellent_health(generator):
    data = _data(customers=["A", "B"], abs_count=0, tacs_count=0)
    generator._add_individual_summary_paragraph("Healthy CSSM", data, days=90)
    text = _last_paragraph_text(generator).lower()
    assert "excellent health" in text


def test_manageable_load_threshold(generator):
    """AB+TAC == 5 must land on the 'manageable load' branch."""
    data = _data(customers=["A", "B"], abs_count=2, tacs_count=3)
    generator._add_individual_summary_paragraph("Steady CSSM", data, days=90)
    text = _last_paragraph_text(generator).lower()
    assert "manageable load" in text


def test_high_volume_only_when_absolute_floor_and_rate(generator):
    """Genuine high-volume: 12 ABs / 5 customers (rate=2.4)."""
    data = _data(customers=["A", "B", "C", "D", "E"], abs_count=12, tacs_count=0)
    generator._add_individual_summary_paragraph("Hot CSSM", data, days=90)
    text = _last_paragraph_text(generator).lower()
    assert "immediate attention" in text


def test_high_absolute_but_low_rate_does_not_trigger(generator):
    """20 ABs / 50 customers (rate=0.4) MUST NOT trip the high-volume
    branch -- the rate floor of 1.0/customer prevents it.  Pre-Round-39
    the AB > customers comparator would also have spared this case
    (20 < 50), but the parallel TAC clause was the actual offender;
    we pin the new logic for both rails."""
    data = _data(customers=[f"C{i}" for i in range(50)], abs_count=20, tacs_count=0)
    generator._add_individual_summary_paragraph("Stretched CSSM", data, days=90)
    text = _last_paragraph_text(generator).lower()
    assert "immediate attention" not in text
    assert "high volumes" not in text


def test_no_catch_all_high_volumes_phrase(generator):
    """The pre-Round-39 catch-all fallback rendered "high volumes"
    on portfolios that fit no other branch.  Pin its absence on a
    representative shape (mixed signal -- AB rate 0.6/customer,
    TAC rate 0.4/customer, neither hits the 10-absolute floor)."""
    data = _data(customers=["A", "B", "C", "D", "E"], abs_count=3, tacs_count=2)
    generator._add_individual_summary_paragraph("Mixed CSSM", data, days=90)
    text = _last_paragraph_text(generator).lower()
    assert "high volumes" not in text
