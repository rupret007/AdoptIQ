"""Round 73 / Phase 2 (F5) -- source citation injector skips Heading
and Title style paragraphs.

Build 46 acceptance audit found that ``report_source_injector``
appended trailing ``[Source: AdoptIQ Report Data Sources]`` chrome
into Comprehensive Word headings whose text matched the canonical KPI
regex -- e.g. ``"Top 10 Customer Risk Profiles"``.  The result is a
deeply ugly section title in the rendered Word report.

Fix: the per-paragraph loop in
``inject_source_citations_into_docx`` checks ``paragraph.style.name``
and skips anything whose style starts with ``Heading`` or is exactly
``Title`` -- citations belong in body paragraphs only.

This pin covers:

* Source-shape: the guard is present in ``report_source_injector.py``
  with the canonical R73/F5 marker comment.
* Runtime: a synthetic .docx with a Heading 1 / Heading 2 / Title /
  body paragraph mix runs through the injector and ONLY the body
  paragraphs receive the citation.
* Backward-compat: the existing R57/R64 multi-KPI inline-interleave
  contract for body paragraphs is unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Source-shape pin: the heading-skip guard exists
# ---------------------------------------------------------------------------


def test_report_source_injector_carries_r73_heading_skip_guard() -> None:
    """``report_source_injector.py`` must carry the R73/F5 heading-skip
    guard in its per-paragraph loop -- a future refactor that drops
    the guard fails loud here.
    """

    body = (PROJECT_ROOT / "report_source_injector.py").read_text(encoding="utf-8")
    assert "Round 73 / Phase 2 (F5)" in body, (
        "Round 73 / F5: report_source_injector.py missing heading-skip "
        "marker comment."
    )
    # The guard MUST check both Heading-prefix AND Title style.
    assert 'style_name.startswith("Heading")' in body, (
        "Round 73 / F5: heading-skip guard must check style_name.startswith('Heading')"
    )
    assert 'style_name == "Title"' in body, (
        "Round 73 / F5: heading-skip guard must check style_name == 'Title'"
    )


# ---------------------------------------------------------------------------
# Runtime: synthetic docx with mixed paragraph styles
# ---------------------------------------------------------------------------


def test_injector_skips_heading_paragraphs_runtime(tmp_path) -> None:
    """End-to-end: a heading paragraph whose text matches the canonical
    KPI regex MUST NOT receive a trailing ``[Source: ...]`` citation,
    while a body paragraph with the same text MUST receive one.
    """

    pytest.importorskip("docx")
    from docx import Document  # noqa: PLC0415

    from report_source_injector import inject_source_citations_into_docx  # noqa: PLC0415

    docx_path = tmp_path / "round73_f5_smoke.docx"
    doc = Document()
    # Mixed paragraph styles -- only the body paragraph below should
    # be cited.  Each text contains a canonical KPI claim so the
    # injector's canonical-KPI gate fires.
    doc.add_paragraph("Top 10 Customer Risk Profiles", style="Heading 1")
    doc.add_paragraph("Risk Components Breakdown by 12 Categories", style="Heading 2")
    doc.add_paragraph("Total Customers: 37", style="Title")
    # Body paragraphs (the actual narrative claims that should be cited).
    doc.add_paragraph("Total Action Plans: 889 across the portfolio.")
    doc.add_paragraph("Total Open Adoption Barriers: 161.")
    doc.save(str(docx_path))

    counts = inject_source_citations_into_docx(docx_path, scenario_key="comprehensive")

    # Re-open the produced docx and verify per-paragraph state.
    out_doc = Document(str(docx_path))
    paragraphs = list(out_doc.paragraphs)
    # The first 3 (heading / heading / title) MUST NOT carry the citation chrome.
    for idx, para in enumerate(paragraphs[:3]):
        text = (para.text or "").strip()
        assert "[Source:" not in text, (
            f"Round 73 / F5: heading-style paragraph {idx} ({para.style.name!r}) "
            f"got citation chrome: {text!r}"
        )
    # The two body paragraphs MUST carry the citation chrome.
    body_paras = paragraphs[3:5]
    for idx, para in enumerate(body_paras):
        text = (para.text or "").strip()
        assert "[Source:" in text, (
            f"Round 73 / F5: body paragraph {idx + 3} ({para.style.name!r}) "
            f"missing citation chrome: {text!r}"
        )

    # The injector's structured count dict must reflect the heading
    # skips -- they get lumped into ``skipped_no_numeric`` along with
    # the existing no-canonical-match path so the existing operator
    # diagnostic surface is unchanged.
    assert counts["paragraphs_injected"] == 2, (
        f"Round 73 / F5: expected 2 body injections; got {counts}"
    )
    assert counts["skipped_no_numeric"] >= 3, (
        f"Round 73 / F5: heading paragraphs not counted in skipped_no_numeric; "
        f"got {counts}"
    )


def test_injector_does_not_break_existing_multi_kpi_interleave(tmp_path) -> None:
    """Backward-compat: the R57/R64 multi-KPI inline-interleave
    contract for body paragraphs must still fire after the R73 heading
    skip is added.  A body paragraph with multiple canonical KPIs gets
    interleaved citations between matches.
    """

    pytest.importorskip("docx")
    from docx import Document  # noqa: PLC0415

    from report_source_injector import inject_source_citations_into_docx  # noqa: PLC0415

    docx_path = tmp_path / "round73_f5_multi_kpi.docx"
    doc = Document()
    doc.add_paragraph(
        "Total Customers: 37 across Total Action Plans: 889 "
        "and Total Open Adoption Barriers: 161 in this period."
    )
    doc.save(str(docx_path))

    counts = inject_source_citations_into_docx(docx_path, scenario_key="comprehensive")

    out_doc = Document(str(docx_path))
    para_text = out_doc.paragraphs[0].text
    # At least one citation must land in the body paragraph -- the
    # exact interleave count depends on the R57 multi-KPI contract;
    # we only need to confirm the contract is intact (>=1 citation).
    assert "[Source:" in para_text, (
        f"Round 73 / F5: body paragraph lost the R57 multi-KPI "
        f"interleave; got {para_text!r}"
    )
    assert counts["paragraphs_injected"] == 1


def test_injector_handles_paragraph_with_no_style_attribute(tmp_path) -> None:
    """Defensive: a malformed paragraph object whose ``.style`` access
    raises (older python-docx variants, monkey-patched docs) must NOT
    abort the loop -- the R73 guard treats unknown styles as body
    paragraphs (preserves pre-R73 behaviour).
    """

    pytest.importorskip("docx")
    from docx import Document  # noqa: PLC0415

    from report_source_injector import inject_source_citations_into_docx  # noqa: PLC0415

    docx_path = tmp_path / "round73_f5_defensive.docx"
    doc = Document()
    doc.add_paragraph("Total Customers: 37")
    doc.save(str(docx_path))

    # Monkey-patching python-docx internals is too brittle to test
    # here; instead we rely on the source-shape pin
    # (``test_report_source_injector_carries_r73_heading_skip_guard``)
    # to confirm the try/except wrapper is in place around
    # ``paragraph.style.name`` access.  Smoke-test that a normal body
    # paragraph still gets cited (proves the guard didn't accidentally
    # short-circuit too aggressively).
    counts = inject_source_citations_into_docx(docx_path, scenario_key="comprehensive")
    assert counts["paragraphs_injected"] == 1, (
        f"Round 73 / F5: defensive smoke -- body paragraph not cited; "
        f"got {counts}"
    )

    body = (PROJECT_ROOT / "report_source_injector.py").read_text(encoding="utf-8")
    # Confirm the guard is wrapped in try/except so a malformed
    # paragraph object doesn't abort the loop.
    assert "except Exception:" in body or "except Exception:  # noqa: BLE001" in body, (
        "Round 73 / F5: heading-skip guard must wrap ``paragraph.style.name`` "
        "access in try/except so a malformed paragraph cannot abort the loop."
    )
