"""Round 33 / Build8: ``templates/progress.html`` watchdog markers and
``templates/analyze.html`` no-stranded-start behavior.

Why marker tests
----------------
The watchdog and the analyze-page start-success behavior live in template JS
inside Jinja templates -- there is no Python entry point we can
unit-test directly.  These string-presence assertions guard against
silent regressions that would re-introduce the "frozen elapsed
counter" or "stranded analyze page" symptoms the user reported in
the build7-to-build8 transition.

If a future refactor renames or removes a marker comment, this test
must be updated *and* the corresponding behavior must be preserved
in the new code -- the markers are documentation hooks, not
production semantics.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_progress_html_contains_watchdog_marker():
    body = _read("templates/progress.html")
    assert "ROUND33_PROGRESS_TIMER_WATCHDOG" in body, (
        "Round 33 watchdog marker missing -- the elapsed-time counter "
        "must re-arm if it stalls >6s while the run is still 'running', "
        "otherwise the user sees a frozen 'Elapsed: 0s' for the entire "
        "report.  Restore the marker AND the surrounding logic."
    )


def test_progress_html_has_init_and_first_tick_breadcrumbs():
    body = _read("templates/progress.html")
    assert "[adoptiq] progress: init" in body, (
        "Init breadcrumb missing.  Console breadcrumbs let support "
        "distinguish 'JS never ran' from 'JS ran but setInterval did "
        "not fire' without asking the user to reproduce the failure."
    )
    assert "[adoptiq] progress: first elapsed tick" in body, (
        "First-elapsed-tick breadcrumb missing.  See above."
    )


def test_progress_html_registers_elapsed_watchdog_interval():
    body = _read("templates/progress.html")
    assert "elapsedWatchdog" in body, (
        "Watchdog interval handle missing -- without it the watchdog "
        "tick function is never invoked and we cannot detect a stalled "
        "elapsed timer."
    )
    # The watchdog must be set with a 6 s cadence; a longer cadence
    # would let the user-visible freeze persist long enough that the
    # watchdog stops being useful.
    assert "elapsedWatchdogTick, 6000" in body


def test_analyze_html_has_jobs_dashboard_instead_of_redirect_fallback():
    body = _read("templates/analyze.html")
    assert "data-report-jobs-panel" in body
    assert "AdoptIQReportJobs.recordStartedJob" in body
    assert "window.location.href = finalRedirectUrl" not in body, (
        "Round 91 replaces the old auto-redirect + fallback link with "
        "the live Report Jobs panel. Reintroducing this redirect would "
        "send the user back to the four-window workflow."
    )


def test_report_jobs_dashboard_uses_dom_api_for_dynamic_rows():
    """Dynamic job rows must be built with DOM APIs and textContent.

    Round 91 moved the "analysis started" safety surface from an
    auto-redirect fallback link to ``static/js/report_jobs_dashboard.js``.
    Keep the same client-side-web-security contract: no interpolated
    ``innerHTML`` from server-controlled status fields.
    """
    body = _read("static/js/report_jobs_dashboard.js")
    assert "createElement" in body
    assert "textContent" in body
    assert "innerHTML" not in body
