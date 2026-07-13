"""Round 15 / Phase 4 -- security adversarial regression tests.

Each test pins a specific Round-15 hardening so a future refactor
can't quietly regress the protection.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 4.1 -- log-injection (CR/LF) sanitization in structured_logging.
# ---------------------------------------------------------------------------


def test_phase_4_1_strip_log_control_chars_replaces_cr_lf():
    """Round 15 / Phase 4.1 -- ``_strip_log_control_chars`` must
    neutralize CR / LF / NUL / 0x7F so a malicious value cannot
    splice fake log lines onto a single record.
    """
    import structured_logging as sl

    s = "abc\r\nFAKE LOG LINE\x00trailer\x7fend"
    out = sl._strip_log_control_chars(s)
    # No raw control characters survive.
    assert "\r" not in out
    assert "\n" not in out
    assert "\x00" not in out
    assert "\x7f" not in out
    # Visible escapes are emitted instead.
    assert "\\x0d" in out
    assert "\\x0a" in out
    assert "\\x00" in out
    assert "\\x7f" in out
    # Tabs collapse to a single space (still single-line, still readable).
    s_tab = "abc\tdef"
    assert sl._strip_log_control_chars(s_tab) == "abc def"


def test_phase_4_1_strip_log_control_chars_preserves_printable():
    """Printable text must not be mangled by the sanitizer."""
    import structured_logging as sl

    plain = "Acme Corp / Region 5 :: 90 d window"
    assert sl._strip_log_control_chars(plain) == plain
    # Empty string passes through unchanged.
    assert sl._strip_log_control_chars("") == ""


def test_phase_4_1_strip_log_control_chars_handles_none_falsy():
    """``None`` and empty falsy values come through cleanly."""
    import structured_logging as sl

    assert sl._strip_log_control_chars(None) is None  # type: ignore[arg-type]
    assert sl._strip_log_control_chars("") == ""


def test_phase_4_1_redact_extra_kv_value_neutralizes_crlf():
    """Round 15 / Phase 4.1 regression guard.

    Even non-redacted primitives must have CR / LF stripped when they
    pass through ``_redact_extra_kv_value`` so the StructuredAdapter
    can never emit a multi-line prefix from a single field.
    """
    import structured_logging as sl

    rendered = sl._redact_extra_kv_value("step", "fetch\r\n[FAKE] login=admin")
    assert "\r" not in rendered
    assert "\n" not in rendered
    assert "\\x0d" in rendered
    assert "\\x0a" in rendered


def test_phase_4_1_structured_adapter_emits_single_line_for_crlf_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end: a CR/LF-bearing ``extra_kv`` value never produces
    a second physical record.
    """
    import structured_logging as sl

    base = logging.getLogger("test_phase_4_1_adapter_crlf")
    base.setLevel(logging.INFO)
    adapter = sl.StructuredAdapter(base, {"_kv": {"analysis_id": "Acme_30d"}})

    with caplog.at_level(logging.INFO, logger=base.name):
        adapter.info(
            "fetch failed",
            extra_kv={"step": "fetch\r\nINJECTED admin"},
        )

    matched = [r for r in caplog.records if r.name == base.name]
    assert len(matched) == 1, (
        f"Round 15 / Phase 4.1 regression: CR/LF in extra_kv value split "
        f"into multiple records: {[r.getMessage() for r in matched]}"
    )
    rendered = matched[0].getMessage()
    assert "\r" not in rendered
    assert "\n" not in rendered
    assert "INJECTED" in rendered  # still readable
    # Visible escape is what survives.
    assert "\\x0d" in rendered or "\\x0a" in rendered


# ---------------------------------------------------------------------------
# Marker presence -- mirrors the Round-14 marker tests so the wiring
# can't quietly disappear.
# ---------------------------------------------------------------------------


def test_phase_4_1_marker_in_structured_logging():
    """Round 15 / Phase 4.1 marker must be present in the source."""
    contents = _read("structured_logging.py")
    assert "Round 15 / Phase 4.1" in contents
    assert "_strip_log_control_chars" in contents
