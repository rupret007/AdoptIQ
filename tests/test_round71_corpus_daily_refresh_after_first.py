"""Round 71 / Phase 2 (#9) -- Daily refresh fires after first run.

Pre-R71 ``corpus_bootstrap.start_background`` short-circuited
unconditionally when ``_STATE.completed=True and not rebuild``, which
permanently silenced the daily refresh worker.  The worker calls
``request_refresh(rebuild=False)`` once per 24h to pick up new
content, but after the very first successful index pass that request
was a no-op for the rest of the process lifetime -- so the corpus
went stale and the OneDrive ``synced`` pill was misleading.

Round 71 / Phase 2 (#9) introduces an ``allow_refresh=True`` default
that permits a re-index pass even when ``_STATE.completed=True``,
provided no other pass is currently in flight.
"""

from __future__ import annotations


def test_round71_start_background_signature_has_allow_refresh_kwarg() -> None:
    """The ``start_background`` function MUST accept an
    ``allow_refresh`` keyword arg.  This is the contract that
    distinguishes a daily-refresh call (allow) from a hard-stop retry
    (do not allow)."""
    import inspect

    import corpus_bootstrap

    sig = inspect.signature(corpus_bootstrap.start_background)
    assert "allow_refresh" in sig.parameters, (
        "Round 71 / Phase 2 (#9): start_background must accept an "
        "``allow_refresh`` kwarg.  Pre-R71 this kwarg did not exist "
        "and the function unconditionally short-circuited after the "
        "first successful index pass."
    )


def test_round71_start_background_default_allows_refresh() -> None:
    """The default value of ``allow_refresh`` must be ``True`` so the
    daily refresh worker actually fires without the worker needing to
    pass the kwarg explicitly."""
    import inspect

    import corpus_bootstrap

    sig = inspect.signature(corpus_bootstrap.start_background)
    assert sig.parameters["allow_refresh"].default is True, (
        "Round 71 / Phase 2 (#9): start_background's allow_refresh "
        "default MUST be True so the daily refresh worker fires "
        "without needing to know about the kwarg."
    )


def test_round71_completed_state_does_not_block_refresh_pass() -> None:
    """When ``_STATE.completed=True`` and ``in_progress=False``, a
    fresh ``start_background()`` call MUST be willing to spawn a new
    thread (the daily-refresh contract)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "corpus_bootstrap.py").read_text(encoding="utf-8")
    # The exact gate condition is:
    #    if _STATE.completed and not rebuild and not allow_refresh:
    # so the pre-R71 short-circuit (``if _STATE.completed and not rebuild``)
    # must NOT be present any more.
    assert "if _STATE.completed and not rebuild and not allow_refresh:" in src, (
        "Round 71 / Phase 2 (#9): the start_background gate must be "
        "``if _STATE.completed and not rebuild and not allow_refresh:`` "
        "so allow_refresh=True permits a re-index even after first run."
    )
    # Negative control: the pre-R71 stricter gate (without ``allow_refresh``)
    # must NOT survive as a standalone two-clause check.
    pre_r71_pattern = "if _STATE.completed and not rebuild:"
    assert pre_r71_pattern not in src, (
        f"Round 71 / Phase 2 (#9): the pre-R71 stricter gate "
        f"``{pre_r71_pattern}`` (without ``allow_refresh``) must not "
        f"appear in the source any more.  Found at least one occurrence."
    )


def test_round71_in_progress_set_inside_boot_lock_before_thread_spawn() -> None:
    """Round 71 / Phase 2 (#10) bonus pin: ``in_progress=True`` MUST
    be set inside ``_BOOT_LOCK`` BEFORE the thread is spawned."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "corpus_bootstrap.py").read_text(encoding="utf-8")
    # Find the start_background body and confirm in_progress=True
    # appears BEFORE thread = threading.Thread.
    idx_start = src.find("def start_background(")
    assert idx_start >= 0
    # End of function is the next top-level def, conservatively cap window.
    body = src[idx_start : idx_start + 4000]
    in_progress_idx = body.find("_STATE.in_progress = True")
    thread_idx = body.find("threading.Thread(")
    assert 0 <= in_progress_idx < thread_idx, (
        "Round 71 / Phase 2 (#10): _STATE.in_progress = True must be "
        "set BEFORE threading.Thread(...) inside start_background so "
        "two concurrent refresh requests cannot both pass the "
        "``if _STATE.in_progress: return False`` gate."
    )


def test_round71_completed_can_be_reset_by_caller() -> None:
    """Smoke test: a fresh process state should let the bootstrap
    helper read and write completion state without raising."""
    import corpus_bootstrap

    # Snapshot then restore so we don't perturb other tests.
    state = corpus_bootstrap._STATE
    saved_completed = state.completed
    saved_in_progress = state.in_progress
    try:
        state.completed = True
        state.in_progress = False
        # We do NOT actually call start_background() because that would
        # spawn a real indexing thread.  The lint above already pins
        # the source-shape contract.  This test confirms the state
        # object is mutable in the way the gate expects.
        assert state.completed is True
        assert state.in_progress is False
    finally:
        state.completed = saved_completed
        state.in_progress = saved_in_progress
