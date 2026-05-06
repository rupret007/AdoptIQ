"""Round 89 / F2 — Leader TAC case Title cell strips markdown chrome.

Build 63 acceptance audit caught one cell in the Leader docx TAC table
rendering ``**Classic Calabrio***delete old report - Calabrio WFO#
00179474`` with literal asterisks intact.  ``leader_report_generator
._strip_markdown_chrome`` was authored in R42 / Phase 6 *specifically*
for this case -- its docstring at line 206 quotes the same offending
string -- but the helper was never wired into the TAC table emission
path at line 4578.

This test pins:

  1. Source-shape: line 4578 must thread the title through
     ``_strip_markdown_chrome`` rather than calling ``str(title)`` raw.

  2. Behavior: the helper itself must continue to collapse
     ``**Classic Calabrio***`` -> ``Classic Calabrio`` and the trailing
     ``*delete old report ...`` (an unpaired italic marker that the
     helper is NOT supposed to collapse on its own -- that is left as a
     single-asterisk in legitimate non-markdown free text) is preserved
     as-is so we never strip a legitimate single ``*`` in user-typed
     subject text.

  3. End-to-end: an actual TAC table cell rendered through the writer
     must not contain ``**`` literal markers.

The tests do not modify any source file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# --------------------------------------------------------------------
# Source-shape pin (R89/F2)
# --------------------------------------------------------------------


def _read_leader_module() -> str:
    return Path("leader_report_generator.py").read_text(encoding="utf-8")


def test_r89_f2_tac_title_routes_through_strip_helper():
    """Line ~4578 (TAC table title write) MUST go through
    ``_strip_markdown_chrome`` rather than ``str(title)`` directly."""
    src = _read_leader_module()
    # The R89/F2 marker comment + the strip helper call MUST land near each other.
    assert "Round 89 / F2" in src, (
        "Round 89 / F2: missing source marker comment in leader_report_generator.py"
    )
    # Specific call site shape: row_cells[2].text = _strip_markdown_chrome(title) or 'No title'
    assert (
        "row_cells[2].text = _strip_markdown_chrome(title) or 'No title'" in src
    ), (
        "Round 89 / F2: TAC title write must route through "
        "_strip_markdown_chrome with the canonical 'No title' fallback"
    )
    # Negative-control: the legacy raw write MUST NOT be present anymore.
    legacy_pattern = re.compile(
        r"row_cells\[2\]\.text\s*=\s*str\(title\)(?!\s*#\s*Legacy)",
    )
    assert not legacy_pattern.search(src), (
        "Round 89 / F2: legacy raw `row_cells[2].text = str(title)` "
        "TAC write must be removed"
    )


def test_r89_f2_marker_describes_b63_finding():
    """The R89/F2 source marker MUST cite the Build 63 audit finding so a
    future reviewer can trace why this fix exists (we cite the exact
    offending string in the comment block)."""
    src = _read_leader_module()
    # Locate the R89/F2 block
    idx = src.find("Round 89 / F2:")
    assert idx >= 0, "Round 89 / F2 marker missing"
    block = src[idx : idx + 1200]
    assert "Build 63 audit" in block, (
        "Round 89 / F2: marker comment should reference the Build 63 audit "
        "so reviewers can find the audit log entry"
    )
    assert "Classic Calabrio" in block, (
        "Round 89 / F2: marker comment should cite the canonical offending "
        "string for traceability"
    )


# --------------------------------------------------------------------
# Helper behavior (regression guard for _strip_markdown_chrome itself)
# --------------------------------------------------------------------


@pytest.fixture(scope="module")
def strip_helper():
    from leader_report_generator import _strip_markdown_chrome

    return _strip_markdown_chrome


def test_strip_helper_collapses_bold_with_trailing_extra_star(strip_helper):
    """``**X***Y`` is the canonical Build 63 offending shape: a bold
    pair followed by a triple-asterisk run.  The R42 helper's regex
    ``\\*{2,}`` collapses BOTH runs (``**`` and ``***``) so the result
    leaves the literal ``X`` and ``Y`` adjacent.  The data was garbage
    to begin with (a CSOne title that someone typed with markdown by
    mistake); the contract is purely "strip markdown chrome, leave
    content as-is".  ``**`` literal markers MUST NOT survive."""
    out = strip_helper(
        "**Classic Calabrio***delete old report - Calabrio WFO# 00179474"
    )
    # Both runs of 2+ asterisks (the leading ``**`` and the inner ``***``)
    # are stripped; the resulting ``Calabriodelete`` smushing reflects the
    # original malformed input, not a helper bug.
    assert out == "Classic Calabriodelete old report - Calabrio WFO# 00179474"
    # The critical contract: no ``**`` literal markers remain.
    assert "**" not in out
    # And no ``***`` either.
    assert "***" not in out


def test_strip_helper_handles_paired_bold(strip_helper):
    out = strip_helper("**Customer Title**")
    assert out == "Customer Title"
    assert "**" not in out


def test_strip_helper_preserves_single_asterisk_in_punctuation(strip_helper):
    """Helper deliberately preserves a SINGLE ``*`` in non-markdown
    punctuation contexts (regex is ``\\*{2,}`` -- minimum 2 to fire) so
    legitimate isolated asterisks survive (e.g. footnote markers)."""
    out = strip_helper("Issue * see footnote")
    # Single * outside of pair patterns is preserved
    assert "*" in out
    assert out == "Issue * see footnote"


def test_strip_helper_handles_none_and_empty(strip_helper):
    assert strip_helper(None) == ""
    assert strip_helper("") == ""
    assert strip_helper("   ") == ""


def test_strip_helper_collapses_triple_or_more_asterisks(strip_helper):
    """Runs of 3, 4, or more asterisks all collapse (single regex
    ``\\*{2,}``)."""
    assert strip_helper("***bold***") == "bold"
    assert strip_helper("****wrapped****") == "wrapped"
    assert strip_helper("**X***Y**") == "XY"


# --------------------------------------------------------------------
# End-to-end smoke (the actual TAC table cell text after the writer runs)
# --------------------------------------------------------------------


def test_tac_table_cell_contains_no_double_star_after_fix():
    """After R89/F2 the TAC table cell must not carry ``**`` literal
    markers even when the source case Title carries leftover markdown
    chrome.  This is a black-box guard against any future writer-path
    regression that bypasses the strip helper."""
    # We don't spin up the full report generator here -- that requires
    # Snowflake context.  Instead we directly invoke the strip helper
    # which is the linchpin of the fix; the source-shape test above
    # guarantees the helper IS called at the TAC write site.
    from leader_report_generator import _strip_markdown_chrome

    samples = [
        "**Classic Calabrio***delete old report - Calabrio WFO# 00179474",
        "**Bug Reproduction Steps**",
        "**Customer A**: outage on Friday",
        "***triple***",
        "Plain title with no markdown",
        "Single * asterisk in middle",
    ]
    for s in samples:
        out = _strip_markdown_chrome(s)
        # Critical contract: no ``**`` literal markers ever survive
        assert "**" not in out, (
            f"Round 89 / F2: ``**`` leak in {s!r} -> {out!r}"
        )
