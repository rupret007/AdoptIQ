"""Round 149 — compact start endpoint thread timeout aligns with harness (1800s)."""

from pathlib import Path


def test_round149_compact_start_uses_thirty_minute_thread_timeout() -> None:
    src = Path("app_simple.py").read_text(encoding="utf-8")
    block_start = src.index("def start_compact_analysis(")
    block = src[block_start : block_start + 12000]
    assert "future.result(timeout=1800)" in block, (
        "compact analysis must allow 30m wall clock (All Managers canonical finalization)"
    )
    assert "future.result(timeout=300)" not in block, "legacy 5-minute compact timeout must be gone"
    assert "Analysis timed out after 30 minutes" in block
