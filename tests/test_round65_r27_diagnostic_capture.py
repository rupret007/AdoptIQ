"""Round 65 / C-3 regression: per-rejection grounding diagnostics now
capture briefing + narrative excerpts plus the full sample_offending
map so Round 66 can root-cause the 34.5% R27 rejection rate without
re-running the report.

Build 37's Brian Frazier / All Contact Center / 90d run rejected
10/29 customer narratives (rate = 0.345) plus the portfolio-level
narrative.  The per-record diagnostic Round 64 emitted only carried
``failure_codes`` and a single ``first_offending_token`` -- not enough
context to decide between validator tuning and prompt enrichment in a
follow-on round.

Round 65 / C-3 extends ``_r64_record_grounding_outcome`` to ALSO
persist:

- ``briefing_excerpt``: ~200-char excerpt of the briefing book sent to
  the LLM (whitespace-collapsed, hard-capped).
- ``narrative_excerpt``: ~200-char excerpt of the rejected narrative.
- ``sample_offending``: the full offending-token map (sanitised,
  per-key/value capped) instead of just the first token.
- ``recorded_at``: UTC ISO timestamp for the rejection event.

This test pins:
1.  The new ``_r65_grounding_excerpt`` helper (whitespace collapse +
    cap + None-safe).
2.  ``_r64_record_grounding_outcome`` accepts ``briefing_excerpt`` and
    ``narrative_excerpt`` kwargs and persists them on the record.
3.  Legacy callers (positional / no excerpt kwargs) still work --
    the new fields default to empty string so no caller breaks.
4.  The new ``/api/grounding-diagnostics/<analysis_id>`` admin
    endpoint returns the structured diagnostic block with all the
    Round 65 fields.

Round 66 will use the captured data to decide between validator
tuning vs prompt enrichment -- this round is instrumentation only.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def app_simple_module():
    return importlib.import_module("app_simple")


@pytest.fixture
def fresh_status() -> dict:
    return {
        "analysis_id": "Comprehensive_Brian_Frazier_All_Contact_Center_90d_1777639146",
        "manager": "Brian Frazier",
        "tech": "Contact Center",
        "days": 90,
        "status": "running",
        "progress": 75,
    }


# ---------------------------------------------------------------------------
# _r65_grounding_excerpt helper
# ---------------------------------------------------------------------------


def test_r65_grounding_excerpt_collapses_whitespace_and_caps_length(app_simple_module) -> None:
    """The excerpt helper must collapse multi-line text to a single
    space-separated line and hard-cap the result so ``analysis_status.json``
    cannot inflate from large briefing books / narratives."""
    raw = (
        "Acme Corp briefing\n"
        "  Total cases: 412\n"
        "    BEMS escalations: 5\n"
        + ("X" * 1000)
    )
    out = app_simple_module._r65_grounding_excerpt(raw, max_len=200)
    assert "\n" not in out, "whitespace must be collapsed to single spaces"
    assert "  " not in out, "consecutive whitespace must be collapsed"
    assert len(out) <= 200, (
        f"excerpt must be hard-capped at max_len; got len={len(out)}"
    )
    assert out.endswith("…"), "truncated excerpts must end with the ellipsis marker"


def test_r65_grounding_excerpt_handles_none_and_empty(app_simple_module) -> None:
    assert app_simple_module._r65_grounding_excerpt(None) == ""
    assert app_simple_module._r65_grounding_excerpt("") == ""
    assert app_simple_module._r65_grounding_excerpt("   ") == ""


def test_r65_grounding_excerpt_short_text_passes_through(app_simple_module) -> None:
    """Text under the cap must pass through unchanged (no ellipsis)."""
    out = app_simple_module._r65_grounding_excerpt("hello world")
    assert out == "hello world"
    assert not out.endswith("…")


# ---------------------------------------------------------------------------
# _r64_record_grounding_outcome -- Round 65 enrichment
# ---------------------------------------------------------------------------


def test_record_grounding_rejection_captures_briefing_and_narrative_excerpts(
    app_simple_module, fresh_status,
) -> None:
    """Round 65 / C-3: rejection records must carry briefing +
    narrative excerpts so Round 66 can root-cause the 34.5% rate."""
    briefing = (
        "Customer: Acme Corp\n"
        "Subscription: ACME-CCAI-12345\n"
        "ARR: $250,000\n"
        "Active barriers: 3\n"
        "Recent cases: 5\n"
        "Risk score: 65 (HIGH)\n"
    )
    narrative = (
        "Acme Corp Storyboard\n"
        "Risk Level: HIGH\n"
        "Acme Corp shows a 97% improvement in case resolution times "
        "(NOT IN BRIEFING -- ungrounded).\n"
    )
    app_simple_module._r64_record_grounding_outcome(
        fresh_status,
        customer_name="Acme Corp",
        narrative_length=len(narrative),
        failures=["claims_unsupported"],
        sample_offending={"claims_unsupported": "97% improvement"},
        rejected=True,
        briefing_excerpt=briefing,
        narrative_excerpt=narrative,
    )
    rec = fresh_status["grounding_diagnostics"]["rejection_records"][0]
    assert rec.get("briefing_excerpt"), "briefing_excerpt must be persisted"
    # Round 71 / Phase 5 (#25): customer names are now redacted from the
    # excerpt before persistence so the on-disk ``analysis_status.json``
    # does not echo PII.  The placeholder ``<CUSTOMER>`` token replaces
    # the raw customer name.  The non-customer briefing payload (ARR,
    # subscription id, risk score) MUST still be present so Round 66 can
    # root-cause the rejection rate.
    assert "Acme Corp" not in rec["briefing_excerpt"], (
        "Round 71 / Phase 5 (#25): customer name must be redacted from "
        "briefing_excerpt before persisting to analysis_status.json"
    )
    assert "<CUSTOMER>" in rec["briefing_excerpt"]
    assert "ACME-CCAI-12345" in rec["briefing_excerpt"]  # subscription id retained
    assert rec.get("narrative_excerpt"), "narrative_excerpt must be persisted"
    assert "97%" in rec["narrative_excerpt"]
    assert "Acme Corp" not in rec["narrative_excerpt"], (
        "Round 71 / Phase 5 (#25): customer name must be redacted from "
        "narrative_excerpt before persisting to analysis_status.json"
    )
    assert len(rec["briefing_excerpt"]) <= 200
    assert len(rec["narrative_excerpt"]) <= 200


def test_record_grounding_rejection_persists_full_sample_offending_map(
    app_simple_module, fresh_status,
) -> None:
    """Pre-Round-65 only ``first_offending_token`` was persisted.
    Round 65 also stores the full offending-token map (per-key and
    per-value capped) so Round 66 can compare patterns across
    rejections."""
    sample = {
        "claims_unsupported": "97% improvement in case resolution",
        "entities_invented": "Globex Inc",
        "numbers_drift": "$1.2M ARR (canonical: $250K)",
    }
    app_simple_module._r64_record_grounding_outcome(
        fresh_status,
        customer_name="Acme Corp",
        narrative_length=1024,
        failures=list(sample.keys()),
        sample_offending=sample,
        rejected=True,
    )
    rec = fresh_status["grounding_diagnostics"]["rejection_records"][0]
    assert isinstance(rec.get("sample_offending"), dict)
    assert rec["sample_offending"].keys() == sample.keys(), (
        "Round 65 / C-3: sample_offending must persist all keys, not just the first"
    )
    for k in sample:
        assert rec["sample_offending"][k] == sample[k]


def test_record_grounding_rejection_persists_recorded_at_timestamp(
    app_simple_module, fresh_status,
) -> None:
    """Each rejection record carries its own UTC ISO timestamp so the
    operator can correlate with the per-PID adoptiq.<pid>.log."""
    app_simple_module._r64_record_grounding_outcome(
        fresh_status,
        customer_name="Acme Corp",
        narrative_length=512,
        failures=["claims_unsupported"],
        sample_offending={"claims_unsupported": "97%"},
        rejected=True,
    )
    rec = fresh_status["grounding_diagnostics"]["rejection_records"][0]
    assert rec.get("recorded_at"), "recorded_at timestamp must be set"
    assert "T" in rec["recorded_at"], "timestamp must be ISO-8601"


def test_record_grounding_legacy_callers_unchanged(app_simple_module, fresh_status) -> None:
    """Legacy callers that don't pass ``briefing_excerpt`` or
    ``narrative_excerpt`` must still work; the new fields default to
    empty string so the persisted record has a stable shape."""
    app_simple_module._r64_record_grounding_outcome(
        fresh_status,
        customer_name="Acme Corp",
        narrative_length=512,
        failures=["entities_invented"],
        sample_offending={"entities_invented": "Globex Inc"},
        rejected=True,
    )
    rec = fresh_status["grounding_diagnostics"]["rejection_records"][0]
    assert rec.get("briefing_excerpt") == ""
    assert rec.get("narrative_excerpt") == ""
    assert rec.get("first_offending_token") == "Globex Inc"


def test_record_grounding_excerpts_only_persisted_on_rejection(
    app_simple_module, fresh_status,
) -> None:
    """Successful narratives must NOT inflate ``analysis_status.json``
    with per-call briefing/narrative text -- only rejected records
    carry the excerpt fields."""
    app_simple_module._r64_record_grounding_outcome(
        fresh_status,
        customer_name="Acme Corp",
        narrative_length=512,
        failures=None,
        sample_offending=None,
        rejected=False,
        briefing_excerpt="this should not be persisted on success",
        narrative_excerpt="neither should this",
    )
    diag = fresh_status["grounding_diagnostics"]
    assert diag["rejection_summary"]["total"] == 1
    assert diag["rejection_summary"]["rejected"] == 0
    assert diag["rejection_records"] == [], (
        "Successful records must not be persisted -- only rejections "
        "carry the excerpt payload."
    )


# ---------------------------------------------------------------------------
# /api/grounding-diagnostics/<analysis_id> admin endpoint
# ---------------------------------------------------------------------------


def test_grounding_diagnostics_endpoint_returns_structured_block(
    app_simple_module,
) -> None:
    """The new read-only admin endpoint must return the structured
    grounding diagnostics for a known analysis id, including every
    Round 65 field."""
    aid = "Comprehensive_Test_Round65_C3_diag"
    test_status = {
        "analysis_id": aid,
        "manager": "Brian Frazier",
        "tech": "Contact Center",
        "days": 90,
        "status": "running",
    }
    app_simple_module._r64_record_grounding_outcome(
        test_status,
        customer_name="Acme Corp",
        narrative_length=1024,
        failures=["claims_unsupported"],
        sample_offending={"claims_unsupported": "97% improvement"},
        rejected=True,
        briefing_excerpt="Customer Acme Corp briefing book contents...",
        narrative_excerpt="Acme Corp shows a 97% improvement (ungrounded)",
    )
    with app_simple_module.analysis_status_lock:
        app_simple_module.analysis_status[aid] = test_status
    try:
        with app_simple_module.app.test_client() as client:
            resp = client.get(f"/api/grounding-diagnostics/{aid}")
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["ok"] is True
            assert body["analysis_id"] == aid
            diag = body["grounding_diagnostics"]
            assert diag["rejection_summary"]["rejected"] == 1
            assert diag["rejection_summary"]["total"] == 1
            assert diag["rejection_summary"]["rate"] == 1.0
            assert isinstance(diag["max_records"], int)
            recs = diag["rejection_records"]
            assert len(recs) == 1
            rec = recs[0]
            assert rec["scope"] == "customer"
            assert rec["customer_digest"]
            assert rec["failure_codes"] == ["claims_unsupported"]
            assert "97%" in rec["first_offending_token"]
            assert isinstance(rec["sample_offending"], dict)
            assert rec["briefing_excerpt"]
            assert rec["narrative_excerpt"]
            assert rec["recorded_at"]
    finally:
        with app_simple_module.analysis_status_lock:
            app_simple_module.analysis_status.pop(aid, None)


def test_grounding_diagnostics_endpoint_returns_404_for_unknown_id(
    app_simple_module,
) -> None:
    with app_simple_module.app.test_client() as client:
        resp = client.get("/api/grounding-diagnostics/__nonexistent_round65_c3__")
        assert resp.status_code == 404
        body = resp.get_json()
        assert body["ok"] is False
        assert body["error"] == "analysis_not_found"


def test_grounding_diagnostics_endpoint_returns_empty_block_for_run_without_diag(
    app_simple_module,
) -> None:
    """A run that completed without recording any narratives at all
    (e.g. an older run from before Round 64) must return an empty
    diag block, not a 500 error."""
    aid = "Comprehensive_Test_Round65_C3_no_diag"
    test_status = {
        "analysis_id": aid,
        "status": "completed",
    }
    with app_simple_module.analysis_status_lock:
        app_simple_module.analysis_status[aid] = test_status
    try:
        with app_simple_module.app.test_client() as client:
            resp = client.get(f"/api/grounding-diagnostics/{aid}")
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["ok"] is True
            assert body["grounding_diagnostics"]["rejection_records"] == []
            assert body["grounding_diagnostics"]["rejection_summary"] == {
                "rejected": 0,
                "total": 0,
                "rate": 0.0,
            }
    finally:
        with app_simple_module.analysis_status_lock:
            app_simple_module.analysis_status.pop(aid, None)


# ---------------------------------------------------------------------------
# Source-shape pin: the helper signature accepts the new kwargs and
# the call sites pass briefing + narrative.
# ---------------------------------------------------------------------------


def test_record_grounding_signature_accepts_briefing_and_narrative_kwargs(
    app_simple_module,
) -> None:
    """Pin the signature so a future refactor can't silently drop the
    Round 65 kwargs."""
    import inspect
    sig = inspect.signature(app_simple_module._r64_record_grounding_outcome)
    params = sig.parameters
    assert "briefing_excerpt" in params
    assert "narrative_excerpt" in params
    assert params["briefing_excerpt"].default is None
    assert params["narrative_excerpt"].default is None


def test_call_sites_pass_briefing_and_narrative_excerpts() -> None:
    """Pin that the portfolio-level and customer-level call sites
    actually wire briefing + narrative through to the helper.
    Without this pin the kwargs would always default to None and
    the rejection records would never carry the excerpts."""
    from pathlib import Path
    src = Path("app_simple.py").read_text(encoding="utf-8")
    assert "briefing_excerpt=" in src, (
        "Round 65 / C-3: at least one call site must pass briefing_excerpt"
    )
    assert "narrative_excerpt=" in src, (
        "Round 65 / C-3: at least one call site must pass narrative_excerpt"
    )
    occurrences = src.count("briefing_excerpt=")
    assert occurrences >= 2, (
        "Round 65 / C-3: expect briefing_excerpt= at BOTH the portfolio "
        "rejection call site (L15288) and the customer rejection call "
        f"site (L15733); success branches don't need excerpts (found {occurrences})"
    )
    # The two narrative_excerpt= call sites must mirror the briefing
    # ones so the persisted record always has both fields together.
    narrative_occurrences = src.count("narrative_excerpt=")
    assert narrative_occurrences >= 2, (
        "Round 65 / C-3: narrative_excerpt= must be passed at every site "
        "where briefing_excerpt= is passed so the rejection record always "
        f"carries both halves of the diagnostic (found {narrative_occurrences})"
    )
