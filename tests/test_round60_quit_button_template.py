"""Round 60 -- pin the Quit button into both UI templates.

Without these source-shape assertions, a future template refactor
could silently drop the ``#adoptiq-quit-btn`` element (e.g. someone
deletes the navbar `<li>` while removing an unrelated button) and
the ``/api/shutdown`` endpoint would still pass tests but the user
would have NO way to actually invoke it.

Two surfaces to pin:

1. ``templates/base.html`` -- main app navbar (loaded on every page
   the user visits in the main UI; the click handler lives in
   ``static/js/quit_adoptiq.js``).
2. ``ENHANCED_ADMIN_TEMPLATE_V2`` -- admin dashboard (the template
   is an inlined Python string constant, not a separate file, so we
   inspect the constant directly; the click handler is inlined in
   the same template's ``<script>`` block because the admin template
   does NOT share base.html with the main app).
"""

from __future__ import annotations

from pathlib import Path

import enhanced_admin_dashboard_v2 as admin_mod


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BASE_TEMPLATE_PATH = _PROJECT_ROOT / "templates" / "base.html"


def _read_base_html() -> str:
    return _BASE_TEMPLATE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Main app navbar (templates/base.html)
# ---------------------------------------------------------------------------


def test_main_navbar_has_quit_button():
    """The main app navbar must contain the Quit button so the user
    has a single in-browser control to stop the local Flask server."""
    html = _read_base_html()
    assert 'id="adoptiq-quit-btn"' in html, (
        "Round 60: templates/base.html navbar must contain "
        '#adoptiq-quit-btn so the user can shut AdoptIQ down without '
        "having to find the PID."
    )


def test_main_navbar_quit_button_loads_handler_script():
    """The page must load static/js/quit_adoptiq.js (the click
    handler) -- otherwise the button would render but do nothing."""
    html = _read_base_html()
    assert "js/quit_adoptiq.js" in html, (
        "Round 60: base.html must include the quit_adoptiq.js script "
        "tag so the button's click handler is wired."
    )


def test_main_navbar_quit_button_has_aria_label():
    """The button must have a non-empty aria-label so screen readers
    announce it as a destructive action and the user is not surprised
    by an unlabeled icon-only control."""
    html = _read_base_html()
    # Find the quit button's aria-label by simple substring proximity
    # check: the aria-label appears within the button's element.
    btn_idx = html.find('id="adoptiq-quit-btn"')
    assert btn_idx > 0
    # Look back ~400 chars to capture the opening tag.
    window = html[max(0, btn_idx - 400): btn_idx + 200]
    assert 'aria-label="Quit AdoptIQ' in window, (
        "Round 60: #adoptiq-quit-btn must carry an aria-label that "
        "starts with 'Quit AdoptIQ' so screen readers correctly "
        "announce the destructive action."
    )


def test_main_template_pre_renders_shutdown_overlay():
    """The post-shutdown overlay element must be PRE-RENDERED with
    ``hidden`` so the JS only has to flip the attribute -- no
    runtime innerHTML construction (CSP-clean, no string
    interpolation of user-influenced data)."""
    html = _read_base_html()
    assert 'id="adoptiq-shutdown-overlay"' in html, (
        "Round 60: base.html must pre-render the post-shutdown "
        "overlay element so the JS does not need to construct HTML "
        "at runtime (keeps the CSP story simple)."
    )
    # The overlay must be hidden by default so it's invisible until
    # the JS unhides it after a 202 ack.
    overlay_idx = html.find('id="adoptiq-shutdown-overlay"')
    overlay_window = html[max(0, overlay_idx - 200): overlay_idx + 200]
    assert "hidden" in overlay_window, (
        "Round 60: the overlay element must be ``hidden`` by default "
        "so it does not leak into the layout before Quit is clicked."
    )


def test_main_template_pre_renders_force_quit_modal():
    """The 409 needs_force confirmation modal must be pre-rendered
    too so the JS can populate + show it without building markup at
    runtime."""
    html = _read_base_html()
    assert 'id="adoptiq-quit-confirm-modal"' in html, (
        "Round 60: base.html must pre-render the confirm modal that "
        "lists running analyses when /api/shutdown returns 409 "
        "needs_force; building it via innerHTML at runtime would "
        "complicate the CSP story."
    )
    # Force-quit button must be present so the JS can wire its
    # click handler.
    assert 'id="adoptiq-quit-force-btn"' in html


def test_main_template_csrf_meta_tag_present():
    """The Quit button's JS reads the CSRF token from
    <meta name="csrf-token">; the meta tag must exist so the POST
    is accepted."""
    html = _read_base_html()
    assert 'name="csrf-token"' in html, (
        "Round 60: the JS handler reads the CSRF token from a "
        "<meta name=\"csrf-token\"> tag; without it /api/shutdown "
        "would 403 every browser-initiated Quit click."
    )


# ---------------------------------------------------------------------------
# Admin dashboard (ENHANCED_ADMIN_TEMPLATE_V2 string constant)
# ---------------------------------------------------------------------------


def test_admin_template_has_quit_button():
    """The admin dashboard's inlined template constant must contain
    the Quit button.  The admin template does NOT share base.html so
    we have to inject the button directly into the string."""
    template = admin_mod.ENHANCED_ADMIN_TEMPLATE_V2
    assert 'id="adoptiq-quit-btn"' in template, (
        "Round 60: ENHANCED_ADMIN_TEMPLATE_V2 must contain "
        "#adoptiq-quit-btn so the admin dashboard also exposes the "
        "Quit action (it cannot reuse base.html)."
    )


def test_admin_template_quit_button_targets_admin_proxy():
    """The admin Quit button's JS must POST to ``/admin_quit`` (the
    admin-side proxy that forwards to /api/shutdown with
    X-AdoptIQ-Internal) -- NOT directly to /api/shutdown, because
    the admin runs on a different port and does not hold the main
    app's CSRF token."""
    template = admin_mod.ENHANCED_ADMIN_TEMPLATE_V2
    assert "/admin_quit" in template, (
        "Round 60: the admin template's Quit JS must POST to "
        "/admin_quit (the proxy route) -- direct POSTs to "
        "/api/shutdown would fail CSRF because the admin holds a "
        "different token."
    )


def test_admin_template_uses_admin_csrf_header():
    """The admin Quit JS must send the admin CSRF token via
    ``X-AdoptIQ-Admin-CSRF`` -- the same header used by the existing
    admin AJAX flows (e.g. /api/debug/verbose toggle)."""
    template = admin_mod.ENHANCED_ADMIN_TEMPLATE_V2
    assert "X-AdoptIQ-Admin-CSRF" in template, (
        "Round 60: admin AJAX uses X-AdoptIQ-Admin-CSRF; the Quit "
        "handler must follow the same convention."
    )


def test_admin_template_pre_renders_shutdown_overlay():
    """Admin template must pre-render its own #adoptiq-shutdown-
    overlay element so the JS can swap to the post-shutdown view
    after a successful 202."""
    template = admin_mod.ENHANCED_ADMIN_TEMPLATE_V2
    assert 'id="adoptiq-shutdown-overlay"' in template, (
        "Round 60: admin template must pre-render the post-shutdown "
        "overlay so the operator gets unambiguous feedback that the "
        "process has stopped."
    )


def test_admin_template_quit_button_has_quit_btn_class():
    """The admin Quit button must use the ``.quit-btn`` CSS class so
    it inherits the destructive-action styling defined in the same
    template's <style> block."""
    template = admin_mod.ENHANCED_ADMIN_TEMPLATE_V2
    btn_idx = template.find('id="adoptiq-quit-btn"')
    assert btn_idx > 0
    window = template[max(0, btn_idx - 400): btn_idx + 200]
    assert 'class="quit-btn"' in window, (
        "Round 60: admin Quit button must use class=\"quit-btn\" so "
        "the red-tinted hover/focus styling applies."
    )
