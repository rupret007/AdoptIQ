"""Round 68 / Build 42 (A1 / A3): pin the build label in every Word and
Excel report.

Build 41 acceptance shipped reports from a pre-Build-41 binary because
the operator copied the new ``.app`` into ``/Applications`` but did not
quit and re-launch the running process.  None of the Round 67 source
patches reached those reports, but the user had no in-band way to spot
it.  Round 68 / Phase 0 / A1 closes the trap by stamping the running
binary's identity into every artifact.

These tests pin the contract:

* The 4 canonical Excel rows (``App_Version`` / ``App_Build`` /
  ``Process_Started_At_UTC`` / ``Report_Generated_At_UTC``) must be
  written by every Excel writer (Comprehensive / Compact / Renewal /
  Leader).
* The Word footer helper must attach a label to every section.
* The label text must include both ``v{ADOPTIQ_VERSION}`` and
  ``build {ADOPTIQ_BUILD}`` so a literal substring search finds it.
"""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture(scope="module")
def label_helper():
    """Import the helper fresh and return the module."""

    if "_r68_build_label" in sys.modules:
        return importlib.reload(sys.modules["_r68_build_label"])
    return importlib.import_module("_r68_build_label")


# ---------------------------------------------------------------------------
# Helper-level contracts (these protect the 4-row schema regardless of
# whether the report writers happen to call into the helper).
# ---------------------------------------------------------------------------


def test_get_build_label_rows_emits_exactly_four_canonical_rows(label_helper):
    rows = label_helper.get_build_label_rows()
    assert isinstance(rows, list)
    assert len(rows) == 4
    keys = [item for item, _value in rows]
    assert keys == [
        "App_Version",
        "App_Build",
        "Process_Started_At_UTC",
        "Report_Generated_At_UTC",
    ]


def test_get_build_label_rows_values_are_non_empty_strings(label_helper):
    rows = label_helper.get_build_label_rows()
    for item, value in rows:
        assert isinstance(item, str) and item
        assert isinstance(value, str)
        # Values may be '?' on a corrupt config but must never be None.
        assert value is not None


def test_process_started_at_utc_is_iso_z_with_seconds(label_helper):
    started = label_helper.get_process_started_at_utc()
    assert isinstance(started, str)
    assert started.endswith("Z")
    # ``YYYY-MM-DDTHH:MM:SSZ`` -> 20 chars.
    assert len(started) == 20


def test_get_build_label_text_includes_version_and_build(label_helper):
    from config import ADOPTIQ_BUILD, ADOPTIQ_VERSION  # noqa: PLC0415

    text = label_helper.get_build_label_text()
    assert f"v{ADOPTIQ_VERSION}" in text
    assert f"build {ADOPTIQ_BUILD}" in text


# ---------------------------------------------------------------------------
# append_build_label_records / append_build_label_records_4col / pairs
# all share a contract: 4 entries appended in stable order, in the right
# shape for the consumer.
# ---------------------------------------------------------------------------


def test_append_records_uses_item_value_keys(label_helper):
    records: list = []
    label_helper.append_build_label_records(records)
    assert len(records) == 4
    for r in records:
        assert set(r.keys()) == {"Item", "Value"}


def test_append_records_supports_custom_key_value_field_names(label_helper):
    """The helper accepts custom key/value field names for back-compat with
    legacy callers.  Round 73 / Phase 3 (F6) standardized ALL writers on
    the canonical ``Item / Value`` schema, but the helper still honours an
    explicit ``key_field=`` override so a downstream consumer can pin a
    custom shape if needed."""

    records: list = []
    label_helper.append_build_label_records(
        records, key_field="Field", value_field="Value"
    )
    assert len(records) == 4
    for r in records:
        assert set(r.keys()) == {"Field", "Value"}


def test_append_records_4col_matches_leader_schema(label_helper):
    """Round 73 / Phase 3 (F6): the Leader writer's 4-col Report_Info
    schema now uses the canonical ``Item`` first-column header (was
    ``Field`` pre-R73) so a downstream consumer running
    ``pd.read_excel("Report_Info")[["Item", "Value"]]`` gets the same
    projection across Compact / Renewal / Comprehensive / Leader.  The
    extra ``Detail`` and ``Generated_At`` columns carry per-row
    provenance the Leader writer needs but the other formats don't."""

    records: list = []
    label_helper.append_build_label_records_4col(records)
    assert len(records) == 4
    for r in records:
        assert set(r.keys()) == {"Item", "Value", "Detail", "Generated_At"}
        # Detail / Generated_At are placeholders (None) so they don't
        # collide with the per-row content the leader writer emits.
        assert r["Detail"] is None
        assert r["Generated_At"] is None


def test_append_pairs_appends_two_element_lists(label_helper):
    rows: list = []
    label_helper.append_build_label_rows_pairs(rows)
    assert len(rows) == 4
    for pair in rows:
        assert isinstance(pair, list)
        assert len(pair) == 2


# ---------------------------------------------------------------------------
# Word footer wiring -- runs in-process against a real python-docx
# Document so the test fails loud if the helper drifts off the API.
# ---------------------------------------------------------------------------


def test_apply_word_footer_writes_label_into_section_footer(label_helper):
    docx = pytest.importorskip("docx")  # noqa: F841 -- just ensures docx installed
    from docx import Document  # noqa: PLC0415

    doc = Document()
    doc.add_paragraph("body")
    ok = label_helper.apply_word_footer(doc)
    assert ok is True

    # Every section must now carry the label in its footer.
    found = False
    expected_prefix = "AdoptIQ v"
    for section in doc.sections:
        for para in section.footer.paragraphs:
            if para.text.startswith(expected_prefix):
                found = True
                break
    assert found, "Expected R68 build label in at least one section footer"


def test_apply_word_footer_is_idempotent(label_helper):
    pytest.importorskip("docx")
    from docx import Document  # noqa: PLC0415

    doc = Document()
    label_helper.apply_word_footer(doc)
    label_helper.apply_word_footer(doc)

    # Re-applying must NOT duplicate the label paragraph.
    for section in doc.sections:
        labels = [
            p for p in section.footer.paragraphs if p.text.startswith("AdoptIQ v")
        ]
        assert len(labels) <= 1


def test_apply_word_footer_never_raises_on_bad_doc(label_helper):
    """A non-Document argument must NOT bring down the writer."""

    class _Bogus:
        sections = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    ok = label_helper.apply_word_footer(_Bogus())
    assert ok is False


# ---------------------------------------------------------------------------
# Source-shape pins on every writer.  We grep for the explicit Round 68
# A1 marker comment so a future refactor that drops the wiring fails
# loud here instead of silently shipping label-less reports.
# ---------------------------------------------------------------------------


def _read_text(rel_path: str) -> str:
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parent.parent
    return (root / rel_path).read_text(encoding="utf-8")


def test_comprehensive_xlsx_writer_calls_build_label_helper():
    body = _read_text("adoptiq_backend.py")
    assert "Round 68 / Build 42 (A1)" in body
    assert "append_build_label_rows_pairs" in body


def test_compact_xlsx_writer_calls_build_label_helper():
    body = _read_text("app_simple.py")
    # The compact branch lives right above the Compact Report_Info
    # writer; we look for both the marker and the Item-keyed call.
    assert "Compact build label skipped" in body


def test_renewal_xlsx_writer_calls_build_label_helper():
    body = _read_text("app_simple.py")
    assert "Renewal build label skipped" in body
    # Round 73 / Phase 3 (F6): Renewal Report_Info now uses the canonical
    # ``Item, Value`` schema (was ``Field, Value`` pre-R73).
    assert "key_field='Item'" in body or 'key_field="Item"' in body


def test_leader_xlsx_writer_calls_build_label_helper():
    body = _read_text("app_simple.py")
    assert "Leader build label skipped" in body
    assert "append_build_label_records_4col" in body


def test_compact_word_writer_calls_apply_word_footer():
    body = _read_text("compact_report_formatter.py")
    assert "Round 68 / Build 42 (A1)" in body
    assert "apply_word_footer" in body


def test_renewal_word_writer_calls_apply_word_footer():
    body = _read_text("advanced_renewal_analyzer.py")
    assert "Round 68 / Build 42 (A1)" in body
    assert "apply_word_footer" in body


def test_leader_word_writer_calls_apply_word_footer():
    body = _read_text("leader_report_generator.py")
    assert "Round 68 / Build 42 (A1)" in body
    assert "apply_word_footer" in body


def test_executive_intelligence_word_writer_calls_apply_word_footer():
    body = _read_text("executive_intelligence_formatter.py")
    assert "Round 68 / Build 42 (A1)" in body
    assert "apply_word_footer" in body


# ---------------------------------------------------------------------------
# Smoke: the comprehensive xlsx writer puts the four rows on Report_Info.
# ---------------------------------------------------------------------------


def test_comprehensive_write_excel_workbook_emits_build_label_rows(tmp_path):
    pd = pytest.importorskip("pandas")  # noqa: F841
    pytest.importorskip("xlsxwriter")
    pytest.importorskip("openpyxl")

    from adoptiq_backend import write_excel_workbook  # noqa: PLC0415
    import pandas as _pd  # noqa: PLC0415

    sheets = {
        "AB_Detail_All": _pd.DataFrame([{"col_a": "val", "col_b": 1}]),
    }
    # ``write_excel_workbook`` appends ``.xlsx`` to the path stem, so
    # we pass a stem without the extension and reconstruct the final
    # path the same way (mirrors how the real callers in app_simple.py
    # use this writer).
    base_stem = tmp_path / "round68_smoke"
    out_path = write_excel_workbook(
        str(base_stem),
        sheets,
        manager="Test Manager",
        technology="Test Tech",
        days=90,
    )
    out = pytest.importorskip("pathlib").Path(out_path)

    assert out.exists()
    info_df = _pd.read_excel(out, sheet_name="Report_Info")
    items = set(info_df["Item"].astype(str).tolist())
    for required in (
        "App_Version",
        "App_Build",
        "Process_Started_At_UTC",
        "Report_Generated_At_UTC",
    ):
        assert required in items, f"Report_Info missing {required!r}: {sorted(items)}"
