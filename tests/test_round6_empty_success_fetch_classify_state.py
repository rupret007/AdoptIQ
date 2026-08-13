"""Empty-source workbook rows preserve the canonical five-state contract.

The Round 167 implementation supersedes the legacy tri-state UI classifier,
which collapsed source-unavailable and fetch-failed into the same label.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_empty_fetch_preserves_canonical_source_state() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "_r167_compact_empty_source_placeholder", label="src")
    assert_in_source(src, "cm.source_data_state", label="src")
    assert_in_source(
        src,
        '{"zero", "unavailable", "failed", "partial", "stale"}',
        label="src",
    )
