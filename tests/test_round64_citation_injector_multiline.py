"""Round 64 / Phase 1 (B4) -- citation injection at end-of-line, not mid-string.

The Build-36 Comprehensive Title Page surfaced a regression where the
post-render source-citation injector was jamming the ``[Source: AdoptIQ
Report Data Sources]`` chrome between a KPI value and its trailing unit:

    "Analysis Period: 90 [Source: AdoptIQ Report Data Sources]Days"
    "Total Customers: 39 [Source: AdoptIQ Report Data Sources]"
    "Total Adoption Barriers: 70 [Source: AdoptIQ Report Data Sources]"
    "Support Cases: 12 [Source: AdoptIQ Report Data Sources]"
    "Report Date: April 30, [Source: AdoptIQ Report Data Sources]2026 UTC"

The root cause was ``_rewrite_paragraph_with_inline_citations`` inserting
the citation immediately after every ``_PARAGRAPH_KPI_NUMERIC_RE`` match's
value end. Because the regex value group does NOT include trailing units
(``Days``, ``UTC``, etc.), the citation landed mid-string between value
and unit on every line of the title-page metric tile.

These tests pin the fix: split the paragraph on newlines first, run the
matcher per-line, and append a SINGLE citation at end-of-line for every
line that contains at least one match. No mid-string injection.

Round 64 / Phase 1.  Made-with: Cursor.
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


def test_single_line_single_kpi_with_unit_no_mid_string_injection() -> None:
    """The Build-36 title-page regression: unit must NOT be split from its value.

    Pre-fix this produced ``"Analysis Period: 90 [Source: ...] Days"``.
    Post-fix the citation lands at end-of-line, after the unit.
    """
    text = "Analysis Period: 90 Days"
    matches = _all_matches(text)
    assert matches, "regex must match the synthetic 'Label: 90' KPI tile"

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # Citation appended at end-of-line, after the unit.
    assert rewritten == f"Analysis Period: 90 Days {_CITATION_BARE}", rewritten
    # And specifically: no value->citation->WORD-on-same-line pattern.
    # (A citation immediately followed by ``\n`` + a new KPI label is
    # legitimate end-of-line behavior; the bug was citations followed
    # by more content on the SAME line.)
    assert re.search(r"\d\s*\[Source:[^\]]+\][^\n]*[A-Za-z]", rewritten) is None, (
        "no value->citation->word pattern should appear on the same line; got "
        f"{rewritten!r}"
    )


def test_single_line_multi_kpi_keeps_per_segment_citations_for_r57_contract() -> None:
    """Single-line ``"Customers: 52. Barriers: 68. Cases: 381"`` -> 3 citations.

    Round 64 deliberately preserves the Round 57
    ``_paragraph_claim_source_backed`` contract for genuine single-line
    multi-KPI sentences (every segment between consecutive matches must
    contain ``[source:]`` for the gate to mark each claim source-backed).
    The R64 fix only changes the SINGLE-MATCH-PER-LINE case, where the
    regex value group did not include trailing units (``Days`` /
    ``UTC``) and the citation was being jammed mid-string.

    For "Customers: 52. Barriers: 68. Cases: 381" each KPI is followed
    by ``.<space>`` and then the next KPI label -- there is no value-
    unit pair to split, so the per-segment R57 behavior is correct
    here and the helper still emits one citation per match.
    """
    text = "Customers: 52. Barriers: 68. Cases: 381"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # R57 contract: per-segment citations.
    assert rewritten.count(_CITATION_BARE) == len(matches), (
        f"R57 contract requires one citation per match on a multi-KPI "
        f"single-line sentence; got {rewritten.count(_CITATION_BARE)} for "
        f"{len(matches)} matches; rewritten={rewritten!r}"
    )
    # And R64 invariant: every match's value is followed immediately
    # by the citation (no mid-string injection between value and unit
    # because no unit was present here).
    for m in matches:
        # Substring "52 [Source: ..." / "68 [Source: ..." / "381 [Source: ..."
        assert f"{m.group('value').strip()} {_CITATION_BARE}" in rewritten, (
            f"match {m.group('label')!r}={m.group('value')!r} should be "
            f"directly followed by the citation; got {rewritten!r}"
        )


def test_multi_line_multi_kpi_one_citation_per_matched_line() -> None:
    """The Title Page tile reproducer: 5 KPI lines -> 5 end-of-line citations.

    This is the direct Build-36 reproducer. The metric paragraph passed
    to ``add_run`` looks like::

        Analysis Period: 90 Days
        Report Date: April 30, 2026 UTC
        Total Customers: 39
        Total Adoption Barriers: 70
        Support Cases: 12

    Pre-fix every line ended with a mid-string citation between value
    and unit (``"90 [Source: ...] Days"``, ``"April 30, [Source: ...]
    2026 UTC"``). Post-fix every line ends with a clean
    ``"... [Source: ...]"`` and no value-unit pair is split.
    """
    text = (
        "Analysis Period: 90 Days\n"
        "Report Date: April 30, 2026 UTC\n"
        "Total Customers: 39\n"
        "Total Adoption Barriers: 70\n"
        "Support Cases: 12"
    )
    matches = _all_matches(text)
    # The matcher fires at least on the four pure ``Label: number`` lines.
    assert len(matches) >= 4, (
        f"expected >=4 KPI matches across the 5-line title tile, got "
        f"{len(matches)}"
    )

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    out_lines = rewritten.split("\n")
    # Every line that contains a KPI label-value pair should end with
    # the citation; the line itself should otherwise be byte-identical
    # to the input (no mid-string injection).
    line_in_to_out = dict(zip(text.split("\n"), out_lines, strict=True))

    for in_line, out_line in line_in_to_out.items():
        line_matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(in_line))
        if line_matches:
            assert out_line == f"{in_line.rstrip()} {_CITATION_BARE}", (
                f"line {in_line!r} should end with citation; got {out_line!r}"
            )
        else:
            assert out_line == in_line, (
                f"line {in_line!r} has no KPI match -> must be untouched; "
                f"got {out_line!r}"
            )

    # No mid-line injections anywhere in the output. (End-of-line
    # citations followed by ``\n`` + a new KPI line are legitimate
    # and expected on multi-line tiles.)
    assert re.search(r"\d\s*\[Source:[^\]]+\][^\n]*[A-Za-z]", rewritten) is None, (
        f"value->citation->word pattern (mid-line injection) appeared in "
        f"the rewritten paragraph: {rewritten!r}"
    )
    # Every line that had a KPI match contributes exactly one citation.
    matched_line_count = sum(
        1 for line in text.split("\n")
        if list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))
    )
    assert rewritten.count(_CITATION_BARE) == matched_line_count, (
        f"expected exactly {matched_line_count} citations (one per matched line), got "
        f"{rewritten.count(_CITATION_BARE)} in {rewritten!r}"
    )


def test_already_cited_line_in_multi_line_paragraph_left_untouched() -> None:
    """Idempotency at line granularity: pre-cited lines do not get a 2nd citation."""
    text = (
        "Customers: 52 [Source: CSConsole / Snowflake C360_CS_TASK_C_VW]\n"
        "Barriers: 68"
    )
    matches = _all_matches(text)
    assert matches

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    out_lines = rewritten.split("\n")
    # Line 1 (already cited) is unchanged.
    assert out_lines[0] == "Customers: 52 [Source: CSConsole / Snowflake C360_CS_TASK_C_VW]"
    # Line 2 (uncited) gains a single end-of-line citation.
    assert out_lines[1] == f"Barriers: 68 {_CITATION_BARE}"


def test_no_matches_falls_through_with_trailing_citation() -> None:
    """When the matcher returned no matches, the legacy fallback is preserved."""
    text = "We saw 23% growth this quarter."
    rewritten = _rewrite_paragraph_with_inline_citations(
        text, [], _CITATION_BARE
    )
    assert rewritten == f"{text} {_CITATION_BARE}", rewritten


def test_paragraph_fallback_citation_constant_unchanged() -> None:
    """Sanity: the fallback citation literal is what callers expect."""
    assert _PARAGRAPH_FALLBACK_CITATION == " [Source: AdoptIQ Report Data Sources]"
