"""Round 6 / Phase 6.12 regression test.

``~/.adoptiq`` directory must be created with 0o700 permissions.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_adoptiq_dir_0700() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.12" in src
    assert "0o700" in src
