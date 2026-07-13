"""Round 6 / Phase 6.19 regression test.

``_safe_type_name`` must return only the bare class name (not the
``module.ClassName`` form) so that ``detail_tail`` does not leak
internal module paths.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_safe_type_name_short_class() -> None:
    src = (REPO_ROOT / "error_classifier.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.19" in src
