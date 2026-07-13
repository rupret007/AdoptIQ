"""Round 86 / Build 62 (P1/F3): internal AdoptIQ-generated filenames
must NOT leak into customer-facing Historical Context narratives.

Pre-R86: a corpus rebuilt from prior AdoptIQ daily reports surfaced
``[src: AdoptIQ_Report_<scope>_<ts>.docx]`` and
``[src: AdoptIQ_Data_<scope>_<ts>.xlsx]`` tags after each
resolution / case bullet -- the user saw their own internal report
filenames quoted in their new report. The section header already
attributes the data to the AdoptIQ corpus, so the filename is noise.

Post-R86: ``_is_internal_adoptiq_filename`` matches the pattern,
``_safe_source_label`` returns ``None`` for those filenames, and BOTH
render paths (``render_to_text`` + ``render_to_word``) drop the
``[src: ...]`` tag entirely. The "Source files:" footer also
suppresses these names because they're filtered at construction time
in ``build_historical_context``.

External / non-AdoptIQ filenames (e.g. ``CSOne_export_2026-04-15.csv``,
``user_uploaded_data.xlsx``) continue to render normally so the
operator-meaningful citations are preserved.
"""

from __future__ import annotations

import report_corpus_context as rcc


# Round 86 / Build 62 (P1/F3)
def test_internal_adoptiq_filename_matcher_positives():
    """Standard AdoptIQ output filenames must match."""
    positives = [
        "AdoptIQ_Report_Brian_Frazier_All_Contact_Center_90d_1777988718.docx",
        "AdoptIQ_Data_Compact_All_Managers_All_Contact_Center_90d_1777988758.xlsx",
        "AdoptIQ_Portfolio_All_Managers_2026_05.xlsx",
        "AdoptIQ_Report_Renewal_Portfolio.docx",
        "ADOPTIQ_DATA_LEADER.XLSX",  # case-insensitive
        " AdoptIQ_Report_x.docx ",  # whitespace-tolerant
        "AdoptIQ-Report-x.docx",  # hyphen-separated also matches
    ]
    for fname in positives:
        assert rcc._is_internal_adoptiq_filename(fname), (
            f"Expected {fname!r} to match internal AdoptIQ pattern"
        )


# Round 86 / Build 62 (P1/F3)
def test_internal_adoptiq_filename_matcher_negatives():
    """External / operator-meaningful filenames must NOT match."""
    negatives = [
        "CSOne_export_2026-04-15.csv",
        "customer_pulse_2026.xlsx",
        "user_uploaded_data.xlsx",
        "support_cases_q1_2026.xlsx",
        "AdoptIQ_secrets.env",  # not a report extension
        "AdoptIQ.docx",  # bare AdoptIQ.docx -- no underscore/hyphen segment
        "",
        None,
        "  ",
        "PriorReport_AdoptIQ_data.xlsx",  # 'AdoptIQ' substring but not at start
    ]
    for fname in negatives:
        assert not rcc._is_internal_adoptiq_filename(fname), (
            f"Expected {fname!r} to NOT match internal AdoptIQ pattern"
        )


# Round 86 / Build 62 (P1/F3)
def test_safe_source_label_drops_internal_filenames():
    """``_safe_source_label`` returns None for AdoptIQ-internal names."""
    assert (
        rcc._safe_source_label(
            "AdoptIQ_Report_Brian_Frazier_All_Contact_Center_90d_1.docx"
        )
        is None
    )
    assert (
        rcc._safe_source_label(
            "AdoptIQ_Data_Compact_All_Managers_All_Contact_Center_90d_1.xlsx"
        )
        is None
    )


# Round 86 / Build 62 (P1/F3)
def test_safe_source_label_preserves_external_filenames():
    """External corpus sources still render their citation."""
    assert (
        rcc._safe_source_label("CSOne_export_2026-04-15.csv")
        == "CSOne_export_2026-04-15.csv"
    )
    assert (
        rcc._safe_source_label("customer_pulse_q1.xlsx")
        == "customer_pulse_q1.xlsx"
    )


# Round 86 / Build 62 (P1/F3)
def test_safe_source_label_handles_none_and_empty():
    """Defensive: empty / None inputs return None."""
    assert rcc._safe_source_label(None) is None
    assert rcc._safe_source_label("") is None


# Round 86 / Build 62 (P1/F3)
def test_render_to_text_suppresses_adoptiq_src_tag():
    """The text-render path drops [src: AdoptIQ_*.xlsx] tags."""
    entry = rcc.HistoricalEntry(
        customer_name="Acme Corp",
        technology="Contact Center",
        first_seen="2026-01-01",
        last_seen="2026-04-01",
        occurrences=3,
        resolutions=(
            rcc.HistoricalResolution(
                method_text="Increased CSM cadence to weekly",
                source_filename="AdoptIQ_Report_x_y_90d_1.docx",
            ),
            rcc.HistoricalResolution(
                method_text="Premium support upgrade",
                source_filename="external_partner_runbook.pdf",
            ),
        ),
    )
    context = rcc.HistoricalContext(
        available=True,
        banner="",
        entries=(entry,),
        source_files=("external_partner_runbook.pdf",),
    )
    rendered = rcc.render_to_text(context)
    # AdoptIQ filename must be suppressed.
    assert "AdoptIQ_Report" not in rendered
    assert "[src: AdoptIQ_" not in rendered
    # External citation must still be present.
    assert "external_partner_runbook.pdf" in rendered


# Round 86 / Build 62 (P1/F3)
def test_render_to_text_preserves_resolution_text_when_src_dropped():
    """When the [src: ...] tag is suppressed, the resolution text still
    renders (we only drop the tag, not the bullet)."""
    entry = rcc.HistoricalEntry(
        customer_name="Acme Corp",
        technology=None,
        first_seen=None,
        last_seen=None,
        occurrences=1,
        resolutions=(
            rcc.HistoricalResolution(
                method_text="Increased CSM cadence to weekly",
                source_filename="AdoptIQ_Report_x_y_90d_1.docx",
            ),
        ),
    )
    context = rcc.HistoricalContext(
        available=True,
        banner="",
        entries=(entry,),
    )
    rendered = rcc.render_to_text(context)
    assert "Increased CSM cadence to weekly" in rendered


# Round 86 / Build 62 (P1/F3)
def test_render_to_word_suppresses_adoptiq_src_tag():
    """The Word render path drops [src: AdoptIQ_*.xlsx] tags."""

    class _FakeDoc:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def add_heading(self, text: str, level: int = 1) -> None:
            self.calls.append(f"H{level}:{text}")

        def add_paragraph(self, text: str) -> None:
            self.calls.append(f"P:{text}")

    entry = rcc.HistoricalEntry(
        customer_name="Acme Corp",
        technology=None,
        first_seen=None,
        last_seen=None,
        occurrences=1,
        resolutions=(
            rcc.HistoricalResolution(
                method_text="Increased CSM cadence to weekly",
                source_filename="AdoptIQ_Report_x_y_90d_1.docx",
            ),
            rcc.HistoricalResolution(
                method_text="Premium support upgrade",
                source_filename="external_partner_runbook.pdf",
            ),
        ),
    )
    context = rcc.HistoricalContext(
        available=True,
        banner="",
        entries=(entry,),
    )
    doc = _FakeDoc()
    rcc.render_to_word(doc, context)
    rendered = "\n".join(doc.calls)
    assert "AdoptIQ_Report" not in rendered
    assert "[src: AdoptIQ_" not in rendered
    # External resolution still has its citation.
    assert "external_partner_runbook.pdf" in rendered
    # Both bullets still render.
    assert "Increased CSM cadence to weekly" in rendered
    assert "Premium support upgrade" in rendered
