"""Round 49 / F-COMP-BEMS-MD-LEAK-R49 regression tests.

Build25 re-audit caught two surfaces R48-D7 missed:

  * Compact docx 'Critical Trouble Spots' section still emitted
    ``IDs: [BEMS01943186], [BEMS01946483], ...`` (~65 occurrences in
    run 1777464851).  Source: the LLM-generated narrative parsed
    through ``_parse_markdown_for_fallback`` did not strip the
    bracket chrome that the compact prompt template
    (adoptiq_backend.py ~L12595) instructs the model to emit.
  * Renewal docx narrative still emitted ``Defect IDs: [BEMS...]``
    style brackets in three direct AdoptIQ-rendered sites
    (app_simple.py ~L10763 / ~L10768 / ~L10780 / ~L11301), totalling
    ~182 individual bracketed IDs in run 1777464863's renewal Word
    document.

R49-B1 fix (two parts):

  1. New shared helper ``report_utils.strip_bems_brackets_from_llm_text``
     that strips square brackets around real BEMS/CSC ID patterns
     (``BEMS\\d{5,12}``, ``CSC[A-Z]{2}\\d+``, ``CSC\\d+``) but
     preserves placeholder text (``[BEMSxxxxxxxx]``) and citation
     chrome (``[Source: ...]``).  Wired into the compact LLM
     callout (compact_report_formatter add_executive_summary +
     add_renewal_recommendations), the comprehensive markdown
     fallback parser (app_simple._parse_markdown_for_fallback), and
     the executive report builder's AI-output contract
     (executive_report_builder._enforce_ai_output_contract).

  2. Direct AdoptIQ-rendered sites in the renewal narrative
     (app_simple.py defect-by-customer block + Troubled Accounts
     deep dive) now emit bare IDs, mirroring the R48-D7 wire pattern
     used in the other 5 renderers.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from report_utils import strip_bems_brackets_from_llm_text  # noqa: E402


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_strips_bracketed_bems_ids() -> None:
    raw = "IDs: [BEMS01943186], [BEMS01946483], [BEMS01947191]"
    out = strip_bems_brackets_from_llm_text(raw)
    assert out == "IDs: BEMS01943186, BEMS01946483, BEMS01947191"


def test_strips_bracketed_csc_letter_ids() -> None:
    raw = "Defect [CSCwa55555] is open and [CSCxx12345] is fixed."
    out = strip_bems_brackets_from_llm_text(raw)
    assert out == "Defect CSCwa55555 is open and CSCxx12345 is fixed."


def test_strips_bracketed_csc_digit_ids() -> None:
    raw = "Bug [CSC123456] correlates with [BEMS01999999]."
    out = strip_bems_brackets_from_llm_text(raw)
    assert out == "Bug CSC123456 correlates with BEMS01999999."


def test_preserves_placeholder_bemsxxxxxxx() -> None:
    """The user-facing 'Verifiable IDs' note in the data sources
    section uses ``[BEMSxxxxxxxx]`` as a generic placeholder.  This
    is NOT a real ID and the strip helper must leave it intact.
    """
    raw = "All [BEMSxxxxxxxx] and [CSCxxxxxxx] are verifiable."
    out = strip_bems_brackets_from_llm_text(raw)
    assert out == raw


def test_preserves_source_citation_chrome() -> None:
    """``[Source: CSOne; Field(s): Case #]`` style chrome must
    survive -- the strip helper must only target ID patterns.
    """
    raw = "Headline: 28 BEMS [Source: CSOne; Field(s): Transaction ID]"
    out = strip_bems_brackets_from_llm_text(raw)
    assert out == raw


def test_preserves_already_bare_ids() -> None:
    raw = "IDs: BEMS01943186, BEMS01946483 (no brackets)"
    out = strip_bems_brackets_from_llm_text(raw)
    assert out == raw


def test_idempotent_on_repeated_strip() -> None:
    raw = "IDs: [BEMS01943186], [BEMS01946483]"
    once = strip_bems_brackets_from_llm_text(raw)
    twice = strip_bems_brackets_from_llm_text(once)
    assert once == twice


def test_handles_none_and_empty_input() -> None:
    assert strip_bems_brackets_from_llm_text(None) == ""
    assert strip_bems_brackets_from_llm_text("") == ""


def test_short_pseudo_id_not_stripped() -> None:
    """``[BEMS01]`` (2 digits) is NOT a real ID -- real BEMS IDs are
    8+ digits.  The strip helper requires at least 5 digits to avoid
    accidentally rewriting unrelated bracket text.
    """
    raw = "Random [BEMS12] should NOT be stripped"
    out = strip_bems_brackets_from_llm_text(raw)
    assert out == raw


# ---------------------------------------------------------------------------
# Wiring pins (defense-in-depth: catches future refactors that break
# the hook between the LLM-output renderer and the strip helper)
# ---------------------------------------------------------------------------


def test_app_simple_markdown_fallback_imports_and_calls_strip_helper() -> None:
    """Pin: ``app_simple._parse_markdown_for_fallback`` MUST route
    LLM output through ``strip_bems_brackets_from_llm_text``.  The
    fallback parser is the gateway for ALL LLM-generated narrative
    rendered into compact / comprehensive Word docs; if the wire
    breaks, every renderer regresses simultaneously.
    """
    src = (PROJECT_ROOT / "app_simple.py").read_text()
    assert "F-COMP-BEMS-MD-LEAK-R49" in src, (
        "R49-B1: app_simple.py must reference F-COMP-BEMS-MD-LEAK-R49 "
        "anchor (defense-in-depth marker)."
    )
    needle = "strip_bems_brackets_from_llm_text"
    assert src.count(needle) >= 1, (
        "R49-B1: app_simple.py must call strip_bems_brackets_from_llm_text "
        "in the markdown fallback parser path."
    )


def test_compact_report_formatter_imports_and_calls_strip_helper() -> None:
    """Pin: ``compact_report_formatter`` MUST strip bracket chrome
    on both the executive_summary callout and the renewal_recommendations
    AI-powered insights paragraph (the two LLM-rendered sites).
    """
    src = (PROJECT_ROOT / "compact_report_formatter.py").read_text()
    assert "F-COMP-BEMS-MD-LEAK-R49" in src
    assert src.count("strip_bems_brackets_from_llm_text") >= 3, (
        "R49-B1: compact_report_formatter.py must import the helper "
        "(line 1) and call it at the executive_summary callout AND "
        "the renewal_recommendations AI insights paragraph (>=3 "
        "total occurrences)."
    )


def test_executive_report_builder_imports_and_calls_strip_helper() -> None:
    """Pin: the comprehensive ``executive_report_builder`` AI-output
    contract MUST also route through the strip helper so the
    comprehensive Word document does not leak bracketed BEMS IDs
    when the LLM emits them per the prompt template at
    adoptiq_backend.py ~L10537.
    """
    src = (PROJECT_ROOT / "executive_report_builder.py").read_text()
    assert "F-COMP-BEMS-MD-LEAK-R49" in src
    assert "strip_bems_brackets_from_llm_text" in src


def test_renewal_narrative_emits_bare_defect_ids() -> None:
    """Pin: ``app_simple.py`` renewal narrative defect-listing sites
    MUST emit bare IDs, mirroring the R48-D7 wire in the 5 other
    renderers.  Pre-R49 each emitted ``[{x}]`` per ID which leaked
    ~182 brackets into the renewal docx for run 1777464863.
    """
    src = (PROJECT_ROOT / "app_simple.py").read_text()
    pattern_bracketed_join = "[f\"[{x}]\""
    assert pattern_bracketed_join not in src or src.count(pattern_bracketed_join) == 0, (
        "R49-B1: app_simple.py renewal narrative MUST NOT emit "
        f"{pattern_bracketed_join!r} -- this is the residual R48-D7 "
        "miss that leaked ~182 bracketed BEMS IDs into the renewal "
        "docx.  Use bare IDs instead: ', '.join(str(x) for x in ids)."
    )
