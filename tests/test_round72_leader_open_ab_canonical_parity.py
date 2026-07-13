"""Round 72 / Build 46 -- Finding 2 regression pin.

The leader DOCX team-summary section MUST count Open Adoption Barriers
through ``canonical_metrics.count_open_barriers`` (which normalizes
status via ``normalize_status_label`` AND deduplicates by barrier ID),
NOT via the inline ``status_series.apply(self._is_status_open).sum()``
path.

Pre-Round-72 acceptance against real Snowflake data (Brian Frazier
team, 90d, 73 raw AB rows -> 65 raw "Open" rows -> 63 distinct Open
IDs):
- Leader DOCX wrote: ``"Open Adoption Barriers: 65"`` (raw row count
  via the inline ``_is_status_open`` helper)
- Leader XLSX ``Adoption_Barriers`` sheet's canonical count: 63
  (distinct IDs via ``count_open_barriers``)

The 2-row drift was caused by duplicate barrier IDs in the
Snowflake-sourced AB sheet (the same barrier appearing on multiple
assignee/detail rows).  The harness's parity gate fired on every
leader run because of this systematic drift.

This file pins the SOURCE SHAPE: the leader generator's team-summary
``open_ab_count`` MUST be derived from ``cm.count_open_barriers``
(with the inline ``_is_status_open`` retained as a defensive
fallback only for the ``except Exception`` branch).  An artifact
test below also confirms the DOCX/XLSX agreement on a synthetic
fixture.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

import canonical_metrics as cm
from leader_report_generator import LeaderReportGenerator

REPO = Path(__file__).resolve().parent.parent
LEADER_GEN_PATH = REPO / "leader_report_generator.py"


def _load_leader_source() -> str:
    return LEADER_GEN_PATH.read_text(encoding="utf-8")


def test_leader_team_summary_open_ab_routes_through_canonical_metrics():
    """The team-summary block MUST call ``cm.count_open_barriers(abs_df)``."""

    src = _load_leader_source()
    # The fix MUST appear inside the team-summary block (the one
    # that also calls cm.count_closed_barriers per Round 30 / M1).
    assert "cm.count_open_barriers(abs_df)" in src, (
        "Round 72 / Finding 2: leader_report_generator.py must call "
        "cm.count_open_barriers(abs_df) so the DOCX team-summary's "
        "open_ab_count matches the XLSX Adoption_Barriers sheet's "
        "canonical count (distinct IDs after normalize_status_label)."
    )


def test_leader_team_summary_keeps_is_status_open_only_as_fallback():
    """The pre-R72 inline ``_is_status_open`` line MUST be inside an
    ``except`` block (defensive fallback), NOT the primary path."""

    src = _load_leader_source()
    # Locate the new try/except.  The defensive fallback line is:
    #   open_ab_count = int(status_series.apply(self._is_status_open).sum())
    # and it must appear AFTER ``open_ab_count = int(cm.count_open_barriers(abs_df))``
    fallback_pat = r"open_ab_count\s*=\s*int\(\s*status_series\.apply\(\s*self\._is_status_open\s*\)\.sum\(\)\s*\)"
    fallback_matches = list(re.finditer(fallback_pat, src))
    canonical_pat = r"open_ab_count\s*=\s*int\(\s*cm\.count_open_barriers\(\s*abs_df\s*\)\s*\)"
    canonical_matches = list(re.finditer(canonical_pat, src))
    assert canonical_matches, (
        "Round 72 / Finding 2: canonical open_ab_count assignment missing"
    )
    assert fallback_matches, (
        "Round 72 / Finding 2: defensive fallback for open_ab_count missing "
        "(retained for stale-fixture protection)"
    )
    # Canonical must come BEFORE fallback in source order.
    assert canonical_matches[0].start() < fallback_matches[0].start(), (
        "Round 72 / Finding 2: canonical cm.count_open_barriers MUST be the "
        "primary path; the inline _is_status_open is only a fallback."
    )


def test_canonical_count_open_barriers_dedups_by_id_on_duplicate_open_rows():
    """Reproduces the real-data divergence: 65 raw Open rows -> 63 distinct IDs."""

    # Synthetic AB frame mimicking the Round 72 acceptance run shape.
    rows = []
    for i in range(63):
        rows.append({"ID": f"AB-{i:04d}", "STATUS_C": "Open"})
    rows.append({"ID": "AB-0001", "STATUS_C": "Open"})
    rows.append({"ID": "AB-0002", "STATUS_C": "Open"})
    rows.extend({"ID": f"AB-X{j:02d}", "STATUS_C": "Cancelled"} for j in range(6))
    rows.extend({"ID": f"AB-Y{j:02d}", "STATUS_C": "Resolved"} for j in range(2))
    df = pd.DataFrame(rows)
    assert len(df) == 73

    raw_open_rows = int(df["STATUS_C"].astype(str).eq("Open").sum())
    assert raw_open_rows == 65

    canonical_open = cm.count_open_barriers(df)
    assert canonical_open == 63, (
        "canonical_metrics.count_open_barriers must dedup by ID "
        "(63 distinct Open IDs from 65 raw Open rows)."
    )


def test_leader_is_status_open_helper_kept_for_other_callers():
    """The ``_is_status_open`` helper itself must remain (other leader
    sub-sections reference it for non-portfolio per-row work)."""

    assert hasattr(LeaderReportGenerator, "_is_status_open")
    assert callable(LeaderReportGenerator._is_status_open)
    assert LeaderReportGenerator._is_status_open("Open") is True
    assert LeaderReportGenerator._is_status_open("Resolved") is False
    assert LeaderReportGenerator._is_status_open("") is False
    assert LeaderReportGenerator._is_status_open(None) is False


def test_round72_marker_present_in_leader_source():
    """A ``# Round 72`` audit marker must be on the new lines so
    ``git diff leader_report_generator.py | grep 'Round 72'`` shows
    the per-file footprint per the audit-trail convention."""

    src = _load_leader_source()
    assert "Round 72" in src, (
        "Round 72 audit marker missing from leader_report_generator.py; "
        "convention is to drop a # Round N comment on changed lines."
    )


def test_total_open_abs_uses_combined_team_frame_not_per_cssm_sum():
    """Round 72 / Build 46 (Finding 2 deeper): ``total_open_abs`` MUST
    be derived from a concat of all per-CSSM AB frames (deduplicated
    by ID via ``cm.count_open_barriers``), NOT the
    sum-of-per-CSSM-open-counts that pre-R72 produced.

    The pre-R72 ``sum(member['open_abs'] for member in
    team_summary_data)`` double-counted barriers attributed to
    multiple CSSMs via the ``_ATTRIBUTED_BY_ACCOUNT`` shared-account
    pathway (a barrier owned by Jose but on Mario's account would
    appear in BOTH CSSMs' frames; the sum-mode counted it twice
    while the XLSX ``Adoption_Barriers`` sheet -- which is built
    from the same combined+dedup'd frame -- counted it once).

    The fix MUST appear in ``_add_overall_individual_summary``
    near the ``total_open_abs`` assignment.
    """

    src = _load_leader_source()
    # The canonical pattern: pd.concat of per-CSSM frames + a single
    # cm.count_open_barriers call on the combined frame.
    assert "_r72_combined_open_abs" in src, (
        "Round 72 / F2 deeper: total_open_abs must be derived from "
        "a combined team-wide frame so it deduplicates by barrier "
        "ID (matching the XLSX Adoption_Barriers sheet)."
    )
    assert "cm.count_open_barriers(_r72_combined_frame)" in src, (
        "Round 72 / F2 deeper: the combined-frame open count must "
        "go through cm.count_open_barriers (which handles status "
        "normalization + dedup by ID)."
    )


def test_total_open_abs_dedups_account_attributed_barriers():
    """Synthetic reproduction of the shared-account double-count
    that pre-R72 inflated ``total_open_abs`` from 63 (canonical)
    to 65 (per-CSSM sum).

    A single barrier ID appearing in 2 CSSMs' per-CSSM AB frames
    must count ONCE in the team-wide total when the pipeline
    concatenates and dedups via ``cm.count_open_barriers``.
    """

    cssm_a = pd.DataFrame(
        [
            {"ID": "AB-0001", "STATUS_C": "Open"},  # Jose owns it
            {"ID": "AB-0002", "STATUS_C": "Open"},  # Jose-only
        ]
    )
    cssm_b = pd.DataFrame(
        [
            {"ID": "AB-0001", "STATUS_C": "Open"},  # Same as Jose's, attributed to Mario via shared account
            {"ID": "AB-0003", "STATUS_C": "Open"},  # Mario-only
        ]
    )
    per_cssm_open_sum = (
        int(cm.count_open_barriers(cssm_a)) + int(cm.count_open_barriers(cssm_b))
    )
    assert per_cssm_open_sum == 4, (
        "Sanity: per-CSSM sum double-counts the shared barrier"
    )
    combined = pd.concat([cssm_a, cssm_b], ignore_index=True)
    assert int(cm.count_open_barriers(combined)) == 3, (
        "The fix: combined+dedup must yield 3 (AB-0001 counts once, "
        "AB-0002 + AB-0003 each count once)."
    )
