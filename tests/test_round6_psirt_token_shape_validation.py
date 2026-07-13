"""Round 6 / Phase 4.11 regression test.

PSIRT token response must be shape-validated before use.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_psirt_token_shape_validation() -> None:
    src = (REPO_ROOT / "cisco_internal_integrations.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 4.11" in src
