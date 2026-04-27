"""Round 26 / Phase C — intel_status.js contract.

Static substring / regex assertions on static/js/intel_status.js so poll cadence,
CSRF headers, and client-side security patterns regress visibly without a JS runner.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

JS_PATH = Path(__file__).parent.parent / "static" / "js" / "intel_status.js"
JS_SRC = JS_PATH.read_text(encoding="utf-8")


def test_intel_status_js_file_exists() -> None:
    assert JS_PATH.is_file(), f"expected {JS_PATH}"
    assert len(JS_SRC) > 0
    assert JS_PATH.stat().st_size >= 200


def test_intel_status_js_uses_correct_status_endpoint() -> None:
    assert "'/api/intel/status'" in JS_SRC
    assert "'/api/intel/refresh'" in JS_SRC


@pytest.mark.parametrize(
    ("pattern", "label"),
    [
        (r"POLL_FAST_MS\s*=\s*5000", "POLL_FAST_MS"),
        (r"POLL_SLOW_MS\s*=\s*60000", "POLL_SLOW_MS"),
    ],
)
def test_intel_status_js_polls_at_5s_when_running_and_60s_when_idle(pattern: str, label: str) -> None:
    assert re.search(pattern, JS_SRC), f"expected {label} assignment in {JS_PATH}"


@pytest.mark.parametrize(
    "header_literal",
    ["'X-CSRFToken'", "'X-CSRF-Token'"],
)
def test_intel_status_js_sends_csrf_header_on_refresh(header_literal: str) -> None:
    assert header_literal in JS_SRC
    assert 'meta[name="csrf-token"]' in JS_SRC


def test_intel_status_js_credentials_same_origin() -> None:
    assert JS_SRC.count("credentials: 'same-origin'") >= 2


def test_intel_status_js_no_innerHTML_with_untrusted_data() -> None:
    assert ".innerHTML" not in JS_SRC


def test_intel_status_js_paints_badge_and_banner_in_lockstep() -> None:
    assert "paintBadge" in JS_SRC
    assert "paintBanner" in JS_SRC
    start = JS_SRC.find("function paint(payload)")
    assert start != -1, "expected paint(payload) dispatcher"
    end = JS_SRC.find("var pollHandle", start)
    assert end != -1, "expected paint block before pollHandle"
    dispatcher = JS_SRC[start:end]
    assert "paintBadge" in dispatcher
    assert "paintBanner" in dispatcher


def test_intel_status_js_visibility_change_triggers_immediate_repoll() -> None:
    assert "'visibilitychange'" in JS_SRC or '"visibilitychange"' in JS_SRC
    # Implementation uses addEventListener with string 'visibilitychange'
    assert "visibilitychange" in JS_SRC


def test_intel_status_js_refresh_button_debounced() -> None:
    assert re.search(r"REFRESH_DEBOUNCE_MS\s*=\s*\d+", JS_SRC)
    m = re.search(r"REFRESH_DEBOUNCE_MS\s*=\s*(\d+)", JS_SRC)
    assert m is not None
    assert int(m.group(1)) >= 1000
