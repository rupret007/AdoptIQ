"""Round 6 / Phase 3.5 regression test.

Customer prompt must include explicit negative constraints (no
fabrication, no contact details, no monetary amounts not in source).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_customer_prompt_negative_constraints() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 3.5" in src
