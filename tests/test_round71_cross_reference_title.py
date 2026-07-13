"""Round 71 / Phase 4 (#22) -- cross_reference_refs uses LIKELY_TITLE_COLS.

Pre-R71 ``cross_reference_refs`` hardcoded ``row.get("title")`` for the
AB rows and ``row.get("Title")`` for the CSOne rows.  When the actual
column name was ``SUBJECT`` (CSOne) or ``Action Plan Title`` (AB), the
matched-row title was silently None and the BST defect ↔ AB / CSOne
cross-reference report rendered the matched-row label as ``None``.

Round 71 / Phase 4 (#22) resolves both columns through the canonical
``LIKELY_TITLE_COLS`` resolver instead.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import adoptiq_backend


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_backend() -> str:
    return (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8", errors="replace")


def test_round71_cross_reference_resolves_ab_title_via_resolver() -> None:
    """The AB title resolution MUST use ``LIKELY_TITLE_COLS``."""
    src = _read_backend()
    assert "_r71_ab_title_col = next((c for c in LIKELY_TITLE_COLS if c in ab_df.columns), None)" in src, (
        "Round 71 / Phase 4 (#22): cross_reference_refs must resolve "
        "the AB title column through LIKELY_TITLE_COLS, not hardcoded "
        "row.get('title')."
    )


def test_round71_cross_reference_resolves_csone_title_via_resolver() -> None:
    """The CSOne title resolution MUST use ``LIKELY_TITLE_COLS``."""
    src = _read_backend()
    assert "_r71_csone_title_col = next((c for c in LIKELY_TITLE_COLS if c in csone_df.columns), None)" in src, (
        "Round 71 / Phase 4 (#22): cross_reference_refs must resolve "
        "the CSOne title column through LIKELY_TITLE_COLS, not hardcoded "
        "row.get('Title')."
    )


def test_round71_cross_reference_renders_subject_column_for_csone() -> None:
    """Live integration: a CSOne frame whose only title-bearing column
    is ``SUBJECT`` MUST surface the subject value in the matched-row
    ``title`` field (pre-R71 it would have been None).

    cross_reference_refs requires:
    - ``bemscsc_refs`` column on the input df with comma-separated CSC IDs.
    - ``ext_bugs`` entries each carry ``bug_id``.
    """
    ab_df = pd.DataFrame()
    csone_df = pd.DataFrame({
        "SUBJECT": ["Webex Meeting crash on join"],
        "bemscsc_refs": ["CSCWH12345"],
        "SR Number": ["SR-001"],
        "customer_name": ["Acme Corp"],
    })
    ext_bugs = [{"bug_id": "CSCWH12345", "headline": "Crash on join"}]
    matches, matched_df = adoptiq_backend.cross_reference_refs(ab_df, csone_df, ext_bugs)
    assert matched_df is not None and not matched_df.empty, (
        "cross_reference_refs must return a non-empty matched frame when "
        "the bemscsc_refs column carries a CSC ID matching ext_bugs."
    )
    titles = list(matched_df["title"])
    assert any("Webex Meeting" in str(t) for t in titles), (
        f"Round 71 / Phase 4 (#22): cross_reference_refs must surface "
        f"the SUBJECT column value as the matched-row title for CSOne; "
        f"got titles={titles}."
    )


def test_round71_cross_reference_falls_back_to_legacy_title_when_resolver_misses() -> None:
    """When LIKELY_TITLE_COLS resolves to 'Title' (an entry it
    contains), the resolver must still render that title."""
    ab_df = pd.DataFrame()
    csone_df = pd.DataFrame({
        "Title": ["Legacy fallback path"],
        "bemscsc_refs": ["CSCWH99999"],
        "SR Number": ["SR-002"],
        "customer_name": ["Beta Inc"],
    })
    ext_bugs = [{"bug_id": "CSCWH99999", "headline": "Legacy bug"}]
    matches, matched_df = adoptiq_backend.cross_reference_refs(ab_df, csone_df, ext_bugs)
    titles = list(matched_df["title"])
    assert any("Legacy" in str(t) for t in titles), (
        f"Round 71 / Phase 4 (#22): when LIKELY_TITLE_COLS resolves "
        f"to 'Title', the matched-row title must still render; got "
        f"titles={titles}."
    )
