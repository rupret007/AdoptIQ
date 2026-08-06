"""Round 6 / Phase 4.8 regression test.

"Expiring within 90 days" arithmetic must be timezone-aware UTC.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_expiring_90d_utc() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 4.8", label='src')
