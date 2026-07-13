"""Round 52.1 / Phase 1: table-only DOCX text + numeric fingerprint primitive.

The 3-iter repeatability proof against the Round 52 manifest showed
comprehensive iter2 failing on overall ``numeric_similarity`` (0.7094
< 0.80) while the table-only numeric fingerprint stayed at exactly 1.0
across baseline + 3 iterations.  Root cause: ``_numeric_fingerprint``
over the entire DOCX text (paragraphs + tables) absorbs digits from the
AI-generated insights section ("Pattern N:", "Root Cause Analysis:",
"Evidence:" -- all of which cite different bug IDs / percentages /
case IDs every run).  Tables hold the actual Snowflake-derived KPI
counts and stay byte-stable.

This module pins the helper that powers the fix:
  * ``_extract_docx_table_text`` returns ONLY the concatenated cell
    text from ``doc.tables`` (no paragraph runs, no headers/footers).
  * Composing it with the existing ``_numeric_fingerprint`` yields a
    drift signal that is provably immune to AI-narrative noise.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document

from report_iteration_loop import (
    _extract_docx_table_text,
    _extract_docx_text,
    _jaccard_similarity,
    _numeric_fingerprint,
)


def _build_docx_with_paragraphs_and_table(
    tmp_path: Path,
    name: str,
    paragraphs: list[str],
    table_rows: list[list[str]],
) -> Path:
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    if table_rows:
        cols = max(len(row) for row in table_rows)
        table = doc.add_table(rows=len(table_rows), cols=cols)
        for r_idx, row in enumerate(table_rows):
            for c_idx, cell_text in enumerate(row):
                table.rows[r_idx].cells[c_idx].text = cell_text
    out = tmp_path / name
    doc.save(out)
    return out


def test_round521_extract_table_text_returns_only_cell_text(tmp_path: Path):
    """Paragraph runs must be excluded from the table-only extractor."""
    docx = _build_docx_with_paragraphs_and_table(
        tmp_path,
        "para_and_table.docx",
        paragraphs=[
            "Pattern 1: BEMS-12345 reproduced 17% of the time",
            "Root Cause Analysis: TAC-99812 escalation evidence",
        ],
        table_rows=[
            ["Metric", "Value"],
            ["Total Customers", "42"],
            ["Open Action Plans", "381"],
        ],
    )

    table_text = _extract_docx_table_text(docx)
    full_text = _extract_docx_text(docx)

    assert "Total Customers" in table_text
    assert "381" in table_text
    assert "Metric" in table_text
    assert "Pattern 1" not in table_text, (
        "table-only extractor must NOT include paragraph runs"
    )
    assert "BEMS-12345" not in table_text, (
        "table-only extractor must exclude AI-narrative paragraphs"
    )
    assert "TAC-99812" not in table_text

    # The full extractor still sees both, by contrast.
    assert "Pattern 1" in full_text
    assert "Total Customers" in full_text


def test_round521_table_only_fingerprint_excludes_paragraph_numerics(
    tmp_path: Path,
):
    """The table-only numeric fingerprint must drop paragraph numerics."""
    docx = _build_docx_with_paragraphs_and_table(
        tmp_path,
        "noise.docx",
        paragraphs=[
            # AI-narrative-style numerics; these would otherwise pollute the
            # overall numeric fingerprint and produce false drift signals.
            "Pattern 1 (17% of cases): BEMS-12345 reproduced 4 times",
            "Sentiment direction: down; prior occurrences: 9",
        ],
        table_rows=[
            ["Metric", "Value"],
            ["Open Action Plans", "381"],
            ["Total Customers", "42"],
        ],
    )

    full_fp = _numeric_fingerprint(_extract_docx_text(docx))
    table_fp = _numeric_fingerprint(_extract_docx_table_text(docx))

    # Table-only must contain the KPI numerics.
    assert "381" in table_fp
    assert "42" in table_fp

    # Table-only must NOT contain the paragraph numerics.
    assert "17%" not in table_fp, (
        f"table-only fingerprint leaked paragraph percentages: {table_fp}"
    )
    assert "4" not in table_fp or "4" in {"42"}  # '4' must come from '42' if at all
    assert "9" not in table_fp, (
        f"table-only fingerprint leaked paragraph numerics: {table_fp}"
    )

    # The full fingerprint, by contrast, sees both sources -- proving the
    # table-only fingerprint is a strict subset and is therefore the
    # noise-immune signal.
    assert "17%" in full_fp
    assert "9" in full_fp
    assert table_fp.issubset(full_fp)


def test_round521_table_only_fingerprint_empty_when_no_tables(tmp_path: Path):
    """Paragraph-only DOCX must yield an empty table-only fingerprint."""
    docx = _build_docx_with_paragraphs_and_table(
        tmp_path,
        "no_tables.docx",
        paragraphs=[
            "Total Customers: 42",
            "Open Action Plans: 381",
        ],
        table_rows=[],
    )

    table_text = _extract_docx_table_text(docx)
    table_fp = _numeric_fingerprint(table_text)

    assert table_text.strip() == ""
    assert table_fp == set()

    # And the overall extractor still finds the paragraph numerics, so
    # the test fixture itself is wired correctly.
    assert "381" in _numeric_fingerprint(_extract_docx_text(docx))


def test_round521_table_only_fingerprint_is_byte_stable_across_paragraph_drift(
    tmp_path: Path,
):
    """The reproducer for the iter2 failure mode.

    Two DOCX files with identical tables but different AI-narrative
    paragraphs must yield IDENTICAL table-only numeric fingerprints
    (Jaccard 1.0).  This is the positive-signal property that powers
    the new gate -- when tables match, we have proof Snowflake KPIs
    did not drift, regardless of how much narrative jitter the LLM
    injected.
    """
    rows = [
        ["Metric", "Value"],
        ["Total Customers", "42"],
        ["Open Action Plans", "381"],
        ["Risk Score", "0.7"],
    ]
    a = _build_docx_with_paragraphs_and_table(
        tmp_path,
        "a.docx",
        paragraphs=["Pattern 1 (17% of cases): BEMS-12345"],
        table_rows=rows,
    )
    b = _build_docx_with_paragraphs_and_table(
        tmp_path,
        "b.docx",
        paragraphs=[
            # Different bug ID, percentage, and case ID -- exactly the
            # AI-narrative jitter we observed in the live iter2 vs iter3
            # diff.  Tables are identical.
            "Pattern 1 (43% of cases): BEMS-99999 reproduced 7 times",
            "Sentiment direction: up; prior occurrences: 12",
        ],
        table_rows=rows,
    )

    fp_a = _numeric_fingerprint(_extract_docx_table_text(a))
    fp_b = _numeric_fingerprint(_extract_docx_table_text(b))
    assert _jaccard_similarity(fp_a, fp_b) == 1.0, (
        "table-only fingerprint must be byte-stable when tables match; "
        f"only_in_a={sorted(fp_a - fp_b)} only_in_b={sorted(fp_b - fp_a)}"
    )

    # Full fingerprint is NOT byte-stable across the same drift -- this
    # asserts the test fixture genuinely reproduces the noise.
    full_a = _numeric_fingerprint(_extract_docx_text(a))
    full_b = _numeric_fingerprint(_extract_docx_text(b))
    assert _jaccard_similarity(full_a, full_b) < 1.0, (
        "fixture must reproduce paragraph-numeric drift to be a real "
        "regression guard"
    )
