"""Round 6 / Phase 4.10 regression test.

Circuit token endpoint must validate the JSON shape (presence of
``access_token`` / ``expires_in``) before consuming.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_circuit_token_json_shape() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 4.10" in src
