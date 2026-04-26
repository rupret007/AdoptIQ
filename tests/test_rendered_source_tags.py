"""Phase 5 / source-attribution regression test.

Every Snowflake / CSOne / CSConsole metric line emitted by a report
must carry an inline ``[Source: ...]`` tag so a reader can verify the
number against the originating system.  This test pins the contract
on the shared formatter helpers; if any helper stops emitting the
``[Source:`` prefix, the docx assemblers that depend on it will
silently start producing un-attributed numbers.
"""
from __future__ import annotations

import report_utils as ru


def test_format_inline_source_emits_canonical_source_prefix() -> None:
    out = ru.format_inline_source(
        "Adoption Barriers",
        fields=["BU_NAME", "SUBJECT_C"],
        record_id="AB001",
    )
    assert out.startswith("[Source:"), (
        "Inline source citation must start with '[Source:' so docx "
        "rendering and downstream parsers can detect attribution."
    )
    assert "Adoption Barriers" in out or "C360" in out, (
        "Inline source must mention either the metric label or the "
        "canonical Snowflake/CSConsole table backing it."
    )
    assert "AB001" in out, (
        "Record ID must be propagated so the reader can look up the "
        "exact row in the source system."
    )


def test_format_metric_with_source_includes_value_and_source_tag() -> None:
    line = ru.format_metric_with_source(
        label="Open TAC Cases",
        value=42,
        metric_name="Support Cases (TAC)",
    )
    assert "Open TAC Cases" in line
    assert "42" in line
    assert "[Source:" in line, (
        "Every metric statement rendered through this helper must "
        "carry an inline [Source: ...] tag.  Removing the tag "
        "removes auditability."
    )
    assert "CSOne" in line or "TAC" in line


def test_canonical_source_lookup_covers_all_intelligence_streams() -> None:
    """The canonical sources table is what every formatter consults
    to render attribution.  If a known intelligence stream is missing
    from this list the docx will render a metric without a source.
    """
    expected_present = {
        "Adoption Barriers",
        "Support Cases (TAC)",
        "BEMS Escalations",
        "Service Incidents",
        "Software Defects",
        "Customer Pulse",
        "Action Plans",
        "Success Priorities",
    }
    actual = {row[0] for row in ru.DATA_SOURCES_CANONICAL}
    missing = expected_present - actual
    assert not missing, (
        f"Canonical source table is missing entries for {sorted(missing)}. "
        "Without them the corresponding metric lines will render with no "
        "[Source: ...] tag and will silently look authored-by-AdoptIQ."
    )
