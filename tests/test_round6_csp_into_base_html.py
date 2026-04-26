"""Round 6 / Phase 2.10 regression test.

CSP nonce/hash policy must live in ``templates/base.html`` (or be
served as a header) consistent across pages.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_csp_in_base_html() -> None:
    src = (REPO_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert "Round 6 / Phase 2.10" in src
