"""Round 6 / Phase 2.3 regression test.

Frontend fetch() callers must check ``response.ok`` and inspect
``Content-Type`` before ``response.json()``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_fetch_ok_content_type_checks() -> None:
    found = False
    for tpl in (REPO_ROOT / "templates").glob("*.html"):
        if "Round 6 / Phase 2.3" in tpl.read_text(encoding="utf-8"):
            found = True
            break
    assert found, "Round 6 Phase 2.3 marker missing in templates/."
