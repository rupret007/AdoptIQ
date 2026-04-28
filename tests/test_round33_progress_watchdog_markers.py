"""Round 33 / Build8: ``templates/progress.html`` watchdog markers and
``templates/analyze.html`` redirect-fallback marker.

Why marker tests
----------------
The watchdog and the redirect-fallback live entirely in inline JS
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


def test_analyze_html_has_redirect_fallback_marker():
    body = _read("templates/analyze.html")
    assert "ROUND33_ANALYZE_REDIRECT_FALLBACK" in body, (
        "Analyze-page redirect fallback marker missing -- without "
        "this the user sits on /analyze with no indication that the "
        "report is already running on the server when the auto-redirect "
        "is silently blocked."
    )


def test_analyze_html_uses_dom_api_for_fallback_link():
    """The fallback host must be built with ``createElement`` /
    ``textContent`` -- never ``innerHTML`` with interpolated values.
    Mirrors codeguard-0-client-side-web-security."""
    body = _read("templates/analyze.html")
    # Fallback host id must exist.
    assert "analyze-redirect-fallback" in body
    # Locate the fallback block and assert it does not assign innerHTML
    # with template-string interpolation that includes finalRedirectUrl.
    idx = body.find("ROUND33_ANALYZE_REDIRECT_FALLBACK")
    assert idx != -1
    # Inspect ~3 KB after the marker -- the entire fallback handler
    # lives there.
    snippet = body[idx:idx + 3000]
    assert "innerHTML" not in snippet, (
        "Fallback link must use createElement+textContent, not innerHTML; "
        "interpolating finalRedirectUrl into innerHTML would re-introduce "
        "the XSS vector that the rest of the analyze-page JS already "
        "avoids."
    )
    assert "createElement" in snippet
    assert "textContent" in snippet
