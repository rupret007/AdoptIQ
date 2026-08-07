"""Round 152 -- UI clarity and accessibility regression pins.

The DOM-safety posture of this app is already strong (no ``|safe``, no
``eval``, no inline ``onclick``, DOMPurify on the Ask AI markdown path).
The gaps the Round 152 audit found are in the *async* surfaces and in the
language shown to non-technical Customer Success managers:

* **E1** ``progress.html`` mutates status, activity, elapsed time, the
  progress bar and the error strip every 2 s and carried no live region,
  no ``role="progressbar"`` and no accessible name -- so a screen-reader
  user heard the page once on load and was never told the run failed.
* **E2** error toasts auto-dismissed after 5 s.  The toast is the only
  channel that reports why a report failed to start, so a manager who
  glanced away came back to a page that looked like nothing happened.
  The Leader form's toasts were never announced at all.
* **E3** the customer typeahead failed completely silently -- with VPN or
  Snowflake down a manager saw an empty dropdown, concluded the customer
  did not exist, and could produce a wrongly scoped report.
* **E4** the submit fallback showed literal developer strings
  ("Server returned non-JSON content-type: text/html").
* **E5** a "Test JavaScript" debug button shipped directly beneath the
  primary call to action.
* **E6** two renewal validation branches never cleared ``aria-busy``.
* **E7/E8/E9** no skip link, no ``aria-current``, 14 ``<th>`` without
  ``scope``, and a 0.35-alpha focus ring below the WCAG 1.4.11 3:1 floor.
"""

from __future__ import annotations

import re

import pytest

from tests.source_shape_utils import assert_in_source, read_repo_file


# ---------------------------------------------------------------------------
# E1 -- the progress page is no longer silent to assistive technology
# ---------------------------------------------------------------------------


def _progress() -> str:
    return read_repo_file("templates/progress.html")


def test_round152_progress_status_card_is_a_live_region() -> None:
    body = _progress()
    match = re.search(r'<div class="info-card"[^>]*>\s*<h3>Current Status</h3>', body)
    assert match, "Current Status card not found"
    assert 'role="status"' in match.group(0)
    assert 'aria-live="polite"' in match.group(0)


def test_round152_progress_activity_box_is_a_live_region() -> None:
    body = _progress()
    match = re.search(r'<div class="status-box[^>]*id="status-box"[^>]*>', body, re.S)
    assert match, "status-box not found"
    assert 'aria-live="polite"' in match.group(0)


def test_round152_progress_bar_exposes_progressbar_semantics() -> None:
    body = _progress()
    match = re.search(r'<div class="progress-fill" id="progress"[^>]*>', body, re.S)
    assert match, "progress bar not found"
    tag = match.group(0)
    for attribute in ('role="progressbar"', 'aria-valuemin="0"', 'aria-valuemax="100"', "aria-valuenow", "aria-label"):
        assert attribute in tag, f"progress bar missing {attribute}"


def test_round152_progress_bar_value_tracks_the_painted_width() -> None:
    """A static ``aria-valuenow`` would be worse than none at all."""
    assert_in_source(_progress(), "bar.setAttribute('aria-valuenow', String(data.progress));")


def test_round152_progress_error_strip_is_an_alert() -> None:
    body = _progress()
    match = re.search(r'<div id="error"[^>]*>', body)
    assert match, "error strip not found"
    assert 'role="alert"' in match.group(0)


# ---------------------------------------------------------------------------
# E2 -- failures persist and are announced
# ---------------------------------------------------------------------------


def test_round152_analyze_error_toasts_do_not_auto_dismiss() -> None:
    body = read_repo_file("templates/analyze.html")
    assert_in_source(body, "const _r152IsFailure = (type === 'error' || type === 'warning');")
    assert_in_source(body, "if (!_r152IsFailure) {")


def test_round152_analyze_toast_carries_a_role() -> None:
    assert_in_source(
        read_repo_file("templates/analyze.html"),
        "notification.setAttribute('role', type === 'error' ? 'alert' : 'status');",
    )


def test_round152_leader_form_toasts_are_announced_and_persist() -> None:
    body = read_repo_file("templates/leader_report_form.html")
    assert_in_source(body, "notification.setAttribute('role', type === 'error' ? 'alert' : 'status');")
    assert_in_source(body, "if (type !== 'error' && type !== 'warning') {")


# ---------------------------------------------------------------------------
# E3 -- the customer lookup reports what happened
# ---------------------------------------------------------------------------


def test_round152_customer_typeahead_has_a_status_region() -> None:
    body = read_repo_file("templates/analyze.html")
    match = re.search(r'<div id="customer-name-status"[^>]*>', body)
    assert match, "customer-name-status region not found"
    assert 'role="status"' in match.group(0)
    assert 'aria-live="polite"' in match.group(0)


@pytest.mark.parametrize(
    "needle",
    [
        "Searching for matching customers...",
        "No matching customers found.",
        "Could not reach the customer directory.",
    ],
)
def test_round152_customer_typeahead_covers_all_three_states(needle: str) -> None:
    """In-flight, genuinely-empty and unreachable must be distinguishable."""
    assert_in_source(read_repo_file("templates/analyze.html"), needle)


def test_round152_typeahead_failure_is_no_longer_console_only() -> None:
    body = read_repo_file("templates/analyze.html")
    # The console warning stays for support; the user now gets text too.
    assert "console.warn('typeahead (customer):', err);" in body
    tail = body.split("console.warn('typeahead (customer):', err);", 1)[1][:400]
    assert "_r152SetCustomerStatus(" in tail, (
        "Round 152 / E3: a typeahead failure must set the visible status line, "
        "not only console.warn."
    )


# ---------------------------------------------------------------------------
# E4 -- plain language for non-technical managers
# ---------------------------------------------------------------------------


def test_round152_submit_fallback_shows_plain_language() -> None:
    body = read_repo_file("templates/analyze.html")
    assert_in_source(body, "AdoptIQ could not start the report.")
    assert "errorMessage += error.message;" not in body, (
        "Round 152 / E4: raw internal exception text is being shown to the user "
        "again; keep it in console.error and show plain language."
    )


def test_round152_technical_detail_still_reaches_the_console() -> None:
    """Support must not lose the diagnostic."""
    assert_in_source(read_repo_file("templates/analyze.html"), "console.error('Form submission error:', error);")


# ---------------------------------------------------------------------------
# E5 -- the debug button is gated
# ---------------------------------------------------------------------------


def test_round152_test_javascript_button_is_debug_gated() -> None:
    body = read_repo_file("templates/analyze.html")
    assert "Test JavaScript" in body, "the button may still exist for local debugging"
    index = body.index("<!-- Test Button for Debugging -->")
    preceding = body[max(0, index - 400):index]
    assert "{% if config.DEBUG or request.args.get('debug') %}" in preceding


def test_round152_debug_button_absent_from_default_render(client) -> None:
    """Behavioural: a normal page load must not ship the developer aid."""
    response = client.get("/")
    assert response.status_code == 200
    assert b"Test JavaScript" not in response.data


# ---------------------------------------------------------------------------
# E6 -- aria-busy is always cleared
# ---------------------------------------------------------------------------


def test_round152_every_submit_exit_clears_aria_busy() -> None:
    """Count exits against clears so a new early return cannot regress this."""
    body = read_repo_file("templates/analyze.html")
    disabled_resets = body.count("elements.submitBtn.disabled = false;")
    aria_clears = body.count("elements.submitBtn.removeAttribute('aria-busy');")
    assert aria_clears >= disabled_resets, (
        f"Round 152 / E6: {disabled_resets} submit-button resets but only "
        f"{aria_clears} aria-busy clears; a screen reader would announce the "
        "button as permanently busy after the branches that were missed."
    )


# ---------------------------------------------------------------------------
# E7 / E8 / E9 -- navigation, tables and focus
# ---------------------------------------------------------------------------


def test_round152_skip_link_exists_and_targets_main() -> None:
    body = read_repo_file("templates/base.html")
    assert_in_source(body, 'href="#main-content" class="visually-hidden-focusable adoptiq-skip-link"')
    assert_in_source(body, '<main id="main-content" tabindex="-1">')


def test_round152_skip_link_is_the_first_focusable_element() -> None:
    body = read_repo_file("templates/base.html")
    after_body = body.split("<body>", 1)[1]
    assert after_body.lstrip().startswith("{#") or "adoptiq-skip-link" in after_body[:800], (
        "Round 152 / E7: the skip link must come first in the body or it "
        "cannot do its job."
    )


def test_round152_active_nav_link_sets_aria_current() -> None:
    body = read_repo_file("templates/base.html")
    assert body.count('aria-current="page"') >= 5, (
        "Round 152 / E7: current page was signalled by a CSS class only."
    )


@pytest.mark.parametrize(
    "template", ["templates/history.html", "templates/external_intelligence.html"]
)
def test_round152_table_headers_declare_scope(template: str) -> None:
    body = read_repo_file(template)
    headers = re.findall(r"<th\b[^>]*>", body)
    assert headers, f"{template} has no <th> elements"
    missing = [tag for tag in headers if "scope=" not in tag]
    assert not missing, (
        f"Round 152 / E8: {len(missing)} <th> in {template} still lack scope=; "
        "screen readers cannot associate cells with headers."
    )


@pytest.mark.parametrize(
    "template", ["templates/history.html", "templates/external_intelligence.html"]
)
def test_round152_decorative_header_icons_are_hidden(template: str) -> None:
    body = read_repo_file(template)
    for header in re.findall(r"<th\b[^>]*>\s*<i class=\"[^\"]*\"[^>]*>", body):
        assert 'aria-hidden="true"' in header, (
            f"Round 152 / E8: decorative icon announced as content in {template}: {header}"
        )


def test_round152_focus_ring_meets_contrast_and_survives_forced_colors() -> None:
    body = read_repo_file("templates/base.html")
    assert "--accent-glow: rgba(0, 188, 235, 0.75);" in body, (
        "Round 152 / E9: the focus ring alpha was below the WCAG 1.4.11 3:1 floor."
    )
    assert "outline: 2px solid transparent;" in body, (
        "Round 152 / E9: box-shadow is dropped in forced-colors mode, so a "
        "transparent outline is required to keep a visible ring."
    )
    assert "outline: none;" not in body.split(".btn:focus", 1)[1][:400]


# ---------------------------------------------------------------------------
# A6 -- interrupted-job copy describes a control that exists
# ---------------------------------------------------------------------------


def test_round152_interrupted_job_copy_is_accurate() -> None:
    body = read_repo_file("templates/base.html")
    assert_in_source(body, "marked as interrupted the next time you open AdoptIQ")
