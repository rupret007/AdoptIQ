"""Round 71 / Phase 5 (#27) -- query_id always recorded, never 404s.

Pre-R71 ``_record_ask_ai_query_diag`` was only called when
``retrieval_diag`` came back non-empty.  Any query whose upstream
returned an empty diag (legacy fallback path, corpus-bypass path)
surfaced a ``query_id`` to the UI but ``GET /api/ask-ai/diagnostics/
<query_id>`` 404'd from that id, surprising the operator who clicked
the debug chip.

Round 71 / Phase 5 (#27) ALWAYS records the diag (with a minimal
``method=unknown`` + ``empty_retrieval_diag=True`` stub when the
upstream returned nothing) so the lookup always returns 200.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8", errors="replace")


def test_round71_record_diag_always_called_after_grounded_ok() -> None:
    """The Ask AI grounded-ok branch MUST call
    ``_record_ask_ai_query_diag`` UNCONDITIONALLY (no
    ``if _retrieval_diag:`` guard)."""
    src = _read_app_simple()
    # Locate the grounded-ok branch by the R71 marker.
    idx = src.find("Round 71 / Phase 5 (#27)")
    assert idx > 0
    body = src[idx : idx + 1800]
    # The record call MUST appear in this branch.
    assert "_record_ask_ai_query_diag(_query_id, _retrieval_diag)" in body, (
        "Round 71 / Phase 5 (#27): the grounded-ok branch must always "
        "call _record_ask_ai_query_diag(...)."
    )


def test_round71_empty_retrieval_diag_falls_back_to_minimal_stub() -> None:
    """When the upstream ``retrieval_diag`` is empty, the grounded-ok
    branch MUST stamp a minimal stub with ``method='unknown'``,
    ``empty_retrieval_diag=True``, and the active model name so the
    UI lookup never sees an empty record."""
    src = _read_app_simple()
    # The stub literal.
    assert '"method": "unknown"' in src, (
        "Round 71 / Phase 5 (#27): the empty-diag fallback must stamp "
        '``method="unknown"``.'
    )
    assert '"empty_retrieval_diag": True' in src, (
        "Round 71 / Phase 5 (#27): the empty-diag fallback must stamp "
        "``empty_retrieval_diag=True`` so the operator can see the "
        "diag came back empty."
    )


def test_round71_record_diag_pre_r71_conditional_check_removed() -> None:
    """The pre-R71 ``if isinstance(_retrieval_diag, dict) and
    _retrieval_diag:`` guard MUST be gone."""
    src = _read_app_simple()
    # The pre-R71 conditional that guarded the record call.
    forbidden = "if isinstance(_retrieval_diag, dict) and _retrieval_diag:"
    # If it appears AT ALL we have a regression.
    assert forbidden not in src, (
        "Round 71 / Phase 5 (#27): the pre-R71 conditional "
        f"{forbidden!r} (which prevented the record call when the "
        "upstream diag was empty) must be removed."
    )
