"""Round 71 / Phase 6 (#31) -- Compact 'Immediate Actions' priority remap.

Pre-R71 the Compact report's "Immediate Actions" prioritization label
read ``"🟡 MEDIUM"`` for items 3-4.  Round 67 / B6 introduced the
user-facing vocabulary contract (``MEDIUM`` → ``MODERATE``), but that
remap missed this priority label site.

Round 71 / Phase 6 (#31) renames the priority literal so the Compact
report agrees with the rest of the app's user-facing vocabulary.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_compact() -> str:
    return (REPO_ROOT / "compact_report_formatter.py").read_text(encoding="utf-8", errors="replace")


def test_round71_compact_priority_uses_moderate_not_medium() -> None:
    """The Compact priority label MUST use ``"🟡 MODERATE"`` (not
    ``"🟡 MEDIUM"``)."""
    src = _read_compact()
    expected = '"🔴 HIGH" if i <= 2 else "🟡 MODERATE" if i <= 4 else "🟢 LOW"'
    assert expected in src, (
        f"Round 71 / Phase 6 (#31): Compact priority label must use "
        f"the user-facing 'MODERATE' vocabulary, not 'MEDIUM'.  "
        f"Expected literal: {expected!r}"
    )


def test_round71_compact_priority_pre_r71_label_removed() -> None:
    """The pre-R71 ``"🟡 MEDIUM"`` literal MUST NOT appear in the
    Compact priority assignment."""
    src = _read_compact()
    forbidden = '"🔴 HIGH" if i <= 2 else "🟡 MEDIUM" if i <= 4 else "🟢 LOW"'
    assert forbidden not in src, (
        f"Round 71 / Phase 6 (#31): the pre-R71 priority literal "
        f"({forbidden!r}) must be removed from the Compact formatter."
    )


def test_round71_compact_priority_marker_present() -> None:
    """The R71 marker comment MUST be present so the audit grep finds
    the change point."""
    src = _read_compact()
    # Either the explicit "Round 71 / Phase 6 (#31)" marker OR the
    # comment block describing the MEDIUM->MODERATE remap.
    assert (
        "Round 71" in src and "MODERATE" in src
    ) or "label MEDIUM -> MODERATE" in src, (
        "Round 71 / Phase 6 (#31): the Compact priority site must "
        "carry a comment marker describing the MEDIUM -> MODERATE remap."
    )
