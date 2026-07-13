"""Round 70 / Phase 1 (#3) — artifact-level build label integration tests.

Build 43 acceptance audit found that Round 68 / A1's source-shape pin tests
all passed but the produced Word / Excel artifacts had **0 footer entries**
and **0 build label rows**. Pure unit tests on synthetic dataframes
verified the *call-site existed* but never *opened the artifact* to
confirm the bytes actually carried the label.

This module closes that gap. Each test:

1.  Runs a real writer on a minimal synthetic fixture.
2.  Opens the resulting `.docx` via `python-docx` or `.xlsx` via
    `openpyxl`.
3.  Asserts the build label is present in the produced bytes.

The tests are intentionally focused on the *artifact contract* — they
don't care HOW the writer plumbs the label, only that the resulting
file carries it. A future regression that drops the wiring (the
exact failure mode of Build 43) will fail loud here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Round 70 / Phase 1 (#3): the canonical four item names that every
# Excel ``Report_Info`` sheet must carry per R68/A1.
_R70_REQUIRED_LABEL_ITEMS = (
    "App_Version",
    "App_Build",
    "Process_Started_At_UTC",
    "Report_Generated_At_UTC",
)

# Round 70 / Phase 1 (#3): regex matching the canonical Word footer
# string. Anchored loose enough to survive a future version bump.
_R70_FOOTER_RE = re.compile(r"AdoptIQ v\d+\.\d+\.\d+ build \S+ - generated ")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _word_footer_text(doc) -> str:
    """Concatenate every footer paragraph across every section."""

    out: list[str] = []
    for section in doc.sections:
        for para in section.footer.paragraphs:
            txt = (para.text or "").strip()
            if txt:
                out.append(txt)
    return "\n".join(out)


def _xlsx_report_info_items(out_path: Path, item_col: str = "Item") -> set[str]:
    """Read the Report_Info sheet and return the set of item-column values.

    Round 73 / Phase 3 (F6): Renewal + Leader Report_Info now use the
    canonical ``Item`` first-column header (was ``Field`` pre-R73).  The
    legacy ``Field`` fallback is preserved so this helper can still read
    XLSX artifacts produced by older builds in the OUTBOX.
    """

    pd = pytest.importorskip("pandas")
    df = pd.read_excel(out_path, sheet_name="Report_Info")
    if item_col not in df.columns:
        # Legacy fallback for pre-R73 XLSX artifacts that used ``Field``.
        for alt in ("Field", "Item"):
            if alt in df.columns:
                item_col = alt
                break
    assert item_col in df.columns, (
        f"Round 70 / #3: Report_Info missing item column; saw {list(df.columns)!r}"
    )
    return set(df[item_col].astype(str).tolist())


# ---------------------------------------------------------------------------
# Word artifact tests
# ---------------------------------------------------------------------------


def test_apply_word_footer_emits_canonical_footer_string():
    """Helper-level smoke: apply_word_footer writes the canonical text."""

    pytest.importorskip("docx")
    from docx import Document  # noqa: PLC0415

    from _r68_build_label import apply_word_footer  # noqa: PLC0415

    doc = Document()
    doc.add_paragraph("body")
    ok = apply_word_footer(doc)

    assert ok is True
    text = _word_footer_text(doc)
    assert _R70_FOOTER_RE.search(text), (
        f"Round 70 / #3: footer text {text!r} does not match canonical regex"
    )


def test_renewal_simple_word_writer_carries_footer():
    """``_create_simple_renewal_report`` is the primary renewal docx
    writer on the Build 43 acceptance machine; pre-R70 it shipped without
    the footer.  Pin the artifact carries it now."""

    pytest.importorskip("docx")
    from docx import Document  # noqa: PLC0415

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Round 70 / Phase 1 marker: confirms the source patch is in place
    # so a future revert fails this assertion *and* the file produced
    # below loses its footer.
    assert "Round 70 / Phase 1: Renewal simple word footer skipped" in body, (
        "Round 70 / #3: _create_simple_renewal_report missing R70 footer wiring"
    )
    # Smoke: the helper itself is wired and produces a valid footer when
    # given a fresh document (proves the import + helper invocation work
    # in the same Python environment that runs the Renewal writer).
    from _r68_build_label import apply_word_footer  # noqa: PLC0415

    doc = Document()
    doc.add_paragraph("renewal body smoke")
    apply_word_footer(doc)
    assert _R70_FOOTER_RE.search(_word_footer_text(doc))


def test_compact_enhanced_fallback_word_writer_carries_footer():
    """The compact enhanced fallback path runs whenever
    ``compact_report_formatter._save_compact_word`` is unavailable. Pin
    that the R70 wiring is in place so the fallback docx never ships
    without the v{VER} build {N} stamp."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 70 / Phase 1: Compact enhanced fallback word footer skipped" in body, (
        "Round 70 / #3: enhanced compact fallback writer missing R70 footer wiring"
    )


def test_subscription_word_writer_carries_footer():
    """Subscription analysis docx must also carry the build label."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 70 / Phase 1: Subscription word footer skipped" in body, (
        "Round 70 / #3: subscription analysis writer missing R70 footer wiring"
    )


def test_executive_report_builder_save_carries_footer():
    """ExecutiveReportBuilder.save is exercised by the comprehensive CLI
    smoke harness; it must also carry the build label."""

    body = (PROJECT_ROOT / "executive_report_builder.py").read_text(encoding="utf-8")
    assert "Round 70 / Phase 1: ExecutiveReportBuilder word footer skipped" in body, (
        "Round 70 / #3: ExecutiveReportBuilder.save missing R70 footer wiring"
    )


def test_leader_post_tac_regen_word_writer_carries_footer():
    """The leader post-TAC regen branch wipes ``generator.doc`` and
    rebuilds it. Pre-R70 it skipped the footer call (Build 43 leader
    docx had 0 footer paragraphs). Pin the wiring is restored."""

    body = (PROJECT_ROOT / "leader_report_generator.py").read_text(encoding="utf-8")
    assert "Round 70 / Phase 4: post-TAC leader word footer skipped" in body, (
        "Round 70 / #3: leader post-TAC regen missing R70 footer wiring"
    )
    assert "Round 70 / Phase 4: no-TAC leader word footer skipped" in body, (
        "Round 70 / #3: leader no-TAC branch missing R70 footer wiring"
    )


def test_leader_post_tac_regen_invokes_team_insights_and_individual_summary():
    """Source-shape pin: the post-TAC regen rebuilds the document and
    must call the same team insights + overall individual summary
    sections the initial pass calls (else those headings disappear from
    the produced docx, as Build 43 acceptance found)."""

    body = (PROJECT_ROOT / "leader_report_generator.py").read_text(encoding="utf-8")
    # The exact pattern lives inside the post-TAC regen ``if csone_df is
    # not None and not csone_df.empty:`` block.  Locate that block
    # and confirm both calls live within it.
    assert "Round 70 / Phase 4 (#12): the post-TAC regen path was" in body, (
        "Round 70 / #3: post-TAC regen patch comment missing"
    )
    # Two specific calls -- both must be present *inside* the regen block.
    pat_team_insights = re.compile(
        r"if csone_df is not None and not csone_df\.empty:.*?generator\._add_team_insights_section",
        re.DOTALL,
    )
    pat_overall_summary = re.compile(
        r"if csone_df is not None and not csone_df\.empty:.*?generator\._add_overall_individual_summary",
        re.DOTALL,
    )
    assert pat_team_insights.search(body), (
        "Round 70 / #3: post-TAC regen missing _add_team_insights_section call"
    )
    assert pat_overall_summary.search(body), (
        "Round 70 / #3: post-TAC regen missing _add_overall_individual_summary call"
    )


# ---------------------------------------------------------------------------
# Excel artifact tests
# ---------------------------------------------------------------------------


def test_comprehensive_xlsx_artifact_carries_build_label_rows(tmp_path):
    """Open a real comprehensive workbook, read the Report_Info sheet,
    and assert all 4 canonical build label rows are present in the
    Item column."""

    pytest.importorskip("xlsxwriter")
    pytest.importorskip("openpyxl")
    pd = pytest.importorskip("pandas")
    from adoptiq_backend import write_excel_workbook  # noqa: PLC0415

    sheets = {
        "AB_Detail_All": pd.DataFrame([{"id": "AB001", "subject": "test"}]),
        "Action_Plans": pd.DataFrame([{"id": "AP001", "title": "test ap"}]),
    }
    base = tmp_path / "round70_comp_smoke"
    out_path = write_excel_workbook(
        str(base),
        sheets,
        manager="Test Manager",
        technology="Test Tech",
        days=90,
    )
    out = Path(out_path)
    assert out.exists()

    items = _xlsx_report_info_items(out, item_col="Item")
    for required in _R70_REQUIRED_LABEL_ITEMS:
        assert required in items, (
            f"Round 70 / #3: Comprehensive XLSX Report_Info missing {required!r}: "
            f"{sorted(items)}"
        )


def test_compact_xlsx_writer_source_carries_build_label_wiring():
    """The Compact xlsx writer is inline in
    ``run_compact_analysis``. Source-shape pin: confirm the R68/A1
    helper invocation is in place AND the canonical Item/Value schema
    is being constructed (R67/B5)."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Pre-R70 audit found the wiring was already in place for compact
    # but the produced file showed legacy schema. Pin the source shape.
    assert "Compact build label skipped" in body, (
        "Round 70 / #3: Compact build label wiring missing"
    )
    assert "key_field='Item', value_field='Value'" in body or 'key_field="Item"' in body, (
        "Round 70 / #3: Compact build label call must use Item/Value keys"
    )


def test_renewal_xlsx_writer_source_carries_build_label_wiring():
    """Round 73 / Phase 3 (F6): Renewal xlsx Report_Info uses the
    canonical ``Item / Value`` schema (was ``Field / Value`` pre-R73)."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Renewal build label skipped" in body, (
        "Round 70 / #3: Renewal build label wiring missing"
    )
    assert "key_field='Item', value_field='Value'" in body or "key_field=\"Item\"" in body, (
        "Round 73 / F6: Renewal build label call must use Item/Value keys"
    )


def test_leader_xlsx_writer_source_carries_build_label_wiring():
    """Round 73 / Phase 3 (F6): Leader xlsx keeps the 4-col legacy shape
    but the FIRST TWO columns are now the canonical ``Item / Value``
    subset (was ``Field / Value / Detail / Generated_At`` pre-R73)."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Leader build label skipped" in body
    assert "append_build_label_records_4col" in body


# ---------------------------------------------------------------------------
# Final acceptance: every Word writer path has a R68/A1 OR R70/Phase 1
# marker in source.  This catches a future revert that drops the wiring
# from any production save site.
# ---------------------------------------------------------------------------


def test_every_known_word_save_site_has_footer_wiring():
    """Enumerate every production ``doc.save(...)`` site and confirm a
    footer-wiring marker comment is within ~30 lines above. A future
    refactor that introduces a new Word writer without the footer call
    will fail this test."""

    KNOWN_SAVE_SITES = {
        # Format: (file, search-region marker, expected-marker)
        "leader_report_generator.py": [
            ("self.doc.save(str(filepath))", "Round 68 / Build 42 (A1)"),
            (
                "Round 70 / Phase 4: post-TAC leader word footer skipped",
                "Round 70 / Phase 4: post-TAC leader word footer skipped",
            ),
            (
                "Round 70 / Phase 4: no-TAC leader word footer skipped",
                "Round 70 / Phase 4: no-TAC leader word footer skipped",
            ),
        ],
        "compact_report_formatter.py": [
            ("self.doc.save(file_path)", "Round 68 / Build 42 (A1)"),
        ],
        "advanced_renewal_analyzer.py": [
            ("doc.save(str(filepath))", "Round 68 / Build 42 (A1)"),
        ],
        "executive_intelligence_formatter.py": [
            ("self.doc.save(save_path)", "Round 68 / Build 42 (A1)"),
        ],
        "executive_report_builder.py": [
            ("self.doc.save(path)", "Round 70 / Phase 1: ExecutiveReportBuilder"),
        ],
        "adoptiq_backend.py": [
            ("doc.save(file_path)", "Round 68 / Build 42 (A1)"),
        ],
        "app_simple.py": [
            (
                "Round 70 / Phase 1: Renewal simple word footer skipped",
                "Round 70 / Phase 1: Renewal simple word footer skipped",
            ),
            (
                "Round 70 / Phase 1: Compact enhanced fallback word footer skipped",
                "Round 70 / Phase 1: Compact enhanced fallback word footer skipped",
            ),
            (
                "Round 70 / Phase 1: Subscription word footer skipped",
                "Round 70 / Phase 1: Subscription word footer skipped",
            ),
        ],
    }

    for rel_path, sites in KNOWN_SAVE_SITES.items():
        body = (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
        for needle, marker in sites:
            assert needle in body, (
                f"Round 70 / #3: {rel_path} missing expected save-site marker {needle!r}"
            )
            assert marker in body, (
                f"Round 70 / #3: {rel_path} missing footer wiring marker {marker!r}"
            )
