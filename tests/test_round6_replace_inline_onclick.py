"""Round 6 / Phase 2.14 regression test.

Inline ``onclick=`` handlers should be replaced with
``addEventListener`` to play well with strict CSP.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_replace_inline_onclick() -> None:
    found = False
    for tpl in (REPO_ROOT / "templates").glob("*.html"):
        if "Round 6 / Phase 2.14" in tpl.read_text(encoding="utf-8"):
            found = True
            break
    assert found, "Round 6 Phase 2.14 marker missing in templates/."
