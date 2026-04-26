"""Round 5 / Phase 1.4 regression test.

The legacy "In a production environment, this would contain real
customer data." placeholder is misleading - it implies the report is a
demo when in fact the upstream fetch returned zero rows.  The fix
replaces it with an explicit "no dataset rows" disclosure.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_no_production_environment_placeholder_text() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 1.4" in src, (
        "Round 5 Phase 1.4 marker missing in app_simple.py."
    )
    # The misleading placeholder must not appear as a *string literal*
    # written into the workbook; we tolerate it inside a code comment
    # that explains why it was removed.  Strip Python-style ``# ...``
    # comment lines before the substring search.
    placeholder = "In a production environment, this would contain real"
    no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert placeholder not in no_comments, (
        "Round 5 Phase 1.4: the misleading placeholder must not be "
        "present in any non-comment source line - the workbook should "
        "render an explicit 'no dataset rows' message instead."
    )
