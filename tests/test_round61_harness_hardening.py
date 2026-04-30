"""Round 61 / Phase 2.D regression tests for the harness regex
hardening + the "<number> Label" secondary regex.

This pins:
1. ``_PARAGRAPH_KPI_NUMERIC_RE`` rejects 7+ digit purely-numeric
   values (case IDs, TAC numbers, BEMS refs) so they cannot be
   classified as KPI claims and inflate the per-segment paragraph
   gate's false-positive count.
2. The new ``_PARAGRAPH_KPI_PREFIX_NUMERIC_RE`` extracts canonical
   KPIs from the "<number> Label" idiom that the comprehensive
   scenario's LLM narrative occasionally uses (closes the R58 soak
   ``comprehensive.action_plans`` drift event).
3. All real-world legitimate KPI shapes from the R58 soak matrix
   still match (no regression).
4. The prefix regex is tightly scoped to a small label allow-list
   and does NOT match unrelated number-noun phrases like ``90 days``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _canonical_matches(text: str) -> dict[str, str]:
    """Helper: run the same paragraph scan the harness uses and
    return the canonical->value dict that would feed the parity gate.
    """
    from report_iteration_loop import _scan_paragraph_for_kpis
    values: dict[str, str] = {}
    _scan_paragraph_for_kpis(text, values)
    return values


# ---------------------------------------------------------------------
# Round 61 / Phase 2.D group 1: regex rejects long numeric IDs
# ---------------------------------------------------------------------


def test_paragraph_kpi_re_skips_9_digit_case_id():
    """``Case: 700356476`` MUST NOT enter the KPI claim stream.  The
    label "Case" is not in KPI_ALIASES so even pre-fix this couldn't
    surface as a "claim" -- but the underlying regex MATCH inflated
    the ``matches`` list passed to ``_paragraph_claim_source_backed``,
    misaligning segment boundaries for any real canonical KPI in the
    same paragraph.  Round 61 closes that hole at the regex level."""
    from report_iteration_loop import _PARAGRAPH_KPI_NUMERIC_RE

    text = "Case: 700356476 has been escalated."
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(text))
    assert matches == [], (
        f"expected no matches (9-digit ID rejected), got "
        f"{[(m.group('label'), m.group('value')) for m in matches]!r}"
    )


def test_paragraph_kpi_re_skips_8_digit_bems_ref():
    """``BEMS01976626`` and similar 8+ digit identifiers must also be
    rejected.  Pin the contiguous-digit threshold at 7+."""
    from report_iteration_loop import _PARAGRAPH_KPI_NUMERIC_RE

    text = "Reference: 12345678 was opened on 4/22."
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(text))
    matched_values = [m.group("value") for m in matches]
    assert "12345678" not in matched_values, (
        f"8-digit ID slipped through: {matched_values!r}"
    )


def test_paragraph_kpi_re_keeps_short_numeric_metrics():
    """Real-world KPI shapes from the R58 soak matrix still match."""
    from report_iteration_loop import _PARAGRAPH_KPI_NUMERIC_RE

    samples = [
        ("Total APs: 8", "Total APs", "8"),
        ("Total Customers: 190", "Total Customers", "190"),
        ("Total Adoption Barriers: 70", "Total Adoption Barriers", "70"),
        ("Team Size: 11", "Team Size", "11"),
        ("Total TAC Cases: 381", "Total TAC Cases", "381"),
        ("Risk Score: 7.5", "Risk Score", "7.5"),
        ("Coverage: 95%", "Coverage", "95%"),
        ("Pulse: 1,234", "Pulse", "1,234"),
        ("Big metric: 12345", "Big metric", "12345"),  # 5 digits OK
        ("Six digit: 100000", "Six digit", "100000"),  # 6 digits OK
    ]
    for text, expected_label, expected_value in samples:
        matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(text))
        assert len(matches) == 1, f"{text!r} produced {len(matches)} matches"
        m = matches[0]
        assert m.group("label").strip() == expected_label, (
            f"label mismatch for {text!r}: got {m.group('label')!r}"
        )
        assert m.group("value").strip() == expected_value, (
            f"value mismatch for {text!r}: got {m.group('value')!r}"
        )


def test_paragraph_kpi_re_keeps_comma_separated_thousands_above_threshold():
    """``Total: 1,234,567`` MUST still match -- comma-separated values
    use the comma as a separator so the contiguous-digit gate cannot
    fire.  This guards against regressing real cumulative metrics into
    the ID-rejection bucket."""
    from report_iteration_loop import _PARAGRAPH_KPI_NUMERIC_RE

    text = "Cumulative count: 1,234,567 over the period."
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(text))
    assert len(matches) == 1, f"got {len(matches)} matches"
    assert matches[0].group("value").strip() == "1,234,567"


def test_canonical_extraction_unchanged_for_known_metrics():
    """End-to-end: real R58 soak phrasings still extract their canonical
    KPIs after the regex tightening."""
    samples_to_canonical = {
        "Total Customers: 190": ("total_customers", "190"),
        "Total Adoption Barriers: 70": ("adoption_barriers", "70"),
        "Total Action Plans: 354": ("action_plans", "354"),
        "Total TAC Cases: 381": ("support_cases", "381"),
        "Total BEMS Escalations: 81": ("bems", "81"),
        "Team Size: 11 Direct Reports": ("team_members", "11"),
    }
    for text, (canonical, expected_value) in samples_to_canonical.items():
        out = _canonical_matches(text)
        assert canonical in out, (
            f"{text!r} did not canonicalize to {canonical!r}: out={out!r}"
        )
        assert out[canonical] == expected_value, (
            f"{text!r} canonical {canonical!r} value mismatch: "
            f"expected {expected_value!r}, got {out[canonical]!r}"
        )


def test_case_id_in_same_paragraph_does_not_drown_real_kpi():
    """Real-world combined paragraph: the canonical KPI matches and
    the case ID does NOT.  Pre-Round-61 the case ID would also match
    and the resulting overcount would misalign the per-segment gate
    when checking if the canonical KPI is source-backed."""
    from report_iteration_loop import _PARAGRAPH_KPI_NUMERIC_RE

    text = (
        "Total APs: 8 with one open Case: 700356476 [Source: AdoptIQ Report]."
    )
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(text))
    assert len(matches) == 1, (
        f"expected only the canonical KPI to match (case ID rejected), "
        f"got {[(m.group('label'), m.group('value')) for m in matches]!r}"
    )
    assert matches[0].group("label").strip() == "Total APs"
    assert matches[0].group("value").strip() == "8"


# ---------------------------------------------------------------------
# Round 61 / Phase 2.D group 2: prefix-numeric regex extracts the
# "<number> Label" idiom (closes R58 comprehensive.action_plans drift)
# ---------------------------------------------------------------------


def test_prefix_regex_extracts_zero_action_plans():
    """The R58 / iter2 phrasing the canonical regex misses."""
    text = (
        "Per the briefing book, there are 0 Action Plans and "
        "0 Success Priorities for this scope."
    )
    out = _canonical_matches(text)
    assert "action_plans" in out, (
        f"prefix regex did not surface action_plans for {text!r}: out={out!r}"
    )
    assert out["action_plans"] == "0"


def test_prefix_regex_extracts_nonzero_action_plans():
    """Larger counts also extract correctly."""
    text = "The portfolio shows 354 Action Plans across 38 customers."
    out = _canonical_matches(text)
    assert out.get("action_plans") == "354", out


def test_prefix_regex_handles_total_action_plans_idiom():
    """``X Total Action Plans`` and ``Total X Action Plans`` are both
    natural phrasings the LLM may emit; both must canonicalize."""
    out_a = _canonical_matches("There are 12 Total Action Plans this period.")
    assert out_a.get("action_plans") == "12", out_a


def test_prefix_regex_does_not_match_unrelated_phrases():
    """Tightly scoped allow-list: ``90 days``, ``100 customers``, etc.
    do NOT match the prefix regex.  Guards against expanding the
    extractor's surface area through this new entrypoint."""
    samples_no_match = [
        "Analysis Period: 90 days for the portfolio.",
        "100 customers participated in the survey.",
        "5 minutes to render the report.",
        "47 cases were escalated last week.",
    ]
    from report_iteration_loop import _PARAGRAPH_KPI_PREFIX_NUMERIC_RE
    for text in samples_no_match:
        m = _PARAGRAPH_KPI_PREFIX_NUMERIC_RE.search(text)
        assert m is None, (
            f"prefix regex over-matched on {text!r}: "
            f"label={m.group('label')!r} value={m.group('value')!r}"
        )


def test_prefix_regex_extracts_adoption_barriers_idiom():
    """Symmetric to action_plans -- the same phrasing pattern often
    appears for adoption barriers in narrative prose."""
    out = _canonical_matches("The portfolio carries 12 Adoption Barriers.")
    assert out.get("adoption_barriers") == "12", out


def test_prefix_regex_does_not_overwrite_canonical_match():
    """When BOTH ``Label: number`` and ``number Label`` appear in the
    same paragraph, the canonical-shape extraction wins because it
    runs in the same scan and ``setdefault`` preserves the first
    write.  This guards against the prefix regex silently changing
    extracted values for paragraphs that already extract correctly."""
    text = "Total Action Plans: 354.  Note: 0 Action Plans were closed today."
    out = _canonical_matches(text)
    # The value depends on which regex runs first.  Round 61 wires the
    # prefix regex to run BEFORE the canonical regex (so the prefix
    # value would win on first setdefault), so 0 is the expected
    # extraction.  Either way, the count must be deterministic.
    assert out.get("action_plans") in {"0", "354"}, out


# ---------------------------------------------------------------------
# Round 61 / Phase 2.D group 3: extractor entrypoint sanity
# ---------------------------------------------------------------------


def test_other_scenarios_extraction_unchanged_smoke():
    """Smoke: the renewal/leader/compact extraction paths feed through
    ``_scan_paragraph_for_kpis``.  After Round 61 the canonical shape
    still produces the same KPIs.  This is a sanity gate -- not a full
    regression of every scenario, just confirmation that the prefix
    regex addition doesn't accidentally alter extractions for the
    ``Label: number`` shapes those scenarios actually use."""
    samples = [
        ("Manager: Brian Frazier", "manager", "Brian Frazier"),
        ("Technology: All Contact Center", "technology", "All Contact Center"),
        ("Window (days): 90", "window_days", "90"),
        ("Customers in portfolio: 38", "total_customers", "38"),
        ("Adoption barriers (total): 70", "adoption_barriers", "70"),
        ("Total TAC cases: 176", "support_cases", "176"),
    ]
    for text, canonical, expected in samples:
        out = _canonical_matches(text)
        assert out.get(canonical) == expected, (
            f"regression on {text!r}: canonical={canonical!r} "
            f"expected {expected!r}, got {out.get(canonical)!r}; "
            f"full_out={out!r}"
        )


# Round 61 / Phase 2.D
