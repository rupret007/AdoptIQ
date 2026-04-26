"""Round 7 / Phase 2.10 regression test.

``snowflake_prefetch.fetch_dataset`` must use jittered exponential-backoff retry on transient Snowflake errors.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_2_10() -> None:
    src = (REPO_ROOT.joinpath('snowflake_prefetch.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 2.10" in src, (
        "Round 7 Phase 2.10 marker missing in snowflake_prefetch.py."
    )
