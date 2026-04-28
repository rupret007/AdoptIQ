"""Round 30 / H2 — BEMS analyzer exception must render a visible
degradation banner before the leader falls through to the basic
``_add_bems_summary`` path.

Previously the exception was logged to stderr only, the same heading
was used for both the advanced and basic paths, and the reader could
not tell which path ran -- meaning a partial report could be
mistaken for a complete strategic analysis.

Round 30 / H2 catches the exception, classifies it via
``error_classifier`` (so the banner labels 'data validation' /
'transient network' / 'internal' rather than the raw exception
string), and renders a CRITICAL-red italic paragraph before
delegating to the basic path.
"""

from __future__ import annotations

import inspect

import leader_report_generator


def test_round30_h2_bems_excepts_classifies_and_renders_banner() -> None:
    """Source pin: the BEMS analyzer exception path MUST call
    ``classify_analysis_error`` (or equivalent error_classifier
    helper) before constructing the banner so the banner labels the
    failure category rather than dumping the raw exception class
    name."""
    src = inspect.getsource(leader_report_generator)
    # Pin the canonical banner phrase.
    assert "BEMS strategic analysis incomplete" in src, (
        "Round 30 / H2: leader must render the canonical "
        "'BEMS strategic analysis incomplete -- reverted to basic "
        "counts' banner so the reader can tell which path ran."
    )
    # Pin the error-classifier integration so the banner does not
    # leak raw exception details.
    assert "classify_analysis_error" in src or "error_classifier" in src, (
        "Round 30 / H2: BEMS degradation banner must classify the "
        "exception via error_classifier rather than dumping the raw "
        "exception class."
    )


def test_round30_h2_banner_renders_before_basic_summary_call() -> None:
    """Behavioural pin: the banner must be rendered BEFORE the call
    to ``self._add_bems_summary(team_data)`` so the reader sees the
    degradation notice at the top of the BEMS section, not buried
    inside the listing."""
    src = inspect.getsource(leader_report_generator)
    # Locate the canonical banner line + the basic-summary call and
    # assert the banner appears first in module order.
    banner_idx = src.find("BEMS strategic analysis incomplete")
    summary_idx = src.find("self._add_bems_summary(team_data)")
    assert banner_idx != -1, (
        "Round 30 / H2: banner phrase missing from source."
    )
    assert summary_idx != -1, (
        "Round 30 / H2: _add_bems_summary call missing."
    )
    assert banner_idx < summary_idx, (
        "Round 30 / H2: degradation banner must be rendered BEFORE "
        "the basic _add_bems_summary call so the reader sees the "
        "notice at the top of the section."
    )


def test_round30_h2_banner_uses_critical_color() -> None:
    """Source pin: the banner colour must signal degradation (red /
    CRITICAL hue), so a casual reader recognizes the section as
    incomplete rather than complete-with-numbers."""
    src = inspect.getsource(leader_report_generator)
    # The implementation uses CANONICAL_RISK_HIGH_RGB (or the same
    # canonical red) for the banner.
    assert (
        "CANONICAL_RISK_HIGH_RGB" in src
        or "RGBColor(0xC0, 0x39, 0x2B)" in src
    ), (
        "Round 30 / H2: BEMS degradation banner must use the canonical "
        "high-risk red color so the visual cue signals degradation."
    )
