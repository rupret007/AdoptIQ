"""Round 162 — progress page surfaces partial-data warnings while running."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRESS = (ROOT / "templates" / "progress.html").read_text(encoding="utf-8")


def test_round162_progress_shows_partial_warnings_while_running() -> None:
    assert "data.status === 'running'" in PROGRESS
    assert "_hasPdw && (data.status === 'completed' || data.status === 'running')" in PROGRESS
