"""Round 90 / Build 66 — paren-balance filter for spurious mid-paren matches.

Build 65 acceptance audit found exactly one P1 mid-string citation
injection across all 8 reports the user reviewed: the Compact Risk
Summary tile rendered ``"Score 4-6 (Watch, 0-10 [Source: AdoptIQ Report
Data Sources] scale): 9"`` -- the citation was jammed mid-string between
``0-10`` and ``scale``.

Root cause: ``_PARAGRAPH_KPI_NUMERIC_RE`` extracts a fourth, spurious
match on the line ``"Risk Summary: ... Score 4-6 (Watch, 0-10 scale):
9"``: ``label='scale)' value='9'``.  ``)`` is in the regex's label
charset (so balanced ``Medium Risk (band)`` matches), but this also
lets a closing paren in the middle of a parenthetical extract a
match starting AFTER the comma.  The R66/B1 unit-deferral branch in
``_rewrite_paragraph_with_inline_citations`` then sees alphabetic
tokens (``Score``, ``Watch``) in the boundary segment between match #3
and match #4 and lands the citation at the next-label-start position
-- which is right BEFORE ``scale``.

Fix: a new ``_paragraph_match_is_well_formed`` helper rejects matches
whose label has more ``)`` than ``(``.  Real KPI labels never start
inside an unmatched ``(``, so this is a strict, conservative filter.
The filter is applied in TWO sites in ``report_source_injector.py``:

  * The caller's ``all_matches`` builder (so the rewriter is never
    handed spurious matches from the start).
  * The rewriter's per-line ``line_matches`` (because the rewriter
    re-extracts matches per-line after splitting on ``\\n``).

This file pins:

  1. Helper unit tests for ``_paragraph_match_is_well_formed``.
  2. Regex source-shape pin: ``_PARAGRAPH_KPI_NUMERIC_RE`` STILL
     extracts ``label='scale)'`` (the fix is the filter, not the regex).
  3. End-to-end pin: the exact failing line produces NO mid-string
     ``"0-10 [Source: ...] scale"`` injection.
  4. Negative control: legitimate balanced-paren labels like ``Medium
     Risk (band): 8`` STILL get cited.
  5. R76 / R76-A regression guard: ``(APs: 16, ABs: 3)`` whole-line
     paren-clusters still emit one trailing citation (the new filter
     does not interfere with R76 cluster detection).
  6. R66 / B1 regression guard: ``Analysis Period: 90 Days`` single-KPI
     line still appends one trailing citation.

Round 90.  Made-with: Cursor.
"""

from __future__ import annotations

import re

from report_source_injector import (
    _PARAGRAPH_KPI_NUMERIC_RE,
    _paragraph_match_is_well_formed,
    _rewrite_paragraph_with_inline_citations,
)


_CITATION_BARE = "[Source: AdoptIQ Report Data Sources]"


def _make_match(label: str):
    """Build a fake match object exposing ``.group('label')``."""

    class _M:
        def group(self, name):  # noqa: D401 - mimics re.Match
            assert name == "label"
            return label

    return _M()


# ---------------------------------------------------------------------------
# 1. Helper unit tests
# ---------------------------------------------------------------------------

def test_helper_rejects_label_with_unmatched_closing_paren():
    """A label like ``'scale)'`` (0 ``(``, 1 ``)``) is a mid-paren fragment;
    reject so the rewriter never lands a citation flush before it."""
    m = _make_match("scale)")
    assert _paragraph_match_is_well_formed(m) is False


def test_helper_rejects_label_with_two_unmatched_closing_parens():
    """Defense-in-depth: even more egregious mid-paren fragments rejected."""
    m = _make_match("close))")
    assert _paragraph_match_is_well_formed(m) is False


def test_helper_keeps_balanced_paren_label():
    """``'Medium Risk (band)'`` (1 ``(``, 1 ``)``) is a real KPI label
    that the regex legitimately matches when paired with ``"(band)"``
    parenthetical KPI annotations.  MUST NOT be filtered out."""
    m = _make_match("Medium Risk (band)")
    assert _paragraph_match_is_well_formed(m) is True


def test_helper_keeps_label_without_parens():
    """``'Overall Risk Score'`` (0 ``(``, 0 ``)``) is the most common
    label shape.  Pinning to make sure the filter doesn't accidentally
    over-filter."""
    m = _make_match("Overall Risk Score")
    assert _paragraph_match_is_well_formed(m) is True


def test_helper_keeps_label_with_unmatched_opening_paren():
    """``'Risk (incremental'`` (1 ``(``, 0 ``)``) is unusual but
    legitimate -- a label that opens a paren before the colon (e.g.
    ``"Risk (incremental: 5 last 90 days)"``).  Conservative rule:
    only ``)`` > ``(`` rejected; ``(`` >= ``)`` always kept."""
    m = _make_match("Risk (incremental")
    assert _paragraph_match_is_well_formed(m) is True


def test_helper_keeps_empty_label_defensively():
    """A degenerate empty label (e.g. construction error) is treated
    as well-formed so the upstream caller can decide what to do."""
    m = _make_match("")
    assert _paragraph_match_is_well_formed(m) is True


def test_helper_returns_true_when_group_raises():
    """When the match object's ``.group()`` raises (degenerate /
    fake match), the helper returns True (defensive: don't drop a
    real match because we couldn't introspect it)."""

    class _BadMatch:
        def group(self, _):
            raise IndexError("no such group")

    assert _paragraph_match_is_well_formed(_BadMatch()) is True


# ---------------------------------------------------------------------------
# 2. Regex source-shape pin -- the fix is the FILTER, not the regex
# ---------------------------------------------------------------------------

def test_regex_still_extracts_spurious_scale_paren_match():
    """Pin the regex source-shape: it MUST still extract
    ``label='scale)' value='9'`` from the failing line.  This pins
    that the fix is the filter, not a regex narrowing -- a future
    refactor that tightens the regex itself should also drop this
    test (and replace it with the new contract)."""
    line = "Score 4-6 (Watch, 0-10 scale): 9"
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))
    labels = [m.group("label") for m in matches]
    assert "scale)" in labels, (
        f"Round 90 / Build 66: regex source-shape pin -- "
        f"_PARAGRAPH_KPI_NUMERIC_RE expected to still match "
        f"'scale)' (the fix is the filter, not the regex). "
        f"Got labels={labels!r}"
    )


# ---------------------------------------------------------------------------
# 3. End-to-end pin -- the failing Build-65 Compact Risk Summary line
# ---------------------------------------------------------------------------

_BUG_LINE = (
    "Risk Summary: Overall Risk Score: 1.5 | High Risk Customers: 0 "
    "| Medium Risk (band): 8 | Score 4-6 (Watch, 0-10 scale): 9"
)


def test_end_to_end_no_mid_string_citation_between_0_10_and_scale():
    """The exact line from Build 65 Compact docx p#19.  Pre-fix:
    ``"0-10 [Source: ...] scale"`` mid-string injection.  Post-fix:
    citation lands at end-of-line, ``"0-10 scale)"`` substring intact."""
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(_BUG_LINE))
    rewritten = _rewrite_paragraph_with_inline_citations(
        _BUG_LINE, matches, _CITATION_BARE
    )
    assert "0-10 scale)" in rewritten, (
        f"Round 90 / Build 66: '0-10 scale)' substring MUST stay intact. "
        f"Got: {rewritten!r}"
    )
    # No citation chrome between 0-10 and scale (the bug pattern).
    bug_pattern = re.compile(r"0-10\s*\[Source:")
    assert not bug_pattern.search(rewritten), (
        f"Round 90 / Build 66: citation injected between '0-10' and "
        f"'scale' -- this is the bug.  Got: {rewritten!r}"
    )


def test_end_to_end_canonical_kpis_still_cited():
    """Pre-fix: Overall Risk Score, High Risk Customers, Score 4-6
    (...): 9 all received citations.  Post-fix: the first three
    canonical claims STILL receive citations; only the spurious
    ``scale)`` match is filtered out."""
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(_BUG_LINE))
    rewritten = _rewrite_paragraph_with_inline_citations(
        _BUG_LINE, matches, _CITATION_BARE
    )
    # End-of-line citation guarantees the line is source-backed.
    assert rewritten.rstrip().endswith(_CITATION_BARE), (
        f"Round 90 / Build 66: line must end with citation chrome "
        f"(end-of-line trailing citation).  Got: {rewritten!r}"
    )
    # The first canonical KPI's value-end placement still gets a citation.
    assert f"1.5 {_CITATION_BARE}" in rewritten, (
        f"Round 90 / Build 66: 'Overall Risk Score: 1.5' canonical "
        f"claim must still get a citation.  Got: {rewritten!r}"
    )


def test_end_to_end_balanced_paren_label_value_intact():
    """``Medium Risk (band): 8`` is a balanced-paren label.  The fix
    must NOT filter it out -- the value 8 must remain immediately
    adjacent to the label without mid-string injection."""
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(_BUG_LINE))
    rewritten = _rewrite_paragraph_with_inline_citations(
        _BUG_LINE, matches, _CITATION_BARE
    )
    assert "Medium Risk (band): 8" in rewritten, (
        f"Round 90 / Build 66: balanced-paren KPI 'Medium Risk (band): 8' "
        f"must stay intact.  Got: {rewritten!r}"
    )


# ---------------------------------------------------------------------------
# 4. Negative controls -- the filter MUST NOT drop legitimate matches
# ---------------------------------------------------------------------------

def test_balanced_paren_label_only_line_still_cited():
    """A line whose ONLY KPI claim has a balanced-paren label still
    gets a trailing citation -- proves the filter doesn't accidentally
    nuke balanced-paren labels."""
    line = "Medium Risk (band): 8"
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))
    rewritten = _rewrite_paragraph_with_inline_citations(
        line, matches, _CITATION_BARE
    )
    assert _CITATION_BARE in rewritten
    assert "Medium Risk (band): 8" in rewritten


# ---------------------------------------------------------------------------
# 5. R76 / R76-A regression guard -- whole-line paren-cluster preserved
# ---------------------------------------------------------------------------

def test_r76_paren_cluster_still_emits_one_trailing_citation():
    """R76 / R76-A pinned: ``(APs: 16, ABs: 3, CPs: 1, TAC: 4)`` is a
    whole-line paren-KPI cluster -- must produce ONE citation
    immediately after the closing ``)``.  The R90 well-formed filter
    must NOT interfere: every label inside the cluster is a balanced
    label-of-its-own (``APs``, ``ABs``, etc), so the filter's
    paren-balance rule keeps them all."""
    line = "(APs: 16, ABs: 3, CPs: 1, TAC: 4)"
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))
    rewritten = _rewrite_paragraph_with_inline_citations(
        line, matches, _CITATION_BARE
    )
    # Exactly one citation chrome.
    assert rewritten.count(_CITATION_BARE) == 1, (
        f"Round 90 / Build 66: R76 paren-cluster contract requires "
        f"exactly one citation chrome.  Got count="
        f"{rewritten.count(_CITATION_BARE)}, output={rewritten!r}"
    )
    # The citation lands right after the closing paren.
    assert f") {_CITATION_BARE}" in rewritten, (
        f"Round 90 / Build 66: R76 paren-cluster contract requires "
        f"the citation immediately after the closing paren.  "
        f"Got: {rewritten!r}"
    )


# ---------------------------------------------------------------------------
# 6. R66 / B1 regression guard -- single-KPI line trailing citation
# ---------------------------------------------------------------------------

def test_r66_b1_single_line_unit_aware_trailing_citation():
    """R66 / B1 pinned: ``Analysis Period: 90 Days`` is a single-KPI
    line where the value ``90`` is followed by a unit ``Days``.  Per
    R66/B1 the rewriter MUST append the citation at end-of-line, NOT
    mid-string between the value and the unit.  The R90 well-formed
    filter must not interfere with this contract."""
    line = "Analysis Period: 90 Days"
    matches = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))
    rewritten = _rewrite_paragraph_with_inline_citations(
        line, matches, _CITATION_BARE
    )
    # Citation at end-of-line, value-and-unit pair stays adjacent.
    assert "90 Days" in rewritten, (
        f"Round 90 / Build 66: R66/B1 contract requires '90 Days' "
        f"value-and-unit pair to stay adjacent.  Got: {rewritten!r}"
    )
    assert rewritten.rstrip().endswith(_CITATION_BARE)


# ---------------------------------------------------------------------------
# Source-shape pin: the helper exists and is wired into the rewriter.
# Detects accidental removal of the filter that landed in R90.
# ---------------------------------------------------------------------------

def test_source_shape_helper_present_in_module():
    """The helper MUST be importable from ``report_source_injector``."""
    from report_source_injector import _paragraph_match_is_well_formed

    assert callable(_paragraph_match_is_well_formed)


def test_source_shape_round_90_marker_in_module_body():
    """Pin the ``# Round 90`` source markers so a future refactor that
    drops the filter shows up in ``git diff`` against this assertion."""
    from pathlib import Path

    body = Path(
        Path(__file__).resolve().parent.parent / "report_source_injector.py"
    ).read_text(encoding="utf-8")
    # Helper MUST carry an R90 marker comment.
    assert "Round 90" in body, (
        "Round 90 / Build 66: report_source_injector.py must carry "
        "at least one '# Round 90' marker comment for the per-file "
        "footprint convention."
    )
    # Helper MUST be referenced by both call sites (caller all_matches
    # builder + rewriter line_matches).
    helper_uses = body.count("_paragraph_match_is_well_formed(")
    # >= 3: 1 def + 2 call sites (caller + rewriter).  Also the helper
    # is referenced in the helper's own docstring example, so we accept
    # >= 3 strictly.
    assert helper_uses >= 3, (
        f"Round 90 / Build 66: _paragraph_match_is_well_formed must "
        f"be referenced at least 3x (1 def + 2 call sites). "
        f"Got count={helper_uses}"
    )
