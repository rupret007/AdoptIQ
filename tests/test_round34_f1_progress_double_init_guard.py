"""Round 34 / F1 -- progress.html IIFE double-init guard.

The progress-page IIFE registers three setIntervals on ``window``:
``refreshInterval`` (status poll, 2s), ``elapsedInterval`` (UI tick,
1s), and ``elapsedWatchdog`` (stall detector, 6s).  If the IIFE
runs twice (bundle accident, hot-reload, or a stray re-include of
the inline script), each ``window.X = setInterval(...)`` overwrites
the previous handle but leaves the prior interval running -- the
cleanup at refreshStatus's "completed" / "error" branches only
stops the most recent handle.  Net: 6 timers running, 3 cleanup
handles, double polling load.

Round 34 / F1 adds the same defensive guard pattern that
R26-003 added to static/js/intel_status.js for the intel poller:
a ``window.__adoptiqProgressPageInit`` sentinel that returns
early on the second IIFE invocation.

Source-shape pin only -- the actual JS execution would need a
browser to exercise.  Pattern matches what
test_round26_intel_status_js_poll_cadence.py does for the intel
poller's R26-003 guard.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_progress_template() -> str:
    return (REPO_ROOT / "templates" / "progress.html").read_text(encoding="utf-8")


def test_f1_double_init_guard_marker_present():
    """The ``ROUND34_PROGRESS_DOUBLE_INIT_GUARD`` marker must be in
    the template so a future polish pass that strips comments still
    leaves an obvious trace of the regression class."""
    body = _read_progress_template()
    assert "ROUND34_PROGRESS_DOUBLE_INIT_GUARD" in body, (
        "Round 34 / F1 marker missing -- the IIFE must check a "
        "window.* sentinel and return early on the second invocation, "
        "otherwise a hot-reload or bundle re-include doubles the "
        "/status polling load and orphans interval handles."
    )


def test_f1_iife_consults_init_sentinel_before_setting_intervals():
    """The guard must run BEFORE the three setInterval registrations
    in init().  Pin both halves of the contract: the sentinel READ
    (early return) and the sentinel WRITE (first invocation flips
    the flag)."""
    body = _read_progress_template()
    assert "if (window.__adoptiqProgressPageInit) { return; }" in body, (
        "Sentinel READ missing -- the IIFE must short-circuit when "
        "the flag is already set."
    )
    assert "window.__adoptiqProgressPageInit = true;" in body, (
        "Sentinel WRITE missing -- the first invocation must set the "
        "flag so the second invocation's READ short-circuits."
    )

    # The guard must precede the IIFE's body (specifically: must
    # appear before ``ANALYSIS_ID = {{ analysis_id|tojson }}`` which
    # is the first line of real work).
    guard_idx = body.index("if (window.__adoptiqProgressPageInit)")
    work_idx = body.index("var ANALYSIS_ID = {{ analysis_id|tojson }}")
    assert guard_idx < work_idx, (
        "Sentinel READ must precede the IIFE's first real statement"
    )


def test_f1_guard_is_idempotent_under_repeated_setinterval_pattern():
    """The whole point: exactly ONE setInterval registration per
    timer (refreshInterval, elapsedInterval, elapsedWatchdog).  The
    template should NOT contain accidental duplicates that would
    survive even with the guard.  Pin the ``setInterval(<fn>,
    <interval>);`` shape per timer."""
    body = _read_progress_template()
    # refreshStatus is polled every 2000ms (one place).
    assert body.count("setInterval(refreshStatus, 2000)") == 1, (
        "refreshStatus should be setInterval'd exactly once"
    )
    # updateElapsed is the elapsed-tick (one place in init() and
    # one in _restartElapsedInterval recovery -- two total).  More
    # than two means an accidental duplicate.
    update_count = body.count("setInterval(updateElapsed, 1000)")
    assert update_count == 2, (
        f"updateElapsed should be setInterval'd exactly twice "
        f"(init + watchdog recovery); got {update_count}"
    )
    # elapsedWatchdogTick is the stall-detector (one place in init).
    assert body.count("setInterval(elapsedWatchdogTick, 6000)") == 1, (
        "elapsedWatchdogTick should be setInterval'd exactly once"
    )


def test_f1_guard_does_not_break_existing_breadcrumbs():
    """The R33 init / first-elapsed-tick breadcrumbs must remain --
    they are the support-channel debugging surface and the F1 guard
    must not silence them by short-circuiting before the breadcrumbs
    fire on the FIRST (legitimate) invocation."""
    body = _read_progress_template()
    # Both breadcrumbs still in the template.
    assert "[adoptiq] progress: init" in body
    assert "[adoptiq] progress: first elapsed tick" in body
