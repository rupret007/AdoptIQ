"""Round 76 / R76-A — bullet-glyph + paren-group citation layouts.

Build 49 acceptance audit (Phase 6 sweep) found two pre-existing
intra-paragraph mid-string citation injections that the R66/B1
unit-deferral fix did NOT cover:

  1. **Bullet-list single paragraph** — leader DOCX rendered KPI
     summaries as one long paragraph with bullet glyphs separating
     each KPI: ``'• Total team activities: 999• Total Action Plans:
     366• Total Adoption Barriers: 69 ...'``.  The boundary ``'• Total
     Action Plans: '`` carries an alphabetic token (``Total``) so the
     R66/B1 unit branch incorrectly fires; the cursor advances past the
     bullet glyph; the citation lands as ``'999• [Source: ...] Total
     Action Plans'`` -- a bullet+citation jam mid-string.

  2. **Comma-paren KPI cluster** — leader/comprehensive DOCX rendered
     compact summaries as ``'(APs: 16, ABs: 3, CPs: 1, TAC: 4)'``.
     Pure-punctuation comma boundaries triggered the R57
     sentence-style branch: a citation landed inside the parens after
     each value: ``'(APs: 16 [Source: ...] , ABs: 3 [Source: ...] ,
     ...)'`` -- four inline citations cramming a one-line cluster.

Round 76 / R76-A adds two new boundary classifiers:

  * **bullet-glyph branch** (`_BOUNDARY_HAS_BULLET_RE`) — checked
    BEFORE the R66/B1 unit branch.  When the boundary carries a list
    separator (``• ``, ``* ``, ``- ``), routes to R57 value-end
    placement so the bullet stays attached to its label.

  * **paren-cluster branch** (`_line_is_paren_kpi_cluster`) — checked
    BEFORE per-pair walking.  When the entire line is a single
    ``(KPI: v, KPI: v, ...)`` group, suppresses per-match
    interleaving and appends ONE citation immediately after the
    closing ``)``.

This file pins the new branches AND every pre-existing R57 / R64 /
R66 contract via negative-control tests so the new branches cannot
silently regress upstream behavior.

Round 76.  Made-with: Cursor.
"""

from __future__ import annotations

from report_source_injector import (
    _PARAGRAPH_KPI_NUMERIC_RE,
    _rewrite_paragraph_with_inline_citations,
)


_CITATION_BARE = "[Source: AdoptIQ Report Data Sources]"


def _all_matches(text: str) -> list:
    return list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(text))


# ---------------------------------------------------------------------------
# R76-A bullet-glyph branch — the canonical Build-49 leader_docx finding
# ---------------------------------------------------------------------------


def test_bullet_list_six_kpis_single_paragraph_no_midstring_injection() -> None:
    """Exact text shape from the Phase 6 leader_docx finding.

    Pre-R76 the R66/B1 unit branch incorrectly fired on the boundary
    ``'• Total Action Plans: '`` (because ``Total`` is alphabetic),
    advancing the cursor past the bullet glyph and producing
    ``'999• [Source: ...] Total Action Plans'`` -- a bullet+citation jam.

    Post-R76 the bullet branch routes to R57 value-end placement so
    the bullet stays attached to its label and we get
    ``'999 [Source: ...] • Total Action Plans: 366 [Source: ...]
    • Total Adoption Barriers: 69 [Source: ...]
    ...'``.
    """
    text = (
        "• Total team activities: 999"
        "• Total Action Plans: 366"
        "• Total Adoption Barriers: 69"
        "• Total Customer Pulse: 12"
        "• Total Success Priorities: 4"
        "• Total TAC Cases: 152"
    )
    matches = _all_matches(text)
    assert len(matches) >= 6

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # Critical: NO ``<value><bullet>[Source:`` pattern (the bug).
    assert "999•" not in rewritten or f"999• {_CITATION_BARE}" not in rewritten, (
        f"Bullet+citation jam regressed: {rewritten!r}"
    )
    # Critical: every value MUST be source-backed, but the bullet must
    # be visually next to its label, not the prior value.
    for value, label in (
        ("999", "Total team activities"),
        ("366", "Total Action Plans"),
        ("69", "Total Adoption Barriers"),
        ("12", "Total Customer Pulse"),
        ("4", "Total Success Priorities"),
        ("152", "Total TAC Cases"),
    ):
        # Sanity: every label is preserved in the rewritten string.
        assert label in rewritten, f"label {label!r} dropped from {rewritten!r}"
        # Sanity: every value is preserved.
        assert value in rewritten, f"value {value!r} dropped from {rewritten!r}"
    # Idempotency anchor: every match got a citation (one per KPI).
    assert rewritten.count(_CITATION_BARE) == len(matches), (
        f"Expected {len(matches)} citations, got {rewritten.count(_CITATION_BARE)} in {rewritten!r}"
    )


def test_bullet_list_three_kpis_each_value_is_source_backed() -> None:
    """3-KPI bullet list: every value flush to a citation, not the bullet."""
    text = "• Cases: 12• Bugs: 3• Maintenances: 7"
    matches = _all_matches(text)
    assert len(matches) >= 3

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # No mid-string ``12• [Source:`` etc.
    assert f"12• {_CITATION_BARE}" not in rewritten, rewritten
    assert f"3• {_CITATION_BARE}" not in rewritten, rewritten
    # Citations should appear on each value end, with the bullet
    # rendering naturally adjacent to its label.
    assert f"12 {_CITATION_BARE}" in rewritten, rewritten
    assert f"3 {_CITATION_BARE}" in rewritten, rewritten
    assert rewritten.endswith(_CITATION_BARE), rewritten


def test_bullet_with_unit_token_bullet_branch_wins_over_unit_branch() -> None:
    """Hybrid: ``999 Days• Customers: 39``.

    The bullet branch must take precedence over the R66/B1 unit
    branch so the bullet stays attached to ``Customers`` and the
    citation lands flush with the value, NOT after the unit token.
    Acceptable shapes: ``"999 [Source: ...] Days• Customers: 39
    [Source: ...]"`` (bullet wins, value flush).  This test pins
    that the bullet-jam regression doesn't recur.
    """
    text = "Period: 999 Days• Customers: 39"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # Critical: NO bullet+citation jam.
    assert f"Days• {_CITATION_BARE}" not in rewritten, rewritten
    # Critical: the bullet must end up adjacent to its OWN label
    # (``Customers``), not jammed against the previous value.
    assert "• Customers" in rewritten, rewritten


def test_bullet_dash_separator_treated_same_as_bullet() -> None:
    """`` - `` separator (whitespace-padded dash) routes through bullet branch."""
    text = "Cases: 12 - Bugs: 3 - Maintenances: 7"
    matches = _all_matches(text)
    assert len(matches) >= 3

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    assert f"12 {_CITATION_BARE}" in rewritten, rewritten
    assert f"3 {_CITATION_BARE}" in rewritten, rewritten
    assert rewritten.count(_CITATION_BARE) == len(matches)


# ---------------------------------------------------------------------------
# R76-A paren-cluster branch — the canonical (APs: 16, ABs: 3, ...) finding
# ---------------------------------------------------------------------------


def test_paren_cluster_four_kpis_single_trailing_citation() -> None:
    """``"(APs: 16, ABs: 3, CPs: 1, TAC: 4)"`` --> ONE citation after ``)``.

    Pre-R76 the R57 pure-punctuation branch interleaved per match,
    producing ``"(APs: 16 [Source: ...] , ABs: 3 [Source: ...] ...)"``
    -- four inline citations crammed inside one parenthetical cluster.

    Post-R76 the paren branch suppresses per-match interleaving and
    emits a single ``[Source: ...]`` immediately after the closing
    paren.
    """
    text = "(APs: 16, ABs: 3, CPs: 1, TAC: 4)"
    matches = _all_matches(text)
    assert len(matches) >= 4

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # Critical: exactly ONE citation, immediately after the closing paren.
    assert rewritten.count(_CITATION_BARE) == 1, (
        f"Expected 1 citation for paren cluster, got "
        f"{rewritten.count(_CITATION_BARE)} in {rewritten!r}"
    )
    assert rewritten.endswith(_CITATION_BARE), rewritten
    assert ") " + _CITATION_BARE in rewritten, rewritten
    # Critical: no inline citation INSIDE the parens.
    inside_parens = rewritten[rewritten.index("(") + 1: rewritten.rindex(")")]
    assert _CITATION_BARE not in inside_parens, (
        f"Citation leaked inside parens: {inside_parens!r}"
    )


def test_paren_cluster_with_trailing_text_citation_after_close_paren() -> None:
    """``"(APs: 16, ABs: 3) total"`` --> citation after ``)`` then ``total``."""
    text = "(APs: 16, ABs: 3) total"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    assert rewritten.count(_CITATION_BARE) == 1, rewritten
    assert ") " + _CITATION_BARE in rewritten, rewritten
    assert "total" in rewritten


def test_paren_cluster_two_kpis_minimal_case() -> None:
    """Even a 2-KPI paren cluster gets a single trailing citation."""
    text = "(APs: 1, ABs: 2)"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    assert rewritten.count(_CITATION_BARE) == 1
    assert ") " + _CITATION_BARE in rewritten
    inside = rewritten[rewritten.index("(") + 1: rewritten.rindex(")")]
    assert _CITATION_BARE not in inside


# ---------------------------------------------------------------------------
# R76-A negative controls — pre-existing R57 / R64 / R66 contracts preserved
# ---------------------------------------------------------------------------


def test_r57_sentence_multi_kpi_line_unchanged_by_r76() -> None:
    """R57 contract: ``"Customers: 52. Barriers: 68. Cases: 381"`` -- per-value citation.

    Mirrors the canonical R57 reproducer pinned in
    ``tests/test_round64_citation_injector_multiline.py:86``.  We use
    NO trailing period so the multi-match walker hits each value
    cleanly.
    """
    text = "Customers: 52. Barriers: 68. Cases: 381"
    matches = _all_matches(text)
    assert len(matches) >= 3

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # R57 invariant: each value gets its own citation flush to the value.
    assert f"52 {_CITATION_BARE}" in rewritten, rewritten
    assert f"68 {_CITATION_BARE}" in rewritten, rewritten
    assert f"381 {_CITATION_BARE}" in rewritten, rewritten


def test_r64_single_match_per_newline_unchanged_by_r76() -> None:
    """R64 contract: newline-split single-match-per-line."""
    text = "Period: 90 Days\nCustomers: 39"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # R64 invariant: ``"90 Days [Source: ...]\nCustomers: 39 [Source: ...]"``
    # (single citation per line, no mid-string injection between value and unit).
    lines = rewritten.split("\n")
    assert len(lines) == 2, rewritten
    for line in lines:
        assert line.endswith(_CITATION_BARE), f"Line missing trailing citation: {line!r}"
    # No splitting between value and unit on line 1.
    assert "90 Days " + _CITATION_BARE in rewritten, rewritten


def test_r66_single_line_unit_deferral_unchanged_by_r76() -> None:
    """R66/B1 contract: ``"Period: 90 Days  Customers: 39"`` (single line)."""
    text = "Analysis Period: 90 Days  Total Customers: 39"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # R66 invariant: citation defers past the ``Days`` unit.
    assert "90 Days " + _CITATION_BARE in rewritten, rewritten
    # And the second value still gets its citation.
    assert f"39 {_CITATION_BARE}" in rewritten, rewritten


def test_idempotency_already_cited_paragraph_no_op() -> None:
    """Lines already carrying ``[source:`` MUST be left untouched."""
    text = f"Customers: 52 {_CITATION_BARE}"
    matches = _all_matches(text)

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # No double-citation.
    assert rewritten.count(_CITATION_BARE) == 1, rewritten


def test_idempotency_bullet_list_already_cited_no_op() -> None:
    """Already-cited bullet list: no double-citation."""
    text = (
        f"• Cases: 12 {_CITATION_BARE}"
        f"• Bugs: 3 {_CITATION_BARE}"
    )
    matches = _all_matches(text)

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # The line carries ``[source:`` so the entire line short-circuits
    # at the idempotency guard (bullet glyph never reaches the
    # multi-match path).
    assert rewritten.count(_CITATION_BARE) == 2, rewritten


def test_idempotency_paren_cluster_already_cited_no_op() -> None:
    """Already-cited paren cluster: no double-citation."""
    text = f"(APs: 16, ABs: 3) {_CITATION_BARE}"
    matches = _all_matches(text)

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    assert rewritten.count(_CITATION_BARE) == 1, rewritten


# ---------------------------------------------------------------------------
# R76-A edge cases
# ---------------------------------------------------------------------------


def test_paren_cluster_with_alpha_token_in_boundary_falls_through_to_walker() -> None:
    """``"(APs: 16 then ABs: 3)"`` is NOT a strict paren-cluster.

    The boundary between matches contains an alphabetic token
    (``then``) which means it's not the comma-separated cluster shape
    we're optimizing for.  The branch must NOT fire; the multi-match
    walker handles it normally.
    """
    text = "(APs: 16 then ABs: 3)"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # The walker fires (R66/B1 unit branch), so we expect MORE than 1
    # citation -- the cluster heuristic should have rejected this shape.
    assert rewritten.count(_CITATION_BARE) >= 2, (
        f"Paren cluster heuristic falsely fired on alpha-boundary: {rewritten!r}"
    )


def test_paren_cluster_split_by_extra_paren_pair_does_not_fire() -> None:
    """``"(APs: 16) (ABs: 3)"`` is two clusters, not one.

    The single-paren-pair guard in `_line_is_paren_kpi_cluster` MUST
    reject this shape so we don't incorrectly suppress the second
    cluster's citation.
    """
    text = "(APs: 16) (ABs: 3)"
    matches = _all_matches(text)
    assert len(matches) >= 2

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # Multi-match walker fires; expect 2+ citations, NOT 1.
    assert rewritten.count(_CITATION_BARE) >= 2, (
        f"Multi-cluster line wrongly collapsed to single citation: {rewritten!r}"
    )


def test_mixed_bullet_paren_in_separate_lines_each_branch_fires() -> None:
    """Two-line paragraph: bullet line + paren line, each gets correct shape."""
    text = (
        "• Cases: 12• Bugs: 3\n"
        "(APs: 16, ABs: 3)"
    )
    matches = _all_matches(text)
    assert len(matches) >= 4

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    lines = rewritten.split("\n")
    assert len(lines) == 2, rewritten
    # Bullet line: 2 citations (one per KPI value, R57 placement).
    assert lines[0].count(_CITATION_BARE) == 2, lines[0]
    # Paren line: ONE citation after closing paren.
    assert lines[1].count(_CITATION_BARE) == 1, lines[1]
    assert lines[1].endswith(_CITATION_BARE), lines[1]


def test_no_kpi_matches_paragraph_left_untouched() -> None:
    """A paragraph with no KPI numeric matches must be unchanged."""
    text = "This sentence has no numeric KPIs at all."
    matches = _all_matches(text)
    assert len(matches) == 0

    rewritten = _rewrite_paragraph_with_inline_citations(
        text, matches, _CITATION_BARE
    )

    # The fallback in line 444: ``return f"{text} {citation_chrome}"``.
    # Either the original text or a fallback shape is fine; the key
    # invariant is that the original string body is preserved.
    assert text in rewritten, rewritten
