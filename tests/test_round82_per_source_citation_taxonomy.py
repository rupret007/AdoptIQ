"""Round 82 / Phase B -- per-source-system citation taxonomy.

Pre-R82 the post-render Word source-citation injector emitted the same
generic chrome ``[Source: AdoptIQ Report Data Sources]`` after every
canonical-KPI claim.  An operator could see ``Adoption Barriers: 68
[Source: AdoptIQ Report Data Sources]`` and ``Support Cases: 381
[Source: AdoptIQ Report Data Sources]`` and have no proof of WHICH
upstream system actually produced each number -- the citation chrome
was generic placeholder.

R82/B introduces ``_R82_KPI_SOURCE_TAGS`` (a canonical-KPI-key →
source-system-tag map keyed off the same alias map the parity gate
uses) and three resolver helpers
(``_r82_resolve_source_tag_for_label``, ``_r82_chrome_for_label``,
``_r82_chrome_for_paragraph``).  The injection sites in
``inject_source_citations_into_docx`` thread per-label / per-paragraph
chrome resolution so each citation now carries the SPECIFIC system
name (``[Source: Snowflake CSConsole]`` for AB / AP / Pulse claims;
``[Source: Snowflake CSOne]`` for support-cases / TAC / BEMS claims;
``[Source: AdoptIQ risk_scoring]`` for risk-score claims).

Mixed-source paragraphs (multiple canonical KPIs from DIFFERENT
systems on the same line) intentionally fall back to the generic
chrome -- the alternative would be rendering several DIFFERENTLY-tagged
citations on a single line and re-introducing the visual mess the
user explicitly cautioned against ("not to the point where its messy
in the report").

Tests below pin:
  * Taxonomy presence and shape.
  * Resolver helpers (label-level, paragraph-level).
  * End-to-end DOCX injection per-label specificity.
  * Backward compat: unknown labels still get the generic fallback.
  * Round-trip preservation: R66/B1 unit-deferral, R76 paren-cluster,
    R57 sentence-style multi-KPI behavior all stay intact.

Round 82 / Phase B.  Made-with: Cursor.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from report_source_injector import (
    _PARAGRAPH_FALLBACK_CITATION,
    _PARAGRAPH_KPI_NUMERIC_RE,
    _R82_KPI_SOURCE_TAGS,
    _r82_chrome_for_label,
    _r82_chrome_for_paragraph,
    _r82_resolve_source_tag_for_label,
    _rewrite_paragraph_with_inline_citations,
    inject_source_citations_into_docx,
)


_GENERIC_CHROME_BARE = "[Source: AdoptIQ Report Data Sources]"
_GENERIC_CHROME_LEADING_SPACE = " " + _GENERIC_CHROME_BARE


def _all_matches(text: str) -> list[Any]:
    return list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(text))


# ---------------------------------------------------------------------------
# Taxonomy shape (B1 source-shape pin)
# ---------------------------------------------------------------------------


def test_r82_taxonomy_is_dict_with_string_keys_and_values() -> None:
    """``_R82_KPI_SOURCE_TAGS`` must be a {str: str} mapping with non-empty values."""
    assert isinstance(_R82_KPI_SOURCE_TAGS, dict), type(_R82_KPI_SOURCE_TAGS)
    assert _R82_KPI_SOURCE_TAGS, "taxonomy must not be empty"
    for canonical, tag in _R82_KPI_SOURCE_TAGS.items():
        assert isinstance(canonical, str) and canonical, canonical
        assert isinstance(tag, str) and tag, (canonical, tag)


def test_r82_taxonomy_covers_every_critical_kpi_in_aliases() -> None:
    """Every canonical key the gate's aliases recognise as a CRITICAL KPI must be tagged.

    Without a tag the citation falls back to the generic chrome -- which
    is acceptable for tail KPIs but would be a regression for the
    high-traffic ones (the ones an operator looks at every day).
    """
    expected_critical = {
        "adoption_barriers",
        "open_adoption_barriers",
        "critical_barriers",
        "support_cases",
        "escalated_support_cases",
        "critical_cases",
        "high_cases",
        "bems",
        "action_plans",
        "customer_pulse",
        "success_priorities",
        "team_members",
        "total_customers",
        "risk_score",
        "risk_category",
        "high_risk_customers",
        "incidents",
    }
    missing = expected_critical - set(_R82_KPI_SOURCE_TAGS.keys())
    assert not missing, f"taxonomy missing critical KPIs: {sorted(missing)}"


def test_r82_taxonomy_csconsole_kpis_use_consistent_tag() -> None:
    """All CSConsole-derived KPIs must agree on the same vendor-tag string.

    Pre-R82 the user complained that the generic chrome was "messy" and
    not authentic -- a regression where AB cites ``Snowflake CSConsole``
    but AP cites ``Snowflake CSCONSOLE`` (case drift) would re-introduce
    a less-bad version of the same problem.  Pin the canonical spelling.
    """
    csconsole_kpis = ("adoption_barriers", "action_plans", "customer_pulse")
    tags = {_R82_KPI_SOURCE_TAGS[k] for k in csconsole_kpis}
    assert tags == {"Snowflake CSConsole"}, tags


def test_r82_taxonomy_csone_kpis_use_consistent_tag() -> None:
    """All CSOne-derived KPIs (cases / TAC / BEMS) agree on the same tag."""
    csone_kpis = ("support_cases", "critical_cases", "high_cases", "bems")
    tags = {_R82_KPI_SOURCE_TAGS[k] for k in csone_kpis}
    assert tags == {"Snowflake CSOne"}, tags


# ---------------------------------------------------------------------------
# Label-level resolver (_r82_resolve_source_tag_for_label)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, expected_tag",
    [
        ("Adoption Barriers", "Snowflake CSConsole"),
        ("Total Adoption Barriers", "Snowflake CSConsole"),
        ("Open Adoption Barriers", "Snowflake CSConsole"),
        ("Customer Pulse", "Snowflake CSConsole"),
        ("Total Action Plans", "Snowflake CSConsole"),
        ("Support Cases", "Snowflake CSOne"),
        ("Total Support Cases 90 Days", "Snowflake CSOne"),
        ("Total TAC Cases", "Snowflake CSOne"),
        ("BEMS Escalations", "Snowflake CSOne"),
        ("Risk Score", "AdoptIQ risk_scoring"),
        ("Risk Category", "AdoptIQ risk_scoring"),
        ("Total Customers", "Snowflake EDW Sales subscriptions"),
        ("Team Members", "Snowflake EDW Sales DSM"),
        ("Service Incidents", "AdoptIQ external_intelligence"),
    ],
)
def test_resolve_label_returns_specific_tag(label: str, expected_tag: str) -> None:
    """Each canonical KPI label resolves to its specific source-system tag.

    The label-to-canonical resolution is the SAME alias map the parity
    gate uses (``_canonical_kpi_label`` in ``report_iteration_loop``);
    this test pins that the per-source taxonomy stays in lock-step.
    """
    assert _r82_resolve_source_tag_for_label(label) == expected_tag, label


@pytest.mark.parametrize(
    "non_canonical_label",
    [
        "Generated",
        "Page",
        "Build",
        "Section Header",
        "Random Title",
        "",
        " ",
        "   \t  ",
    ],
)
def test_resolve_label_returns_none_for_non_canonical_labels(
    non_canonical_label: str,
) -> None:
    """Non-canonical labels (metadata fragments, whitespace, empty) → None.

    Forces the caller to fall back to the generic fallback chrome.
    Critical for not over-citing benign metadata like ``Generated:
    2026`` (R57 narrative-token guard contract).
    """
    assert _r82_resolve_source_tag_for_label(non_canonical_label) is None


def test_resolve_label_does_not_raise_on_pathological_input() -> None:
    """Resolver swallows internal failures and returns None."""
    # None input -- should be defensive
    assert _r82_resolve_source_tag_for_label(None) is None  # type: ignore[arg-type]
    # Numeric input -- canonical normalizer can handle stringification
    assert _r82_resolve_source_tag_for_label(12345) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Chrome construction (_r82_chrome_for_label)
# ---------------------------------------------------------------------------


def test_chrome_for_label_returns_specific_chrome_for_known_label() -> None:
    """Known label returns ``" [Source: <specific>]"`` (leading space + bracketed)."""
    chrome = _r82_chrome_for_label("Adoption Barriers")
    assert chrome == " [Source: Snowflake CSConsole]", chrome


def test_chrome_for_label_returns_default_fallback_when_no_arg() -> None:
    """Unknown label without explicit fallback returns the module-level generic chrome."""
    chrome = _r82_chrome_for_label("Generated")
    assert chrome == _PARAGRAPH_FALLBACK_CITATION, chrome


def test_chrome_for_label_respects_custom_fallback() -> None:
    """When a custom fallback is supplied, an unknown label returns that."""
    custom = " [Source: Custom Test Tag]"
    chrome = _r82_chrome_for_label("Generated", fallback=custom)
    assert chrome == custom, chrome


# ---------------------------------------------------------------------------
# Paragraph-level resolver (_r82_chrome_for_paragraph)
# ---------------------------------------------------------------------------


def test_paragraph_chrome_single_source_returns_specific_chrome() -> None:
    """Multi-match paragraph where ALL canonical KPIs share a source → specific chrome."""
    text = "Total Adoption Barriers: 68. Open Adoption Barriers: 11."
    matches = _all_matches(text)
    chrome = _r82_chrome_for_paragraph(matches)
    assert chrome == " [Source: Snowflake CSConsole]", chrome


def test_paragraph_chrome_mixed_sources_falls_back_to_generic() -> None:
    """Multi-match paragraph with KPIs from DIFFERENT systems → generic fallback.

    Preserves the user's "not messy" requirement: rendering a single
    line with two DIFFERENTLY-tagged citations is visually noisier than
    a single generic-tagged line.  Mixed-source paragraphs lose the
    R82 specificity gain in exchange for visual cleanliness; the
    operator can still verify per-system attribution by checking the
    canonical "Report Data Sources" paragraph at the end of the
    document.
    """
    text = "Adoption Barriers: 68. Support Cases: 381."
    matches = _all_matches(text)
    chrome = _r82_chrome_for_paragraph(matches)
    assert chrome == _PARAGRAPH_FALLBACK_CITATION, chrome


def test_paragraph_chrome_empty_matches_falls_back_to_generic() -> None:
    """Empty match list (defensive) returns the fallback unchanged."""
    chrome = _r82_chrome_for_paragraph([])
    assert chrome == _PARAGRAPH_FALLBACK_CITATION, chrome


def test_paragraph_chrome_with_non_canonical_match_only_falls_back() -> None:
    """When matches are present but NONE canonicalise → generic fallback.

    Defensive: a paragraph like ``Build: 58. Page: 1.`` regex-matches
    twice but both labels are non-canonical metadata.  The resolver
    must not crash AND must return generic fallback.
    """
    text = "Build: 58. Page: 1."
    matches = _all_matches(text)
    chrome = _r82_chrome_for_paragraph(matches)
    assert chrome == _PARAGRAPH_FALLBACK_CITATION, chrome


# ---------------------------------------------------------------------------
# End-to-end DOCX injection (per-source specificity)
# ---------------------------------------------------------------------------


def _make_synthetic_docx(tmp_path: Path, paragraphs: list[str]) -> Path:
    """Create a small DOCX containing the given paragraphs, return its path."""
    pytest.importorskip("docx")
    from docx import Document  # type: ignore[import-not-found]

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    out = tmp_path / "synthetic_r82.docx"
    doc.save(str(out))
    return out


def _read_docx_paragraphs(path: Path) -> list[str]:
    pytest.importorskip("docx")
    from docx import Document  # type: ignore[import-not-found]

    return [p.text for p in Document(str(path)).paragraphs]


def test_inject_single_canonical_match_uses_specific_chrome(tmp_path: Path) -> None:
    """End-to-end: a single-canonical-match paragraph carries the SPECIFIC tag.

    Pre-R82: ``Total Adoption Barriers: 68 [Source: AdoptIQ Report Data Sources]``
    Post-R82: ``Total Adoption Barriers: 68 [Source: Snowflake CSConsole]``
    """
    docx = _make_synthetic_docx(tmp_path, ["Total Adoption Barriers: 68"])
    counts = inject_source_citations_into_docx(docx)
    assert counts["paragraphs_injected"] == 1, counts

    paragraphs = _read_docx_paragraphs(docx)
    assert any("Snowflake CSConsole" in p for p in paragraphs), paragraphs
    assert not any("[Source: AdoptIQ Report Data Sources]" in p for p in paragraphs), (
        "single-source canonical paragraph should not get the generic fallback",
        paragraphs,
    )


def test_inject_single_canonical_support_cases_uses_csone_tag(tmp_path: Path) -> None:
    """Support-cases label resolves to ``Snowflake CSOne`` (NOT CSConsole)."""
    docx = _make_synthetic_docx(tmp_path, ["Total Support Cases: 381"])
    counts = inject_source_citations_into_docx(docx)
    assert counts["paragraphs_injected"] == 1, counts

    paragraphs = _read_docx_paragraphs(docx)
    assert any("Snowflake CSOne" in p for p in paragraphs), paragraphs
    assert not any("Snowflake CSConsole" in p for p in paragraphs), paragraphs


def test_inject_multi_match_same_source_uses_specific_chrome(tmp_path: Path) -> None:
    """Multi-match paragraph where all KPIs share a source → specific chrome on every match."""
    docx = _make_synthetic_docx(
        tmp_path, ["Total Adoption Barriers: 68. Open Adoption Barriers: 11."]
    )
    counts = inject_source_citations_into_docx(docx)
    assert counts["paragraphs_injected"] == 1, counts

    paragraphs = _read_docx_paragraphs(docx)
    full_text = "\n".join(paragraphs)
    csconsole_count = full_text.count("[Source: Snowflake CSConsole]")
    generic_count = full_text.count("[Source: AdoptIQ Report Data Sources]")
    assert csconsole_count >= 2, (csconsole_count, full_text)
    assert generic_count == 0, (generic_count, full_text)


def test_inject_multi_match_mixed_sources_uses_generic_chrome(tmp_path: Path) -> None:
    """Multi-match paragraph with mixed sources falls back to generic (visual cleanliness)."""
    docx = _make_synthetic_docx(
        tmp_path, ["Total Adoption Barriers: 68. Total Support Cases: 381."]
    )
    counts = inject_source_citations_into_docx(docx)
    assert counts["paragraphs_injected"] == 1, counts

    paragraphs = _read_docx_paragraphs(docx)
    full_text = "\n".join(paragraphs)
    # Mixed-source paragraph: generic chrome only (no per-source tags).
    assert "[Source: AdoptIQ Report Data Sources]" in full_text, full_text
    assert "[Source: Snowflake CSConsole]" not in full_text, full_text
    assert "[Source: Snowflake CSOne]" not in full_text, full_text


def test_inject_two_column_table_caption_preserves_per_source_taxonomy(
    tmp_path: Path,
) -> None:
    """Round 115 / Build 84: two-column ``label | value`` cards now carry ONE
    aggregated ``Sources: ...`` caption below the table (the R114 matrix
    declutter, extended to KPI cards).  The value cells stay CLEAN; the R82
    per-source taxonomy is preserved IN THE CAPTION -- each cited row's label
    is attributed to its specific source system, not the generic placeholder.
    """
    pytest.importorskip("docx")
    from docx import Document  # type: ignore[import-not-found]

    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Adoption Barriers"
    table.cell(0, 1).text = "68"
    table.cell(1, 0).text = "Total Support Cases"
    table.cell(1, 1).text = "381"
    docx = tmp_path / "synthetic_r82_table.docx"
    doc.save(str(docx))

    counts = inject_source_citations_into_docx(docx)
    # R115: one caption per card, cells untouched.
    assert counts["table_captions_added"] == 1, counts
    assert counts.get("table_cells_injected", 0) == 0, counts

    doc2 = Document(str(docx))
    table2 = doc2.tables[0]
    # Cells must be clean -- no in-cell citation chrome.
    ab_cell = table2.cell(0, 1).text
    cases_cell = table2.cell(1, 1).text
    assert "[Source:" not in ab_cell, ab_cell
    assert "[Source:" not in cases_cell, cases_cell

    # The caption below the table preserves the R82 per-source taxonomy:
    # Adoption Barriers -> CSConsole, Total Support Cases -> CSOne.
    captions = [p.text for p in doc2.paragraphs if p.text.strip().startswith("Sources:")]
    assert len(captions) == 1, captions
    caption = captions[0]
    assert "Snowflake CSConsole" in caption, caption
    assert "Snowflake CSOne" in caption, caption
    # Critical: each label sits with its OWN source system in the caption.
    assert re.search(r"Adoption Barriers[^;]*Snowflake CSConsole", caption), caption
    assert re.search(r"Total Support Cases[^;]*Snowflake CSOne", caption), caption


# ---------------------------------------------------------------------------
# Backward compat: unknown labels still get the generic fallback
# ---------------------------------------------------------------------------


def test_inject_paragraph_with_only_narrative_tokens_keeps_generic(tmp_path: Path) -> None:
    """Pure narrative paragraph with short numeric tokens (no canonical KPI label)
    still gets the generic chrome -- pre-R82 behavior preserved.
    """
    text = "We saw approximately 25 events this week, of which 8 were resolved."
    docx = _make_synthetic_docx(tmp_path, [text])
    counts = inject_source_citations_into_docx(docx)
    # may inject or skip depending on narrative-token gate; what we
    # care about is: IF it injected, it used the GENERIC chrome (not
    # a fabricated per-source tag).
    paragraphs = _read_docx_paragraphs(docx)
    full_text = "\n".join(paragraphs)
    if counts["paragraphs_injected"] >= 1:
        assert "[Source: AdoptIQ Report Data Sources]" in full_text, full_text
        # Defensive: no per-source tag should appear (the paragraph
        # carries no canonical label that could justify one).
        for tag in {"Snowflake CSConsole", "Snowflake CSOne", "AdoptIQ risk_scoring"}:
            assert tag not in full_text, (tag, full_text)


def test_inject_idempotent_on_second_run(tmp_path: Path) -> None:
    """Running the injector a second time on the SAME docx is a no-op (R57 contract).

    Critical: per-source chrome must not double-inject.  Pre-R82 the
    idempotency guard was ``_SOURCE_TOKEN_RE.search`` which matches
    any ``[source:`` substring; the post-R82 specific chrome still
    contains ``[source:`` (case-insensitive), so the guard continues
    to work.
    """
    docx = _make_synthetic_docx(tmp_path, ["Total Adoption Barriers: 68"])

    counts_pass1 = inject_source_citations_into_docx(docx)
    assert counts_pass1["paragraphs_injected"] == 1, counts_pass1

    counts_pass2 = inject_source_citations_into_docx(docx)
    assert counts_pass2["paragraphs_injected"] == 0, counts_pass2
    assert counts_pass2["skipped_already_cited"] >= 1, counts_pass2


# ---------------------------------------------------------------------------
# Round-trip: existing R66/B1 unit-deferral and R76 paren-cluster contracts
# preserved (negative regression guards)
# ---------------------------------------------------------------------------


def test_r66_b1_unit_deferral_still_works_with_specific_chrome() -> None:
    """``"Analysis Period: 90 Days"`` line still defers the citation past ``Days``.

    The R82 chrome change must not break the R66/B1 placement logic --
    ``_rewrite_paragraph_with_inline_citations`` still inserts the chrome
    at the SAME position, only the chrome's content changes.  Asserts on
    the WINDOW_DAYS canonical (``Analysis Period`` → ``window_days``)
    which maps to ``AdoptIQ analysis scope``.
    """
    text = "Analysis Period: 90 Days  Total Customers: 39"
    matches = _all_matches(text)
    chrome = _r82_chrome_for_paragraph(matches)
    rewritten = _rewrite_paragraph_with_inline_citations(text, matches, chrome.strip())

    # No mid-string citation between value and unit (R66/B1 anti-pattern).
    assert re.search(r"90\s*\[Source:[^\]]+\]\s*Days", rewritten) is None, rewritten


def test_r76_paren_cluster_still_emits_one_citation_with_specific_chrome() -> None:
    """``"(APs: 16, ABs: 3)"`` cluster still gets ONE post-) citation.

    R76's whole-line paren-cluster contract is independent of the
    chrome content; this regression guard ensures the R82 chrome
    swap doesn't accidentally re-introduce per-comma in-paren citations.
    """
    text = "(APs: 16, ABs: 3)"
    matches = _all_matches(text)
    chrome = _r82_chrome_for_paragraph(matches)
    rewritten = _rewrite_paragraph_with_inline_citations(text, matches, chrome.strip())

    # Inside the parens there should be NO citation.
    inside_paren = rewritten[rewritten.find("(") + 1: rewritten.rfind(")")]
    assert "[Source:" not in inside_paren, (inside_paren, rewritten)
    # Exactly one citation total on the rewritten line.
    assert rewritten.count("[Source:") == 1, rewritten


# ---------------------------------------------------------------------------
# Source-shape pin: report_source_injector ships R82 markers
# ---------------------------------------------------------------------------


def test_report_source_injector_carries_r82_markers() -> None:
    """The injector module source must carry the R82 phase-B markers so
    a future round can tell at a glance whether the per-source taxonomy
    is wired (i.e. doing a ``git diff <file> | grep 'Round 82'`` should
    show the R82 footprint per the audit-log convention).
    """
    src = Path("report_source_injector.py").read_text(encoding="utf-8")
    assert "_R82_KPI_SOURCE_TAGS" in src, "taxonomy missing"
    assert "_r82_resolve_source_tag_for_label" in src, "label resolver missing"
    assert "_r82_chrome_for_label" in src, "chrome helper missing"
    assert "_r82_chrome_for_paragraph" in src, "paragraph helper missing"
    # Verify all three injection sites carry the R82 wiring.
    assert "Round 82 / Phase B2" in src, "B2 wiring marker missing"
    assert src.count("Round 82 / Phase B3") >= 2, "B3 wiring marker missing (expect 2 sites)"
