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
from source_shape_utils import assert_in_source

from pathlib import Path

import enhanced_admin_dashboard_v2 as admin_mod


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BASE_TEMPLATE_PATH = _PROJECT_ROOT / "templates" / "base.html"
_QUIT_JS_PATH = _PROJECT_ROOT / "static" / "js" / "quit_adoptiq.js"


def _read_base_html() -> str:
    return _BASE_TEMPLATE_PATH.read_text(encoding="utf-8")


def _read_quit_js() -> str:
    return _QUIT_JS_PATH.read_text(encoding="utf-8")


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
    assert_in_source(html, 'id="adoptiq-quit-force-btn"', label='html')


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
# Round 116 / Build 85 (C): Admin Console link vs Quit separation
# ---------------------------------------------------------------------------


def test_r116_quit_button_has_visible_label():
    """Round 116: the Quit button must carry a visible 'Quit' text label
    (not an icon-only glyph) so it is never mistaken for a nav control.

    Build-83 acceptance: users clicking near the Admin Console link
    mis-clicked the bare power-glyph Quit button and reported 'Admin
    Console tries to shut down'."""
    html = _read_base_html()
    btn_idx = html.find('id="adoptiq-quit-btn"')
    assert btn_idx > 0
    window = html[btn_idx: btn_idx + 400]
    assert "quit-btn-label" in window and ">Quit<" in window, (
        "Round 116 / Build 85 (C): #adoptiq-quit-btn must render a visible "
        "'Quit' text label so it reads as a distinct destructive control."
    )


def test_r116_quit_button_has_separator_divider():
    """A vertical divider must physically separate Quit from the nav
    links so a near-miss click lands on empty space, not Quit."""
    html = _read_base_html()
    assert "adoptiq-quit-divider" in html, (
        "Round 116 / Build 85 (C): a visual divider must separate the "
        "destructive Quit control from the navigation links."
    )


def test_r116_admin_console_link_is_port_aware_not_hardcoded():
    """The Admin Console href must come from the ``admin_console_url``
    context value (resolved via _resolve_admin_port), NOT a hardcoded
    ``http://127.0.0.1:5152/`` literal in the anchor."""
    html = _read_base_html()
    assert "admin_console_url" in html, (
        "Round 116 / Build 85 (C): the Admin Console link must use the "
        "port-aware admin_console_url context value."
    )
    # The anchor itself must carry the data hook and must NOT have a
    # raw hardcoded :5152 href as its primary value.
    link_idx = html.find("data-admin-console-link")
    assert link_idx > 0, "Admin Console anchor must carry data-admin-console-link"
    window = html[max(0, link_idx - 200): link_idx + 100]
    assert "admin_console_url" in window


def test_r116_admin_console_link_is_never_shutdown_endpoint():
    """The Admin Console link must be a plain navigation anchor -- it
    must NEVER point at /api/shutdown or /admin_quit (the mis-click that
    triggered the 'Admin Console tries to shut down' report)."""
    html = _read_base_html()
    link_idx = html.find("data-admin-console-link")
    assert link_idx > 0
    # Inspect the whole anchor element.
    anchor_open = html.rfind("<a", 0, link_idx)
    anchor_close = html.find("</a>", link_idx)
    anchor = html[anchor_open: anchor_close]
    assert "/api/shutdown" not in anchor, (
        "Round 116: the Admin Console anchor must never reference "
        "/api/shutdown."
    )
    assert "/admin_quit" not in anchor, (
        "Round 116: the Admin Console anchor must never reference "
        "/admin_quit."
    )


def test_r116_context_processor_resolves_admin_port():
    """The inject_version context processor must resolve the admin port
    via enhanced_admin_dashboard_v2._resolve_admin_port so the link
    tracks the live port (mirrors the Round 80 _live_main_url pattern)."""
    app_src = (_PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "_resolve_admin_port" in app_src, (
        "Round 116: app_simple must resolve the admin port via "
        "_resolve_admin_port for the port-aware Admin Console link."
    )
    assert_in_source(app_src, "'admin_console_url'", label='app_src')


def test_r116_shutdown_js_binds_only_quit_button_not_nav_links():
    """The shutdown click handler must bind ONLY to #adoptiq-quit-btn --
    never to a .nav-link or the admin anchor.  This is the source-level
    guarantee that navigating the navbar can never trigger a shutdown."""
    js = _read_quit_js()
    assert_in_source(js, "adoptiq-quit-btn", label='js')
    # No selector that would grab nav links or the admin anchor.
    assert ".nav-link" not in js, (
        "Round 116: the Quit JS must not bind shutdown to .nav-link "
        "elements -- that would make navigation links destructive."
    )
    assert "data-admin-console-link" not in js, (
        "Round 116: the Quit JS must not bind shutdown to the Admin "
        "Console anchor."
    )


def test_r116_resolve_admin_port_returns_int():
    """The resolver used by the context processor must return a usable
    integer port (smoke-level behavioral check)."""
    port = admin_mod._resolve_admin_port()
    assert isinstance(port, int)
    assert 1 <= port <= 65535


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
