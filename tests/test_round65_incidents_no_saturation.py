"""Round 65 / R-2 regression tests.

Build 37 audit found that every Renewal report customer's
``Risk_Components.Incidents`` component scored a perfect 100/100
because:

* ``status.webex.com`` Webex Status incidents have NO customer
  tagging field — every incident is portfolio-shared.
* The Renewal pipeline passed the FULL portfolio's ``ext_incidents``
  list (33 items in the audited Brian Frazier / All Contact Center
  / 90d run) into ``risk_scoring._score_incidents`` for EVERY
  customer.
* ``_score_incidents`` formula: ``count*6 + active*8 + high*12 +
  critical*8`` clamped to 100.  ``33 * 6 = 198`` -> clamped to 100
  before any severity weight even applied.

Round 65 / R-2 fixes both ends:

1. **Formula side** (defense in depth, in
   ``risk_scoring.py``): cap the count term at
   ``min(count, 5) * 6.0`` so a runaway count cannot dominate.
   ``count_capped`` and ``count_cap_applied`` are exposed in the
   details dict so the Risk_Components sheet can disclose the cap.
2. **Caller side** (in ``app_simple.py``): a new
   ``_r65_filter_customer_tagged_incidents`` helper filters the
   incident list by customer when the source carries a tagging
   field (``customer_id`` / ``customer_name`` / ``BU_NAME``).  For
   Webex Status incidents (no tagging field) the helper is a
   no-op and the formula-side cap is the active defense.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, List

import risk_scoring as _rs


def _make_inc(
    *,
    status: str = "resolved",
    impact: str = "Minor",
    customer_name: str | None = None,
    customer_id: str | None = None,
) -> Dict[str, Any]:
    inc: Dict[str, Any] = {
        "status": status,
        "impact_level": impact,
        "title": "Synthetic test incident",
    }
    if customer_name is not None:
        inc["customer_name"] = customer_name
    if customer_id is not None:
        inc["customer_id"] = customer_id
    return inc


# ---- Formula-side fix in risk_scoring._score_incidents ----


def test_score_incidents_caps_count_term_at_five() -> None:
    """33 portfolio-shared incidents (all resolved, no severity)
    must NOT clamp the score to 100.  After Round 65 / R-2 the
    count term is capped at ``min(count, 5) * 6.0 = 30``."""
    incidents = [_make_inc() for _ in range(33)]
    out = _rs._score_incidents(incidents)
    assert out["details"]["count"] == 33
    assert out["details"]["count_capped"] == 5
    assert out["details"]["count_cap_applied"] is True
    # Floor: 5*6 = 30 (no severity).  Saturation pre-fix was 100.
    assert out["score"] == 30.0, (
        f"Expected count-only saturation guard at 30/100, got {out['score']} "
        "(Round 65 / R-2 formula-side cap regression)"
    )


def test_score_incidents_low_count_unchanged() -> None:
    """For low counts (count <= 5) the cap is a no-op, preserving
    the legacy scoring behavior."""
    incidents = [_make_inc() for _ in range(3)]
    out = _rs._score_incidents(incidents)
    assert out["details"]["count"] == 3
    assert out["details"]["count_capped"] == 3
    assert out["details"]["count_cap_applied"] is False
    # 3 * 6 = 18 (no severity).
    assert out["score"] == 18.0


def test_score_incidents_severity_still_dominates() -> None:
    """High-severity active incidents must still drive score up
    even though the count term is capped.  Pin: 33 incidents
    where 2 are active + 1 is high gives a meaningful score."""
    incidents: List[Dict[str, Any]] = []
    for _ in range(30):
        incidents.append(_make_inc())
    incidents.append(_make_inc(status="active", impact="Minor"))
    incidents.append(_make_inc(status="active", impact="Minor"))
    incidents.append(_make_inc(status="resolved", impact="High"))
    out = _rs._score_incidents(incidents)
    # 5 (capped count) * 6 = 30
    # 2 active * 8 = 16
    # 1 high * 12 = 12
    # 0 critical * 8 = 0
    # total = 58
    assert out["score"] == 58.0, (
        f"Expected severity-weighted score 58, got {out['score']}"
    )
    assert out["details"]["active_count"] == 2
    assert out["details"]["high_impact_count"] == 1
    assert out["details"]["critical_impact_count"] == 0


def test_score_incidents_critical_weight_preserved() -> None:
    """Critical impact incidents still saturate to 100 when they
    actually exist in volume — the cap only neutralizes pure-count
    saturation."""
    incidents = [
        _make_inc(status="active", impact="Critical")
        for _ in range(10)
    ]
    out = _rs._score_incidents(incidents)
    # 5 (capped count) * 6 = 30
    # 10 active * 8 = 80
    # 10 critical -> 10 high_impact (legacy) and 10 critical_impact
    #   high * 12 = 120
    #   critical * 8 = 80
    # uncapped = 30 + 80 + 120 + 80 = 310 -> clamps to 100
    assert out["score"] == 100.0
    assert out["details"]["critical_impact_count"] == 10


def test_score_incidents_empty_input_returns_zero() -> None:
    """Empty incident list must return a healthy zero (preserves
    legacy contract)."""
    out = _rs._score_incidents([])
    assert out["score"] == 0.0
    assert out["details"]["count"] == 0
    assert out["details"].get("count_capped") == 0
    assert out["details"].get("count_cap_applied") is False


# ---- Caller-side fix: _r65_filter_customer_tagged_incidents ----


def test_filter_helper_pass_through_when_no_tagging_field() -> None:
    """Webex Status incidents carry no ``customer_id`` field.
    The filter helper must pass them through unchanged so the
    formula-side cap is the defense (no false negatives)."""
    app_simple = importlib.import_module("app_simple")
    helper = app_simple._r65_filter_customer_tagged_incidents
    incidents = [_make_inc() for _ in range(5)]
    out = helper(incidents, "Test Customer")
    assert len(out) == 5
    assert out == incidents


def test_filter_helper_filters_when_customer_name_tagged() -> None:
    """Future incident sources may carry ``customer_name`` per row.
    When ANY incident has a tagging field, only matching incidents
    propagate to per-customer scoring."""
    app_simple = importlib.import_module("app_simple")
    helper = app_simple._r65_filter_customer_tagged_incidents
    incidents = [
        _make_inc(customer_name="Acme Corp"),
        _make_inc(customer_name="Beta Inc"),
        _make_inc(customer_name="Acme Corp"),
        _make_inc(customer_name="Gamma LLC"),
    ]
    out = helper(incidents, "Acme Corp")
    assert len(out) == 2
    for inc in out:
        assert inc.get("customer_name") == "Acme Corp"


def test_filter_helper_filters_when_customer_id_tagged() -> None:
    """``customer_id`` is also recognized as a tagging field."""
    app_simple = importlib.import_module("app_simple")
    helper = app_simple._r65_filter_customer_tagged_incidents
    incidents = [
        _make_inc(customer_id="cust-001"),
        _make_inc(customer_id="cust-002"),
    ]
    # ``customer_id`` is opaque so passing the customer NAME
    # ``cust-001`` will match by normalized form.  This is a
    # pinned behavior — the helper does not interpret IDs, it
    # just compares normalized strings.
    out = helper(incidents, "cust-001")
    assert len(out) == 1


def test_filter_helper_empty_input_returns_empty() -> None:
    """Empty input must return empty (and not crash)."""
    app_simple = importlib.import_module("app_simple")
    helper = app_simple._r65_filter_customer_tagged_incidents
    assert helper(None, "Acme") == []
    assert helper([], "Acme") == []


def test_filter_helper_empty_customer_name_returns_pass_through() -> None:
    """An empty customer name can't filter; pass through rather
    than dropping every incident silently."""
    app_simple = importlib.import_module("app_simple")
    helper = app_simple._r65_filter_customer_tagged_incidents
    incidents = [_make_inc(customer_name="Acme")]
    assert helper(incidents, "") == incidents


# ---- Source-shape pin: caller integrations ----


def test_renewal_caller_uses_filter_helper() -> None:
    """Pin: ``_calculate_simple_renewal_risk`` must route
    ``ext_incidents`` through the Round-65 filter helper before
    calling ``compute_customer_risk_profile``."""
    src = (Path(__file__).resolve().parent.parent / "app_simple.py").read_text(
        encoding="utf-8",
    )
    assert "_r65_filter_customer_tagged_incidents(" in src, (
        "Round 65 / R-2: filter helper not invoked in app_simple.py"
    )
    # Confirm both renewal AND comprehensive paths route through it.
    helper_call_count = src.count("_r65_filter_customer_tagged_incidents(")
    assert helper_call_count >= 3, (
        f"Round 65 / R-2: filter helper invocation count {helper_call_count} "
        "(expected >= 3 — definition + renewal caller + comprehensive caller)"
    )


def test_score_incidents_exports_count_capped_in_details() -> None:
    """Pin: the Risk_Components sheet relies on
    ``details.count_capped`` and ``details.count_cap_applied`` to
    disclose the cap to operators.  These keys must be in the
    details dict on every call."""
    out = _rs._score_incidents([_make_inc() for _ in range(7)])
    assert "count_capped" in out["details"]
    assert "count_cap_applied" in out["details"]
    assert out["details"]["count_cap_applied"] is True
    assert out["details"]["count_capped"] == 5
