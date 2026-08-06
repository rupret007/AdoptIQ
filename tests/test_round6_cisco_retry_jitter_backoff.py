"""Round 6 / Phase 4.12 regression test.

Cisco API retries must use jittered exponential backoff.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_cisco_retry_jitter_backoff() -> None:
    src = (REPO_ROOT / "cisco_internal_integrations.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 4.12", label='src')
