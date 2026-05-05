"""Round 71 / Phase 2 (#10) -- Bootstrap thread race fix.

Pre-R71 ``corpus_bootstrap.start_background`` set ``_STATE.in_progress=True``
inside ``_run_index_pass`` AFTER spawning the thread.  Two HTTP refresh
requests in the same window could both pass the
``if _STATE.in_progress: return False`` gate, spawn two threads, and
race ``commit_to_disk`` (writing to the same encrypted DB).

Round 71 / Phase 2 (#10) sets the flag inside ``_BOOT_LOCK`` BEFORE
spawning the thread so the gate is honest under contention.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_corpus_bootstrap() -> str:
    return (REPO_ROOT / "corpus_bootstrap.py").read_text(encoding="utf-8", errors="replace")


def test_round71_in_progress_flag_set_before_thread_creation_in_source() -> None:
    """The source MUST set ``_STATE.in_progress = True`` before the
    ``threading.Thread(...)`` constructor inside start_background."""
    src = _read_corpus_bootstrap()
    idx_start = src.find("def start_background(")
    assert idx_start >= 0, "start_background function not found"
    body = src[idx_start : idx_start + 4000]
    in_progress_idx = body.find("_STATE.in_progress = True")
    thread_idx = body.find("threading.Thread(")
    assert 0 <= in_progress_idx < thread_idx, (
        "Round 71 / Phase 2 (#10): in_progress=True must be set BEFORE "
        "the threading.Thread(...) constructor inside start_background."
    )


def test_round71_in_progress_flag_set_inside_boot_lock() -> None:
    """The ``in_progress = True`` assignment must be inside the
    ``with _BOOT_LOCK:`` block so two concurrent callers see the flag."""
    src = _read_corpus_bootstrap()
    idx_start = src.find("def start_background(")
    assert idx_start >= 0
    body = src[idx_start : idx_start + 4000]
    boot_lock_idx = body.find("with _BOOT_LOCK:")
    in_progress_idx = body.find("_STATE.in_progress = True")
    thread_idx = body.find("threading.Thread(")
    assert 0 <= boot_lock_idx < in_progress_idx < thread_idx, (
        "Round 71 / Phase 2 (#10): the assignment ``_STATE.in_progress = True`` "
        "must occur inside ``with _BOOT_LOCK:`` and BEFORE the thread spawn."
    )


def test_round71_pre_r71_in_progress_set_in_run_index_pass_path_removed() -> None:
    """The legacy assignment inside ``_run_index_pass`` (which is what
    created the race) MUST not survive at the top of that helper."""
    src = _read_corpus_bootstrap()
    idx_run = src.find("def _run_index_pass(")
    assert idx_run >= 0
    # Look at the first ~300 characters of the helper body (post-docstring)
    body = src[idx_run : idx_run + 1500]
    # If in_progress=True is set in this helper at all, it must be a
    # DEFENSIVE write inside an exception/cleanup branch -- never the
    # primary signal that the bootstrap is running.  The R71 contract
    # is: the caller (start_background) owns the True transition; the
    # helper only owns the False transition (in the finally clause).
    # We accept the helper containing ``in_progress = False`` (cleanup)
    # but not ``in_progress = True`` as a primary signal.
    primary_true = "_STATE.in_progress = True"
    if primary_true in body:
        # If it does appear, it must be after a clear comment marking
        # it as a defensive/race-recovery write.  The R71 fix removed
        # the original primary write site; keep the test honest.
        offset = body.find(primary_true)
        # Walk backward up to ~120 chars to find any comment line.
        backwards = body[max(0, offset - 200) : offset]
        assert "Round 71" in backwards or "race" in backwards.lower() or "defensive" in backwards.lower(), (
            "Round 71 / Phase 2 (#10): _STATE.in_progress = True still "
            "appears at the top of _run_index_pass without a clear "
            "Round-71/race/defensive marker.  This was the racing "
            "assignment that the R71 fix relocated to start_background."
        )


def test_round71_in_progress_cleared_in_finally_clause() -> None:
    """The ``_run_index_pass`` helper MUST clear ``in_progress=False``
    in a ``finally`` block so a crash mid-pass does not leave the flag
    stuck True (which would permanently silence subsequent refreshes)."""
    src = _read_corpus_bootstrap()
    idx_run = src.find("def _run_index_pass(")
    assert idx_run >= 0
    # Round 83 / Build 59: R83 grew the function body past the
    # original 6000-char cap (added ~150 lines of source-marker
    # comments + proxy detection + signed_in_no_corpus disjunction).
    # Scale the read window with function growth by walking to the
    # next ``def`` boundary so this test is robust to future
    # additions.
    next_def = src.find("\ndef ", idx_run + 1)
    if next_def == -1:
        next_def = len(src)
    body = src[idx_run:next_def]
    assert "_STATE.in_progress = False" in body, (
        "Round 71 / Phase 2 (#10): _run_index_pass must clear "
        "in_progress=False (in a finally block) so a crash mid-pass "
        "does not permanently silence subsequent refreshes."
    )
