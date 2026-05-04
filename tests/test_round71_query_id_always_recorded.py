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
    ``if _retrieval_diag:`` guard).

    Round 74 / P4: the diag payload now carries an additional
    ``_r74_evidence_records`` field (so the new evidence-drawer
    lookup endpoint can resolve a source id back to a record).
    The persistence call now passes a normalised dict
    (``_r74_diag_to_persist``) which is built from
    ``_retrieval_diag`` + the evidence records.  The R71 contract
    is preserved (always called with the canonical query id, never
    skipped); only the second argument's name changed from
    ``_retrieval_diag`` to ``_r74_diag_to_persist``.
    """
    src = _read_app_simple()
    # Locate the grounded-ok branch by the R71 marker.
    idx = src.find("Round 71 / Phase 5 (#27)")
    assert idx > 0
    body = src[idx : idx + 3500]
    # Round 74 / P4: accept either the legacy direct-pass form or
    # the new pre-built diag-dict form.  Both preserve the R71
    # always-called-with-query-id contract.
    legacy_call = "_record_ask_ai_query_diag(_query_id, _retrieval_diag)"
    r74_call = "_record_ask_ai_query_diag(_query_id, _r74_diag_to_persist)"
    assert legacy_call in body or r74_call in body, (
        "Round 71 / Phase 5 (#27): the grounded-ok branch must always "
        "call _record_ask_ai_query_diag(_query_id, ...) -- either with "
        "the raw _retrieval_diag (pre-R74 form) or with the R74 "
        "_r74_diag_to_persist wrapper that adds evidence_records."
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
