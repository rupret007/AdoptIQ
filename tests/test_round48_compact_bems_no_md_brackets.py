"""Round 48 / F-COMP-BEMS-MD-LEAK regression tests.

Pin that the four Word-rendering call sites for BEMS ID lists emit
comma-separated bare IDs, NOT the legacy ``[BEMS01916938], [BEMS01952872]``
shape.  The brackets are valid in the briefing book (the LLM uses
them as citation anchors) but in the rendered Word document they
look like the head of an unfinished markdown link ``[text](url)``
and create visible chrome leak.

The fix sites are:
  * executive_intelligence_formatter.py (~ line 1018, BEMS IDs run)
  * compact_report_formatter.py (~ line 975, per-customer barriers)
  * compact_report_formatter.py (~ line 1725, BEMS escalations heading)
  * app_simple.py (~ line 10479, renewal All BEMS IDs paragraph)
  * leader_report_generator.py (~ line 2756, leader bems_id table cell)

We assert against the source (the fix anchor) rather than rendered
output because the rendered output requires a full report run with
fixtures.  The fix anchor proves the correct code path is in place
and a future refactor that replaces it must update the test.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source, assert_not_in_source, count_in_source, index_in_source

import re
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path_rel: str) -> str:
    return (_ROOT / path_rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-site fix anchors
# ---------------------------------------------------------------------------


def test_round48_eif_bems_ids_run_no_brackets():
    """``executive_intelligence_formatter.py`` BEMS IDs run must
    join bare IDs with ``, ``; the legacy ``[{bid}]`` pattern must
    be gone from the BEMS-IDs section.
    """

    src = _read("executive_intelligence_formatter.py")
    # Forbidden legacy shape (immediately after the BEMS IDs run).
    legacy = re.compile(
        r"metrics_para\.add_run\([^\n]*'\\[\\{bid\\}\\]'"
    )
    assert not legacy.search(src), (
        "executive_intelligence_formatter.py BEMS IDs run still uses "
        "legacy [{bid}] bracketed citation; the fix anchor "
        "F-COMP-BEMS-MD-LEAK must replace it with bare IDs"
    )
    assert_in_source(src, "F-COMP-BEMS-MD-LEAK", label='src')


def test_round48_compact_bems_per_customer_no_brackets():
    """``compact_report_formatter.py`` per-customer barriers narrative
    must emit ``bems_id_examples`` without brackets.  The fix anchor
    must reference F-COMP-BEMS-MD-LEAK so a future refactor cannot
    silently drop the protection.
    """

    src = _read("compact_report_formatter.py")
    # The bems_id_examples line should NOT contain f'[{bid}]'.
    bems_examples_block = re.search(
        r"bems_id_examples\s*=\s*', '\.join\([^)]+\)",
        src,
    )
    assert bems_examples_block is not None, (
        "compact_report_formatter.py bems_id_examples assignment "
        "structure changed; update the test if intentional"
    )
    assert "[{bid}]" not in bems_examples_block.group(0) and "[{b}]" not in bems_examples_block.group(0), (
        "compact_report_formatter.py bems_id_examples still wraps "
        "IDs in [] -- markdown chrome leak in per-customer barriers"
    )


def test_round48_compact_bems_escalations_heading_no_brackets():
    """``compact_report_formatter.py`` BEMS Escalations heading
    (``bems_id_str``) must emit bare IDs, not bracketed ones.
    """

    src = _read("compact_report_formatter.py")
    bems_str_block = re.search(
        r"bems_id_str\s*=\s*', '\.join\([^)]+\)",
        src,
    )
    assert bems_str_block is not None, (
        "compact_report_formatter.py bems_id_str assignment missing"
    )
    assert "[{bid}]" not in bems_str_block.group(0), (
        "compact_report_formatter.py bems_id_str still wraps IDs in "
        "[] -- markdown chrome leak in BEMS Escalations heading"
    )


def test_round48_renewal_all_bems_ids_paragraph_no_brackets():
    """``app_simple.py`` renewal "All BEMS IDs:" paragraph must emit
    bare IDs.  The fix anchor must reference F-COMP-BEMS-MD-LEAK.
    """

    src = _read("app_simple.py")
    # Locate the All BEMS IDs paragraph and assert no [{bid}] in
    # the surrounding 400-character window.
    anchor = 'ids_para.add_run("All BEMS IDs:'
    pos = index_in_source(src, anchor)
    if pos < 0:
        pos = index_in_source(src, "ids_para.add_run('All BEMS IDs:")
    assert pos != -1, "Renewal 'All BEMS IDs:' paragraph anchor missing"
    window = src[pos : pos + 400]
    assert "[{bid}]" not in window, (
        "Renewal All BEMS IDs paragraph still wraps IDs in [] -- "
        "markdown chrome leak"
    )
    assert_in_source(window, "F-COMP-BEMS-MD-LEAK", label='window')


def test_round48_leader_bems_id_table_cell_no_brackets():
    """``leader_report_generator.py`` BEMS-ID table cell must emit
    bare ID, not ``f'[{bems_id}]'``.
    """

    src = _read("leader_report_generator.py")
    legacy = "f'[{bems_id}]'"
    assert_not_in_source(src, legacy, label='src')
    assert_in_source(src, "F-COMP-BEMS-MD-LEAK", label='src')


# ---------------------------------------------------------------------------
# Briefing book MUST keep brackets (LLM citation anchor)
# ---------------------------------------------------------------------------


def test_round48_briefing_book_keeps_bems_brackets_for_llm_citation():
    """``adoptiq_backend.py`` briefing-book builders MUST keep the
    ``[{bid}]`` shape because the LLM uses square brackets as the
    canonical citation anchor (see prompt template ``[BEMS01916938]``
    examples).  This test is the explicit guard against an
    over-eager fix that strips brackets from the LLM-facing path.
    """

    src = _read("adoptiq_backend.py")
    # At least two briefing builders use the [{bid}] pattern.
    bracket_uses = count_in_source(src, "f'[{bid}]'")
    assert bracket_uses >= 2, (
        f"Expected >=2 briefing-book sites using f'[{{bid}}]' for LLM "
        f"citation; found {bracket_uses}.  R48 must preserve them."
    )
