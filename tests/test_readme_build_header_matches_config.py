"""README release-notes header must track config.ADOPTIQ_BUILD (Round 127 PC README sync)."""

from __future__ import annotations

from pathlib import Path

from config import ADOPTIQ_BUILD, ADOPTIQ_VERSION


def test_readme_header_mentions_current_build_and_version():
    root = Path(__file__).resolve().parents[1]
    text = (root / "README.md").read_text(encoding="utf-8")
    header = "\n".join(text.splitlines()[:6])
    assert ADOPTIQ_VERSION in header
    assert f"build {ADOPTIQ_BUILD}" in header
