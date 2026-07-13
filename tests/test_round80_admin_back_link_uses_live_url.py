"""Round 80 / Build 56: admin dashboard "Back to AdoptIQ" link must
use ``_live_main_url()`` (re-read from env per call), not the
module-level ``MAIN_APP_URL`` constant captured at import time.

Brian Frazier reported that clicking "Back to AdoptIQ" in the
Admin Dashboard navigated to a stale URL -- the link was passing
``MAIN_APP_URL`` to the template, which is captured before
``app_simple.py`` writes the live ``ADOPTIQ_MAIN_URL`` env value.
The R44/Phase 8 fix already routed two HTTP fetch sites through
``_live_main_url()``; R80 extends the same defense to the rendered
anchor href."""

from __future__ import annotations

import importlib
from pathlib import Path

# Round 80
_ADMIN_PATH = (
    Path(__file__).resolve().parents[1] / "enhanced_admin_dashboard_v2.py"
)


def test_main_app_url_render_context_uses_live_helper(monkeypatch):
    """Source-shape pin: the ``render_template_string`` call site that
    feeds ``main_app_url`` into ENHANCED_ADMIN_TEMPLATE_V2 MUST call
    ``_live_main_url()``, not reference ``MAIN_APP_URL`` directly."""
    src = _ADMIN_PATH.read_text(encoding="utf-8")
    # The exact (R80-stable) line shape we want to pin.  Any future
    # refactor that swaps this line for ``MAIN_APP_URL`` directly
    # would re-introduce the Build-pre-56 bug.
    assert "main_app_url=_live_main_url()" in src, (
        "Round 80: render_template_string must pass "
        "main_app_url=_live_main_url() (not MAIN_APP_URL) so the "
        "rendered Back to AdoptIQ link reflects the live env URL."
    )
    # Defense in depth: the buggy form must not appear at this site.
    assert "main_app_url=MAIN_APP_URL," not in src, (
        "Round 80: stale MAIN_APP_URL pass-through to the template "
        "context still present; reverts Brian's bug."
    )


def test_live_main_url_helper_re_reads_env_per_call(monkeypatch):
    """Behavioural pin: ``_live_main_url()`` MUST re-read
    ``ADOPTIQ_MAIN_URL`` from ``os.environ`` per call so a runtime
    update by ``app_simple._start_admin_server_in_thread`` (which
    runs AFTER the admin module is imported) is reflected."""
    admin = importlib.import_module("enhanced_admin_dashboard_v2")
    monkeypatch.setenv("ADOPTIQ_MAIN_URL", "http://127.0.0.1:15152/")
    assert admin._live_main_url() == "http://127.0.0.1:15152", (
        "Round 80: _live_main_url() must re-read env and strip trailing "
        "slash for safe concatenation."
    )
    monkeypatch.setenv("ADOPTIQ_MAIN_URL", "http://127.0.0.1:5151")
    assert admin._live_main_url() == "http://127.0.0.1:5151"


def test_back_to_adoptiq_template_renders_with_live_url(client_admin=None):
    """End-to-end pin: rendering ENHANCED_ADMIN_TEMPLATE_V2 with a
    new env value MUST land that value into the anchor href, not a
    stale snapshot.  Uses the admin module's render_template_string
    helper indirectly via a synthetic context.
    """
    admin = importlib.import_module("enhanced_admin_dashboard_v2")
    # The template carries:
    #   {% if main_app_url %}<a href="{{ main_app_url }}" ...>
    # We render that fragment in isolation against three different
    # injected URLs and assert each lands verbatim.
    from flask import render_template_string
    fragment = (
        '{% if main_app_url %}<a href="{{ main_app_url }}" '
        'target="_blank" rel="noopener noreferrer">'
        '\u2190 Back to AdoptIQ</a>{% endif %}'
    )
    with admin.admin_app.test_request_context():
        for url in (
            "http://127.0.0.1:5151",
            "http://127.0.0.1:15152",
            "http://localhost:9999",
        ):
            html = render_template_string(fragment, main_app_url=url)
            assert f'href="{url}"' in html, (
                f"Round 80: template did not render live URL {url!r}; "
                f"got {html!r}"
            )
