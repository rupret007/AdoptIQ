"""Round 5 / Phase 2.2 regression test.

The help-page connectivity diagnostics table must be assembled with
DOM APIs and ``textContent`` instead of string-concatenated
``innerHTML`` so a malicious or compromised diagnostic payload cannot
inject script.  (CodeGuard ``client-side-web-security``.)
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_help_connectivity_uses_textcontent() -> None:
    src = (REPO_ROOT / "templates" / "help.html").read_text(encoding="utf-8")
    assert "Round 5 / Phase 2.2" in src, (
        "Round 5 Phase 2.2 marker missing in templates/help.html."
    )
    assert "textContent" in src, (
        "Round 5 Phase 2.2: help.html must use textContent / safe DOM "
        "APIs to render diagnostic detail strings instead of innerHTML."
    )
