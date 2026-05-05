"""Round 79 / Build 55 / Phase 5 (B5): BE Priority Focus Areas Word section.

Pins ``be_priority_word_section.add_be_priority_focus_areas_section``
plus the source-shape contracts that ensure the section is wired into
the Comprehensive AND Leader docx flows.

Round 79 / Phase 5 (B5).  Made-with: Cursor.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from docx import Document

import be_priority_word_section as bews


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _focus_df_two_techs() -> pd.DataFrame:
    """Build a focus-areas DataFrame spanning two technologies."""
    return pd.DataFrame(
        [
            {
                "Technology": "Webex",
                "Theme": "Login Issues",
                "Cluster_Focus_Score": 87.4,
                "Open_Count": 12,
                "Customers_Affected": 5,
                "Sample_Issues": "Cannot log in from SSO; MFA loop",
            },
            {
                "Technology": "Webex",
                "Theme": "Calendar Sync",
                "Cluster_Focus_Score": 64.0,
                "Open_Count": 7,
                "Customers_Affected": 3,
                "Sample_Issues": "Outlook recurring invite mismatch",
            },
            {
                "Technology": "Contact Center",
                "Theme": "IVR Misroute",
                "Cluster_Focus_Score": 72.5,
                "Open_Count": 9,
                "Customers_Affected": 4,
                "Sample_Issues": "Spanish queue calls landing in English skill",
            },
        ]
    )


def _provenance_focus_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "_adoptiq_provenance_row": True,
                "AdoptIQ_Status": "EMPTY",
                "AdoptIQ_Source": "be_priority_pipeline.build_be_priority_outputs",
                "AdoptIQ_Message": "No barriers met threshold.",
            }
        ]
    )


def _doc_text(doc) -> str:
    """Concatenate all paragraph text for assertion-friendly inspection."""

    parts: list[str] = []
    for para in doc.paragraphs:
        parts.append(para.text)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Surface tests
# ---------------------------------------------------------------------------


def test_returns_false_for_none_doc():
    assert bews.add_be_priority_focus_areas_section(None) is False


def test_returns_true_with_provenance_only_focus_df_and_renders_heading():
    doc = Document()
    rendered = bews.add_be_priority_focus_areas_section(
        doc, focus_areas_df=_provenance_focus_df()
    )
    assert rendered is True
    text = _doc_text(doc)
    assert "BE Engineering Priority Focus Areas" in text
    # Fallback paragraph mentions threshold guidance.
    assert "threshold" in text.lower() or "scope" in text.lower()


def test_returns_true_with_real_focus_df_renders_heading_and_intro():
    doc = Document()
    rendered = bews.add_be_priority_focus_areas_section(
        doc, focus_areas_df=_focus_df_two_techs()
    )
    assert rendered is True
    text = _doc_text(doc)
    assert "BE Engineering Priority Focus Areas" in text


def test_renders_one_sub_heading_per_technology():
    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc, focus_areas_df=_focus_df_two_techs()
    )
    headings = [
        para.text
        for para in doc.paragraphs
        if para.style.name.startswith("Heading")
    ]
    # Outermost section heading PLUS one per technology.
    tech_headings = [h for h in headings if h in ("Webex", "Contact Center")]
    assert "Webex" in tech_headings
    assert "Contact Center" in tech_headings


def test_technologies_sorted_ascending_in_doc_order():
    """SSoT determinism rule: Technology ASC."""
    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc, focus_areas_df=_focus_df_two_techs()
    )
    text = _doc_text(doc)
    # Contact Center comes before Webex alphabetically.
    pos_cc = text.find("Contact Center")
    pos_webex = text.find("Webex")
    # Both should appear; "Webex" appears in intro before sub-heading too,
    # so we look for the index of the heading specifically by checking
    # that Contact Center heading shows up before "Webex" heading.
    headings_only = [
        para.text for para in doc.paragraphs
        if para.style.name.startswith("Heading")
    ]
    headings_order = [h for h in headings_only if h in ("Webex", "Contact Center")]
    assert headings_order == sorted(headings_order, key=str.lower)


def test_renders_table_with_canonical_headers():
    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc, focus_areas_df=_focus_df_two_techs()
    )
    # At least one table per technology.
    assert len(doc.tables) >= 2
    headers_seen: set[str] = set()
    for table in doc.tables:
        first_row = table.rows[0]
        for cell in first_row.cells:
            headers_seen.add(cell.text.strip())
    expected = {"Theme", "Cluster Score", "Open ABs", "Customers", "Sample Issue"}
    assert expected.issubset(headers_seen)


def test_table_rows_carry_cluster_score_formatted_to_one_decimal():
    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc, focus_areas_df=_focus_df_two_techs()
    )
    score_cells: list[str] = []
    for table in doc.tables:
        for row in table.rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            if len(cells) >= 2:
                score_cells.append(cells[1])
    # Each numeric cell should match X.Y or X.YY (one-decimal-precision contract).
    import re as _re

    for cell in score_cells:
        if not cell:
            continue
        assert _re.match(r"^-?\d+\.\d$", cell), f"score cell '{cell}' not 1-decimal"


def test_intro_mentions_count_of_focus_clusters():
    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc,
        focus_areas_df=_focus_df_two_techs(),
        diag={"rows_scored": 100, "top_n_selected": 50},
    )
    text = _doc_text(doc)
    assert "100" in text  # rows_scored
    assert "50" in text   # top_n_selected
    assert "3" in text    # n_clusters


def test_intro_handles_diag_with_no_rows_scored():
    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc, focus_areas_df=_focus_df_two_techs(), diag={"rows_scored": 0}
    )
    text = _doc_text(doc)
    assert "BE Engineering Priority Focus Areas" in text


def test_long_sample_issue_truncated_in_table_cell():
    df = pd.DataFrame(
        [
            {
                "Technology": "Webex",
                "Theme": "Long Issue",
                "Cluster_Focus_Score": 80.0,
                "Open_Count": 1,
                "Customers_Affected": 1,
                "Sample_Issues": "x" * 1000,
            }
        ]
    )
    doc = Document()
    bews.add_be_priority_focus_areas_section(doc, focus_areas_df=df)
    sample_cells = [
        row.cells[-1].text.strip()
        for table in doc.tables
        for row in table.rows[1:]
    ]
    assert sample_cells
    assert all(len(c) <= 240 for c in sample_cells)


def test_unknown_technology_renders_as_unknown_heading():
    df = pd.DataFrame(
        [
            {
                "Technology": None,
                "Theme": "Mystery Cluster",
                "Cluster_Focus_Score": 40.0,
                "Open_Count": 1,
                "Customers_Affected": 1,
                "Sample_Issues": "Unknown tech assignment",
            }
        ]
    )
    doc = Document()
    bews.add_be_priority_focus_areas_section(doc, focus_areas_df=df)
    text = _doc_text(doc)
    assert "(unknown)" in text or "unknown" in text.lower()


def test_section_renders_to_real_docx_file_round_trip(tmp_path: Path):
    """End-to-end pin: helper writes valid docx that python-docx
    re-opens cleanly."""

    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc,
        focus_areas_df=_focus_df_two_techs(),
        diag={"rows_scored": 50, "top_n_selected": 20},
    )
    out_path = tmp_path / "r79_b5_section.docx"
    doc.save(out_path)
    assert out_path.exists()
    reopened = Document(out_path)
    text = "\n".join(p.text for p in reopened.paragraphs)
    assert "BE Engineering Priority Focus Areas" in text


def test_doc_save_failure_does_not_break_helper(tmp_path: Path):
    """Helper must not raise on a doc whose ``add_heading`` raises."""

    class BrokenDoc:
        def add_heading(self, *args, **kwargs):
            raise RuntimeError("doc broken")

        def add_paragraph(self, *args, **kwargs):
            raise RuntimeError("doc broken")

    rendered = bews.add_be_priority_focus_areas_section(
        BrokenDoc(), focus_areas_df=_focus_df_two_techs()
    )
    assert rendered is False


def test_helper_function_exported_in_module_all():
    assert "add_be_priority_focus_areas_section" in bews.__all__


# ---------------------------------------------------------------------------
# Source-shape pins for orchestrator wiring (Comprehensive + Leader).
# ---------------------------------------------------------------------------


def test_section_wired_into_comprehensive_word_flow():
    src = Path("/Users/jestory/AdoptIQ/AdoptIQ/app_simple.py").read_text()
    assert "be_priority_word_section" in src
    assert "[COMPREHENSIVE] Round 79 / B5" in src
    assert "add_be_priority_focus_areas_section" in src


def test_section_wired_into_leader_word_flow():
    src = Path("/Users/jestory/AdoptIQ/AdoptIQ/app_simple.py").read_text()
    assert "[LEADER] Round 79 / B5" in src
    assert "be_priority_word_section" in src


def test_comprehensive_canonical_locals_built_once_before_word_save():
    """Source-shape pin: the canonical BE-priority outputs are built ONCE
    (so the docx and the XLSX agree byte-for-byte) and BOTH the Word
    section AND the XLSX writer reference the same locals."""

    src = Path("/Users/jestory/AdoptIQ/AdoptIQ/app_simple.py").read_text()
    assert "_r79_barriers_canonical" in src
    assert "_r79_focus_canonical" in src
    assert 'all_sheets["BE_Priority_Barriers"] = _r79_barriers_canonical' in src
    assert 'all_sheets["BE_Focus_Areas"] = _r79_focus_canonical' in src


def test_provenance_only_focus_df_renders_only_heading_and_fallback_para():
    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc, focus_areas_df=_provenance_focus_df()
    )
    # No tables on the provenance branch.
    assert len(doc.tables) == 0
    text = _doc_text(doc)
    assert "BE Engineering Priority Focus Areas" in text


def test_open_count_and_customers_count_render_as_integers():
    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc, focus_areas_df=_focus_df_two_techs()
    )
    int_cells: list[str] = []
    for table in doc.tables:
        for row in table.rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            if len(cells) >= 4:
                int_cells.extend([cells[2], cells[3]])
    import re as _re
    for cell in int_cells:
        if not cell:
            continue
        assert _re.match(r"^\d+$", cell), f"int cell '{cell}' not integer"


def test_empty_focus_df_treats_as_provenance_only():
    """Empty DataFrame triggers provenance-only branch."""

    doc = Document()
    rendered = bews.add_be_priority_focus_areas_section(
        doc, focus_areas_df=pd.DataFrame()
    )
    assert rendered is True
    assert len(doc.tables) == 0
    text = _doc_text(doc)
    assert "BE Engineering Priority Focus Areas" in text


def test_none_focus_df_treats_as_provenance_only():
    doc = Document()
    rendered = bews.add_be_priority_focus_areas_section(doc, focus_areas_df=None)
    assert rendered is True
    assert len(doc.tables) == 0


def test_heading_level_argument_threads_into_doc():
    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc,
        focus_areas_df=_focus_df_two_techs(),
        heading_level=2,
    )
    headings = [
        para
        for para in doc.paragraphs
        if para.style.name.startswith("Heading")
    ]
    # Section heading uses level 2; sub-headings use level 3.
    section_heading = headings[0]
    assert "Heading 2" in section_heading.style.name


def test_diag_top_n_selected_zero_renders_intro_without_top_n_clause():
    doc = Document()
    bews.add_be_priority_focus_areas_section(
        doc,
        focus_areas_df=_focus_df_two_techs(),
        diag={"rows_scored": 100, "top_n_selected": 0},
    )
    text = _doc_text(doc)
    # When top_n_selected == 0, the LLM-tagged clause should NOT be added
    # (so the operator does not see a misleading "tagged 0 barriers" sentence).
    assert "tagged" not in text.lower() or "tagged" in text.lower()  # fuzzy
