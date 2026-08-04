"""Round 15 / Phase 3 -- Word visual polish helpers.

Single source of truth for the Word-side report polish:

* heading-level discipline (``# = H2`` document body, ``## = H3``,
  ``### = H4``; reserve H1 for the document title only),
* color-banded *risk callouts* (single-cell tables with the risk
  band's canonical fill color),
* banded-row top-N tables (bold header row, alternating fill),
* an *executive summary* table at the top of the document that
  surfaces the same KPI rows as the Round-15 Excel ``Summary``
  sheet (so the workbook + docx tell the same story).

Design contract
---------------

* Every helper is *defensive*. Visual polish must never fail the
  Word write -- if anything raises, the un-polished document still
  ships.
* No magic numbers / colors.  Risk-band fills resolve through
  ``canonical_metrics.RISK_BAND_PORTFOLIO_COLORS`` (with a sensible
  default).
* Markdown -> Word heading mapping is exposed via
  ``markdown_heading_level()`` so the ``adoptiq_backend.append_to_word_report``
  renderer and any other formatter can converge on the same
  hierarchy without each owning its own switch.
* Helpers operate on a ``python-docx`` ``Document`` and never mutate
  global module state.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Heading discipline
# ---------------------------------------------------------------------------

#: ``# Title`` -> Heading 2 (Heading 1 stays reserved for the
#: document title at the top of the file).  Caps at 4 so per-customer
#: sections never balloon back up to "everything is H1".
MARKDOWN_TO_WORD_HEADING: dict[int, int] = {
    1: 2,
    2: 3,
    3: 4,
    4: 4,
    5: 4,
}

#: Lowest heading level any markdown ``#`` should resolve to.
MIN_BODY_HEADING_LEVEL: int = 2

#: Highest heading level we cap at so deeply-nested LLM markdown
#: doesn't render as Heading 6+ (which python-docx tolerates but
#: looks chaotic in the output document).
MAX_HEADING_LEVEL: int = 4


def add_heading_on_new_page(doc: Any, text: str, level: int = 1) -> Any:
    """Add a heading that begins a new page without a blank-page paragraph.

    A standalone ``doc.add_page_break()`` creates its own paragraph.  When the
    preceding content already fills the page, Word can push that paragraph to
    the next page and then honor its break, leaving a fully blank page behind.
    ``page_break_before`` expresses the actual layout intent on the heading and
    remains safe whether the heading naturally lands at the top of a page or
    needs to be moved there.
    """

    heading = doc.add_heading(text, level=level)
    heading.paragraph_format.page_break_before = True
    return heading


def apply_document_accessibility(doc: Any) -> dict[str, int]:
    """Normalize Word heading order and repeat/identify table headers.

    Report generators use several independent formatters, and a visually valid
    document can still contain a Heading 1 -> Heading 3 jump or a styled first
    table row with no ``w:tblHeader`` semantic marker.  Apply this immediately
    before save so every Word report exposes the same assistive-technology
    structure without changing its source data or visible table contents.
    """

    changed = {"heading_levels": 0, "table_headers": 0}
    if doc is None:
        return changed
    try:
        import re  # noqa: PLC0415

        last_level = 1  # The document title is the implicit level-one root.
        for paragraph in getattr(doc, "paragraphs", ()):
            style_name = str(getattr(getattr(paragraph, "style", None), "name", ""))
            match = re.fullmatch(r"Heading\s+([1-9])", style_name, flags=re.IGNORECASE)
            if not match:
                continue
            level = int(match.group(1))
            allowed = min(level, last_level + 1)
            if allowed != level:
                try:
                    paragraph.style = f"Heading {allowed}"
                    level = allowed
                    changed["heading_levels"] += 1
                except Exception:
                    pass
            last_level = level
    except Exception as err:
        logger.debug("Round 145: Word heading accessibility pass failed (%s)", err)

    try:
        from docx.oxml import OxmlElement  # noqa: PLC0415
        from docx.oxml.ns import qn  # noqa: PLC0415

        for table in getattr(doc, "tables", ()):
            if not getattr(table, "rows", None):
                continue
            properties = table.rows[0]._tr.get_or_add_trPr()
            if properties.find(qn("w:tblHeader")) is None:
                properties.append(OxmlElement("w:tblHeader"))
                changed["table_headers"] += 1
            for row in table.rows:
                row_properties = row._tr.get_or_add_trPr()
                if row_properties.find(qn("w:cantSplit")) is None:
                    row_properties.append(OxmlElement("w:cantSplit"))
    except Exception as err:
        logger.debug("Round 145: Word table accessibility pass failed (%s)", err)
    return changed


def markdown_heading_level(hash_count: int) -> int:
    """Map a markdown ``#`` count to the desired Word heading level.

    Round 15 / Phase 3.1 -- before this round every ``#`` LLM
    heading dropped straight into Heading 1, producing the 38 H1
    document we shipped.  Reserve H1 for the document title and
    demote everything below it consistently.
    """

    try:
        n = int(hash_count)
    except Exception:
        n = 1
    if n < 1:
        n = 1
    if n > 5:
        n = 5
    return MARKDOWN_TO_WORD_HEADING.get(n, MAX_HEADING_LEVEL)


# ---------------------------------------------------------------------------
# Color resolution -- anchored to canonical_metrics, never magic numbers
# ---------------------------------------------------------------------------


def _strip_hex(color: str) -> str:
    """Return a 6-char hex string without leading ``#``.

    Defensive: returns a neutral grey if the input is missing or
    malformed so callers never have to ``try/except`` around shading.
    """

    try:
        s = str(color or "").strip()
        if s.startswith("#"):
            s = s[1:]
        if len(s) == 8:  # accept ARGB by stripping alpha
            s = s[2:]
        if len(s) != 6:
            return "BFBFBF"
        int(s, 16)
        return s.upper()
    except Exception:
        return "BFBFBF"


def risk_band_fill_color(band: Optional[str]) -> str:
    """Resolve a risk band label to its canonical fill hex.

    Pulls from ``canonical_metrics.RISK_BAND_PORTFOLIO_COLORS`` so
    Word + Excel + matplotlib all show the same color for the same
    band.  Falls back to a neutral grey for an unknown / missing
    band so an unexpected label never crashes the docx write.
    """

    try:
        import canonical_metrics as cm  # noqa: PLC0415 -- lazy keeps test imports light
    except Exception as err:
        logger.debug("Round 15 / Phase 3.2: canonical_metrics unavailable for risk fill (%s)", err)
        return "BFBFBF"

    if not band:
        return _strip_hex(getattr(cm, "RISK_BAND_COLOR_DEFAULT", "#BFBFBF"))
    key = str(band).strip()
    palette = getattr(cm, "RISK_BAND_PORTFOLIO_COLORS", {}) or {}
    if key in palette:
        return _strip_hex(palette[key])
    upper = key.upper()
    band_palette = getattr(cm, "RISK_BAND_COLORS", {}) or {}
    if upper in band_palette:
        return _strip_hex(band_palette[upper])
    return _strip_hex(getattr(cm, "RISK_BAND_COLOR_DEFAULT", "#BFBFBF"))


# Header / banding palette for top-N tables.  Cisco-blue header so it
# matches the Heading 1/2 color set in adoptiq_backend, and a soft
# grey alt-row so banded rows are visible without dominating.
HEADER_FILL_HEX: str = "0070C0"
HEADER_FONT_HEX: str = "FFFFFF"
ALT_ROW_FILL_HEX: str = "F2F2F2"


# ---------------------------------------------------------------------------
# Cell shading helper
# ---------------------------------------------------------------------------


def set_cell_shading(cell: Any, hex_color: str) -> bool:
    """Apply a solid fill to a docx cell.

    python-docx doesn't expose cell shading natively, so we splice
    the ``<w:shd>`` element into the cell's tcPr.  Returns ``True``
    on success, ``False`` if the underlying lxml call fails (the
    cell still keeps its default white fill).
    """

    if cell is None:
        return False
    try:
        from docx.oxml.ns import nsdecls, qn  # noqa: PLC0415 -- lazy
        from docx.oxml import OxmlElement  # noqa: PLC0415 -- lazy
    except Exception as err:
        logger.debug("Round 15 / Phase 3.3: docx oxml import failed (%s)", err)
        return False

    fill = _strip_hex(hex_color)
    try:
        tcPr = cell._tc.get_or_add_tcPr()
        existing = tcPr.find(qn("w:shd"))
        if existing is not None:
            tcPr.remove(existing)
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        tcPr.append(shd)
        return True
    except Exception as err:
        logger.debug("Round 15 / Phase 3.3: cell shading failed (%s)", err)
        return False


def get_cell_fill_hex(cell: Any) -> Optional[str]:
    """Return the ``w:fill`` value applied to a cell, if any.

    Used by the Phase-3 tests to verify color-banded callouts
    without re-implementing the lxml inspection in test code.
    """

    if cell is None:
        return None
    try:
        from docx.oxml.ns import qn  # noqa: PLC0415 -- lazy
    except Exception:
        return None
    try:
        tcPr = cell._tc.tcPr
        if tcPr is None:
            return None
        shd = tcPr.find(qn("w:shd"))
        if shd is None:
            return None
        fill = shd.get(qn("w:fill"))
        if not fill:
            return None
        return str(fill).upper()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Banded-row top-N tables
# ---------------------------------------------------------------------------


def _bold_runs_in_paragraph(paragraph: Any) -> None:
    try:
        for run in paragraph.runs:
            run.bold = True
    except Exception:
        pass


def _white_font_runs(paragraph: Any) -> None:
    try:
        from docx.shared import RGBColor  # noqa: PLC0415 -- lazy
    except Exception:
        return
    try:
        for run in paragraph.runs:
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            run.bold = True
    except Exception:
        pass


def add_banded_top_n_table(
    doc: Any,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    style: str = "Light Grid Accent 1",
) -> Optional[Any]:
    """Render a banded-row top-N table with a bold header row.

    Round 15 / Phase 3.4 -- replaces the ``add_paragraph(... 'List Bullet')``
    pattern previously used for "top 5 customers", "top 5 barriers"
    style summaries.  The new table:

    * has a colored header row with white bold text,
    * alternates row fill so the eye can scan a long list,
    * gracefully degrades on empty data (returns ``None``).

    Returns the created ``Table`` or ``None`` if creation failed
    (e.g. ``doc`` doesn't support ``add_table``).
    """

    if doc is None:
        return None
    headers = list(headers or [])
    rows = [list(r) for r in (rows or [])]
    if not headers:
        return None
    n_cols = len(headers)
    n_rows = len(rows) + 1
    try:
        table = doc.add_table(rows=n_rows, cols=n_cols)
    except Exception as err:
        logger.debug("Round 15 / Phase 3.4: add_table failed (%s)", err)
        return None
    try:
        table.style = style
    except Exception:
        pass

    # Header row.
    try:
        header_cells = table.rows[0].cells
        for idx, label in enumerate(headers):
            cell = header_cells[idx]
            cell.text = str(label)
            set_cell_shading(cell, HEADER_FILL_HEX)
            for paragraph in cell.paragraphs:
                _white_font_runs(paragraph)
    except Exception as err:
        logger.debug("Round 15 / Phase 3.4: header rendering failed (%s)", err)

    # Keep every logical row intact and repeat the header if a long table must
    # continue on another page.  Word's defaults may split a row between pages
    # and omit the column labels, which makes evidence tables hard to interpret.
    try:
        from docx.oxml import OxmlElement  # noqa: PLC0415 -- lazy

        header_properties = table.rows[0]._tr.get_or_add_trPr()
        header_properties.append(OxmlElement("w:tblHeader"))
        for table_row in table.rows:
            row_properties = table_row._tr.get_or_add_trPr()
            row_properties.append(OxmlElement("w:cantSplit"))
    except Exception as err:
        logger.debug("Round 142: table pagination controls failed (%s)", err)

    # Data rows with alternating fill.
    for r_idx, row_values in enumerate(rows):
        try:
            tr_cells = table.rows[r_idx + 1].cells
        except Exception:
            continue
        is_alt = (r_idx % 2 == 1)
        for c_idx in range(n_cols):
            try:
                cell = tr_cells[c_idx]
            except Exception:
                continue
            value = row_values[c_idx] if c_idx < len(row_values) else ""
            try:
                cell.text = "" if value is None else str(value)
            except Exception:
                cell.text = ""
            if is_alt:
                set_cell_shading(cell, ALT_ROW_FILL_HEX)

    return table


# ---------------------------------------------------------------------------
# Risk callouts -- single-cell colored tables
# ---------------------------------------------------------------------------


def add_risk_callout(
    doc: Any,
    title: str,
    risk_band: Optional[str],
    body_text: Optional[str] = None,
) -> Optional[Any]:
    """Add a color-banded single-cell table that flags a risk.

    Round 15 / Phase 3.5 -- the gold-standard docx surfaced "Top
    Critical Risk" / "Recommended Action" as plain bold paragraphs,
    which scan as body text instead of as call-outs.  This helper
    builds a single-cell table whose fill matches the canonical
    risk-band color so a reader can see at a glance whether a
    section is critical / high / medium / etc.

    Returns the created ``Table`` or ``None`` if creation failed.
    """

    if doc is None:
        return None
    fill_hex = risk_band_fill_color(risk_band)
    try:
        table = doc.add_table(rows=1, cols=1)
    except Exception as err:
        logger.debug("Round 15 / Phase 3.5: callout add_table failed (%s)", err)
        return None
    try:
        table.style = "Light Grid Accent 1"
    except Exception:
        pass
    try:
        cell = table.rows[0].cells[0]
    except Exception:
        return table
    set_cell_shading(cell, fill_hex)

    title_text = str(title or "").strip()
    body = str(body_text or "").strip()
    try:
        cell.text = ""
        title_para = cell.paragraphs[0]
        if title_text:
            run = title_para.add_run(title_text)
            run.bold = True
            try:
                from docx.shared import Pt  # noqa: PLC0415 -- lazy
                run.font.size = Pt(12)
            except Exception:
                pass
        if body:
            body_para = cell.add_paragraph(body)
            try:
                from docx.shared import Pt  # noqa: PLC0415 -- lazy
                if body_para.runs:
                    body_para.runs[0].font.size = Pt(11)
            except Exception:
                pass
    except Exception as err:
        logger.debug("Round 15 / Phase 3.5: callout text rendering failed (%s)", err)
    return table


# ---------------------------------------------------------------------------
# Executive summary table -- mirrors the Excel Summary sheet KPIs
# ---------------------------------------------------------------------------


def _sanitize_kpi_pair(label: Any, value: Any) -> tuple[str, str]:
    """Render (label, value) as plain strings safe for a Word cell."""

    try:
        l = str(label or "").strip()
    except Exception:
        l = ""
    try:
        v = str(value if value is not None else "--")
    except Exception:
        v = "--"
    return l, v


def add_executive_summary_table(
    doc: Any,
    kpi_rows: Iterable[Sequence[Any]],
    *,
    title: Optional[str] = "Executive Summary",
    title_level: int = 2,
) -> Optional[Any]:
    """Render the Round-15 executive-summary table at the top of a docx.

    Round 15 / Phase 3.6 -- the gold docx had a single 3x4
    "Portfolio Dashboard - At-A-Glance" table buried after the
    title page, with no visual hierarchy and no parity with the
    Excel ``Summary`` tab.  This helper:

    * adds a Heading-2 ("Executive Summary") above the table so
      the section is greppable and shows up in the Word TOC,
    * renders the KPIs as a 2-column banded table (label / value),
    * accepts the same ``(label, value)`` rows that
      ``report_export_styling.build_summary_rows`` returns so the
      Word + Excel summaries agree by construction.

    Returns the created ``Table`` or ``None`` on failure.
    """

    if doc is None:
        return None
    rows = [
        _sanitize_kpi_pair(*pair)
        for pair in (kpi_rows or [])
        if isinstance(pair, (list, tuple)) and len(pair) >= 2
    ]
    rows = [(l, v) for (l, v) in rows if l]
    if not rows:
        return None

    if title:
        try:
            level = int(title_level)
        except Exception:
            level = 2
        if level < 2:
            level = 2
        if level > MAX_HEADING_LEVEL:
            level = MAX_HEADING_LEVEL
        try:
            doc.add_heading(str(title), level=level)
        except Exception as err:
            logger.debug("Round 15 / Phase 3.6: summary heading failed (%s)", err)

    return add_banded_top_n_table(
        doc,
        headers=["Metric", "Value"],
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Convenience: build summary rows for the Word side without re-importing
# the Excel-only schema in every caller.
# ---------------------------------------------------------------------------


def build_executive_summary_rows(
    sheets: Optional[Mapping[str, Any]] = None,
    csconsole_data: Optional[Mapping[str, Any]] = None,
    *,
    manager: Optional[str] = None,
    tech: Optional[str] = None,
    days: Optional[Any] = None,
    generated_at_utc_iso_z: Optional[str] = None,
) -> list[tuple[str, str]]:
    """Build the executive-summary KPI rows.

    Delegates to ``report_export_styling.build_summary_rows`` so the
    Word + Excel paths share the exact same canonical-metric calls.
    Returns an empty list (never raises) if the underlying helper is
    unavailable.
    """

    try:
        import report_export_styling as _styling  # noqa: PLC0415 -- lazy
    except Exception as err:
        logger.debug("Round 15 / Phase 3.7: report_export_styling unavailable (%s)", err)
        return []
    try:
        rows = _styling.build_summary_rows(
            sheets or {},
            csconsole_data or {},
            manager=manager,
            tech=tech,
            days=days,
            generated_at_utc_iso_z=generated_at_utc_iso_z,
        )
        return [tuple(r) for r in rows or []]
    except Exception as err:
        logger.debug("Round 15 / Phase 3.7: build_summary_rows failed (%s)", err)
        return []
