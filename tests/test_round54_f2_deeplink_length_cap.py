"""Round 54 / F2 -- pin the deep-link length cap on both the Python
``_r53_safe_onedrive_deep_link`` and the JS ``r53SafeDeepLink``
mirror.

Background
----------
Round 53 / Phase 53.4.1 added a scheme allow-list (`http://`,
`https://`, `odopen:`, `ms-onedrive:`) so an env-driven override of
``Config.ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK`` could not inject
``javascript:`` / ``data:`` / ``file:`` into the rendered anchor.

The Round 54 review found that the validator accepted a 100 KB URL as
long as the scheme matched.  No XSS impact (the scheme check still
holds), but it would bloat the status payload (which lands in the
analyze panel AND the rotating file log under
``~/.adoptiq/adoptiq.<pid>.log``) without any legitimate use case.

Round 54 / F2 caps the deep link at 2048 UTF-8 bytes server-side and
2048 UTF-16 code units client-side.  These tests pin every branch:

* Python: under cap accepted, at cap accepted, over cap rejected,
  multi-byte codepoints counted in bytes (not chars), scheme cap
  interaction.
* JS validator: source-text inspection that the cap constant exists
  and is applied in the validator.
"""

# Round 54

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import config as live_config
import app_simple as app_mod


# ---------------------------------------------------------------------------
# Python validator
# ---------------------------------------------------------------------------


def test_constant_pinned_at_two_kib():
    """Pin the cap value so a future tweak is a deliberate change.
    2048 bytes is comfortably above every legitimate OneDrive /
    SharePoint share URL we've seen (longest in the wild ~600 chars).
    """
    assert app_mod._R53_ONEDRIVE_DEEP_LINK_MAX_BYTES == 2048


def test_short_https_url_passes():
    short_url = "https://cisco.sharepoint.com/sites/AdoptIQ"
    with patch.object(
        live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK",
        short_url, create=True,
    ):
        out = app_mod._r53_safe_onedrive_deep_link()
    assert out == short_url


def test_url_at_exactly_cap_is_accepted():
    """Boundary: URLs at exactly the cap pass.  This pins the cap
    semantics as ``<=`` not ``<``."""
    # Build a URL whose UTF-8 byte length is exactly the cap.
    prefix = "https://x.com/"
    pad = "A" * (app_mod._R53_ONEDRIVE_DEEP_LINK_MAX_BYTES - len(prefix))
    boundary_url = prefix + pad
    assert len(boundary_url.encode("utf-8")) == app_mod._R53_ONEDRIVE_DEEP_LINK_MAX_BYTES
    with patch.object(
        live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK",
        boundary_url, create=True,
    ):
        out = app_mod._r53_safe_onedrive_deep_link()
    assert out == boundary_url


def test_url_one_byte_over_cap_is_rejected():
    """One byte over the cap collapses to ``None`` so the panel falls
    back to text-only remediation."""
    prefix = "https://x.com/"
    pad = "A" * (app_mod._R53_ONEDRIVE_DEEP_LINK_MAX_BYTES - len(prefix) + 1)
    over_url = prefix + pad
    assert len(over_url.encode("utf-8")) == app_mod._R53_ONEDRIVE_DEEP_LINK_MAX_BYTES + 1
    with patch.object(
        live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK",
        over_url, create=True,
    ):
        out = app_mod._r53_safe_onedrive_deep_link()
    assert out is None


def test_url_far_over_cap_is_rejected_without_log_bloat():
    """100 KB URL -- the previously-tolerated bloat case.  Reject."""
    huge_url = "https://x.com/" + ("B" * 100_000)
    with patch.object(
        live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK",
        huge_url, create=True,
    ):
        out = app_mod._r53_safe_onedrive_deep_link()
    assert out is None


def test_multibyte_codepoints_counted_as_utf8_bytes():
    """A hostile URL composed of multi-byte codepoints (e.g. Chinese
    characters at 3 bytes each) must NOT bypass the cap by virtue of
    its lower character count.  The cap is on UTF-8 byte length, not
    character count, so 700 4-byte codepoints (2800 bytes) trips
    even though the string is only 700 chars long."""
    multibyte_url = "https://x.com/" + ("\U0001F600" * 700)  # smiley = 4 bytes
    assert len(multibyte_url) < app_mod._R53_ONEDRIVE_DEEP_LINK_MAX_BYTES, (
        "char count must be under cap to prove the byte cap is the gate"
    )
    assert len(multibyte_url.encode("utf-8")) > app_mod._R53_ONEDRIVE_DEEP_LINK_MAX_BYTES
    with patch.object(
        live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK",
        multibyte_url, create=True,
    ):
        out = app_mod._r53_safe_onedrive_deep_link()
    assert out is None


def test_cap_applies_before_scheme_check_for_hostile_long_javascript():
    """A multi-megabyte hostile URL with an obviously bad scheme MUST
    still reject -- the cap is defense-in-depth, not an alternative
    to the scheme check.  Both gates must hold independently."""
    hostile_long = "javascript:" + ("alert(1)//" * 10_000)
    with patch.object(
        live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK",
        hostile_long, create=True,
    ):
        out = app_mod._r53_safe_onedrive_deep_link()
    assert out is None


def test_short_hostile_scheme_still_rejected():
    """The pre-Round-54 scheme check must continue to hold for short
    hostile URLs -- no F2 regression that drops the scheme gate."""
    for hostile in (
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
    ):
        with patch.object(
            live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK",
            hostile, create=True,
        ):
            out = app_mod._r53_safe_onedrive_deep_link()
        assert out is None, f"hostile scheme {hostile!r} must reject"


def test_odopen_and_ms_onedrive_under_cap_accepted():
    """Pin the OS-level deep-link schemes still work after the cap
    addition."""
    for url in (
        "odopen://launch?siteId=foo",
        "ms-onedrive:?cmd=open&path=/AI%20Projects/AdoptIQ_CSOne_Reports",
    ):
        with patch.object(
            live_config.Config, "ADOPTIQ_CORPUS_ONEDRIVE_DEEP_LINK",
            url, create=True,
        ):
            out = app_mod._r53_safe_onedrive_deep_link()
        assert out == url


# ---------------------------------------------------------------------------
# JS mirror -- source-text inspection.  Mirrors the pattern other
# Round 53 JS tests use.
# ---------------------------------------------------------------------------


_JS_PATH = Path("static/js/intel_status.js")


def _read_js() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def test_js_validator_pins_max_len_constant():
    src = _read_js()
    assert "R53_DEEP_LINK_MAX_LEN" in src, (
        "JS validator must declare a length cap constant mirroring the "
        "server-side _R53_ONEDRIVE_DEEP_LINK_MAX_BYTES"
    )
    assert "2048" in src, (
        "JS cap must match the server cap value (2048) -- otherwise "
        "client and server disagree on the rejection boundary"
    )


def test_js_validator_applies_max_len_check():
    src = _read_js()
    # The validator function body must include both the cap constant
    # AND a length check that uses it.
    assert "trimmed.length > R53_DEEP_LINK_MAX_LEN" in src, (
        "r53SafeDeepLink must reject when trimmed.length exceeds the cap"
    )


def test_js_validator_returns_null_for_oversized():
    """The cap-trip path must return ``null`` so the renderer hides
    the anchor (paintDeepLink keys off ``safe == null``)."""
    src = _read_js()
    # Find the validator function and confirm the length check
    # short-circuits with ``return null``.
    idx = src.find("function r53SafeDeepLink")
    assert idx != -1
    body = src[idx:idx + 800]
    assert "R53_DEEP_LINK_MAX_LEN" in body
    assert "return null" in body
