"""Round 162 — progress page surfaces partial-data warnings while running."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRESS = (ROOT / "templates" / "progress.html").read_text(encoding="utf-8")


def test_round162_progress_shows_partial_warnings_while_running() -> None:
    assert "data.status === 'running'" in PROGRESS
    assert "_hasPdw && (data.status === 'completed' || data.status === 'running')" in PROGRESS


def test_round162_progress_uses_amber_warning_state_without_masking_errors() -> None:
    assert ".status-box.warning" in PROGRESS
    assert "border-left: 4px solid var(--warning-fg)" in PROGRESS
    assert "background-color: var(--warning-bg)" in PROGRESS
    assert "sb.className = 'status-box warning'" in PROGRESS
    assert "data.status === 'error' ? 'error' : 'running'" in PROGRESS


def test_round162_progress_renders_production_warning_shape() -> None:
    # F2 warnings are shaped as {dataset, kind, error}; the progress page
    # must show the dataset and useful explanation instead of only an opaque
    # kind such as ``tech_filter_scope_excluded:``.
    assert "raw.dataset || raw.source || raw.type" in PROGRESS
    assert "raw.reason || raw.message || raw.detail || raw.error" in PROGRESS
    assert "prefix + ' (' + kind + ')'" in PROGRESS
    assert "li.textContent = label" in PROGRESS


def test_round162_progress_warning_panel_is_announced_accessibly() -> None:
    assert 'id="partial-warnings-box" class="partial-warnings-box"' in PROGRESS
    assert 'role="status" aria-live="polite" aria-atomic="true"' in PROGRESS
    assert 'aria-labelledby="partial-warnings-heading"' in PROGRESS
    assert 'id="partial-warnings-heading"' in PROGRESS


def test_round162_progress_reveals_terminal_csone_statuses() -> None:
    for status in ("success", "warning", "error", "invalid", "none"):
        assert f"{status}: true" in PROGRESS
    assert "cs.style.display = 'block'" in PROGRESS
    assert "data.csone_import_message || ('CSOne status: ' + safeCsoneStatus)" in PROGRESS
