"""Round 124 / F10: strip [Source:...] chrome from XLSX Risk_Components cells.

``risk_scoring`` builds ``risk_factors[0]`` with an inline
``format_inline_source(...)`` citation that is meaningful in the Word
narrative but leaks verbatim into the structured XLSX ``Top_Risk_Factor``
cell.  The comprehensive XLSX writer now strips a trailing ``[Source: ...]``
suffix before assigning the cell.

Made-with: Cursor.
"""

import re
from pathlib import Path

# The exact strip applied at app_simple.py ~L19461 (Round 124 / F10).
_STRIP_RE = re.compile(r"\s*\[Source:[^\]]*\]\s*$")


def _strip(value: str) -> str:
    return _STRIP_RE.sub("", value).strip()


def test_trailing_source_chrome_stripped():
    raw = "High open adoption-barrier load [Source: Snowflake CSConsole]"
    assert _strip(raw) == "High open adoption-barrier load"


def test_no_chrome_is_unchanged():
    raw = "High open adoption-barrier load"
    assert _strip(raw) == raw


def test_mid_string_source_preserved():
    # Only a trailing citation is stripped; an embedded reference stays.
    raw = "Driven by [Source: X] support cases and pulse decline"
    assert _strip(raw) == raw


def test_app_simple_carries_the_strip_source_marker():
    src = Path(__file__).parent.parent / "app_simple.py"
    text = src.read_text(encoding="utf-8")
    assert "Round 124 / F10" in text
    assert r"\[Source:[^\]]*\]\s*$" in text
