"""Round 30 / M2 — external-intelligence truncation flags must reach
user-visible disclosures in the leader, executive, and compact
reports.

``incident_storage`` already returned a ``list_truncated`` /
``truncated`` dict on its read paths but no renderer was reading the
flag, so a capped sample of help.webex bugs (or status.webex
incidents) looked identical to "no more rows to show".  Round 30 /
M2 threads the flags through ``_add_external_intelligence_section``
in the leader, the Known Defects / Service Incidents sections in
the executive, and a single advisory banner in the compact report.
"""

from __future__ import annotations

import inspect

import compact_report_formatter
import executive_intelligence_formatter
import leader_report_generator


def test_round30_m2_leader_signature_accepts_intel_truncated() -> None:
    """Source pin: ``_add_external_intelligence_section`` must accept
    ``intel_truncated`` and ``intel_fetch_limit`` so the wrapper can
    forward the storage flags."""
    src = inspect.getsource(leader_report_generator)
    assert "intel_truncated" in src, (
        "Round 30 / M2: leader must accept intel_truncated kwarg."
    )
    assert "intel_fetch_limit" in src, (
        "Round 30 / M2: leader must accept intel_fetch_limit kwarg."
    )
    # Pin the canonical disclosure phrase.
    assert "table truncated; fetch limit reached" in src, (
        "Round 30 / M2: leader must render the canonical "
        "'table truncated; fetch limit reached' disclosure phrase "
        "for affected sub-sections."
    )


def test_round30_m2_executive_renders_truncation_disclosure() -> None:
    """Source pin: executive Known Defects / Service Incidents
    sections must render the canonical truncation phrase."""
    src = inspect.getsource(executive_intelligence_formatter)
    assert "Table truncated; fetch limit reached" in src, (
        "Round 30 / M2: executive must render the canonical "
        "'Table truncated; fetch limit reached' disclosure."
    )
    # The executive must also accept the flags as kwargs.
    assert "intel_truncated" in src, (
        "Round 30 / M2: executive must accept intel_truncated kwarg."
    )
    assert "intel_fetch_limit" in src, (
        "Round 30 / M2: executive must accept intel_fetch_limit kwarg."
    )


def test_round30_m2_compact_renders_external_intel_capped_banner() -> None:
    """Source pin: compact must render a single 'External Intelligence
    Capped' advisory banner (since compact does not list individual
    rows the way leader / executive do)."""
    src = inspect.getsource(compact_report_formatter)
    assert "External Intelligence Capped" in src, (
        "Round 30 / M2: compact must render a single 'External "
        "Intelligence Capped' advisory banner when any sub-feed was "
        "truncated."
    )
    assert "intel_truncated" in src, (
        "Round 30 / M2: compact must accept intel_truncated kwarg."
    )


def test_round30_m2_truncation_disclosure_lists_subfeeds() -> None:
    """The compact advisory must enumerate which sub-feeds (incidents
    / bugs / maintenances) were truncated so the reader knows which
    counts are sampled."""
    src = inspect.getsource(compact_report_formatter)
    # The implementation iterates over a fixed tuple of sub-feeds and
    # renders the affected names.  Pin the iteration shape.
    for feed in ("incidents", "bugs"):
        assert feed in src, (
            f"Round 30 / M2: compact advisory must enumerate the "
            f"{feed!r} sub-feed when checking intel_truncated."
        )
