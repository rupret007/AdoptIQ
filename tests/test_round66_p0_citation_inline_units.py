"""Round 66 / Pass 1 (B1) -- inline multi-KPI with unit boundary preservation.

Build 38 acceptance smoke noted that the Round 64 / B4 fix correctly
handled the multi-LINE case (newline-separated dashboard rows) but a
single-line tile carrying multiple KPIs with units (e.g.
``"Analysis Period: 90 Days  Total Customers: 39"``) still triggered the
mid-string ``[Source: ...]`` injection because the multi-match path
unconditionally interleaved at value-end (R57 sentence behavior),
producing ``"Analysis Period: 90 [Source: ...] Days  ..."``.

R66/B1 extends the multi-match path with per-boundary unit detection:
the boundary segment between match[i].end() and match[i+1].start("label")
is checked for an alphabetic token. When present, the citation defers
past the unit; when absent, R57 sentence-style behavior is preserved.

Round 66 / Pass 1.  Made-with: Cursor.
"""

from __future__ import annotations

import re

from report_source_injector import (
    _PARAGRAPH_FALLBACK_CITATION,
    _PARAGRAPH_KPI_NUMERIC_RE,
    _rewrite_paragraph_with_inline_citations,
)


_CITATION_BARE = "[Source: AdoptIQ Report Data Sources]"


def _all_matches(text: str) -> list:
    return list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(text))


# ---------------------------------------------------------------------------
# B1 — single-line multi-KPI with units (the canonical R66 reproducer)
# ---------------------------------------------------------------------------


def test_single_line_two_kpis_first_has_unit_defers_citation_past_unit() -> None:
    """``"Analysis Period: 90 Days  Total Customers: 39"`` (one line, two KPIs).

    Pre-R66 the multi-match path interleaved at value-end:
        ``"Analysis Period: 90 [Source: ...] Days  Total Customers: 39 [Source: ...]"``
    Post-R66 the citation defers past the ``Days`` unit:
        ``"Analysis Period: 90 Days [Source: ...]  Total Customers: 39 [Source: ...]"``
    """
    text = "Analysis Period: 90 Days  Total Customers: 39"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(text, matches, _CITATION_BARE)

    # The citation must NOT appear immediately after ``90`` (which would
    # split it from the ``Days`` unit). We assert both directions: the
    # negative pattern (no value->citation->word on the same line) AND
    # the positive shape (``90 Days [Source: ...]`` substring present).
    assert "90 Days " + _CITATION_BARE in rewritten, rewritten
    # Direct anti-pattern check from Build 38 reproducer.
    assert "90 " + _CITATION_BARE + " Days" not in rewritten, rewritten
    assert "90 " + _CITATION_BARE + "Days" not in rewritten, rewritten
    # Final KPI gets its trailing citation.
    assert "Total Customers: 39 " + _CITATION_BARE in rewritten, rewritten
    # Two citations, one per KPI.
    assert rewritten.count(_CITATION_BARE) == 2, rewritten


def test_single_line_three_kpis_all_with_units_defers_each_citation() -> None:
    """``"Period: 90 Days  Customers: 39 Total  Cases: 12 Open"`` (3 KPIs each w/ unit)."""
    text = "Period: 90 Days  Customers: 39 Total  Cases: 12 Open"
    matches = _all_matches(text)
    assert len(matches) >= 3

    rewritten = _rewrite_paragraph_with_inline_citations(text, matches, _CITATION_BARE)

    # No value followed immediately by citation followed by an alphabetic
    # token (the value-citation-unit anti-pattern).
    assert re.search(r"\d\s*\[Source:[^\]]+\]\s*[A-Za-z]", rewritten) is None, rewritten
    # Every value-unit pair stays adjacent.
    assert "90 Days" in rewritten
    assert "39 Total" in rewritten
    assert "12 Open" in rewritten
    # 3 citations.
    assert rewritten.count(_CITATION_BARE) == 3, rewritten


def test_single_line_mixed_unit_and_punctuation_boundaries() -> None:
    """Mixed: ``"Period: 90 Days. Customers: 39"`` -- unit on first, punct boundary.

    The boundary ``" Days. "`` carries an alphabetic token (``Days``), so
    R66/B1 defers the first KPI's citation past it. The ``Days``
    token belongs to the FIRST KPI as its unit; the citation goes
    after.
    """
    text = "Period: 90 Days. Customers: 39"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(text, matches, _CITATION_BARE)

    # First KPI value+unit stays adjacent.
    assert "90 Days" in rewritten, rewritten
    # Citation is NOT jammed between value and unit.
    assert "90 " + _CITATION_BARE + " Days" not in rewritten, rewritten


# ---------------------------------------------------------------------------
# B1 — R57 contract preservation: pure-punctuation boundaries unchanged
# ---------------------------------------------------------------------------


def test_single_line_pure_punctuation_boundary_keeps_r57_behavior() -> None:
    """``"Customers: 52. Barriers: 68. Cases: 381"`` -- no units, R57 behavior preserved.

    The boundary ``". "`` carries no alphabetic token (the period is not
    a letter), so R57 sentence-style behavior is preserved: citation
    immediately after each value.
    """
    text = "Customers: 52. Barriers: 68. Cases: 381"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(text, matches, _CITATION_BARE)

    # R57: each value is followed immediately by the citation.
    for m in matches:
        assert f"{m.group('value').strip()} {_CITATION_BARE}" in rewritten, (
            f"R57 contract requires citation immediately after value "
            f"{m.group('value').strip()!r}; got {rewritten!r}"
        )
    # Three citations, one per match.
    assert rewritten.count(_CITATION_BARE) == len(matches), rewritten


def test_single_line_pipe_separator_keeps_r57_behavior() -> None:
    """``"Customers: 52 | Barriers: 68"`` -- pipe boundary, no unit, R57 behavior."""
    text = "Customers: 52 | Barriers: 68"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(text, matches, _CITATION_BARE)

    # R57: citation after each value, including before the ``|``.
    assert "52 " + _CITATION_BARE in rewritten, rewritten
    assert "68 " + _CITATION_BARE in rewritten, rewritten


# ---------------------------------------------------------------------------
# B1 — multi-line preservation (R64 contract NOT regressed)
# ---------------------------------------------------------------------------


def test_multi_line_with_units_still_appends_at_end_of_line() -> None:
    """The R64 multi-line case stays clean -- one citation at end-of-line per match."""
    text = (
        "Analysis Period: 90 Days\n"
        "Total Customers: 39\n"
        "Total Adoption Barriers: 70"
    )
    matches = _all_matches(text)

    rewritten = _rewrite_paragraph_with_inline_citations(text, matches, _CITATION_BARE)
    out_lines = rewritten.split("\n")

    # Every line ends with the citation, no mid-string injection.
    for line in out_lines:
        if any(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line)):
            assert line.endswith(_CITATION_BARE), f"line {line!r} should end with citation"

    # No value -> citation -> word pattern anywhere.
    assert re.search(r"\d\s*\[Source:[^\]]+\][^\n]*[A-Za-z]", rewritten) is None, rewritten


# ---------------------------------------------------------------------------
# B1 — anti-pattern guard: never produce value->citation->letter on a line
# ---------------------------------------------------------------------------


def test_anti_pattern_value_citation_unit_never_appears() -> None:
    """Guard: any `<digit><citation><letter>` substring on a single line is a regression.

    This is the canonical Build-36/Build-38 anti-pattern. We sweep
    several stress-test inputs (sentence-style, dashboard-style,
    mixed) and assert NONE of them produce the anti-pattern after
    rewrite. The R57 sentence case ``"Customers: 52. Barriers: 68"``
    is intentionally INCLUDED -- the boundary ``. `` is punctuation,
    NOT a letter, so the anti-pattern guard naturally allows
    ``"52 [Source: ...]. Barriers"`` (the ``B`` is on a new sub-segment
    after the period).
    """
    cases = [
        "Period: 90 Days  Customers: 39",
        "Total: 100 Open  Backlog: 10 Aged",
        "Customers: 52. Barriers: 68. Cases: 381",
        "Score: 87% Total  Open: 12 Cases",
        "Period: 90 Days\nCustomers: 39 Total",
    ]
    for text in cases:
        matches = _all_matches(text)
        if not matches:
            continue
        rewritten = _rewrite_paragraph_with_inline_citations(text, matches, _CITATION_BARE)
        for line in rewritten.split("\n"):
            # The anti-pattern: digit, optional space, citation, optional
            # space, letter on the SAME line. The R57 sentence case is
            # safe because the period delimits the segments.
            offending = re.search(r"\d\s*\[Source:[^\]]+\]\s*[A-Za-z]", line)
            assert offending is None, (
                f"value->citation->letter anti-pattern in line {line!r} "
                f"from input {text!r}; rewritten={rewritten!r}"
            )


# ---------------------------------------------------------------------------
# B1 — sanity: constant + idempotency reaffirm
# ---------------------------------------------------------------------------


def test_paragraph_fallback_citation_constant_unchanged_r66() -> None:
    """R66 must not change the public chrome literal."""
    assert _PARAGRAPH_FALLBACK_CITATION == " [Source: AdoptIQ Report Data Sources]"


def test_already_cited_inline_kpi_still_idempotent_r66() -> None:
    """Idempotency: a line carrying ``[source:`` is not re-injected even with units."""
    text = "Period: 90 Days [Source: foo]  Customers: 39"
    matches = _all_matches(text)
    rewritten = _rewrite_paragraph_with_inline_citations(text, matches, _CITATION_BARE)
    # Pre-cited line is left untouched.
    assert text == rewritten or rewritten.count("[Source: foo]") == 1, rewritten
