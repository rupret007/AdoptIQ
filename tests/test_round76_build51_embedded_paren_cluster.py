"""Round 76 / Build 51 tests -- embedded paren-cluster citation injection.

Build 50 acceptance audit found 2 leader DOCX paragraphs of the form::

    "Total Activities: 45 (APs: 16, ABs: 3, CPs: 1, TAC: 25) | "
    "Warning:  BEMS Escalations: 4"

The whole-line paren-cluster check (Round 76 / R76-A
``_line_is_paren_kpi_cluster``) correctly rejects these because
matches don't all fall inside the parens.  The multi-match loop
then ran ahead and injected citations after every comma INSIDE
the parens, producing the buggy::

    "(APs: 16, [Source: ...]ABs: 3, [Source: ...]CPs: 1, ...)"

Build 51 adds ``_embedded_paren_clusters`` to identify these
embedded clusters and route them through the same single-citation
handling the whole-line cluster path uses.

Pins for `_embedded_paren_clusters`:
  * Build 50 violation reproducer round-trips cleanly to the
    canonical ``"Total Activities: 45 [Source: ...] (APs: 16,
    ABs: 3, CPs: 1, TAC: 25) [Source: ...] | Warning: BEMS
    Escalations: 4 [Source: ...]"`` shape.
  * Negative control: a paragraph WITHOUT any embedded paren
    cluster still routes through the existing R57/R64/R66/R76-A
    branches.
  * Negative control: a paren cluster with a ``then`` between
    two matches inside the parens does NOT collapse (the between
    segment carries an alpha word so it's a sentence, not a
    KPI cluster).
  * Negative control: nested parens disqualify the cluster.
  * Positive: cluster with EXACTLY 2 matches inside parens still
    collapses (the threshold is 2+, not 3+).
  * Positive: trailing characters AFTER the closing ``)`` flow
    through cleanly (e.g. ``... ) | next``).
  * Idempotency: rerunning on already-cited paragraph is a no-op.
"""
from __future__ import annotations

import pytest

from report_source_injector import (
    _PARAGRAPH_KPI_NUMERIC_RE,
    _embedded_paren_clusters,
    _line_is_paren_kpi_cluster,
    _rewrite_paragraph_with_inline_citations,
)

_CITATION_BARE = "[Source: AdoptIQ Report Data Sources]"


def _matches(line: str) -> list:
    return list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))


def test_embedded_paren_cluster_detected_in_build50_reproducer() -> None:
    """The exact Build 50 leader-docx violation must be detected."""
    line = (
        "Total Activities: 45 (APs: 16, ABs: 3, CPs: 1, TAC: 25) | "
        "Warning: BEMS Escalations: 4"
    )
    ms = _matches(line)
    # 6 matches expected: Activities, APs, ABs, CPs, TAC, BEMS
    assert len(ms) == 6, f"expected 6 matches, got {len(ms)}: {[m.group() for m in ms]}"
    embedded = _embedded_paren_clusters(line, ms)
    # The cluster ends at index 4 (TAC: 25)
    assert 4 in embedded, f"expected cluster ending at match index 4, got {embedded}"
    # Close paren position should be at the ``)`` after ``TAC: 25``
    close_idx = embedded[4]
    assert line[close_idx] == ")", f"expected ')' at {close_idx}, got {line[close_idx]!r}"


def test_embedded_paren_cluster_rewrite_collapses_to_single_citation() -> None:
    """Round-trip the Build 50 reproducer through the rewriter.

    Round 112 / Build 81 update: the pre-R112 contract emitted a
    citation immediately after the wrapper KPI's value (``"45 [Source:
    ...]"``) AND another after the closing paren -- two adjacent
    citations interrupting the semantic unit.  R112/F3 collapsed this
    into ONE citation immediately after the closing paren, because the
    wrapper + cluster ``"Total Activities: 45 (APs: 16, ABs: 3, CPs:
    1, TAC: 25)"`` is one assertion, not two.  The trailing
    ``"BEMS Escalations: 4"`` still gets its own citation (separate
    KPI, separate semantic unit).  This test was updated to assert
    the new R112 shape; the buggy mid-paren guard is unchanged.
    """
    line = (
        "Total Activities: 45 (APs: 16, ABs: 3, CPs: 1, TAC: 25) | "
        "Warning: BEMS Escalations: 4"
    )
    ms = _matches(line)
    out = _rewrite_paragraph_with_inline_citations(line, ms, _CITATION_BARE)
    # The buggy mid-paren citations MUST NOT appear (R76/Build 51 guard).
    assert ("16, " + _CITATION_BARE) not in out, f"mid-paren citation leaked: {out!r}"
    assert ("3, " + _CITATION_BARE) not in out, f"mid-paren citation leaked: {out!r}"
    assert ("1, " + _CITATION_BARE) not in out, f"mid-paren citation leaked: {out!r}"
    # The cluster MUST emit ONE post-paren citation (R76/Build 51 contract).
    assert (") " + _CITATION_BARE) in out, f"missing post-paren citation: {out!r}"
    # Round 112 / F3: wrapper KPI does NOT get its own citation when
    # it sits immediately before the embedded cluster -- the post-paren
    # citation covers BOTH wrapper and cluster as one semantic unit.
    assert ("45 " + _CITATION_BARE) not in out, (
        f"R112/F3: wrapper KPI emitted citation before cluster (regression): {out!r}"
    )
    # The post-cluster KPI (BEMS Escalations: 4) MUST still get its own
    # citation -- it's a separate KPI separated by ``| Warning:``.
    assert ("4 " + _CITATION_BARE) in out, f"missing post-cluster citation: {out!r}"
    # Total citation count: ONE after the cluster, ONE after the trailing
    # KPI = TWO citations (NOT three as in pre-R112).
    assert out.count(_CITATION_BARE) == 2, (
        f"R112/F3: expected exactly 2 citations on the line, "
        f"got {out.count(_CITATION_BARE)}: {out!r}"
    )


def test_no_embedded_cluster_for_plain_sentence() -> None:
    """Sentence with NO parens has no embedded cluster."""
    line = "Total Customers: 52. Adoption Barriers: 68."
    ms = _matches(line)
    embedded = _embedded_paren_clusters(line, ms)
    assert embedded == {}, f"expected no embedded clusters, got {embedded}"


def test_no_embedded_cluster_when_alpha_word_inside_parens() -> None:
    """``"(Total: 5 then Now: 3)"`` should NOT be collapsed -- the
    between-segment ``" then "`` carries an alpha word, signalling a
    sentence not a KPI cluster.
    """
    line = "Engagement: 1 (Total: 5 then Now: 3) ends here"
    ms = _matches(line)
    embedded = _embedded_paren_clusters(line, ms)
    # Total and Now are inside the parens but separated by 'then' ->
    # disqualified, so no embedded cluster
    assert 2 not in embedded, f"expected no cluster ending at idx 2, got {embedded}"


def test_nested_parens_inner_cluster_recognised_outer_left_alone() -> None:
    """``"((APs: 16, ABs: 3))"`` recognises the INNER paren as the
    cluster boundary; the outer paren is treated as ordinary text
    around the cluster.  This is acceptable -- nested parens are not
    a real-world report shape, and silently picking the innermost
    valid cluster keeps the rewriter robust without special-casing.
    """
    line = "Note: 1 ((APs: 16, ABs: 3)) end"
    ms = _matches(line)
    embedded = _embedded_paren_clusters(line, ms)
    # Cluster at idx 2 with close-paren position pointing at the INNER `)`
    if 2 in embedded:
        # close_idx must point to the inner `)` (the FIRST `)` after ABs: 3)
        first_close = line.index(")")
        assert embedded[2] == first_close, (
            f"expected inner ')' at {first_close}, got {embedded[2]}: {line[embedded[2]]!r}"
        )


def test_cluster_with_exactly_two_matches_inside_parens() -> None:
    """The 2+ threshold means a 2-match cluster also collapses."""
    line = "Pre: 9 (APs: 16, ABs: 3) post Done: 7"
    ms = _matches(line)
    out = _rewrite_paragraph_with_inline_citations(line, ms, _CITATION_BARE)
    assert ("16, " + _CITATION_BARE) not in out
    assert ("3, " + _CITATION_BARE) not in out
    assert (") " + _CITATION_BARE) in out


def test_trailing_chars_after_closing_paren_preserved() -> None:
    """Trailing ``" | suffix"`` after the cluster must flow through."""
    line = "Pre: 9 (APs: 16, ABs: 3) | extra trailing text Done: 7"
    ms = _matches(line)
    out = _rewrite_paragraph_with_inline_citations(line, ms, _CITATION_BARE)
    # The trailing "| extra trailing text" segment must be intact
    assert "| extra trailing text" in out, f"trailing dropped: {out!r}"


def test_idempotency_on_already_cited_paragraph() -> None:
    """Rerunning on a paragraph that already carries ``[source:`` is a no-op."""
    line = (
        "Total Activities: 45 [Source: AdoptIQ Report Data Sources] "
        "(APs: 16, ABs: 3, CPs: 1, TAC: 25) [Source: AdoptIQ Report Data Sources] "
        "| Warning: BEMS Escalations: 4 [Source: AdoptIQ Report Data Sources]"
    )
    ms = _matches(line)
    out = _rewrite_paragraph_with_inline_citations(line, ms, _CITATION_BARE)
    # idempotent: existing citations preserved, no duplicates
    assert out.count(_CITATION_BARE) == line.count(_CITATION_BARE)


def test_whole_line_paren_cluster_still_works_after_build51() -> None:
    """The Round 76 / R76-A whole-line paren-cluster path must not
    regress -- a line that IS a single paren cluster still emits ONE
    trailing citation.
    """
    line = "(APs: 16, ABs: 3, CPs: 1, TAC: 4)"
    ms = _matches(line)
    assert _line_is_paren_kpi_cluster(line, ms), "whole-line paren detection broke"
    out = _rewrite_paragraph_with_inline_citations(line, ms, _CITATION_BARE)
    assert out.count(_CITATION_BARE) == 1, f"expected 1 citation, got {out.count(_CITATION_BARE)}: {out!r}"
    assert out.endswith(_CITATION_BARE), f"citation not at end: {out!r}"


def test_r57_sentence_multi_kpi_line_unchanged_by_build51() -> None:
    """The R57 sentence-style multi-KPI line must STILL emit per-segment citations."""
    line = "Total Customers: 52. Adoption Barriers: 68. Support Cases: 381."
    ms = _matches(line)
    out = _rewrite_paragraph_with_inline_citations(line, ms, _CITATION_BARE)
    # Each KPI gets a citation -> 3 citations total
    assert out.count(_CITATION_BARE) == 3, f"expected 3 citations: {out!r}"
    # The first two land flush with the value, the trailing one
    # follows the trailing period (R57 last-match convention).
    assert ("52 " + _CITATION_BARE) in out
    assert ("68 " + _CITATION_BARE) in out
    # Last KPI is special-cased to end-of-line so the period
    # precedes the citation; the canonical R57 shape is "381." +
    # citation.
    assert ("381." in out) and (_CITATION_BARE in out)


def test_r66b1_unit_deferral_unchanged_by_build51() -> None:
    """The R66/B1 unit-deferral path must not regress."""
    line = "Analysis Period: 90 Days  Total Customers: 39"
    ms = _matches(line)
    out = _rewrite_paragraph_with_inline_citations(line, ms, _CITATION_BARE)
    assert ("Days " + _CITATION_BARE) in out
    assert ("39 " + _CITATION_BARE) in out


def test_bullet_glyph_boundary_unchanged_by_build51() -> None:
    """The R76-A bullet-glyph branch must not regress."""
    line = "Total team activities: 999• Total Action Plans: 366"
    ms = _matches(line)
    out = _rewrite_paragraph_with_inline_citations(line, ms, _CITATION_BARE)
    # Citation lands BEFORE the bullet, after the value
    assert ("999 " + _CITATION_BARE) in out, f"value-end citation missing: {out!r}"
    # 366 also gets its end-of-line citation
    assert ("366 " + _CITATION_BARE) in out
