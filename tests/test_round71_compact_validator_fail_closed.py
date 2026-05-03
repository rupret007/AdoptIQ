"""Round 71 / Phase 3 (#15) -- Compact validator exception fail-closed.

Pre-R71 the Compact path's ``except Exception as _anv_err`` branch
silently accepted the raw LLM output as ``_safe_insight_text``,
defeating the R16/R27 grounding gate the moment the validator import
or a regex hit any runtime hiccup.

Round 71 / Phase 3 (#15) substitutes ``GROUNDING_FAILURE_PLACEHOLDER``
on validator exception so the failure mode matches the validator-
rejected mode: the report NEVER quotes raw LLM text unless the gate
ran successfully.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8", errors="replace")


def test_round71_compact_validator_exception_uses_placeholder() -> None:
    """The Compact path's validator exception branch MUST substitute
    ``GROUNDING_FAILURE_PLACEHOLDER`` (fail-closed)."""
    src = _read_app_simple()
    assert (
        "from ai_narrative_validator import GROUNDING_FAILURE_PLACEHOLDER as _r71_compact_placeholder"
        in src
    ), (
        "Round 71 / Phase 3 (#15): Compact validator exception branch "
        "must import GROUNDING_FAILURE_PLACEHOLDER (aliased as "
        "_r71_compact_placeholder) for the placeholder substitution."
    )
    assert "_safe_insight_text = _r71_compact_placeholder" in src, (
        "Round 71 / Phase 3 (#15): Compact validator exception branch "
        "must assign _r71_compact_placeholder to _safe_insight_text "
        "(fail-closed)."
    )


def test_round71_compact_validator_exception_carries_round71_marker() -> None:
    """The fail-closed branch MUST carry a Round 71 marker so the
    audit grep finds it."""
    src = _read_app_simple()
    assert "Round 71 / Phase 3 (#15)" in src or "R71/#15" in src, (
        "Round 71 / Phase 3 (#15): Compact validator exception branch "
        "must carry a ``Round 71 / Phase 3 (#15)`` or ``R71/#15`` "
        "marker comment near the fail-closed substitution."
    )


def test_round71_compact_validator_exception_logs_at_warning() -> None:
    """The fail-closed branch MUST log at WARNING (not silently swap
    in the placeholder) so a regression in the validator is visible
    in operator logs."""
    src = _read_app_simple()
    # Find the Round 71 marker, then look at the surrounding ~600 chars.
    idx = src.find("R71/#15")
    if idx < 0:
        idx = src.find("Round 71 / Phase 3 (#15)")
    assert idx > 0
    window = src[max(0, idx - 200) : idx + 800]
    assert "logger.warning" in window, (
        "Round 71 / Phase 3 (#15): fail-closed branch must log at "
        "WARNING so a validator regression surfaces in operator logs."
    )


def test_round71_compact_validator_exception_provides_text_fallback() -> None:
    """If the placeholder import itself fails, the writer MUST still
    have a text fallback so the report build never crashes."""
    src = _read_app_simple()
    # The text fallback line is the last-resort message.
    assert "AI narrative withheld" in src, (
        "Round 71 / Phase 3 (#15): if the placeholder import itself "
        "fails, the Compact writer must use a hard-coded text "
        "fallback (``AI narrative withheld...``) so the report build "
        "still succeeds."
    )
