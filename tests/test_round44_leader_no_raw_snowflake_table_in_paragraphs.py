"""Round 44 / Phase 4 regression test.

Pin the leader Word source-attribution paragraphs (the per-CSSM
Engagement Insights bullets emitted from
``_add_enhanced_insights_summary``) so they NEVER render the raw
Snowflake ``DATABASE.SCHEMA.TABLE`` identifier.  Round 42 / Phase 5
friendlied the *Comprehensive Data Source Summary* table headers but
missed two paragraph sites:

  - ``leader_report_generator.py:7008`` (account "Source: ..." line)
  - ``leader_report_generator.py:7018`` (contract "Source: ..." line)
  - ``leader_report_generator.py:7041`` (per-engagement "  - X: N
    records" bullet)

The 2026-04-28 audited Build-20 leader artifact rendered raw
``EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C`` 34 times -- once
per CSSM engagement bullet.

Round 44 / Phase 4 introduces a ``_friendly_source_label`` helper that
maps raw Snowflake table IDs to director-facing business labels and
applies it at all three sites.
"""

from __future__ import annotations

from pathlib import Path

import leader_report_generator as lrg


REPO_ROOT = Path(__file__).resolve().parent.parent
LRG_PATH = REPO_ROOT / "leader_report_generator.py"


def test_friendly_source_label_helper_exists() -> None:
    """The Round 44 / Phase 4 helper MUST be exposed at module level."""

    assert hasattr(lrg, "_friendly_source_label"), (
        "leader_report_generator must expose _friendly_source_label per "
        "Round 44 / Phase 4."
    )


def test_friendly_source_label_maps_canonical_tables() -> None:
    """The helper MUST map the two raw IDs that appeared in the audited
    Build-20 leader Word artifact to business-friendly labels."""

    assert (
        lrg._friendly_source_label(
            "EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C"
        )
        == "CSConsole Customer Pulse"
    )
    # Both the canonical view name and the underlying table name should
    # map to the same friendly label so the per-CSSM bullets agree.
    assert (
        lrg._friendly_source_label("EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW")
        == "CSConsole Adoption Barriers / Action Plans"
    )
    assert (
        lrg._friendly_source_label("EDW_SALES_ETL_DB.SS.ESA_C360_CS_TASK__C")
        == "CSConsole Adoption Barriers / Action Plans"
    )


def test_friendly_source_label_passes_through_unknown_keys() -> None:
    """Unknown table IDs MUST surface raw rather than be silently dropped
    so a future feed name still appears (visibly raw) in the report."""

    assert (
        lrg._friendly_source_label("EDW_SOMETHING_NEW.SS.NEW_FEED__C")
        == "EDW_SOMETHING_NEW.SS.NEW_FEED__C"
    )


def test_friendly_source_label_handles_none_and_empty() -> None:
    """Round-trip None / empty inputs to the empty string so the bullet
    line still renders without a raw ``None`` / ``""`` artifact."""

    assert lrg._friendly_source_label(None) == ""
    assert lrg._friendly_source_label("") == ""
    assert lrg._friendly_source_label("   ") == ""


def test_engagement_paragraph_sites_use_helper() -> None:
    """All three source-attribution paragraph sites in
    ``_add_enhanced_insights_summary`` MUST route through
    ``_friendly_source_label(...)`` -- NOT the pre-fix raw
    ``f\"...{source['table']}...\"`` pattern."""

    src = LRG_PATH.read_text(encoding="utf-8")
    # Account Summary "Source: ..." line
    assert (
        "_friendly_source_label(source['table'])" in src
    ), (
        "Round 44 / Phase 4: per-CSSM engagement bullets must friendly-"
        "wrap source['table'] before rendering."
    )
    # Defense in depth: the unfriended pattern must not survive in the
    # three audited sites.  Search for the specific pre-fix substrings
    # that were live in Build-20.
    assert (
        "Source: {source['table']} ({source['records_found']} records)"
        not in src
    ), (
        "Round 44 / Phase 4: pre-fix raw 'Source: {source[table]}...' "
        "string must not appear -- it should be wrapped in "
        "_friendly_source_label."
    )
    assert (
        "    - {source['table']}: {source['records_found']} records"
        not in src
    ), (
        "Round 44 / Phase 4: pre-fix raw '    - {source[table]}: ...' "
        "string must not appear -- it should be wrapped in "
        "_friendly_source_label."
    )
