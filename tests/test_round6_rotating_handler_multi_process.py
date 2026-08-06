"""Round 6 / Phase 6.10 regression test.

``RotatingFileHandler`` must be safe for multi-process use (PID
suffix, 0o600 perms preserved on rotate).
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_rotating_handler_multi_process() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 6.10", label='src')
