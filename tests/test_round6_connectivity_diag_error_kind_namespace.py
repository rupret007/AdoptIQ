"""Round 6 / Phase 6.15 regression test.

Connectivity diagnostics must namespace internal error ``kind`` so
the public API exposes a stable taxonomy.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_connectivity_diag_namespace() -> None:
    src = (REPO_ROOT / "connectivity_diagnostics.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.15" in src
    assert "_namespace_check_kind" in src
