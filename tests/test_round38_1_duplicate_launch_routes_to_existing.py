"""Round 38.1 / Build13 - duplicate-launch UX fix.

Real-world symptom that motivated this round:

The user reported "ok when i tried to open the app it just bounced and
would not open."  Investigation of ``/Users/jestory/.adoptiq/adoptiq.68285.log``
showed the *first* launch was healthy and actually served a leader-report
request -- but a *second* double-click of the .app icon hit
``app_simple.py``'s "port in use" branch, which:

  1. Printed a message to stderr the user could not see (.app launched
     from Dock has stderr redirected to a log).
  2. Spawned an ``osascript`` ``display dialog`` *behind other windows*
     because the script had no ``activate`` directive.
  3. Silently called ``sys.exit(0)`` after the 15s ``osascript`` timeout
     when no one clicked the dialog.

From the user's perspective the Dock icon bounced for ~2 minutes (the
LaunchServices ``LSCheckedInTimeout`` window) then stopped, with no UI
ever appearing.

This file pins the two minimal fixes that address the root cause without
touching boot order or any Round 36 / Round 37 / Round 38 code paths:

* ``_probe_existing_adoptiq(port)`` returns ``True`` only when the
  listener on ``port`` actually responds with an AdoptIQ-marked HTML
  body, so we never mistake an unrelated app (e.g. ``Duo Desktop`` on
  127.0.0.1:53100) for our own.

* The packaged-Mac ``osascript`` dialog now prefixes
  ``tell application "System Events" to activate`` so when it *is*
  shown (for the rare non-AdoptIQ port-collision case) the dialog
  actually surfaces to the front.

Together with a browser-open short-circuit + ``sys.exit(0)`` in the
duplicate-AdoptIQ path, the second double-click now silently routes the
user to the existing tab instead of bouncing into a void.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

import importlib
import io
from unittest import mock


def _load_app_simple():
    """Lazy import so module import side-effects only run once per worker.

    ``app_simple`` registers a Flask app at import time; we only need
    the helper functions, not the runnable WSGI app, so we pull the
    module and grab the helpers directly.
    """
    return importlib.import_module('app_simple')


# ---------------------------------------------------------------------------
# _probe_existing_adoptiq
# ---------------------------------------------------------------------------


def test_probe_returns_true_when_body_contains_adoptiq_marker():
    """A live AdoptIQ instance returns HTML containing ``adoptiq`` in
    the title / body, so the probe must classify it as ours."""
    mod = _load_app_simple()
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = (
        b'<html><head><title>AdoptIQ - executive analytics</title></head>'
        b'<body>...</body></html>'
    )
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: None
    with mock.patch('urllib.request.urlopen', return_value=fake_resp) as mocked:
        assert mod._probe_existing_adoptiq(5151) is True
    args, kwargs = mocked.call_args
    assert args[0] == 'http://127.0.0.1:5151/'
    assert kwargs.get('timeout') == 2.0


def test_probe_returns_false_when_body_lacks_marker():
    """A different server on 5151 (e.g. Duo Desktop, Webex) returns
    HTML without the AdoptIQ marker.  We MUST NOT silently re-route the
    browser to it -- that would mask a real port collision."""
    mod = _load_app_simple()
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = b'<html><body>Some other app</body></html>'
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: None
    with mock.patch('urllib.request.urlopen', return_value=fake_resp):
        assert mod._probe_existing_adoptiq(5151) is False


def test_probe_returns_false_on_connection_refused():
    """Port not bound at all -> probe must return False (the caller
    will fall through to the existing port-in-use dialog flow)."""
    mod = _load_app_simple()
    with mock.patch(
        'urllib.request.urlopen',
        side_effect=ConnectionRefusedError('no listener'),
    ):
        assert mod._probe_existing_adoptiq(5151) is False


def test_probe_returns_false_on_timeout():
    """Slow / hung listener -> probe must return False quickly so boot
    is never blocked.  The 2.0s default timeout is bounded."""
    mod = _load_app_simple()
    import socket

    with mock.patch(
        'urllib.request.urlopen',
        side_effect=socket.timeout('probe timeout'),
    ):
        assert mod._probe_existing_adoptiq(5151) is False


def test_probe_rejects_invalid_port():
    """Defence in depth: garbage port values must not raise; just
    return False so the caller treats the listener as 'not us'."""
    mod = _load_app_simple()
    assert mod._probe_existing_adoptiq('not-an-int') is False
    assert mod._probe_existing_adoptiq(-1) is False
    assert mod._probe_existing_adoptiq(70000) is False


def test_probe_handles_unicode_decode_errors_in_body():
    """Some servers return non-UTF-8 bodies (binary protocols, gzip,
    etc.).  ``decode('utf-8', errors='ignore')`` must keep the probe
    from raising; the body just won't contain the marker."""
    mod = _load_app_simple()
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = b'\xff\xfe\x00\x00binary garbage'
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: None
    with mock.patch('urllib.request.urlopen', return_value=fake_resp):
        assert mod._probe_existing_adoptiq(5151) is False


# ---------------------------------------------------------------------------
# Source-shape pins for the duplicate-launch flow itself.  Asserting
# behaviour by execing the ``__main__`` block is brittle; instead we pin
# the source so a future refactor can't quietly remove either fix.
# ---------------------------------------------------------------------------


def _read_app_simple_source():
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.join(os.path.dirname(here), 'app_simple.py')
    with open(src_path, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_duplicate_launch_short_circuits_to_existing_adoptiq():
    """The ``if not available:`` block must call
    ``_probe_existing_adoptiq`` BEFORE printing the port-in-use message
    or showing any dialog, and on a True result must
    ``_open_browser_url`` then ``sys.exit(0)``.  Otherwise the user sees
    the original "bounce and stop" symptom on second double-click.

    Round 87 / Phase 3 expanded this block by adding the
    auto-quit-stale launcher path BEFORE the R38.1 short-circuit
    fallback, so the window has to be wider than the original 3000
    chars (the R87 branches push the original ``sys.exit(0)`` farther
    down).  The R87 block ALSO adds two of its own ``sys.exit(0)``
    calls on the force-quit-failed and post-force-quit-port-still-
    busy paths, so the contract is preserved on every code path that
    routes via ``webbrowser.open``."""
    src = _read_app_simple_source()
    block_anchor = src.find('available, other_pid, other_name = _check_port_available(PORT)')
    assert block_anchor != -1, (
        'duplicate-launch port check anchor not found; refactor changed '
        'the structure -- update this test'
    )
    # R87 widened the block; 6000 chars covers the whole expanded
    # ``if not available:`` block (R87 auto-quit branches +
    # original R38.1 short-circuit + Mac + Windows + tty fallbacks).
    block = src[block_anchor:block_anchor + 6000]
    assert_in_source(block, '_probe_existing_adoptiq(PORT)', label='block')
    assert_in_source(block, "_open_browser_url('http://localhost:%s/' % PORT)", label='block')
    assert_in_source(block, 'sys.exit(0)', label='block')


def test_osascript_dialog_activates_system_events_to_surface_to_front():
    """When the dialog IS shown (non-AdoptIQ holds the port), it must
    surface to the front -- otherwise the user can't see it and we
    repeat the original "bounce into the void" UX bug."""
    src = _read_app_simple_source()
    assert_in_source(src, "'tell application \"System Events\" to activate'", label='src')


def test_probe_helper_is_defined_in_module():
    """Smoke check that the helper is exported as a module attribute so
    PyInstaller bundles it (no lazy/conditional import path)."""
    mod = _load_app_simple()
    assert callable(getattr(mod, '_probe_existing_adoptiq', None))
