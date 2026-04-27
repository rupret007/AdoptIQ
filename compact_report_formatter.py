#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AdoptIQ Compact Report Formatter
Creates focused executive summaries highlighting high-risk customers and renewal concerns
"""

import os
import pandas as pd
import numpy as np
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import logging
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.shared import OxmlElement, qn
from risk_scoring import compute_customer_risk_profile, RISK_BAND_THRESHOLDS as _RBT_0_100
from data_normalization import (
    add_case_lifecycle_fields,
    detect_bems_mask,
    extract_bems_ids_from_row,
    normalize_customer_name,
)
from report_consistency import validate_report_consistency
from report_utils import format_inline_source, format_metric_with_source
import canonical_metrics as cm
# Round 16 / Phase 5.2: pull the banded top-N table helper from the
# Round-15 Word styling SSoT so the compact-formatter top-N tables get
# the same Cisco-blue header + alternating-row banding as the
# title-page summary table, instead of the inherited ``Table Grid``
# default.
from report_word_styling import add_banded_top_n_table as _r16_add_banded_top_n_table

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _compact_portfolio_band(score_0_10: float) -> Dict[str, str]:
    """Round 10 / Phase 2.2: single source of truth for the 0-10 portfolio
    band label rendered by both the executive summary and the executive
    takeaway sections.

    Previously ``add_executive_summary`` cut at HIGH (5.5) and MEDIUM (3.5),
    while ``add_executive_takeaway_section`` cut at CRITICAL (7.5) and HIGH
    (5.5). The same ``overall_risk_score`` therefore produced opposite tier
    labels in the same DOCX (e.g. 6.0 was "HIGH" in the summary and
    "MODERATE" in the takeaway). Anchor both surfaces to the canonical
    HIGH / MEDIUM cuts so they always agree.
    """
    try:
        score = float(score_0_10)
    except (TypeError, ValueError):
        score = 0.0

    try:
        high_cut = float(_RBT_0_100["HIGH"]) / 10.0     # 5.5
        med_cut = float(_RBT_0_100["MEDIUM"]) / 10.0    # 3.5
    except Exception as _err:
        logger.error(
            "_compact_portfolio_band: RISK_BAND_THRESHOLDS unavailable (%s); "
            "refusing to render with stale literal cuts.", _err,
        )
        raise RuntimeError(
            "_compact_portfolio_band requires canonical RISK_BAND_THRESHOLDS"
        ) from _err

    if score >= high_cut:
        return {
            'tier': 'HIGH',
            'label': f'🔴 HIGH RISK - {score}/10',
            'summary_message': '⚠️ Immediate executive attention required. Multiple critical issues identified.',
            'takeaway_lead': 'Portfolio is in RED ZONE. ',
            'takeaway_body': 'Adoption is blocked by operational, integration, and defect bottlenecks causing unresolved high-impact issues. ',
            'takeaway_action': 'Action is required NOW to prevent critical escalations and protect revenue.',
        }
    if score >= med_cut:
        return {
            'tier': 'MODERATE',
            'label': f'🟡 MODERATE RISK - {score}/10',
            'summary_message': '📋 Proactive monitoring recommended. Some areas require attention.',
            'takeaway_lead': 'Portfolio is at MODERATE RISK. ',
            'takeaway_body': 'Several customers require proactive intervention to prevent escalation. ',
            'takeaway_action': 'Focused attention on at-risk accounts recommended.',
        }
    return {
        'tier': 'LOW',
        'label': f'🟢 LOW RISK - {score}/10',
        'summary_message': '✅ Portfolio performing well. Continue current initiatives.',
        'takeaway_lead': 'Portfolio is STABLE. ',
        'takeaway_body': 'Continue standard engagement cadence with regular monitoring. ',
        'takeaway_action': 'Address any emerging issues proactively.',
    }


def _safe_doc_text(value: Any, max_len: int = 200) -> str:
    """Round 6 / Phase 1.4: sanitize a string before sending it to
    ``doc.add_heading`` / ``add_run``.

    docx's XML serializer rejects control codes (anything in 0x00-0x08,
    0x0B, 0x0C, 0x0E-0x1F) and silently drops surrogate halves; some
    customer / manager strings flowing in from CSOne or csv uploads
    have stray U+0000 (null) bytes or runaway lengths that blow out the
    Word "heading" style and trip Office's repair dialog.  Coerce to
    text, strip illegal codepoints, collapse whitespace, and cap to a
    sane length so the doc never emits a malformed heading.
    """
    try:
        s = "" if value is None else str(value)
    except Exception:
        return ""
    # Drop XML-illegal control chars (keep \t \n \r)
    cleaned = []
    for ch in s:
        cp = ord(ch)
        if cp < 0x20 and ch not in ("\t", "\n", "\r"):
            continue
        if 0xD800 <= cp <= 0xDFFF:
            continue
        cleaned.append(ch)
    s = "".join(cleaned)
    s = re.sub(r"\s+", " ", s).strip()
    if max_len and len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


# Round 8 / Phase 3.9: lift the brand-color hex constants to a
# single shared namespace so the same values are referenced by every
# call site.  Previously the same Cisco blue was spelled three
# different ways (``RGBColor(0x00, 0x7B, 0xC7)``, the string
# ``'007BC7'`` for shading, and ``RGBColor(0, 123, 199)`` in legacy
# code), which made a brand-refresh a manual hunt.  Drop ``Hex``
# (string form for ``w:fill``) and ``RGB`` (tuple form for
# python-docx ``RGBColor``) variants on each constant.
class _ThemeColors:
    CISCO_BLUE_HEX = '007BC7'
    CISCO_BLUE_RGB = (0x00, 0x7B, 0xC7)
    CISCO_GRAY_HEX = '58595B'
    CISCO_GRAY_RGB = (0x58, 0x59, 0x5B)
    # Round 13 / Phase 5.5: ``CRIMSON_HEX`` was historically the W3C
    # named-colour ``crimson`` (``#DC143C`` / RGB(220,20,60)), which
    # is NOT the canonical CRITICAL hex used by every matplotlib
    # chart, Excel band fill, and (after Round 13 Phase 5.1)
    # leader-report BEMS heading: ``RISK_BAND_COLORS["CRITICAL"]``
    # is ``#d62728`` / RGB(214,39,40).  Rendering Critical-P1 and
    # BEMS counts in compact-Word in ``crimson`` while the chart
    # pages and EI Word output rendered the same semantic in
    # ``#d62728`` made the two reds look subtly different on the
    # same page (notably distracting on tone-matched displays).
    # Resolve the constant from ``canonical_metrics`` at import-time
    # so any future palette tweak lands in exactly one place; fall
    # back to the canonical hex as RGB if the import fails.
    try:
        from canonical_metrics import RISK_BAND_COLORS as _R13_CRF_RBC
        _r13_crit_hex = (_R13_CRF_RBC.get('CRITICAL', '#d62728') or '#d62728').lstrip('#').upper()
        if len(_r13_crit_hex) != 6:
            _r13_crit_hex = 'D62728'
        CRIMSON_HEX = _r13_crit_hex
        CRIMSON_RGB = (
            int(_r13_crit_hex[0:2], 16),
            int(_r13_crit_hex[2:4], 16),
            int(_r13_crit_hex[4:6], 16),
        )
    except Exception:
        CRIMSON_HEX = 'D62728'
        CRIMSON_RGB = (0xD6, 0x27, 0x28)
    WHITE_HEX = 'FFFFFF'
    WHITE_RGB = (255, 255, 255)


def _safe_add_run(paragraph, text: Any, *, max_len: int = 5000):
    """Round 8 / Phase 3.1: thin wrapper around ``paragraph.add_run`` that
    routes the supplied text through ``_safe_doc_text`` first.

    ``python-docx`` will raise (or worse, silently emit a malformed
    XML chunk that opens "Repair this document" in Word) when given a
    raw control code (NUL/U+0000-U+001F minus the printable
    whitespace) or an unpaired surrogate half.  Backend-derived
    strings (customer names from CSOne, manager free-text, raw issue
    descriptions from Snowflake, etc.) routinely carry these.

    Use the larger ``max_len`` here than in headings so that body
    text such as long defect descriptions is not silently truncated
    to 200 chars.  Callers that need a different cap can pass
    ``max_len=`` explicitly.
    """
    return paragraph.add_run(_safe_doc_text(text, max_len=max_len))


def _safe_cell_text(cell, text: Any, *, max_len: int = 5000) -> None:
    """Round 8 / Phase 3.1: companion to ``_safe_add_run`` for
    ``table cell.text = ...`` assignments, which share the same
    illegal-control-code / surrogate-half failure modes.
    """
    cell.text = _safe_doc_text(text, max_len=max_len)


def _ensure_inline_source_claim(
    text: Any,
    metric_name: str = "Derived Metric",
    fields: Optional[List[str]] = None,
) -> str:
    claim = str(text or "").strip()
    if not claim:
        return ""
    if re.search(r"\[\s*source\s*:", claim, flags=re.IGNORECASE):
        return claim
    return f"{claim} {format_inline_source(metric_name, fields=fields or [])}"

class CompactReportFormatter:
    """Creates compact executive reports focused on renewal risk and critical issues"""
    
    def __init__(self):
        self.doc = Document()
        self.setup_document_styles()
    
    def _add_section_separator(self):
        """Add a visual separator line between major sections for better readability"""
        from docx.shared import RGBColor
        self.doc.add_paragraph()  # Spacing before
        separator_para = self.doc.add_paragraph()
        separator_run = separator_para.add_run("_" * 100)
        # Round 8 / Phase 3.9: shared brand color.
        separator_run.font.color.rgb = RGBColor(*_ThemeColors.CISCO_GRAY_RGB)
        separator_run.font.size = Pt(10)
        separator_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self.doc.add_paragraph()  # Spacing after
    
    def setup_document_styles(self):
        """Setup document styles for professional appearance"""
        try:
            # Set default font
            style = self.doc.styles['Normal']
            font = style.font
            font.name = 'Calibri'
            font.size = Pt(11)
            style.paragraph_format.line_spacing = 1.15
            style.paragraph_format.space_after = Pt(6)
            
            # Create custom styles
            self._create_custom_styles()
            
        except Exception as e:
            logger.warning(f"Could not setup custom styles: {e}")
    
    def _create_custom_styles(self):
        """Create custom styles for the document"""
        try:
            # Round 6 / Phase 1.17: ``CompactTitle`` and
            # ``CompactSubtitle`` were registered but never applied --
            # the title page uses ``add_heading(..., 0)`` /
            # ``add_heading(..., level=1)`` instead, so the bespoke
            # styles only inflated the docx style table.  Removed to
            # avoid carrying dead styles that imply they're in use.

            # Header style
            header_style = self.doc.styles.add_style('CompactHeader', 1)
            header_font = header_style.font
            header_font.name = 'Calibri'
            header_font.size = Pt(14)
            header_font.bold = True
            header_font.color.rgb = None  # Dark blue
            header_style.paragraph_format.space_before = Pt(12)
            header_style.paragraph_format.space_after = Pt(6)
            
            # Risk style
            risk_style = self.doc.styles.add_style('RiskText', 1)
            risk_font = risk_style.font
            risk_font.name = 'Calibri'
            risk_font.size = Pt(11)
            risk_font.color.rgb = None  # Red for warnings
            
            # Highlight style for important information
            highlight_style = self.doc.styles.add_style('CompactHighlight', 1)
            highlight_font = highlight_style.font
            highlight_font.name = 'Calibri'
            highlight_font.size = Pt(11)
            highlight_font.bold = True
            highlight_font.color.rgb = None  # Red for warnings
            highlight_style.paragraph_format.space_after = Pt(6)
            
            # Callout style for key insights
            callout_style = self.doc.styles.add_style('CompactCallout', 1)
            callout_font = callout_style.font
            callout_font.name = 'Calibri'
            callout_font.size = Pt(11)
            callout_font.italic = True
            callout_style.paragraph_format.left_indent = Inches(0.5)
            callout_style.paragraph_format.space_after = Pt(6)
            
            # Metric style for numbers
            metric_style = self.doc.styles.add_style('CompactMetric', 1)
            metric_font = metric_style.font
            metric_font.name = 'Calibri'
            metric_font.size = Pt(12)
            metric_font.bold = True
            metric_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
        except Exception as e:
            logger.warning(f"Could not create custom styles: {e}")
    
    def create_compact_title_page(self, manager: str, technology: str, days: int, 
                                 analysis_id: str, risk_summary: Dict,
                                 data_retrieved_at: Optional[datetime] = None):
        """Create compact title page with key metrics.

        Phase 3.1: ``data_retrieved_at`` (UTC) is the timestamp at which
        the underlying data was fetched from Snowflake / CSConsole. When
        provided, the title page renders both ``Generated`` (render time)
        and ``Data as of`` so readers can tell whether stale numbers are
        the AI's fault or the source's fault.
        """
        try:
            # Title
            title = self.doc.add_heading('AdoptIQ Compact Executive Report', 0)
            title.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Subtitle
            subtitle = self.doc.add_heading(
                _safe_doc_text(f'Renewal Risk Analysis - {manager or "N/A"}'),
                level=1,
            )
            subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            details = self.doc.add_paragraph()
            details.add_run(f'Technology: {technology or "N/A"}\n').bold = True
            details.add_run(f'Analysis Period: {days or "N/A"} days\n').bold = True
            # Round 8 / Phase 3.3: pin "Generated" to UTC so two
            # readers in different time zones see the same timestamp
            # for the same report.  Previously this used naive
            # local time, which made it ambiguous when reports
            # crossed time zones in email/screenshots.
            _gen_utc = datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M %p UTC")
            _safe_add_run(details, f'Generated: {_gen_utc}\n').bold = True
            if data_retrieved_at is not None:
                try:
                    _data_str = data_retrieved_at.strftime("%B %d, %Y at %I:%M %p UTC")
                except Exception:
                    _data_str = str(data_retrieved_at)
                details.add_run(f'Data as of: {_data_str}\n').bold = True
            details.add_run(f'Analysis ID: {analysis_id}').bold = True
            details.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Key metrics box
            self.doc.add_paragraph()
            metrics_p = self.doc.add_paragraph()
            metrics_p.add_run('KEY METRICS').bold = True
            metrics_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Create metrics table
            metrics_table = self.doc.add_table(rows=2, cols=4)
            metrics_table.alignment = WD_TABLE_ALIGNMENT.CENTER
            metrics_table.style = 'Table Grid'
            
            # Header row
            header_cells = metrics_table.rows[0].cells
            header_cells[0].text = 'High Risk Customers'
            header_cells[1].text = 'Critical ABs'
            header_cells[2].text = 'Escalated Cases'
            header_cells[3].text = 'Renewal Risk Score'
            
            # Data row
            data_cells = metrics_table.rows[1].cells
            # Round 8 / Phase 3.1: route through ``_safe_cell_text``
            # so any control codes / surrogate halves coming from the
            # backend (e.g. risk_summary keys derived from CSOne free
            # text) cannot crash python-docx serialization.
            _safe_cell_text(data_cells[0], str(risk_summary.get('high_risk_customers', 0)))
            _safe_cell_text(data_cells[1], str(risk_summary.get('critical_adoption_barriers', 0)))
            _safe_cell_text(data_cells[2], str(risk_summary.get('escalated_cases', 0)))
            _safe_cell_text(data_cells[3], f"{risk_summary.get('overall_risk_score', 'N/A')}/10")
            
            # Style the table with enhanced formatting
            for row_idx, row in enumerate(metrics_table.rows):
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        for run in paragraph.runs:
                            run.bold = True
                            if row_idx == 0:  # Header row
                                run.font.size = Pt(12)
                                run.font.color.rgb = None  # Dark blue
                            else:  # Data row
                                run.font.size = Pt(14)
                                # Color code the risk score
                                if (cell.text or '').endswith('/10') and isinstance(risk_summary.get('overall_risk_score'), (int, float)):
                                    score = risk_summary.get('overall_risk_score', 0)
                                    # Round 6 / Phase 5.1: derive the
                                    # high/medium cuts from the canonical
                                    # ``risk_scoring.RISK_BAND_THRESHOLDS``
                                    # (75/55/35/15 on the 0-100 scale,
                                    # ie 7.5 / 5.5 / 3.5 / 1.5 on 0-10)
                                    # so the colour band cannot drift from
                                    # the executive tile / Excel headline.
                                    _high_cut = float(_RBT_0_100["HIGH"]) / 10.0
                                    _med_cut = float(_RBT_0_100["MEDIUM"]) / 10.0
                                    if score >= _high_cut:
                                        run.font.color.rgb = None  # Red for high risk
                                    elif score >= _med_cut:
                                        run.font.color.rgb = None  # Orange for moderate risk
                                    else:
                                        run.font.color.rgb = None  # Green for low risk
            
            self.doc.add_page_break()
            
        except Exception as e:
            logger.error(f"Error creating title page: {e}")
            raise
    
    def add_at_a_glance_dashboard(
        self,
        ab_data: pd.DataFrame,
        csone_data: pd.DataFrame,
        total_customers_override: Optional[int] = None,
        *,
        extra_customer_frames: Optional[List[pd.DataFrame]] = None,
        account_to_customer: Optional[Dict[str, str]] = None,
        pulse_df: Optional[pd.DataFrame] = None,
    ):
        """Add At-a-Glance Dashboard matching the example report format.

        Round 25 / Phase A: the headline ``Total Customers`` tile now
        derives from the SAME narrow universe the Excel ``Summary`` row
        uses -- ``count_customers(ab_df=, csone_df=, pulse_df=)``.
        Previously the Compact tile widened the count via
        ``extra_frames`` (team subs, action plans, success priorities,
        csconsole adoption barriers), which inflated the headline above
        what readers can manually reconcile by counting unique customers
        across the three detail sheets the report actually displays
        (AB_Detail_All, CSOne_Detail_All, CSConsole_Customer_Pulse).
        The reference report rendered ``49`` here while Excel showed
        ``37`` for the same dataset; the headline is now the displayed
        universe so Word and Excel always agree.

        ``extra_customer_frames`` is still accepted (and threaded into
        per-customer narrative coverage downstream) but no longer
        contributes to the headline tile.  Pass ``pulse_df`` (typically
        ``csconsole_customer_pulse``) so the tile mirrors the Excel
        Summary row exactly.

        Round 6 / Phase 5.17: the Compact "at-a-glance" dashboard does
        NOT currently render a customer-pulse tile.  If a future
        revision adds one (e.g. "Positive Sentiment / Negative
        Sentiment" tiles like the EI / Leader dashboards do), it MUST
        be derived from ``cm.pulse_sentiment(customer_pulse_df,
        scale=cm.PULSE_SCALE_0_TO_10)`` so the Compact tile cannot
        disagree with the EI / Leader dashboards on the same dataset.
        Inline thresholds (e.g. ``score >= 7.5``) are forbidden here
        for the same reason -- they silently drift from
        ``PULSE_POSITIVE_THRESHOLD_0_TO_10`` /
        ``PULSE_NEGATIVE_THRESHOLD_0_TO_10`` over time.  See
        ``tests/test_leader_pulse_single_scale_per_paragraph.py`` for
        the equivalent guardrail on the leader path.
        """
        from docx.shared import RGBColor  # Import for color styling
        try:
            ab_data = ab_data if ab_data is not None else pd.DataFrame()
            csone_data = csone_data if csone_data is not None else pd.DataFrame()
            self.doc.add_heading('At-a-Glance Dashboard', level=1)

            # All five tile values are produced by canonical_metrics so
            # this dashboard always agrees with the EI / Leader / Admin
            # views and with the cross-report consistency contract.
            csone_norm = add_case_lifecycle_fields(csone_data)
            # Round 25 / Phase A: narrow customer-count universe to the
            # three sheets the report displays (AB + CSOne + Pulse).
            # ``extra_customer_frames`` and ``account_to_customer`` are
            # accepted for backward compatibility but intentionally
            # ignored for the headline count -- including them produced
            # 49 in the reference Brian Frazier / All Contact Center /
            # 90d report while the Excel ``Summary`` row showed 37 (the
            # narrow displayed universe).  Word and Excel must agree.
            _ = extra_customer_frames  # noqa: F841 -- threaded for future per-section use
            _ = account_to_customer    # noqa: F841 -- ditto
            total_customers = cm.count_customers(
                ab_df=ab_data,
                csone_df=csone_norm,
                pulse_df=pulse_df,
            )
            # Round 3 / Phase 2.9: stash the canonical total so any
            # subsequent prose section in this same run (e.g. the
            # "Key Portfolio Metrics" summary) can reuse the exact
            # same number instead of pulling a different value from
            # ``risk_summary['total_customers']``.
            try:
                self._last_total_customers = int(total_customers)
            except Exception:
                self._last_total_customers = total_customers
            # ``total_customers_override`` (typically risk_summary['total_customers'])
            # is honored only when it agrees with the canonical multi-source
            # count. If it disagrees we keep the canonical value and log a
            # warning instead of silently shadowing the SSoT — this prevents
            # the same portfolio from rendering different headline tiles in
            # Compact vs EI.
            if total_customers_override is not None:
                try:
                    _override_int = int(total_customers_override)
                except (TypeError, ValueError):
                    _override_int = total_customers
                if _override_int != total_customers:
                    # Round 8 / Phase 3.8: down-grade the
                    # "override differs from canonical" message to
                    # DEBUG when the difference is benign (the
                    # canonical multi-source count routinely picks
                    # up rows that the override scope does not),
                    # and re-raise when ``ADOPTIQ_STRICT_MODE`` is
                    # enabled so test runs catch silent drift.
                    _strict = str(os.environ.get('ADOPTIQ_STRICT_MODE', '0')).strip().lower() in ('1', 'true', 'yes')
                    if _strict:
                        raise AssertionError(
                            f"Compact total_customers_override={_override_int} "
                            f"differs from cm.count_customers={total_customers} "
                            f"(strict mode)"
                        )
                    logger.debug(
                        "[[CONSISTENCY]] Compact total_customers_override=%s differs from "
                        "cm.count_customers=%s; keeping canonical value to preserve "
                        "cross-report parity.",
                        _override_int,
                        total_customers,
                    )
                # else: override matches canonical; no-op.
            total_support_cases = cm.count_total_tac(csone_norm)
            critical_p1 = cm.count_p1(csone_norm)
            high_p2 = cm.count_p2(csone_norm)
            # Canonical (TAC-only) BEMS count. This is what the cross-report
            # consistency contract enforces.
            bems_count = cm.count_bems(csone_norm)
            
            # Create dashboard table (matches example format)
            dashboard_table = self.doc.add_table(rows=2, cols=5)
            dashboard_table.alignment = WD_TABLE_ALIGNMENT.CENTER
            dashboard_table.style = 'Table Grid'
            
            # Header row
            headers = ['Total Customers', 'Support Cases', 'Critical (P1)', 'High (P2)', 'BEMS Escalations']
            for i, header in enumerate(headers):
                cell = dashboard_table.rows[0].cells[i]
                cell.text = header
                for para in cell.paragraphs:
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in para.runs:
                        run.bold = True
                        run.font.size = Pt(11)
                # Set header background color (Cisco blue)
                shading = OxmlElement('w:shd')
                # Round 8 / Phase 3.9: shared brand color.
                shading.set(qn('w:fill'), _ThemeColors.CISCO_BLUE_HEX)
                cell._element.get_or_add_tcPr().append(shading)
                for para in cell.paragraphs:
                    for run in para.runs:
                        run.font.color.rgb = RGBColor(*_ThemeColors.WHITE_RGB)
            
            # Data row
            values = [str(total_customers), str(total_support_cases), str(critical_p1), str(high_p2), str(bems_count)]
            for i, value in enumerate(values):
                cell = dashboard_table.rows[1].cells[i]
                # Round 8 / Phase 3.1: safe text wrap.
                _safe_cell_text(cell, value)
                for para in cell.paragraphs:
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in para.runs:
                        run.bold = True
                        run.font.size = Pt(14)
                        # Color code critical metrics
                        try:
                            _v = int(value)
                        except (ValueError, TypeError):
                            _v = 0
                        if i == 2 and _v > 0:  # Critical P1
                            run.font.color.rgb = RGBColor(*_ThemeColors.CRIMSON_RGB)
                        elif i == 4 and _v > 0:  # BEMS
                            run.font.color.rgb = RGBColor(*_ThemeColors.CRIMSON_RGB)
            
            # Inline source-backed facts for each metric
            fact_p = self.doc.add_paragraph()
            fact_p.add_run("Metric source backing:\n").bold = True
            metric_facts = [
                format_metric_with_source(
                    "Total Customers",
                    total_customers,
                    "Derived Metric",
                    fields=["BU_NAME", "customer_name", "ACCOUNT_ID_C"],
                    source_override="Normalized customer set from team subscriptions + CSConsole + CSOne",
                    verification_override="Cross-check customer IDs/names in source exports",
                ),
                format_metric_with_source("Support Cases", total_support_cases, "Support Cases (TAC)", fields=["Case #", "Status"]),
                format_metric_with_source("Critical (P1)", critical_p1, "Support Cases (TAC)", fields=["Severity"]),
                format_metric_with_source("High (P2)", high_p2, "Support Cases (TAC)", fields=["Severity"]),
                format_metric_with_source("BEMS Escalations", bems_count, "BEMS Escalations", fields=["Transaction ID", "bemscsc_refs"]),
            ]
            for metric_fact in metric_facts:
                bullet = self.doc.add_paragraph(style='List Bullet')
                bullet.add_run(metric_fact)

            self.doc.add_paragraph()  # Spacing
            
        except Exception as e:
            logger.error(f"Error adding At-a-Glance dashboard: {e}")
            raise
    
    def add_executive_summary(self, risk_summary: Dict, ai_insights: Dict):
        """Add enhanced executive summary section with comprehensive analysis"""
        try:
            if risk_summary is None:
                risk_summary = {}
            if ai_insights is None:
                ai_insights = {}
            summary_heading = self.doc.add_heading('📊 Executive Summary', level=1)
            summary_heading.style = 'CompactHeader'
            
            risk_section = self.doc.add_paragraph()
            risk_section.add_run('🎯 Overall Portfolio Risk Assessment: ').bold = True
            risk_section.add_run('\n')
            
            raw_overall = risk_summary.get('overall_risk_score', 0)
            overall_risk = raw_overall if isinstance(raw_overall, (int, float)) else 0
            risk_level_p = self.doc.add_paragraph()
            risk_level_p.style = 'CompactMetric'
            
            # Round 6 / Phase 5.1 + Round 10 / Phase 2.2: drive the band
            # label from the shared ``_compact_portfolio_band`` helper so
            # the executive summary and the executive takeaway can never
            # disagree about the tier for the same ``overall_risk_score``.
            _band = _compact_portfolio_band(overall_risk)
            risk_level_p.add_run(_band['label']).bold = True
            risk_level_p.runs[-1].font.color.rgb = None
            risk_level_p.add_run('\n\n' + _band['summary_message'])
            
            # Key metrics summary
            metrics_p = self.doc.add_paragraph()
            metrics_p.add_run('📈 Key Portfolio Metrics:\n').bold = True

            # Round 3 / Phase 2.9: derive 'Total customers analyzed'
            # from the same canonical source the at-a-glance tile uses
            # (cm.count_customers via the multi-source frames stashed
            # earlier in this run) instead of an unrelated
            # ``risk_summary.get('total_customers')`` that may be
            # populated only from the risk_profiles dict and skip
            # subscription-only customers. The previous code printed
            # one value here and a different one in the dashboard
            # tile -- both labeled "Total Customers".
            _prose_total = risk_summary.get('total_customers') if isinstance(risk_summary, dict) else None
            try:
                _tile_total = getattr(self, "_last_total_customers", None)
            except Exception:
                _tile_total = None
            if _tile_total is not None:
                if _prose_total is not None:
                    try:
                        if int(_prose_total) != int(_tile_total):
                            logger.warning(
                                "[[CONSISTENCY]] Compact prose total_customers=%s "
                                "differs from at-a-glance tile total_customers=%s; "
                                "using tile value to keep prose and tile in agreement.",
                                _prose_total,
                                _tile_total,
                            )
                    except (TypeError, ValueError):
                        pass
                _prose_total = _tile_total
            if _prose_total is None:
                _prose_total = 'N/A'
            metrics_data = [
                f"• High-risk customers: {risk_summary.get('high_risk_customers', 0)}",
                f"• Critical adoption barriers: {risk_summary.get('critical_adoption_barriers', 0)}",
                f"• Escalated support cases: {risk_summary.get('escalated_cases', 0)}",
                f"• Total customers analyzed: {_prose_total}"
            ]
            
            for metric in metrics_data:
                metrics_p.add_run(f'{metric}\n')
            
            # Key concerns with enhanced formatting
            concerns_p = self.doc.add_paragraph()
            concerns_p.add_run('🚨 Critical Renewal Concerns:\n').bold = True
            
            concerns = risk_summary.get('key_concerns', [])
            if concerns:
                # FIXED: Show ALL concerns
                for i, concern in enumerate(concerns, 1):
                    concerns_p.add_run(
                        f"{i}. {_ensure_inline_source_claim(concern, 'Derived Metric', fields=['customer_name', 'ACCOUNT_ID_C'])}\n"
                    )
            else:
                concerns_p.add_run('✅ No critical concerns identified in this analysis period.\n')
            
            # Immediate actions with priority indicators
            actions_p = self.doc.add_paragraph()
            actions_p.add_run('⚡ Immediate Actions Required:\n').bold = True
            
            actions = risk_summary.get('immediate_actions', [])
            if actions:
                # FIXED: Show ALL actions with appropriate priority
                for i, action in enumerate(actions, 1):
                    priority = "🔴 HIGH" if i <= 2 else "🟡 MEDIUM" if i <= 4 else "🟢 LOW"
                    actions_p.add_run(
                        f"{priority} Priority: {_ensure_inline_source_claim(action, 'Derived Metric', fields=['risk_score', 'customer_name'])}\n"
                    )
            else:
                actions_p.add_run('📋 Continue monitoring current initiatives and maintain regular check-ins.\n')
            
            # AI insights summary with enhanced formatting.
            # Phase 1.6: detect the non-AI fallback marker so we don't label
            # rule-based output as AI. The fallback writer prefixes its
            # bundle with "[Non-AI fallback summary]"; if we see it (or the
            # text is empty), we render under a "Non-AI fallback summary"
            # heading and include the LLM error string when available.
            if ai_insights:
                # Round 6 / Phase 3.13: render only whitelisted keys.
                # Previously the ``else`` branch fell back to
                # ``str(ai_insights)`` for any dict shape we did not
                # explicitly recognise, which could leak debug fields,
                # raw LLM payload metadata, or partially-failed
                # dictionaries into the executive summary.  Restrict
                # the dict shape to the two documented keys
                # (``portfolio_summary.executive_summary`` and the
                # top-level ``executive_summary``) and otherwise emit
                # an empty string so the fallback rendering path
                # kicks in cleanly.
                ai_summary = ""
                if isinstance(ai_insights, dict):
                    _ps = ai_insights.get('portfolio_summary') if 'portfolio_summary' in ai_insights else None
                    if isinstance(_ps, dict):
                        _candidate = _ps.get('executive_summary')
                        if isinstance(_candidate, str):
                            ai_summary = _candidate
                    if not ai_summary and 'executive_summary' in ai_insights:
                        _candidate = ai_insights.get('executive_summary')
                        if isinstance(_candidate, str):
                            ai_summary = _candidate
                elif isinstance(ai_insights, str):
                    ai_summary = ai_insights

                _is_fallback = (
                    not ai_summary
                    or ai_summary.strip() == ""
                    or "AI analysis is currently processing" in ai_summary
                    or "[Non-AI fallback summary]" in ai_summary
                )

                ai_section = self.doc.add_paragraph()
                if _is_fallback:
                    ai_section.add_run('Non-AI fallback summary:\n').bold = True
                else:
                    ai_section.add_run('🤖 AI-Powered Analysis Summary:\n').bold = True

                if not ai_summary or ai_summary.strip() == "" or "AI analysis is currently processing" in ai_summary:
                    # Generate meaningful fallback content based on actual data
                    ai_summary = self._generate_fallback_insights(risk_summary)
                
                # FIXED: Create a callout box for AI insights - show FULL summary
                ai_callout = self.doc.add_paragraph()
                ai_callout.style = 'CompactCallout'
                ai_callout.add_run(
                    f"\"{_ensure_inline_source_claim(ai_summary, 'Derived Metric', fields=['customer_name', 'risk_score'])}\""
                )
            
            # Add visual separator
            self.doc.add_paragraph('─' * 50).alignment = WD_ALIGN_PARAGRAPH.CENTER
            
        except Exception as e:
            logger.error(f"Error adding enhanced executive summary: {e}")
            raise
    
    def add_high_risk_customers(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame, 
                                risk_data: Dict[str, Dict]):
        """Add section highlighting high-risk customers with color-coded categories"""
        try:
            self.doc.add_heading('Customer Risk Assessment - Color-Coded Categories', level=1)
            
            # Round 10 / Phase 2.3: drive the red bucket from the canonical
            # ``cm.is_high_risk_profile`` predicate so this section's row
            # count cannot disagree with the headline (which already uses
            # ``is_high_risk_profile``). Equality on ``color == 'Red'`` was
            # case-sensitive and ignored profiles carrying
            # ``risk_band='HIGH'`` / ``'CRITICAL'`` without an explicit
            # color string. Make the moderate / green / gray buckets
            # case-insensitive for the same reason and exclude any profile
            # already classified as red so a single profile can land in
            # exactly one bucket.
            def _is_red(p):
                # Round 10 / Phase 2.3 (follow-up): compact risk_data
                # carries 0-10 scale ``score`` values (and an optional
                # ``color`` short-circuit), so we MUST call the canonical
                # predicate on the 0-10 branch.  The default 0-100 path
                # ignores ``color == "Red"`` and bare ``score`` keys, so
                # without this scale hint a ``{score: 7.0, color: "Red"}``
                # profile would silently fall out of the red bucket and
                # not get rendered in the section.
                return isinstance(p, dict) and cm.is_high_risk_profile(
                    p, scale=cm.RISK_SCALE_0_TO_10
                )
            def _color_eq(p, target):
                if not isinstance(p, dict):
                    return False
                return str(p.get('color', '')).strip().lower() == target
            red_customers = {k: v for k, v in risk_data.items() if _is_red(v)}
            yellow_customers = {
                k: v for k, v in risk_data.items()
                if _color_eq(v, 'yellow') and k not in red_customers
            }
            green_customers = {
                k: v for k, v in risk_data.items()
                if _color_eq(v, 'green') and k not in red_customers and k not in yellow_customers
            }
            gray_customers = {
                k: v for k, v in risk_data.items()
                if _color_eq(v, 'gray') and k not in red_customers and k not in yellow_customers and k not in green_customers
            }
            
            if not risk_data:
                no_risk_p = self.doc.add_paragraph()
                no_risk_p.add_run('✅ No customer data available for risk assessment.').bold = True
                return
            
            # Red customers (Critical/High Risk) - FIXED: Show ALL red customers
            if red_customers:
                self.doc.add_heading('🔴 RED - Critical/High Risk Customers', level=2)
                # Round 13 / Phase 6.7: previously this sort had no
                # secondary tie-break, so customers with the same
                # risk score appeared in input-dict order which is
                # not stable across runs (especially when the
                # upstream renewal analyzer is fed in worker-pool
                # completion order).  Add a deterministic
                # case-insensitive secondary key on customer name so
                # ties break alphabetically.
                sorted_red = sorted(
                    red_customers.items(),
                    key=lambda x: (
                        -(x[1].get('score', 0) if isinstance(x[1], dict) else 0),
                        str(x[0] or '').casefold(),
                    ),
                )
                
                for customer_name, risk_info in sorted_red:  # Show ALL red customers
                    self._add_customer_risk_section(customer_name, risk_info, ab_data, csone_data, 'Red')
            
            # Yellow customers (Moderate Risk) - FIXED: Show ALL yellow customers
            if yellow_customers:
                self.doc.add_heading('🟡 YELLOW - Moderate Risk Customers', level=2)
                # Round 16 / Phase 2.1: previously sorted by score alone,
                # which left the rendering order undefined for two
                # yellow customers that shared the same risk score (e.g.
                # both at 5.5).  Mirror the ``sorted_red`` tuple-key
                # pattern so the Word output is byte-stable across runs
                # over identical input.
                sorted_yellow = sorted(
                    yellow_customers.items(),
                    key=lambda x: (
                        -(x[1].get('score', 0) if isinstance(x[1], dict) else 0),
                        str(x[0] or '').casefold(),
                    ),
                )
                
                for customer_name, risk_info in sorted_yellow:  # Show ALL yellow customers
                    self._add_customer_risk_section(customer_name, risk_info, ab_data, csone_data, 'Yellow')
            
            # Green customers (Low Risk) - FIXED: Show ALL green customers
            if green_customers:
                self.doc.add_heading('🟢 GREEN - Low Risk Customers', level=2)
                green_p = self.doc.add_paragraph()
                green_p.add_run(f'✅ {len(green_customers)} customers are classified as low risk with standard monitoring requirements.').bold = True
                # Round 6 / Phase 1.16: sort the rendered list
                # explicitly so the same risk_data dict produces the
                # same Word output regardless of dict insertion order
                # (which is not stable across Python sessions / process
                # boundaries and produced diff churn between runs).
                green_list = ', '.join(sorted(green_customers.keys(), key=lambda s: str(s).lower()))
                green_p.add_run(f'\nCustomers: {green_list}')
            
            # Gray customers (No Risk) - FIXED: Show ALL gray customers
            if gray_customers:
                self.doc.add_heading('⚫ GRAY - No Renewal Risk', level=2)
                gray_p = self.doc.add_paragraph()
                gray_p.add_run(f'✅ {len(gray_customers)} customers have minimal engagement requirements.').bold = True
                # Round 6 / Phase 1.16: see green list comment above.
                gray_list = ', '.join(sorted(gray_customers.keys(), key=lambda s: str(s).lower()))
                gray_p.add_run(f'\nCustomers: {gray_list}')
                
        except Exception as e:
            logger.error(f"Error adding high-risk customers section: {e}")
            raise
    
    def _add_customer_risk_section(self, customer_name: str, risk_info: Dict, 
                                  ab_data: pd.DataFrame, csone_data: pd.DataFrame, color: str):
        """Add detailed risk section for a customer - MATCHES EXAMPLE FORMAT"""
        import re
        try:
            # Customer header with risk level (matches example: "### 1. NATIONAL GRID PLC US – Risk: CRITICAL")
            customer_heading = self.doc.add_heading(
                _safe_doc_text(
                    f'{customer_name} – Risk: {risk_info.get("category", "Unknown").upper()}'
                ),
                level=3,
            )
            
            if ab_data is None:
                ab_data = pd.DataFrame()
            if csone_data is None:
                csone_data = pd.DataFrame()
            target_customer = normalize_customer_name(customer_name)
            ab_customer_col = next(
                (c for c in ('customer_name', 'BU_NAME', 'Customer Name', 'CUSTOMER_NAME') if c in ab_data.columns),
                None,
            )
            csone_customer_col = next(
                (c for c in ('customer_name', 'BU_NAME', 'Customer Name', 'CUSTOMER_NAME') if c in csone_data.columns),
                None,
            )
            customer_ab = (
                ab_data[ab_data[ab_customer_col].fillna('').astype(str).apply(normalize_customer_name) == target_customer]
                if (not ab_data.empty and ab_customer_col)
                else pd.DataFrame()
            )
            customer_csone = (
                csone_data[csone_data[csone_customer_col].fillna('').astype(str).apply(normalize_customer_name) == target_customer]
                if (not csone_data.empty and csone_customer_col)
                else pd.DataFrame()
            )
            
            # Extract BEMS IDs and count from customer cases.
            # Round 2 / Phase 3.4: ``bems_count`` was previously
            # ``len(unique BEMS IDs)`` while every other surface (EI,
            # Leader, risk_scoring) counts BEMS rows via
            # ``cm.count_bems`` / ``detect_bems_mask``.  We retain the
            # unique-id list for the parenthetical citation but replace
            # the headline number with the canonical TAC-row BEMS count
            # so this paragraph cannot disagree with the dashboard.
            bems_ids = set()
            bems_unique_id_count = 0
            bems_count = 0
            if not customer_csone.empty:
                for _, row in customer_csone.iterrows():
                    bems_ids.update(extract_bems_ids_from_row(row))
                bems_unique_id_count = len(bems_ids)
                try:
                    bems_count = int(cm.count_bems(customer_csone))
                except Exception:
                    bems_count = bems_unique_id_count
            
            # PROBLEMS section (matches example format)
            problems_p = self.doc.add_paragraph()
            problems_p.add_run('- Problems: ').bold = True
            # Generate problems from risk factors and barrier subjects
            problems_list = []
            if risk_info.get('risk_factors'):
                problems_list.extend(risk_info['risk_factors'])  # FIXED: Show all risk factors
            if not customer_ab.empty:
                for _, ab in customer_ab.iterrows():  # FIXED: Include all adoption barriers
                    subject = ab.get('SUBJECT_C', '')
                    if subject and subject not in problems_list:
                        problems_list.append(subject)
            problems_p.add_run('; '.join(problems_list) if problems_list else 'Multiple technical/operational issues identified')  # FIXED: Show all
            
            # BARRIERS section with BEMS IDs (matches example format)
            barriers_p = self.doc.add_paragraph()
            barriers_p.add_run('- Barriers: ').bold = True
            barriers_text_parts = []
            if bems_count > 0 or bems_unique_id_count > 0:
                # FIXED: Show all BEMS IDs for full verification
                bems_id_examples = ', '.join([f'[{bid}]' for bid in sorted(list(bems_ids))])
                # Round 2 / Phase 3.4: headline = canonical row count;
                # ``bems_unique_id_count`` is shown in the parenthetical
                # so users can reconcile both numbers.
                if bems_unique_id_count and bems_unique_id_count != bems_count:
                    barriers_text_parts.append(
                        f'{bems_count} BEMS escalations '
                        f'({bems_unique_id_count} unique IDs: {bems_id_examples})'
                    )
                else:
                    barriers_text_parts.append(
                        f'{bems_count} BEMS escalations ({bems_id_examples})'
                    )
            # Round 3 hardening: the previous label "{N} open TAC cases" used
            # ``len(customer_csone)`` which counted ALL TAC rows (including
            # closed) and disagreed with the leader/EI dashboards' canonical
            # open-TAC count. Use ``cm.count_open_tac`` so this reconciles
            # with every other report.
            # Phase 3.3: do not silently substitute ``len(customer_csone)``
            # for the canonical open-TAC count when normalization fails.
            # Previously this inflated "open TAC" by also counting closed
            # rows. Surface "TAC count unavailable" instead and log the
            # cause so operators can fix the upstream normalization.
            _tac_counts_ok = True
            try:
                _open_tac = int(cm.count_open_tac(customer_csone))
                _total_tac = int(cm.count_total_tac(customer_csone))
            except Exception as _tac_exc:
                logger.warning(
                    "TAC count unavailable for customer; canonical_metrics raised: %s",
                    _tac_exc,
                )
                _tac_counts_ok = False
                _open_tac = 0
                _total_tac = 0
            if _tac_counts_ok:
                barriers_text_parts.append(f'{_open_tac} open TAC cases (of {_total_tac} total)')
            else:
                barriers_text_parts.append('TAC count unavailable (normalization failed)')
            barriers_text_parts.append(f'{len(customer_ab)} adoption barriers')
            barriers_p.add_run(', '.join(barriers_text_parts))
            
            # DEFECTS section (matches example format)
            defects_p = self.doc.add_paragraph()
            defects_p.add_run('- Defects: ').bold = True
            # Extract CSC defect IDs from case titles/descriptions
            defect_ids = set()
            if not customer_csone.empty:
                for _, case in customer_csone.iterrows():
                    for col in ['Title', 'Problem Description', 'Description']:
                        if col in case and pd.notna(case[col]):
                            found = re.findall(r'CSC[A-Z]{2}\d+', str(case[col]), re.IGNORECASE)
                            defect_ids.update([d.upper() for d in found])
            if defect_ids:
                # FIXED: Show all defect IDs for complete verification
                defects_p.add_run(', '.join([f'[{d}]' for d in sorted(list(defect_ids))]))
            else:
                defects_p.add_run('Pending defect correlation analysis')
            
            # IMPACT section (matches example format)
            impact_p = self.doc.add_paragraph()
            impact_p.add_run('- Impact: ').bold = True
            score = risk_info.get("score", 0)
            score_str = f"{score:.1f}" if isinstance(score, (int, float)) else "N/A"
            impact_text = f'Risk score {score_str}/10 - '
            if color == 'Red':
                impact_text += 'High renewal risk, potential customer churn, escalation to executive level likely'
            elif color == 'Yellow':
                impact_text += 'Moderate risk, requires proactive engagement to prevent escalation'
            else:
                impact_text += 'Standard monitoring and engagement recommended'
            impact_p.add_run(impact_text)
            
            # ACTION section with timeframe (matches example format)
            action_p = self.doc.add_paragraph()
            action_p.add_run('- Action: ').bold = True
            if color == 'Red':
                action_p.add_run('Immediate executive engagement and joint troubleshooting session (within 7 days)')
            elif color == 'Yellow':
                action_p.add_run('Schedule proactive check-in and review open cases (within 14 days)')
            else:
                action_p.add_run('Continue standard engagement cadence')
            
            self.doc.add_paragraph()  # Spacing between customers
            
        except Exception as e:
            logger.error(f"Error adding high-risk customers: {e}")
            raise
    
    def add_critical_adoption_barriers(self, ab_data: pd.DataFrame):
        """Add section for critical adoption barriers"""
        try:
            self.doc.add_heading('Critical Adoption Barriers Requiring Action', level=1)
            
            if ab_data is None or ab_data.empty:
                no_ab_p = self.doc.add_paragraph()
                no_ab_p.add_run('✅ No critical adoption barriers identified in this analysis period.').bold = True
                return
            
            # Round 3 hardening: route severity through canonical
            # ``normalize_severity_label`` so that "Critical/High" matches the
            # leader/EI definitions and ``cm.count_critical_barriers``
            # exactly. The previous ``str.contains`` heuristic over-matched
            # on labels like "Highly recurring" and "Critical-but-resolved".
            # Phase 3.3: do not silently fall back to "no critical
            # barriers" when severity normalization fails. That hid real
            # critical issues behind a green checkmark. Distinguish
            # normalization failure from a genuine zero count.
            _norm_failed = False
            _norm_error = ""
            try:
                from data_normalization import normalize_severity_label as _norm_sev_label
                if 'severity_norm' in ab_data.columns:
                    _sev_series = ab_data['severity_norm'].fillna('').astype(str)
                else:
                    _sev_picked_col = next(
                        (c for c in ('SEVERITY_C', 'severity_c', 'Severity', 'PRIORITY')
                         if c in ab_data.columns),
                        None,
                    )
                    if _sev_picked_col is None:
                        _sev_series = pd.Series([], dtype=str)
                    else:
                        _sev_series = (
                            ab_data[_sev_picked_col].apply(_norm_sev_label).fillna('').astype(str)
                        )
                if _sev_series.empty:
                    critical_ab = pd.DataFrame()
                else:
                    critical_ab = ab_data[_sev_series.isin(['Critical', 'High'])]
            except Exception as _norm_exc:
                _norm_failed = True
                _norm_error = str(_norm_exc)
                logger.warning(
                    "Critical adoption barrier severity normalization failed: %s",
                    _norm_exc,
                )
                critical_ab = pd.DataFrame()

            if _norm_failed:
                err_p = self.doc.add_paragraph()
                err_p.add_run(
                    '⚠ Critical adoption barrier counts unavailable (severity normalization failed). '
                    'Treat this section as unknown, not zero.'
                ).bold = True
                if _norm_error:
                    err_p.add_run(f'\n  Reason: {_norm_error}').italic = True
                return

            if critical_ab.empty:
                no_critical_p = self.doc.add_paragraph()
                no_critical_p.add_run('✅ No critical severity adoption barriers found.').bold = True
                return
            
            cust_col = 'customer_name' if 'customer_name' in critical_ab.columns else ('BU_NAME' if 'BU_NAME' in critical_ab.columns else None)
            if not cust_col:
                return
            # Round 11 / Phase 3.2: previously grouped on the raw
            # ``customer_name`` / ``BU_NAME``, which produced
            # duplicate subheads when CSOne held two spelling
            # variants of the same account (e.g. ``"ACME, Inc."``
            # and ``"Acme Inc"``).  Group on a normalized key and
            # render the most common raw label so the bullet
            # subheads merge correctly.
            try:
                from utils import normalize_customer_name as _norm_cust
            except Exception:
                _norm_cust = lambda v: str(v) if v is not None else ''  # type: ignore
            try:
                _norm_series = critical_ab[cust_col].astype(str).map(
                    lambda v: _norm_cust(v) or v
                )
                critical_ab = critical_ab.assign(_r11_norm_cust=_norm_series)
                customer_groups = critical_ab.groupby('_r11_norm_cust')
            except Exception:
                customer_groups = critical_ab.groupby(cust_col)

            # Round 8 / Phase 3.7: iterate customer_groups in
            # case-insensitive alphabetical order so the section
            # output is reproducible across runs and the order
            # does not depend on incidental groupby() ordering.
            for _norm_key, group in sorted(customer_groups, key=lambda kv: str(kv[0] or '').lower()):
                # Render the most common raw label for the group so
                # the visible heading matches what the operator sees
                # in CSOne.  Fall back to the normalized key when
                # raw labels are missing.
                try:
                    customer_name = (
                        group[cust_col].astype(str).mode().iloc[0]
                        if cust_col in group.columns and not group[cust_col].dropna().empty
                        else _norm_key
                    )
                except Exception:
                    customer_name = _norm_key
                customer_p = self.doc.add_paragraph()
                # Round 8 / Phase 3.1: route backend-derived strings
                # (customer name, AB subject/status/assignee/description)
                # through ``_safe_add_run`` so stray control codes from
                # CSOne free text cannot crash python-docx.
                _safe_add_run(customer_p, f'{customer_name} ({len(group)} critical barriers)').bold = True
                
                # Show all barriers for this customer, not limited
                for _, ab in group.iterrows():
                    ab_p = self.doc.add_paragraph()
                    ab_id = ab.get("ID", ab.get("RECORD_ID", ""))
                    subject = ab.get("SUBJECT_C", "No title")
                    ab_p.add_run('• ')
                    if ab_id:
                        _safe_add_run(ab_p, f'[{ab_id}] ').font.size = Pt(8)
                    _safe_add_run(ab_p, f'{subject}\n')
                    _safe_add_run(ab_p, f'  Status: {ab.get("AB_STATUS_C", "Unknown")} | ')
                    _safe_add_run(ab_p, f'Severity: {ab.get("SEVERITY_C", "Unknown")} | ')
                    _safe_add_run(ab_p, f'Assignee: {ab.get("ASSIGNEE_C", "Unassigned")}\n')
                    description = ab.get("DESCRIPTION_C", ab.get("DESCRIPTION__C", ""))
                    if description:
                        _safe_add_run(ab_p, f'  Description: {description}\n').italic = True
                
                self.doc.add_paragraph()  # Spacing
            
        except Exception as e:
            logger.error(f"Error adding critical adoption barriers: {e}")
            raise
    
    def add_renewal_recommendations_detailed(self, risk_scores: Dict, 
                                  ab_data: pd.DataFrame, csone_data: pd.DataFrame):
        """Add detailed renewal-specific recommendations (alternative to add_renewal_recommendations)"""
        try:
            self.doc.add_heading('Renewal Risk Mitigation Recommendations', level=1)

            # Round 4: anchor the high/moderate cuts to canonical
            # RISK_BAND_THRESHOLDS (75/55/35/15 on 0-100 scale, ie
            # 7.5/5.5/3.5/1.5 on the 0-10 displayed scale).  Previously
            # the section used 6/4 cuts that disagreed with the rest of
            # the report at the boundaries.
            _RBT_0_10_HIGH = _RBT_0_100["HIGH"] / 10.0  # 5.5
            _RBT_0_10_MEDIUM = _RBT_0_100["MEDIUM"] / 10.0  # 3.5

            # High-risk customer recommendations (risk_scores can be
            # Dict[str, float] or Dict[str, Dict] with 'score' key).
            #
            # Round 5 / Phase 5.2: previously this used a raw
            # ``score >= _RBT_0_10_HIGH`` filter which:
            #   - missed the canonical ``risk_band == "HIGH"|"CRITICAL"``
            #     short-circuit (so a score of 5.4 with band="HIGH"
            #     would NOT be counted here even though the headline
            #     count and ``compute_high_risk_count`` DO count it)
            #   - missed the legacy ``color == "red"`` short-circuit
            # Route every dict-shaped profile through the canonical
            # ``cm.is_high_risk_profile`` helper so the renewal
            # recommendations table always agrees with the headline
            # count.  Bare-float scores (legacy callers) keep the
            # threshold compare for backward compat.
            try:
                import canonical_metrics as _cm_local
            except Exception:
                _cm_local = None  # type: ignore

            def _is_high(v):
                if isinstance(v, dict):
                    if _cm_local is not None:
                        try:
                            return _cm_local.is_high_risk_profile(
                                v,
                                scale=_cm_local.RISK_SCALE_0_TO_10,
                                high_threshold_0_to_10=_RBT_0_10_HIGH,
                            )
                        except Exception as _isr_err:
                            # Fall through to the bare-float compare
                            # below.  Log at DEBUG so we don't lose the
                            # diagnostic but the production log isn't
                            # spammed.
                            logger.debug(
                                "is_high_risk_profile fallback for renewal recs: %s",
                                _isr_err,
                            )
                    score = v.get('score', v.get('risk_score_0_10'))
                    try:
                        return float(score) >= _RBT_0_10_HIGH
                    except (TypeError, ValueError):
                        return False
                try:
                    return float(v) >= _RBT_0_10_HIGH
                except (TypeError, ValueError):
                    return False

            def _score(v):
                if isinstance(v, dict):
                    return v.get('score', v.get('risk_score_0_10', 0))
                return v
            high_risk_customers = {k: v for k, v in risk_scores.items() if _is_high(v)}

            if high_risk_customers:
                rec_p = self.doc.add_paragraph()
                # Round 11 / Phase 9.3: previous heading said only
                # "Risk Score >= X.X", but membership above is driven
                # by ``cm.is_high_risk_profile``, which also pulls in
                # profiles whose canonical band is HIGH/CRITICAL even
                # when their numeric score sits a hair under the
                # threshold (e.g. via ``risk_band`` short-circuit).
                # The numeric-only heading therefore lied about the
                # selection criterion; surface both rules instead.
                rec_p.add_run(
                    f'For High-Risk Customers (Risk Score \u2265 {_RBT_0_10_HIGH:.1f} '
                    f'or canonical risk band of HIGH/CRITICAL):\n'
                ).bold = True
                rec_p.add_run('• Schedule executive-level customer meetings within 30 days\n')
                rec_p.add_run('• Assign dedicated Customer Success Manager for intensive support\n')
                rec_p.add_run('• Create custom adoption plan addressing specific barriers\n')
                rec_p.add_run('• Implement weekly check-ins and progress reviews\n')
                rec_p.add_run('• Consider proactive support credits or additional resources\n')

            # Moderate-risk customer recommendations
            moderate_risk_customers = {
                k: v for k, v in risk_scores.items()
                if _RBT_0_10_MEDIUM <= _score(v) < _RBT_0_10_HIGH
            }

            if moderate_risk_customers:
                rec_p = self.doc.add_paragraph()
                rec_p.add_run(
                    f'For Moderate-Risk Customers (Risk Score {_RBT_0_10_MEDIUM:.1f}-{_RBT_0_10_HIGH:.1f}):\n'
                ).bold = True
                rec_p.add_run('• Increase touch frequency to bi-weekly check-ins\n')
                rec_p.add_run('• Provide targeted training and enablement resources\n')
                rec_p.add_run('• Monitor adoption metrics more closely\n')
                rec_p.add_run('• Address open adoption barriers within 60 days\n')
            
            # General recommendations
            rec_p = self.doc.add_paragraph()
            rec_p.add_run('General Portfolio Recommendations:\n').bold = True
            rec_p.add_run('• Implement proactive health checks for all customers\n')
            rec_p.add_run('• Create early warning system for adoption barriers\n')
            rec_p.add_run('• Establish regular executive business reviews\n')
            rec_p.add_run('• Develop customer success playbooks for common issues\n')
            
        except Exception as e:
            logger.error(f"Error adding renewal recommendations: {e}")
            raise
    
    def save_document(
        self,
        file_path: str,
        customer_name: Optional[str] = None,
        report_subject: Optional[str] = None,
    ) -> str:
        """Save the compact report document.

        Round 25 / Phase E: stamp ``doc.core_properties`` with AdoptIQ
        identity *before* writing to disk.  Pre-Round-25 the ``.docx``
        carried python-docx defaults (``author='python-docx'``,
        ``created='2013-12-23T08:00:00+00:00'``, empty title), so when
        a recipient opened the file's Properties dialog the document
        appeared to have been generated by an unrelated 2013 build of
        the python-docx library rather than AdoptIQ.  The stamp lives
        right next to ``doc.save(...)`` because python-docx serializes
        ``core.xml`` from the in-memory properties on save -- mutating
        them after save would silently no-op.
        """

        try:
            # Round 25 / Phase E: best-effort stamping.  We never let a
            # core-properties failure block the report from shipping,
            # but every property below is well-documented and accepted
            # by python-docx, so failures here would indicate a much
            # deeper problem in the docx surface.
            try:
                from datetime import datetime as _r25e_datetime, timezone as _r25e_tz

                _r25e_now = _r25e_datetime.now(_r25e_tz.utc)
                _r25e_subject = (report_subject or "").strip()
                _r25e_customer = (customer_name or "").strip()

                if _r25e_customer:
                    _r25e_title = (
                        f"{_r25e_customer} - Executive Analysis - "
                        f"{_r25e_now.strftime('%Y-%m-%d')}"
                    )
                else:
                    _r25e_title = (
                        f"AdoptIQ Compact Executive Report - "
                        f"{_r25e_now.strftime('%Y-%m-%d')}"
                    )

                cp = self.doc.core_properties
                cp.author = "AdoptIQ Executive Report Generator"
                cp.last_modified_by = "AdoptIQ Executive Report Generator"
                cp.title = _r25e_title
                cp.subject = _r25e_subject or "AdoptIQ Compact Executive Report"
                cp.comments = "AdoptIQ Compact Executive Report"
                cp.category = "Executive Analytics"
                cp.created = _r25e_now
                cp.modified = _r25e_now
                # ``company`` lives on the extended-properties part
                # rather than core.xml; python-docx exposes it via the
                # core_properties shim, so set it the same way.
                try:
                    cp.company = "AdoptIQ"
                except Exception:
                    # Some python-docx releases do not expose
                    # ``company`` on the core_properties shim.  Skip
                    # silently rather than fail the save.
                    pass
            except Exception as _r25e_err:
                logger.debug(
                    "Round 25 / Phase E: core-properties stamp skipped: %s",
                    _r25e_err,
                )

            self.doc.save(file_path)
            logger.info(f"Compact report saved: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Error saving compact report: {e}")
            raise
    
    def _generate_key_concerns(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame) -> List[str]:
        """Generate key concerns from canonical metrics.

        "Critical" here means strict Critical-only (P1 severity) so the
        narrative does not silently include P2/High rows. The headline
        At-a-Glance dashboard uses ``CRITICAL_AB_MODE_CRITICAL_OR_HIGH``
        for the broader executive label; both modes are explicit and
        named to prevent silent drift.
        """
        concerns: List[str] = []

        critical_abs = cm.count_critical_barriers(
            ab_data, mode=cm.CRITICAL_AB_MODE_CRITICAL_ONLY
        )
        if critical_abs > 0:
            concerns.append(
                f"{critical_abs} critical adoption barriers requiring immediate attention"
            )

        p1_cases = cm.count_p1(csone_data)
        if p1_cases > 0:
            concerns.append(
                f"{p1_cases} P1 support cases indicating customer dissatisfaction"
            )

        if not concerns:
            concerns.append("No critical concerns identified in current analysis period")

        return concerns

    def _generate_immediate_actions(
        self, ab_data: pd.DataFrame, csone_data: pd.DataFrame, risk_scores: Dict
    ) -> List[str]:
        """Generate immediate actions from canonical metrics.

        High-risk count uses the canonical 0-10 legacy scale here for
        backwards compatibility with existing risk_scores dicts that
        carry a `score` key only; the canonical 0-100 banded model is
        used elsewhere via ``cm.compute_high_risk_count``.
        """
        actions: List[str] = []

        high_risk_count = cm.compute_high_risk_count(
            risk_scores, scale=cm.RISK_SCALE_0_TO_10
        )
        if high_risk_count > 0:
            actions.append(
                f"Schedule executive meetings with {high_risk_count} high-risk customers"
            )

        critical_abs = cm.count_critical_barriers(
            ab_data, mode=cm.CRITICAL_AB_MODE_CRITICAL_ONLY
        )
        if critical_abs > 0:
            actions.append(
                f"Assign dedicated CSM resources to address {critical_abs} critical adoption barriers"
            )

        p1_cases = cm.count_p1(csone_data)
        if p1_cases > 0:
            actions.append(
                f"Escalate and prioritize resolution of {p1_cases} P1 support cases"
            )

        if not actions:
            actions.append("Continue monitoring current initiatives and maintain regular check-ins")

        return actions
    
    def _generate_fallback_insights(self, risk_summary: Dict) -> str:
        """Generate meaningful fallback insights when AI is not available"""
        insights = []
        
        # Analyze risk summary data
        if risk_summary:
            total_customers = risk_summary.get('total_customers', 0)
            high_risk_count = risk_summary.get('high_risk_customers', 0)
            critical_abs = risk_summary.get('critical_adoption_barriers', 0)
            escalated_cases = risk_summary.get('escalated_cases', 0)
            
            # Portfolio health assessment
            if high_risk_count > 0:
                risk_percentage = (high_risk_count / total_customers * 100) if total_customers > 0 else 0
                if risk_percentage > 20:
                    insights.append(f"Portfolio shows elevated risk with {high_risk_count} of {total_customers} customers ({risk_percentage:.1f}%) requiring immediate attention.")
                else:
                    insights.append(f"Portfolio health is stable with {high_risk_count} customers requiring focused support out of {total_customers} total.")
            
            # Adoption barriers analysis
            if critical_abs > 0:
                insights.append(f"Identified {critical_abs} critical adoption barriers that could impact customer success and renewal likelihood.")
            
            # Support case analysis
            if escalated_cases > 0:
                insights.append(f"Currently tracking {escalated_cases} escalated support cases requiring executive attention.")
            
            # Strategic recommendations
            if high_risk_count > 0 or critical_abs > 0:
                insights.append("Recommend implementing proactive customer health monitoring and establishing dedicated success plans for at-risk accounts.")
        
        if not insights:
            insights.append("Portfolio analysis completed. Continue monitoring customer health metrics and maintaining regular engagement cadence.")
        
        return " ".join(insights)
    
    def add_portfolio_health_dashboard(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame, risk_summary: Dict):
        """Add portfolio health dashboard section"""
        try:
            if risk_summary is None:
                risk_summary = {}
            self.doc.add_heading('📈 Portfolio Health Dashboard', level=1)
            
            # Create health metrics table
            health_table = self.doc.add_table(rows=5, cols=3)
            health_table.style = 'Table Grid'
            
            # Headers
            headers = health_table.rows[0].cells
            headers[0].text = 'Health Metric'
            headers[1].text = 'Current Status'
            headers[2].text = 'Trend Indicator'
            
            # Data rows
            score = risk_summary.get('overall_risk_score', 0)
            # Round 4: route the renewal-risk row through the canonical
            # RISK_BAND_THRESHOLDS (75/55/35/15 on the 0-100 scale, ie
            # 7.5/5.5/3.5/1.5 on the displayed 0-10 scale) so the same
            # numeric score cannot show as "Moderate" here while it
            # appears as "High" in another section of the same doc.
            if isinstance(score, (int, float)):
                _score_0_100 = float(score) * 10.0 if float(score) <= 10.0 else float(score)
                if _score_0_100 >= _RBT_0_100["CRITICAL"]:
                    renewal_risk_status = 'Critical'
                elif _score_0_100 >= _RBT_0_100["HIGH"]:
                    renewal_risk_status = 'High'
                elif _score_0_100 >= _RBT_0_100["MEDIUM"]:
                    renewal_risk_status = 'Moderate'
                elif _score_0_100 >= _RBT_0_100["LOW"]:
                    renewal_risk_status = 'Low'
                else:
                    renewal_risk_status = 'Healthy'
            else:
                renewal_risk_status = 'Unknown'
            metrics_data = [
                ('Customer Satisfaction', 'Good' if risk_summary.get('escalated_cases', 0) < 5 else 'Needs Attention', '📊'),
                ('Adoption Health', 'Healthy' if risk_summary.get('critical_adoption_barriers', 0) < 3 else 'At Risk', '📈'),
                ('Support Load', 'Normal' if risk_summary.get('escalated_cases', 0) < 10 else 'High', '⚠️'),
                ('Renewal Risk', renewal_risk_status, '🎯')
            ]
            
            for i, (metric, status, trend) in enumerate(metrics_data, 1):
                cells = health_table.rows[i].cells
                # Round 8 / Phase 3.1: safe text wrap.
                _safe_cell_text(cells[0], metric)
                _safe_cell_text(cells[1], status)
                _safe_cell_text(cells[2], trend)
                
                # Style the cells
                for cell in cells:
                    for paragraph in cell.paragraphs:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        for run in paragraph.runs:
                            run.bold = True
            
        except Exception as e:
            logger.error(f"Error adding portfolio health dashboard: {e}")
            raise
    
    def add_renewal_recommendations(self, risk_summary: Dict, ai_insights: Dict):
        """Add renewal recommendations section"""
        try:
            if risk_summary is None:
                risk_summary = {}
            self.doc.add_heading('💡 Strategic Renewal Recommendations', level=1)
            
            # Immediate actions
            immediate_p = self.doc.add_paragraph()
            immediate_p.add_run('⚡ Immediate Actions (Next 30 Days):\n').bold = True
            
            # FIXED: Show ALL immediate actions
            actions = risk_summary.get('immediate_actions', [])
            for i, action in enumerate(actions, 1):
                immediate_p.add_run(f'{i}. {action}\n')
            
            # Strategic recommendations
            strategic_p = self.doc.add_paragraph()
            strategic_p.add_run('🎯 Strategic Recommendations (Next 90 Days):\n').bold = True
            
            strategic_recommendations = [
                "Implement proactive customer health monitoring",
                "Develop customer success playbooks for high-risk segments",
                "Create escalation protocols for critical issues",
                "Establish regular executive business reviews"
            ]
            
            for i, rec in enumerate(strategic_recommendations, 1):
                strategic_p.add_run(f'{i}. {rec}\n')
            
            # AI-powered insights
            if ai_insights:
                # Round 6 / Phase 3.13: render only whitelisted keys,
                # mirroring add_executive_summary above.  See that
                # function for rationale.
                ai_summary = ""
                if isinstance(ai_insights, dict):
                    _ps = ai_insights.get('portfolio_summary') if 'portfolio_summary' in ai_insights else None
                    if isinstance(_ps, dict):
                        _candidate = _ps.get('executive_summary')
                        if isinstance(_candidate, str):
                            ai_summary = _candidate
                    if not ai_summary and 'executive_summary' in ai_insights:
                        _candidate = ai_insights.get('executive_summary')
                        if isinstance(_candidate, str):
                            ai_summary = _candidate
                elif isinstance(ai_insights, str):
                    ai_summary = ai_insights

                # Round 3 / Phase 2.10: when we are about to render
                # the canned static playbook (because the LLM either
                # returned nothing or the placeholder "AI analysis is
                # currently processing" string), do NOT advertise the
                # block as "AI-Powered Strategic Insights". Relabel
                # the heading honestly so a reader can tell that this
                # paragraph is hard-coded fallback copy rather than
                # model output.
                _is_fallback = not (
                    ai_summary
                    and ai_summary.strip()
                    and "AI analysis is currently processing" not in ai_summary
                )
                ai_p = self.doc.add_paragraph()
                if _is_fallback:
                    ai_p.add_run('📘 Static Strategic Playbook (AI insights unavailable):\n').bold = True
                    fallback_insights = self._generate_fallback_insights(risk_summary)
                    ai_p.add_run(fallback_insights)
                else:
                    ai_p.add_run('🤖 AI-Powered Strategic Insights:\n').bold = True
                    ai_p.add_run(ai_summary)
            
        except Exception as e:
            logger.error(f"Error adding renewal recommendations: {e}")
            raise
    
    def add_bems_escalation_section(self, csone_data: pd.DataFrame):
        """Add comprehensive BEMS escalation analysis section"""
        try:
            if csone_data is None:
                csone_data = pd.DataFrame()
            self.doc.add_heading('🚨 BEMS Escalations & TAC Case Analysis', level=1)
            
            if csone_data.empty:
                # Phase 3.2: distinguish failed / not_configured / empty for CSOne TAC.
                from report_utils import classify_data_state, render_empty_state_message
                _state = classify_data_state(csone_data)
                no_data_p = self.doc.add_paragraph()
                no_data_p.add_run('Data availability: ').bold = True
                if _state == "failed":
                    err = (csone_data.attrs or {}).get("fetch_error", "")
                    no_data_p.add_run(render_empty_state_message(
                        _state, source_label='CSOne (TAC) case data',
                        error_detail=err,
                        suggested_action='BEMS analysis is unavailable for this run; do not interpret missing rows as zero escalations.',
                    ))
                else:
                    no_data_p.add_run('No CSOne (TAC) case data was provided for this analysis. BEMS escalations are identified from CSOne (Transaction ID, bemscsc_refs). Upload a CSOne export to include TAC cases and BEMS analysis.')
                return
            
            # Canonical BEMS extraction from normalized TAC fields
            csone_norm = add_case_lifecycle_fields(csone_data)
            bems_mask = detect_bems_mask(csone_norm)
            bems_cases = csone_norm[bems_mask]
            total_bems = len(bems_cases)
            # Round 8 / Phase 3.5: drive bems_rate off the canonical
            # ``cm.bems_rate`` helper so the section header and the
            # later "watch list" callout (which already calls
            # ``cm.bems_rate``) report identical values.  Falling
            # back to a local recomputation for the empty-csone case
            # is preserved for safety.
            try:
                bems_rate = float(cm.bems_rate(csone_data))
            except Exception:
                bems_rate = (total_bems / len(csone_norm) * 100) if len(csone_norm) > 0 else 0.0
            
            # Active BEMS summary
            summary_heading = self.doc.add_paragraph()
            summary_heading.add_run('Active BEMS Escalations\n').bold = True
            summary_p = self.doc.add_paragraph()
            summary_p.add_run(
                f"Total BEMS Escalations: {total_bems} "
                f"{format_inline_source('BEMS Escalations', fields=['Transaction ID', 'bemscsc_refs'])}\n"
            )
            
            if total_bems > 0 and 'customer_name' in bems_cases.columns:
                summary_p.add_run('\nBEMS Escalations by Customer:\n')

                # Round 11 / Phase 3.3: previously iterated raw
                # ``customer_name.unique()`` which produced separate
                # bullets for spelling variants of the same account
                # (e.g. ``"ACME, Inc."`` and ``"Acme Inc"``).  Group
                # by a normalized key and render the most common raw
                # label so the operator sees a single bullet per
                # account.
                try:
                    from utils import normalize_customer_name as _norm_cust
                except Exception:
                    _norm_cust = lambda v: str(v) if v is not None else ''  # type: ignore
                try:
                    _norm_series = bems_cases['customer_name'].astype(str).map(
                        lambda v: _norm_cust(v) or v
                    )
                    bems_cases_grouped = bems_cases.assign(_r11_norm_cust=_norm_series)
                    _iter = bems_cases_grouped.groupby('_r11_norm_cust', sort=True)
                except Exception:
                    bems_cases_grouped = bems_cases
                    _iter = bems_cases.groupby('customer_name', sort=True)

                for _norm_key, customer_bems in _iter:
                    try:
                        display_name = (
                            customer_bems['customer_name']
                            .astype(str).mode().iloc[0]
                            if 'customer_name' in customer_bems.columns
                            and not customer_bems['customer_name'].dropna().empty
                            else _norm_key
                        )
                    except Exception:
                        display_name = _norm_key

                    # Extract actual BEMS IDs
                    bems_ids = set()
                    for _, row in customer_bems.iterrows():
                        bems_ids.update(extract_bems_ids_from_row(row))

                    # Format ALL BEMS IDs with brackets for citation like [BEMS01916938]
                    bems_id_list = sorted(list(bems_ids))
                    bems_id_str = ', '.join([f'[{bid}]' for bid in bems_id_list])  # FIXED: Show all IDs

                    summary_p.add_run(
                        f"• {display_name}: {len(customer_bems)} escalation(s) - {bems_id_str} "
                        f"{format_inline_source('BEMS Escalations', fields=['Transaction ID', 'bemscsc_refs'])}\n"
                    )
            else:
                summary_p.add_run('\nActive BEMS Escalations: None Detected\n')
                summary_p.add_run('No BEMS escalations were detected in the CSOne data for this period.\n')
                summary_p.add_run('Note: BEMS detection checks Transaction ID and bemscsc_refs columns.\n')
            
            # Debug info
            debug_p = self.doc.add_paragraph()
            debug_p.add_run('\nDebug Information:\n').bold = True
            debug_p.add_run(f'• CSOne records analyzed: {len(csone_data)}\n')
            if 'Transaction ID' in csone_data.columns:
                debug_p.add_run(f'• Transaction ID column: {csone_data["Transaction ID"].notna().sum()} non-null values\n')
            if 'bemscsc_refs' in csone_data.columns:
                debug_p.add_run(f'• bemscsc_refs column: {csone_data["bemscsc_refs"].notna().sum()} non-null values\n')
            
            # TAC Cases without BEMS
            self.doc.add_paragraph()
            non_bems_heading = self.doc.add_paragraph()
            non_bems_heading.add_run('TAC Cases Without BEMS Escalations\n').bold = True
            
            non_bems_cases = csone_norm[~bems_mask]
            non_bems_p = self.doc.add_paragraph()
            non_bems_p.add_run(
                f"Total TAC Cases Without BEMS: {len(non_bems_cases)} "
                f"{format_inline_source('Support Cases (TAC)', fields=['Case #', 'Status'])}\n"
            )
            non_bems_p.add_run('These cases may require monitoring for potential escalation risk.\n\n')
            
            if not non_bems_cases.empty and 'customer_name' in non_bems_cases.columns:
                non_bems_p.add_run('TAC Cases by Customer (No BEMS):\n')

                # Round 11 / Phase 3.3: aggregate by normalized
                # customer name so spelling variants (case /
                # punctuation) collapse into one bullet, matching
                # the BEMS-by-customer behavior just above.
                try:
                    from utils import normalize_customer_name as _norm_cust
                except Exception:
                    _norm_cust = lambda v: str(v) if v is not None else ''  # type: ignore
                try:
                    _norm_series = non_bems_cases['customer_name'].astype(str).map(
                        lambda v: _norm_cust(v) or v
                    )
                    non_bems_grouped = non_bems_cases.assign(_r11_norm_cust=_norm_series)
                    customer_counts = non_bems_grouped['_r11_norm_cust'].value_counts()
                except Exception:
                    non_bems_grouped = non_bems_cases.copy()
                    non_bems_grouped['_r11_norm_cust'] = non_bems_grouped['customer_name']
                    customer_counts = non_bems_grouped['_r11_norm_cust'].value_counts()

                # FIXED: Show ALL customers and ALL their cases
                for _norm_key, count in customer_counts.items():
                    cust_cases = non_bems_grouped[non_bems_grouped['_r11_norm_cust'] == _norm_key]
                    try:
                        display_name = (
                            cust_cases['customer_name']
                            .astype(str).mode().iloc[0]
                            if 'customer_name' in cust_cases.columns
                            and not cust_cases['customer_name'].dropna().empty
                            else _norm_key
                        )
                    except Exception:
                        display_name = _norm_key
                    case_nums = []
                    for _, case in cust_cases.iterrows():
                        case_num = case.get('SR Number', case.get('Case Number', 'Unknown'))
                        case_nums.append(f"TAC {case_num}")

                    case_nums_str = ', '.join(case_nums)
                    # Round 8 / Phase 3.1: customer / case-num are
                    # backend-derived; route through ``_safe_add_run``.
                    _safe_add_run(non_bems_p, f'• {display_name}: {count} case(s) - {case_nums_str}\n')
            
        except Exception as e:
            logger.error(f"Error adding BEMS escalation section: {e}")
            raise
    
    def add_adoption_barriers_voice_section(self, ab_data: pd.DataFrame):
        """Add Voice of Customer section for adoption barriers"""
        ab_data = ab_data if ab_data is not None else pd.DataFrame()
        try:
            self.doc.add_heading('🗣️ Voice of Customer: Adoption Barriers', level=1)
            
            intro_p = self.doc.add_paragraph()
            intro_p.add_run('This section represents direct feedback from customers about what is blocking their adoption and success.\n\n').italic = True
            
            if ab_data.empty:
                # Phase 3.2: tristate empty-state classification
                from report_utils import classify_data_state, render_empty_state_message
                _state = classify_data_state(ab_data)
                no_data_p = self.doc.add_paragraph()
                if _state == "failed":
                    err = (ab_data.attrs or {}).get("fetch_error", "")
                    no_data_p.add_run(render_empty_state_message(
                        _state, source_label='Adoption barrier data (CSConsole / Snowflake)',
                        error_detail=err,
                        suggested_action='Voice-of-customer narrative is unavailable; do not infer "no barriers".',
                    ))
                else:
                    no_data_p.add_run(render_empty_state_message(
                        _state, source_label='Adoption barrier records'
                    ))
                return
            
            # Overview stats
            overview_p = self.doc.add_paragraph()
            overview_p.add_run('Adoption Barriers Overview\n').bold = True
            overview_p.add_run(f'• Total Adoption Barriers: {len(ab_data)}\n')
            
            if 'customer_name' in ab_data.columns:
                # Round 13 / Phase 3.13: count customers against the
                # canonical normalized name so cosmetic spelling drift
                # (NBSPs, casing, trailing punctuation) does not inflate
                # the "Customers with Barriers" tile relative to the
                # rolled-up Voice-of-Customer narrative below (which
                # iterates per-customer).  Without this fix the tile
                # said e.g. "12 customers" while the narrative bullet
                # list contained 11 distinct accounts.
                try:
                    from data_normalization import normalize_customer_name as _r13_norm_cust_voc
                except Exception:
                    _r13_norm_cust_voc = lambda v: v  # noqa: E731
                _cust_norm_series = (
                    ab_data['customer_name'].fillna('').apply(_r13_norm_cust_voc)
                )
                _cust_norm_series = _cust_norm_series[_cust_norm_series.astype(str) != '']
                n_cust = int(_cust_norm_series.nunique())
                overview_p.add_run(f'• Customers with Barriers: {n_cust}\n')
                avg_per_customer = len(ab_data) / n_cust if n_cust > 0 else 0
                overview_p.add_run(f'• Average Barriers per Customer: {avg_per_customer:.1f}\n')
            
            # Critical barriers
            self.doc.add_paragraph()
            critical_heading = self.doc.add_paragraph()
            critical_heading.add_run('Critical Adoption Barriers Requiring Attention\n').bold = True
            
            # Round 3 hardening: route severity through canonical
            # normalization so this matches ``cm.count_critical_barriers``
            # and the Compact "Critical Adoption Barriers Requiring Action"
            # section above. The previous substring matcher disagreed when
            # the source had labels like "Highest" or "Critical-Resolved".
            try:
                from data_normalization import normalize_severity_label as _norm_sev_label
                _sev_picked_col = next(
                    (c for c in ('severity_norm', 'SEVERITY_C', 'severity_c', 'Severity', 'PRIORITY')
                     if c in ab_data.columns),
                    None,
                )
                if _sev_picked_col == 'severity_norm':
                    _sev_series = ab_data[_sev_picked_col].fillna('').astype(str)
                elif _sev_picked_col is not None:
                    _sev_series = (
                        ab_data[_sev_picked_col].apply(_norm_sev_label).fillna('').astype(str)
                    )
                else:
                    _sev_series = pd.Series([], dtype=str)
                if _sev_series.empty:
                    critical_barriers = ab_data.iloc[0:0]
                else:
                    critical_barriers = ab_data[_sev_series.isin(['Critical', 'High'])]
                sev_col = _sev_picked_col  # preserved so downstream display still works
            except Exception:
                critical_barriers = ab_data.iloc[0:0]
                sev_col = None
            if sev_col:
                
                if not critical_barriers.empty:
                    # FIXED: Show ALL critical barriers
                    for _, barrier in critical_barriers.iterrows():
                        subj = barrier.get('SUBJECT_C', barrier.get('subject_c', barrier.get('title', 'No subject')))
                        customer = barrier.get('customer_name', 'Unknown')
                        sev = barrier.get(sev_col, 'Unknown')
                        
                        barrier_p = self.doc.add_paragraph()
                        # Round 8 / Phase 3.1: safe wrap.
                        _safe_add_run(barrier_p, f'• {customer}: ').bold = True
                        _safe_add_run(
                            barrier_p,
                            f"{subj} (Severity: {sev}) "
                            f"{format_inline_source('Adoption Barriers', fields=['SEVERITY_C', 'AB_STATUS_C'])}",
                        )
                else:
                    no_critical_p = self.doc.add_paragraph()
                    no_critical_p.add_run('No critical adoption barriers identified in current data.')
            
        except Exception as e:
            logger.error(f"Error adding adoption barriers voice section: {e}")
            raise
    
    def add_data_citations_section(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame):
        """Add data citations and source verification section – uses canonical data sources (same across all AdoptIQ reports)."""
        ab_data = ab_data if ab_data is not None else pd.DataFrame()
        csone_data = csone_data if csone_data is not None else pd.DataFrame()
        csone_norm = add_case_lifecycle_fields(csone_data) if not csone_data.empty else pd.DataFrame()
        try:
            self.doc.add_heading('📑 Data Citations & Source Verification', level=1)
            
            intro_p = self.doc.add_paragraph()
            intro_p.add_run('Every metric in this report is traceable to its source. Same canonical data sources used across all AdoptIQ reports.\n\n').italic = True
            
            # Canonical Report Data Sources paragraph
            try:
                from report_utils import get_data_sources_paragraph_text, get_data_sources_list
            except ImportError:
                get_data_sources_paragraph_text = lambda: (
                    'Adoption Barriers: CSConsole / Snowflake C360_CS_TASK_C_VW. Support Cases: CSOne (TAC case data). '
                    'BEMS: CSOne. Service Incidents: status.webex.com. Software Defects: help.webex.com. '
                    'Customer Pulse, Action Plans, Success Priorities: CSConsole.'
                )
                get_data_sources_list = lambda: [
                    ('Adoption Barriers', 'CSConsole / Snowflake C360_CS_TASK_C_VW', 'Query by Record ID in CSConsole or Snowflake'),
                    ('Support Cases (TAC)', 'CSOne (TAC case data)', 'Query by Case Number / SR Number in CSOne'),
                    ('BEMS Escalations', 'CSOne (Transaction ID, bemscsc_refs)', 'BEMS IDs verifiable in CSOne'),
                ]
            sources_para = self.doc.add_paragraph()
            sources_para.add_run(get_data_sources_paragraph_text())
            self.doc.add_paragraph()
            
            # Run-specific metrics table (values from this analysis, sources from canonical)
            canonical = {m: (s, v) for m, s, v in get_data_sources_list()}
            support_source, support_verif = canonical.get('Support Cases (TAC)', ('CSOne (TAC case data)', 'Query by Case Number in CSOne'))
            ab_source, ab_verif = canonical.get('Adoption Barriers', ('CSConsole / Snowflake C360_CS_TASK_C_VW', 'Query by Record ID in CSConsole or Snowflake'))
            # Use cm.count_p1 so this Citations table shares its P1 count with
            # every other report (Compact dashboard, EI, Leader). The previous
            # inline ``(sev_series == 'P1').sum()`` duplicated the canonical
            # function and could drift if the helper changes.
            p1_critical_count = cm.count_p1(csone_norm) if not csone_norm.empty else 0

            csone_unique_customers = (
                int(
                    csone_norm['customer_name']
                    .dropna()
                    .astype(str)
                    .apply(normalize_customer_name)
                    .replace("Unknown", pd.NA)
                    .dropna()
                    .nunique()
                )
                if not csone_norm.empty and 'customer_name' in csone_norm.columns
                else 0
            )
            ab_customer_col = next(
                (c for c in ('customer_name', 'BU_NAME', 'Customer Name', 'CUSTOMER_NAME') if c in ab_data.columns),
                None,
            )
            ab_unique_customers = (
                int(
                    ab_data[ab_customer_col]
                    .dropna()
                    .astype(str)
                    .apply(normalize_customer_name)
                    .replace("Unknown", pd.NA)
                    .dropna()
                    .nunique()
                )
                if (not ab_data.empty and ab_customer_col)
                else 0
            )
            
            citations_table = self.doc.add_table(rows=1, cols=5)
            citations_table.style = 'Table Grid'
            header_cells = citations_table.rows[0].cells
            for i, header in enumerate(['Metric', 'Value', 'Data Source', 'Verification Method', 'Confidence']):
                header_cells[i].text = header
                for paragraph in header_cells[i].paragraphs:
                    for run in paragraph.runs:
                        run.bold = True
            
            # Phase 3.4: replace hardcoded "95% / 90%" confidence
            # percentages with qualitative labels. The numeric values
            # were not derived from any model output and implied a
            # statistical confidence we don't measure here.
            citations_data = [
                ('Total Support Cases', str(len(csone_norm)) if not csone_norm.empty else '0', support_source, support_verif, 'Source-of-truth'),
                ('Unique Customers with Cases', str(csone_unique_customers), support_source, support_verif, 'Source-of-truth'),
                ('P1/Critical Cases', str(p1_critical_count), support_source, support_verif, 'Source-of-truth'),
                ('Total Adoption Barriers', str(len(ab_data)) if not ab_data.empty else '0', ab_source, ab_verif, 'Source-of-truth'),
                ('Customers with Adoption Barriers', str(ab_unique_customers), ab_source, ab_verif, 'Source-of-truth'),
            ]
            for metric, value, source, method, confidence in citations_data:
                row = citations_table.add_row()
                # Round 8 / Phase 3.1: safe text wrap on backend-derived
                # cell values.
                _safe_cell_text(row.cells[0], metric)
                _safe_cell_text(row.cells[1], value)
                _safe_cell_text(row.cells[2], source)
                _safe_cell_text(row.cells[3], method)
                _safe_cell_text(row.cells[4], confidence)
            
            self.doc.add_paragraph()
            note_p = self.doc.add_paragraph()
            note_p.add_run('To verify any metric: ').bold = True
            note_p.add_run('Use the Verification Method column to locate the specific record in the source system (CSOne, CSConsole, or Snowflake).')
            
        except Exception as e:
            logger.error(f"Error adding data citations section: {e}")
            raise
    
    def add_early_warning_section(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame, risk_data: Dict):
        """Add early warning indicators section for proactive risk identification.

        Round 3 note: the "increasing case volume" check uses a fixed
        30-day momentum window regardless of the analysis ``days``
        configured on the run. This is intentional — a short-horizon
        spotlight is more sensitive to *new* spikes than a long
        analysis window — but the narrative below now explicitly
        labels it as a 30-day spotlight to avoid surprising readers.
        """
        try:
            if ab_data is None:
                ab_data = pd.DataFrame()
            if csone_data is None:
                csone_data = pd.DataFrame()
            self.doc.add_heading('🚨 Early Warning Indicators - Proactive Risk Identification', level=1)
            
            intro_p = self.doc.add_paragraph()
            intro_p.add_run('These indicators identify at-risk customers BEFORE issues escalate. ')
            intro_p.add_run(
                'This predictive analysis enables proactive intervention to prevent escalations. '
                'The case-volume check below uses a 30-day short-horizon spotlight regardless of the '
                'overall analysis window so new momentum is surfaced quickly.\n\n'
            ).italic = True
            
            warnings = []
            
            csone_norm = add_case_lifecycle_fields(csone_data) if not csone_data.empty else pd.DataFrame()

            # Round 12 / Phase 3.3: every Early-Warning indicator
            # below loops ``unique()`` on the raw ``customer_name``
            # column, so a single account that appears with two
            # spelling variants (case, whitespace, NBSP) is counted
            # twice and surfaces twice in the predictive risk table.
            # Precompute a ``customer_name_norm`` column once and
            # drive every ``unique()`` / mask / count off the
            # normalized key, while still rendering the canonical
            # display label.
            if not csone_norm.empty and 'customer_name' in csone_norm.columns:
                try:
                    csone_norm = csone_norm.copy()
                    csone_norm['customer_name_norm'] = (
                        csone_norm['customer_name']
                        .fillna('')
                        .astype(str)
                        .map(normalize_customer_name)
                    )
                except Exception:
                    csone_norm['customer_name_norm'] = csone_norm.get('customer_name', '')

            # Check for BEMS escalations
            if not csone_norm.empty:
                bems_mask = detect_bems_mask(csone_norm)
                if bems_mask.any() and 'customer_name_norm' in csone_norm.columns:
                    bems_cases = csone_norm[bems_mask]
                    for customer in bems_cases['customer_name_norm'].dropna().unique():
                        if not customer:
                            continue
                        count = len(bems_cases[bems_cases['customer_name_norm'] == customer])
                        warnings.append({
                            'severity': 'CRITICAL',
                            'customer': customer,
                            'indicator': 'Active Engineering Escalations',
                            'detail': f'{customer} has {count} cases with BEMS escalations. These require immediate engineering attention.',
                            'action': 'Coordinate with engineering team and provide customer with escalation timeline',
                            'confidence_label': 'Verified (BEMS escalation rule)'
                        })
            
            # Check for increasing case volume
            date_col_csone = next((c for c in ['open_date', 'Date/Time Opened', 'CREATED_DATE', 'Created', 'Created Date'] if c in csone_norm.columns), None)
            if not csone_norm.empty and 'customer_name' in csone_norm.columns and date_col_csone:
                try:
                    csone_copy = csone_norm.copy()
                    # Round 8 / Phase 3.4: pin the 30-day window to
                    # the UTC clock so the cutoff does not silently
                    # shift +/- a few hours around the daily edge
                    # depending on the process timezone.  Strip the
                    # tzinfo so it can be compared against the naive
                    # ``date_opened`` column produced by
                    # ``pd.to_datetime(..., errors='coerce')`` (which
                    # is also naive when the source data lacks an
                    # offset).
                    csone_copy['date_opened'] = pd.to_datetime(csone_copy[date_col_csone], errors='coerce')
                    _now_naive_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                    recent_30 = csone_copy[csone_copy['date_opened'] >= (_now_naive_utc - timedelta(days=30))]

                    # Round 12 / Phase 3.3: drive the case-volume
                    # spotlight off the normalized customer key so
                    # spelling variants do not split a single account
                    # into multiple sub-threshold buckets.
                    if 'customer_name_norm' not in recent_30.columns:
                        recent_30 = recent_30.copy()
                        recent_30['customer_name_norm'] = (
                            recent_30['customer_name'].fillna('').astype(str).map(normalize_customer_name)
                        )
                    for customer in recent_30['customer_name_norm'].dropna().unique():
                        if not customer:
                            continue
                        recent_count = len(recent_30[recent_30['customer_name_norm'] == customer])
                        total_count = len(csone_copy[csone_copy['customer_name_norm'] == customer]) if 'customer_name_norm' in csone_copy.columns else len(csone_copy[csone_copy['customer_name'] == customer])
                        
                        if recent_count >= 5 and recent_count / max(total_count, 1) > 0.5:
                            warnings.append({
                                'severity': 'HIGH',
                                'customer': customer,
                                'indicator': 'Increasing Support Case Volume',
                                'detail': f'{customer} has {recent_count} cases in the last 30 days ({total_count} total). This trend suggests potential escalation risk.',
                                'action': 'Schedule proactive customer success review and identify root causes before escalation',
                                'confidence_label': 'Heuristic (volume momentum rule)'
                            })
                except Exception as _ew_err:
                    logger.debug(f"Early warning date parse error: {_ew_err}")
            
            # Check for unresolved adoption barriers
            if not ab_data.empty and 'customer_name' in ab_data.columns:
                status_col = 'AB_STATUS_C' if 'AB_STATUS_C' in ab_data.columns else ('STATUS_C' if 'STATUS_C' in ab_data.columns else None)
                if status_col:
                    open_mask = ab_data[status_col].astype(str).str.contains('Open|New|In Progress', case=False, na=False)
                    open_barriers = ab_data[open_mask].copy()

                    # Round 12 / Phase 3.3: same defect on the
                    # adoption-barrier path -- normalize once and
                    # drive ``unique()`` / count off the canonical
                    # key.
                    try:
                        open_barriers['customer_name_norm'] = (
                            open_barriers['customer_name']
                            .fillna('')
                            .astype(str)
                            .map(normalize_customer_name)
                        )
                    except Exception:
                        open_barriers['customer_name_norm'] = open_barriers['customer_name']

                    for customer in open_barriers['customer_name_norm'].dropna().unique():
                        if not customer:
                            continue
                        count = len(open_barriers[open_barriers['customer_name_norm'] == customer])
                        if count >= 3:
                            warnings.append({
                                'severity': 'MEDIUM',
                                'customer': customer,
                                'indicator': 'Unresolved Adoption Barriers',
                                'detail': f'{customer} has {count} unresolved adoption barriers. These may be blocking value realization.',
                                'action': 'Review barriers and create remediation plan with customer',
                                'confidence_label': 'Heuristic (open-barrier threshold)'
                            })
            
            # Display warnings by severity
            for severity in ['CRITICAL', 'HIGH', 'MEDIUM']:
                sev_warnings = [w for w in warnings if w['severity'] == severity]
                if sev_warnings:
                    if severity == 'CRITICAL':
                        sev_heading = self.doc.add_paragraph()
                        sev_heading.add_run(f'🔴 {severity} - Immediate Action Required\n').bold = True
                    elif severity == 'HIGH':
                        sev_heading = self.doc.add_paragraph()
                        sev_heading.add_run(f'🟠 {severity} - Proactive Intervention Needed\n').bold = True
                    else:
                        sev_heading = self.doc.add_paragraph()
                        sev_heading.add_run(f'🟡 {severity} - Monitor Closely\n').bold = True
                    
                    # FIXED: Show ALL warnings per severity
                    for warning in sev_warnings:
                        warn_p = self.doc.add_paragraph()
                        warn_p.add_run(f'{warning["indicator"]}\n').bold = True
                        warn_p.add_run(f'{warning["detail"]}\n')
                        warn_p.add_run(f'Recommended Action: {warning["action"]}\n')
                        warn_p.add_run(
                            f'Basis: {warning.get("confidence_label", warning.get("confidence", "Heuristic"))}\n'
                        )
            
            # Summary
            self.doc.add_paragraph()
            summary_p = self.doc.add_paragraph()
            summary_p.add_run('Early Warning Summary:\n').bold = True
            summary_p.add_run(f'• Total Warnings: {len(warnings)}\n')
            summary_p.add_run(f'• Critical: {len([w for w in warnings if w["severity"] == "CRITICAL"])}\n')
            summary_p.add_run(f'• High: {len([w for w in warnings if w["severity"] == "HIGH"])}\n')
            summary_p.add_run(f'• Medium: {len([w for w in warnings if w["severity"] == "MEDIUM"])}\n')
            
        except Exception as e:
            logger.error(f"Error adding early warning section: {e}")
            raise
    
    def add_common_problems_section(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame):
        """Add Common Problems Across Portfolio section - MATCHES EXAMPLE FORMAT"""
        import re
        try:
            self.doc.add_heading('Common Problems Across Portfolio', level=1)
            
            # Analyze patterns across all data
            problem_themes = {}
            affected_customers_by_theme = {}
            
            # Extract common themes from adoption barriers
            if not ab_data.empty:
                subjects = ab_data['SUBJECT_C'].fillna('').astype(str).str.lower() if 'SUBJECT_C' in ab_data.columns else pd.Series("", index=ab_data.index)
                descriptions = ab_data['DESCRIPTION_C'].fillna('').astype(str).str.lower() if 'DESCRIPTION_C' in ab_data.columns else pd.Series("", index=ab_data.index)
                row_text = (subjects + " " + descriptions).str.strip()
                customer_col = next(
                    (c for c in ('customer_name', 'BU_NAME', 'Customer Name', 'CUSTOMER_NAME') if c in ab_data.columns),
                    None,
                )
                customer_series = (
                    ab_data[customer_col].fillna('').astype(str).apply(normalize_customer_name)
                    if customer_col
                    else pd.Series("Unknown", index=ab_data.index)
                )
                
                # Common problem patterns
                patterns = {
                    'Backend Escalation Bottleneck': ['bems', 'escalat', 'engineering', 'backend'],
                    'Integration/Config Issues': ['integration', 'firewall', 'api', 'config', 'sso'],
                    'Product Defects': ['defect', 'bug', 'csc', 'fix', 'issue'],
                    'Performance Problems': ['slow', 'timeout', 'performance', 'latency'],
                    'User Experience Issues': ['login', 'access', 'permission', 'role']
                }
                
                for theme, keywords in patterns.items():
                    theme_mask = row_text.apply(lambda text: any(kw in str(text) for kw in keywords))
                    count = int(theme_mask.sum())
                    if count > 0:
                        problem_themes[theme] = count
                        affected_customers_by_theme[theme] = sorted(
                            customer_series[theme_mask]
                            .replace("Unknown", pd.NA)
                            .dropna()
                            .unique()
                            .tolist()
                        )
            
            # Sort by frequency - FIXED: Show ALL problem themes
            # Round 16 / Phase 2.2: tie-break on the theme name (case-
            # folded) so two themes that detect the same number of
            # affected rows render in the same order across runs.
            # Without the tiebreaker the order is dict-insertion order,
            # which is stable inside a single Python process but not
            # across re-runs that ingest the same source frames in a
            # different order.
            sorted_themes = sorted(
                problem_themes.items(),
                key=lambda x: (-x[1], str(x[0] or '').casefold()),
            )

            # Round 11 / Phase 9.1: when no themes were detected the
            # section previously rendered an empty heading and then
            # silently moved on, leaving the reader to guess whether
            # the analysis ran or was suppressed.  Emit an explicit
            # empty-state paragraph so the absence is intentional and
            # auditable.
            if not sorted_themes:
                _empty_p = self.doc.add_paragraph()
                _empty_p.add_run(
                    'No recurring problem themes detected in the available '
                    'adoption barrier or support case text for this window. '
                    'This indicates either insufficient narrative content to '
                    'cluster, or that no theme reached the keyword-match '
                    'threshold.'
                ).italic = True

            for idx, (theme, count) in enumerate(sorted_themes, 1):
                theme_heading = self.doc.add_heading(f'{idx}. {theme}', level=3)
                
                # What's Happening
                what_p = self.doc.add_paragraph()
                what_p.add_run("- What's Happening: ").bold = True
                # Round 11 / Phase 9.2: previous prose hard-coded
                # "instances ... affecting multiple customers" and
                # therefore lied about singletons (1 instance / 1
                # customer).  Pluralize per actual counts.
                _count = int(count or 0)
                _affected_count = len(affected_customers_by_theme.get(theme, []))
                _instance_word = 'instance' if _count == 1 else 'instances'
                if _affected_count <= 0:
                    _customer_phrase = 'no specific customer attribution available'
                elif _affected_count == 1:
                    _customer_phrase = 'affecting 1 customer with a recurring pattern'
                else:
                    _customer_phrase = f'affecting {_affected_count} customers with recurring patterns'
                what_p.add_run(f'{_count} {_instance_word} detected - {_customer_phrase}')
                
                # Barrier
                barrier_p = self.doc.add_paragraph()
                barrier_p.add_run('- Barrier: ').bold = True
                barrier_p.add_run('Root cause resolution delayed, requiring cross-functional coordination')
                
                # Customers Affected (extract from data) - FIXED: Show all affected customers
                affected_customers = affected_customers_by_theme.get(theme, [])
                affected_p = self.doc.add_paragraph()
                affected_p.add_run('- Customers Affected: ').bold = True
                affected_p.add_run(', '.join(affected_customers) if affected_customers else 'Multiple customers')
                
                # Business Impact
                impact_p = self.doc.add_paragraph()
                impact_p.add_run('- Business Impact: ').bold = True
                impact_p.add_run('Resolution times exceed SLA, customer satisfaction at risk, potential renewal impact')
                
                # Fix Needed
                fix_p = self.doc.add_paragraph()
                fix_p.add_run('- Fix Needed: ').bold = True
                fix_p.add_run('Prioritized remediation plan with engineering engagement and customer communication')
                
                self.doc.add_paragraph()  # Spacing
            
        except Exception as e:
            logger.error(f"Error adding common problems section: {e}")
            raise
    
    def add_action_plan_section(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame, risk_data: Dict):
        """Add What We Need To Do section - MATCHES EXAMPLE FORMAT"""
        try:
            self.doc.add_heading('What We Need To Do', level=1)
            
            # IMMEDIATE section
            immediate_heading = self.doc.add_heading('🔥 IMMEDIATE (This Week)', level=2)
            
            immediate_actions = self.doc.add_paragraph()
            
            # Round 5 / Phase 5.9: previously the war-room list keyed
            # off the legacy ``color == 'Red'`` flag alone, which
            # silently disagreed with every other "high risk
            # customer" surface (the headline tile, EI / Leader
            # tables, and ``compute_high_risk_count``) the moment a
            # customer was scored 5.4 / band="HIGH" without an
            # explicit color.  Route through ``cm.is_high_risk_profile``
            # so the list always matches the canonical headline.
            red_customers = []
            if risk_data:
                for k, v in risk_data.items():
                    if not isinstance(v, dict):
                        continue
                    try:
                        is_high = cm.is_high_risk_profile(
                            v, scale=cm.RISK_SCALE_0_TO_10
                        )
                    except Exception:
                        is_high = (str(v.get('color', '')).lower() == 'red')
                    if is_high:
                        red_customers.append(k)
            
            if red_customers:
                # Round 8 / Phase 3.6: sort by case-insensitive name
                # so the war-room list is deterministic across runs.
                # ``risk_data`` is a dict, and dict iteration order
                # follows insertion order, which can vary between
                # runs depending on ingestion order.  Customers
                # listed first in war-room communications were
                # silently skewed by that incidental ordering.
                _red_sorted = sorted(red_customers, key=lambda x: str(x or '').lower())
                immediate_actions.add_run('- Engineering War Room: ').bold = True
                # Round 8 / Phase 3.1: red_customers are
                # backend-derived; route through ``_safe_add_run``.
                _safe_add_run(immediate_actions, f'For {", ".join(_red_sorted)} - daily status until critical issues resolved\n')
            
            immediate_actions.add_run('- Adoption Barrier Review: ').bold = True
            immediate_actions.add_run('Categorize all known barriers, assign owners, and communicate timelines in CSConsole (by Friday)\n')
            
            immediate_actions.add_run('- Executive Communication: ').bold = True
            immediate_actions.add_run('Brief leadership on at-risk accounts and remediation plan\n')
            
            # SHORT-TERM section
            short_term_heading = self.doc.add_heading('📋 SHORT-TERM (30-60 Days)', level=2)
            
            short_term_actions = self.doc.add_paragraph()
            short_term_actions.add_run('- Defect Blitz: ').bold = True
            short_term_actions.add_run('Engineering/Product to hotfix & deploy for all open "multi-customer" defects\n')
            
            short_term_actions.add_run('- Pre-Change Checklist: ').bold = True
            short_term_actions.add_run('Launch mandatory checklist for all customers planning integration, upgrade, or config work\n')
            
            short_term_actions.add_run('- Customer Health Reviews: ').bold = True
            short_term_actions.add_run('Schedule quarterly reviews with all moderate+ risk customers\n')
            
        except Exception as e:
            logger.error(f"Error adding action plan section: {e}")
            raise
    
    def add_predictive_risk_section(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame, risk_data: Dict):
        """Add Predictive Risk section - MATCHES EXAMPLE FORMAT"""
        import re
        try:
            if ab_data is None:
                ab_data = pd.DataFrame()
            if csone_data is None:
                csone_data = pd.DataFrame()
            self.doc.add_heading('Predictive Risk', level=2)
            
            # Use canonical high-risk count (legacy 0-10 ``color/score`` model
            # because risk_data here comes from ``calculate_renewal_risk_scores``
            # which still returns a 0-10 score with a ``color`` flag).
            red_customers = cm.compute_high_risk_count(
                risk_data, scale=cm.RISK_SCALE_0_TO_10
            )

            bems_count = cm.count_bems(csone_data)
            bems_rate = cm.bems_rate(csone_data)

            risk_p = self.doc.add_paragraph()
            risk_p.add_run('- High-Risk Customers: ').bold = True
            risk_p.add_run(f'{red_customers} customers currently in red-risk status\n')

            risk_p.add_run('- Escalation Probability: ').bold = True
            if bems_count > 0:
                risk_p.add_run(f'BEMS rate at {bems_rate:.1f}% - if not reduced below 15% in 90 days, expect additional escalations\n')
            else:
                risk_p.add_run('Low - minimal BEMS escalations detected\n')
            
            risk_p.add_run('- Monitoring Needed: ').bold = True
            risk_p.add_run('Weekly portfolio health check-ins, monthly defect/adoption review\n')
            
        except Exception as e:
            logger.error(f"Error adding predictive risk section: {e}")
            raise
    
    def add_executive_takeaway_section(self, risk_summary: Dict):
        """Add Executive Takeaway section - MATCHES EXAMPLE FORMAT"""
        try:
            if risk_summary is None:
                risk_summary = {}
            self.doc.add_heading('Executive Takeaway', level=1)
            
            overall_score = risk_summary.get('overall_risk_score', 5)

            # Round 5 / Phase 5.13: previously the takeaway used hard-coded
            # 7 / 5 cutoffs that disagreed with the canonical band edges
            # (HIGH = 5.5, CRITICAL = 7.5 on the 0-10 displayed scale).
            # That meant a portfolio scored 5.4 was labeled RED ZONE in
            # the Executive Risk Pie but only "STABLE" in the takeaway,
            # and a portfolio scored 6.9 was MODERATE in the takeaway but
            # CRITICAL elsewhere.  Anchor to ``cm.RISK_BAND_THRESHOLDS``
            # so the takeaway always agrees with the rest of the report.
            # Round 6 / Phase 5.15: previously the except branch
            # silently fell back to literal 7.5/5.5 cuts.  That meant
            # if ``cm.RISK_BAND_THRESHOLDS`` was unavailable (e.g. an
            # import-time bug or a partial bundle) the takeaway would
            # *appear* to work but silently use stale thresholds while
            # the rest of the report used the canonical ones.  Fail
            # loud here so we either honor the canonical thresholds or
            # surface a clear error -- never silently drift.
            # Round 10 / Phase 2.2: route through the shared
            # ``_compact_portfolio_band`` helper so the takeaway tier and
            # the executive-summary tier are guaranteed to agree (they
            # previously cut at CRITICAL/HIGH vs HIGH/MEDIUM and could
            # contradict each other in the same DOCX).
            _band = _compact_portfolio_band(overall_score)
            takeaway_p = self.doc.add_paragraph()
            takeaway_p.add_run(_band['takeaway_lead']).bold = True
            takeaway_p.add_run(_band['takeaway_body'])
            _takeaway_action = takeaway_p.add_run(_band['takeaway_action'])
            if _band['tier'] == 'HIGH':
                _takeaway_action.bold = True
            
        except Exception as e:
            logger.error(f"Error adding executive takeaway section: {e}")
            raise
    
    def add_methodology_section(self):
        """Add methodology and data quality section"""
        try:
            self.doc.add_heading('📋 Analysis Methodology & Data Quality', level=1)
            
            methodology_p = self.doc.add_paragraph()
            methodology_p.add_run('🔍 Data Sources (canonical – same across all AdoptIQ reports):\n').bold = True
            try:
                from report_utils import get_data_sources_list
                for metric, source, _ in get_data_sources_list():
                    methodology_p.add_run(f'• {metric}: {source}\n')
            except ImportError:
                methodology_p.add_run('• Adoption Barriers: CSConsole / Snowflake C360_CS_TASK_C_VW\n')
                methodology_p.add_run('• Support Cases (TAC): CSOne (TAC case data)\n')
                methodology_p.add_run('• BEMS Escalations: CSOne (Transaction ID, bemscsc_refs)\n')
                methodology_p.add_run('• Service Incidents: status.webex.com\n')
                methodology_p.add_run('• Software Defects: help.webex.com, CSC/BST refs in CSOne and Adoption Barriers\n')
                methodology_p.add_run('• Customer Pulse, Action Plans, Success Priorities: CSConsole\n')
            methodology_p.add_run('• AI-powered analysis using CircuIT AI\n')
            
            risk_p = self.doc.add_paragraph()
            risk_p.add_run('📊 Risk Scoring Methodology:\n').bold = True
            risk_p.add_run('• Deterministic weighted model reused across reports (0-100 -> displayed as /10)\n')
            risk_p.add_run('• Inputs: adoption barriers, TAC cases, BEMS, customer pulse, action plans, incidents, contract signals\n')
            risk_p.add_run('• TAC severity/priority and case type are normalized before scoring\n')
            risk_p.add_run('• Missing fields are treated as unknown, not auto-escalated to high severity\n')
            
            quality_p = self.doc.add_paragraph()
            quality_p.add_run('✅ Data Quality Assurance:\n').bold = True
            quality_p.add_run('• All data validated for completeness\n')
            quality_p.add_run('• Duplicate entries removed\n')
            quality_p.add_run('• Date/time fields standardized\n')
            quality_p.add_run('• Customer names normalized\n')
            
        except Exception as e:
            logger.error(f"Error adding methodology section: {e}")
            raise


def calculate_renewal_risk_scores(
    ab_data: pd.DataFrame,
    csone_data: pd.DataFrame,
    *,
    extra_frames: Optional[List[pd.DataFrame]] = None,
    account_to_customer: Optional[Dict[str, str]] = None,
    recent_window_days: int = 30,
) -> Dict[str, Dict]:
    """Calculate renewal risk scores and color categories for each customer.

    Round 4: accept optional ``extra_frames`` (subscriptions, pulse,
    success priorities, action plans) and ``account_to_customer`` so
    subscription-only and pulse-only customers receive a renewal-risk
    row.  Previously the universe was AB ∪ CSOne only, which silently
    dropped customers visible in the headline ``total_customers`` from
    the renewal table.
    """
    try:
        ab_data = ab_data if ab_data is not None else pd.DataFrame()
        csone_data = csone_data if csone_data is not None else pd.DataFrame()
        csone_norm = add_case_lifecycle_fields(csone_data)
        risk_data = {}

        # Use the canonical customer-list helper so the renewal table's
        # universe matches the headline ``total_customers``.
        try:
            canonical_names = cm.list_customers(
                ab_df=ab_data,
                csone_df=csone_norm,
                extra_frames=extra_frames,
                account_to_customer=account_to_customer,
            )
            customers = {normalize_customer_name(v) for v in canonical_names}
        except Exception as _cu_err:
            logger.debug(
                f"calculate_renewal_risk_scores: falling back to AB+CSOne universe: {_cu_err}"
            )
            customers = set()
            if not ab_data.empty and 'customer_name' in ab_data.columns:
                customers.update(normalize_customer_name(v) for v in ab_data['customer_name'].dropna().unique())
            if not csone_norm.empty and 'customer_name' in csone_norm.columns:
                customers.update(normalize_customer_name(v) for v in csone_norm['customer_name'].dropna().unique())
        customers = {c for c in customers if c and c != "Unknown"}
        
        for customer in customers:
            customer_ab = (
                ab_data[ab_data['customer_name'].fillna("").astype(str).apply(normalize_customer_name) == customer]
                if not ab_data.empty and 'customer_name' in ab_data.columns
                else pd.DataFrame()
            )
            customer_csone = (
                csone_norm[csone_norm['customer_name'].fillna("").astype(str).apply(normalize_customer_name) == customer]
                if not csone_norm.empty and 'customer_name' in csone_norm.columns
                else pd.DataFrame()
            )
            profile = compute_customer_risk_profile(
                customer_name=customer,
                customer_ab=customer_ab,
                customer_csone=customer_csone,
                customer_pulse=pd.DataFrame(),
                customer_action_plans=pd.DataFrame(),
                customer_subs=pd.DataFrame(),
                ext_incidents=None,
                # Round 3 / Phase 4.2: thread the report's analysis
                # horizon down to the support-case "recent" window
                # so renewal risk reflects activity in the period
                # the report is actually summarising.
                recent_window_days=int(recent_window_days)
                if recent_window_days
                else 30,
            )
            final_score = profile["risk_score_0_10"]
            risk_factors = list(profile["risk_factors"])
            aging_open = profile["components"]["adoption_barriers"]["details"].get("aging_open_count", 0)
            if aging_open > 0:
                risk_factors.append(
                    f"{aging_open} barrier(s) open 60+ days "
                    f"{format_inline_source('Adoption Barriers', fields=['OPEN_DATE_C', 'AB_STATUS_C'])}"
                )
            
            # Round 4: route the color/category tiers through the
            # canonical RISK_BAND_THRESHOLDS (CRITICAL=75, HIGH=55,
            # MEDIUM=35, LOW=15 on the 0-100 axis) so the same numeric
            # score gets the same color/label across every report.
            _band = profile["risk_band"]
            if _band == "CRITICAL":
                color = "Red"
                category = "Critical Risk - Immediate Action Required"
            elif _band == "HIGH":
                color = "Red"
                category = "High Risk - Urgent Attention Needed"
            elif _band == "MEDIUM":
                color = "Yellow"
                category = "Moderate Risk - Monitor Closely"
            elif _band == "LOW":
                color = "Green"
                category = "Low Risk - Standard Monitoring"
            else:
                color = "Gray"
                category = "No Renewal Risk - Minimal Engagement"
            
            risk_data[customer] = {
                'score': final_score,
                'color': color,
                'category': category,
                'risk_factors': risk_factors,
                'risk_score_0_100': profile["risk_score_0_100"],
                'risk_band': profile["risk_band"],
            }
        
        return risk_data
        
    except Exception as e:
        logger.error(f"Error calculating risk scores: {e}")
        return {}


def create_compact_executive_report(analysis_id: str, manager: str, technology: str, days: int,
                                  ab_data: pd.DataFrame, csone_data: pd.DataFrame, 
                                  ai_insights: Dict, output_path: str,
                                  *,
                                  team_subs_df: Optional[pd.DataFrame] = None,
                                  csconsole_action_plans: Optional[pd.DataFrame] = None,
                                  csconsole_customer_pulse: Optional[pd.DataFrame] = None,
                                  csconsole_success_priorities: Optional[pd.DataFrame] = None,
                                  csconsole_adoption_barriers: Optional[pd.DataFrame] = None,
                                  account_to_customer: Optional[Dict[str, str]] = None,
                                  partial_data_warnings: Optional[List[Dict[str, Any]]] = None,
                                  data_retrieved_at: Optional[datetime] = None) -> str:
    """Create COMPREHENSIVE compact executive report focused on renewal risk with full data detail.

    Round 3 hardening: optional ``team_subs_df`` / CSConsole frames /
    ``account_to_customer`` are threaded through so the Compact at-a-glance
    customer tile and portfolio metrics use the SAME multi-source customer
    universe that EI / Admin Dashboard use. Without these, Compact would
    under-report any customer that exists only in subscription / pulse /
    action-plan data.
    """
    
    ab_data = ab_data if ab_data is not None else pd.DataFrame()
    csone_data = csone_data if csone_data is not None else pd.DataFrame()
    csone_norm = add_case_lifecycle_fields(csone_data)
    _extra_customer_frames = [
        f for f in (
            team_subs_df,
            csconsole_action_plans,
            csconsole_customer_pulse,
            csconsole_success_priorities,
            csconsole_adoption_barriers,
        ) if f is not None and not (hasattr(f, 'empty') and f.empty)
    ]
    # Round 8 / Phase 3.2: do not log the raw manager name at INFO --
    # log aggregators routinely persist these and a manager full name
    # is PII.  Emit a short SHA-256 digest so support can correlate
    # without leaking the identity, and keep the verbatim name at
    # DEBUG.
    try:
        import hashlib as _h
        _mgr_digest = _h.sha256(str(manager or '').encode('utf-8', 'replace')).hexdigest()[:12]
    except Exception:
        _mgr_digest = '?'
    logger.info("Creating compact executive report (manager_digest=%s)", _mgr_digest)
    logger.debug("Creating compact executive report for %s", manager)
    
    try:
        formatter = CompactReportFormatter()
        
        # Calculate risk scores.  Round 4: pass through the multi-source
        # ``extra_frames`` and ``account_to_customer`` map already
        # available in this scope so subscription-only / pulse-only
        # customers receive a renewal-risk row.
        risk_data = calculate_renewal_risk_scores(
            ab_data,
            csone_data,
            extra_frames=_extra_customer_frames if _extra_customer_frames else None,
            account_to_customer=account_to_customer,
            # Phase 4.2: thread the report's analysis horizon so
            # support-case "recent" counts match the period the
            # Compact / EI / Excel surfaces describe.
            recent_window_days=int(days) if days else 30,
        )
        
        # Create risk summary.
        # Phase 1.5: cm.is_high_risk_profile honors color="Red" and risk_band
        # overrides on the 0-10 branch so the count agrees with EI / Leader.
        high_risk_customers = {
            k: v for k, v in risk_data.items()
            if isinstance(v, dict) and cm.is_high_risk_profile(v, scale=cm.RISK_SCALE_0_TO_10)
        }
        # Round 5 / Phase 5.5: align the moderate band with the
        # canonical RISK_BAND_THRESHOLDS (MEDIUM=35, HIGH=55 on the
        # 0-100 scale, which is 3.5..5.5 on the 0-10 displayed
        # scale).  The previous ``4 <= score < 5.5`` band silently
        # excluded scores in [3.5, 4.0), so a customer scored 3.7
        # was neither "high risk" nor "moderate risk" anywhere on
        # the report -- they simply disappeared.  Use [3.5, 5.5)
        # so every non-high-risk customer falls into a documented
        # band.
        _COMPACT_MOD_LO = float(_RBT_0_100["MEDIUM"]) / 10.0  # 3.5
        _COMPACT_MOD_HI = float(_RBT_0_100["HIGH"]) / 10.0    # 5.5
        moderate_risk_customers = {
            k: v for k, v in risk_data.items()
            if isinstance(v, dict)
            and not cm.is_high_risk_profile(v, scale=cm.RISK_SCALE_0_TO_10)
            and _COMPACT_MOD_LO <= v.get('score', 0) < _COMPACT_MOD_HI
        }
        
        # All cross-report counts come from canonical_metrics so this
        # report agrees byte-for-byte with the Leader / EI / Comprehensive
        # documents. ``critical_adoption_barriers`` here is the executive
        # "Critical or High" definition; the strict Critical-only count is
        # exposed via _generate_key_concerns where appropriate.
        total_bems = cm.count_bems(csone_norm)

        _scores = [v.get('score', 0) for v in risk_data.values() if isinstance(v, dict) and isinstance(v.get('score'), (int, float)) and not np.isnan(v.get('score', 0))] if risk_data else []
        overall_risk_score = float(np.mean(_scores)) if _scores else 0.0
        if np.isnan(overall_risk_score) or np.isinf(overall_risk_score):
            overall_risk_score = 0.0

        critical_adoption_barriers = cm.count_critical_barriers(
            ab_data, mode=cm.CRITICAL_AB_MODE_CRITICAL_OR_HIGH
        )
        critical_only_barriers = cm.count_critical_barriers(
            ab_data, mode=cm.CRITICAL_AB_MODE_CRITICAL_ONLY
        )
        escalated_cases = cm.count_escalated(csone_norm)
        p1_cases = cm.count_p1(csone_norm)
        p2_cases = cm.count_p2(csone_norm)
        # Round 25 / Phase A: align the in-prose ``risk_summary['total_customers']``
        # value with the narrow displayed-sheets universe so the
        # ``📊 Executive Summary`` "Total customers analyzed" line cannot
        # diverge from the at-a-glance tile.
        canonical_total_customers = cm.count_customers(
            ab_df=ab_data,
            csone_df=csone_norm,
            pulse_df=csconsole_customer_pulse,
        )

        risk_summary = {
            'overall_risk_score': round(overall_risk_score, 1),
            'high_risk_customers': len(high_risk_customers),
            'moderate_risk_customers': len(moderate_risk_customers),
            'total_customers': canonical_total_customers,
            'critical_adoption_barriers': critical_adoption_barriers,
            'critical_only_adoption_barriers': critical_only_barriers,
            'escalated_cases': escalated_cases,
            'bems_escalations': total_bems,
            'key_concerns': [
                f"{len(high_risk_customers)} customers at high renewal risk",
                f"{critical_adoption_barriers} critical/high adoption barriers",
                # Round 10 / Phase 2.1: every other support-pressure headline
                # in the compact report (title page, Key Portfolio Metrics,
                # executive takeaway) drives off ``escalated_cases`` (P1+P2),
                # but this bullet was using ``p1_cases`` only. That made the
                # one-line key-concern in the executive summary materially
                # under-state support pressure relative to the same number
                # rendered three sections below. Mirror the canonical metric
                # and label so all four sites agree.
                f"{escalated_cases} P1+P2 escalated cases",
                f"{total_bems} BEMS engineering escalations",
            ],
            'immediate_actions': [
                "Schedule executive meetings with high-risk customers",
                "Assign dedicated CSM resources to critical accounts",
                "Create targeted adoption plans for at-risk customers",
                "Coordinate with engineering on BEMS escalations",
            ],
        }
        factual_claims = []
        for profile in risk_data.values():
            if isinstance(profile, dict):
                factual_claims.extend(profile.get("risk_factors", []) or [])
                factual_claims.extend(profile.get("key_findings", []) or [])
        factual_claims.extend(risk_summary.get("key_concerns", []) or [])
        factual_claims.extend(risk_summary.get("immediate_actions", []) or [])
        # Round 6 / Phase 3.13: render only whitelisted keys for the
        # validator/factual-claims path.  ``raw_response`` is no longer
        # accepted because it can carry the entire LLM payload (with
        # debug fields) into the validator and inflate factual-claim
        # extraction beyond the documented surface.  The two
        # whitelisted keys are ``portfolio_summary.executive_summary``
        # and the top-level ``executive_summary``.
        ai_summary_text = ""
        if isinstance(ai_insights, dict):
            _ps = ai_insights.get('portfolio_summary') if 'portfolio_summary' in ai_insights else None
            if isinstance(_ps, dict):
                _cand = _ps.get('executive_summary')
                if isinstance(_cand, str):
                    ai_summary_text = _cand
            if not ai_summary_text and 'executive_summary' in ai_insights:
                _cand = ai_insights.get('executive_summary')
                if isinstance(_cand, str):
                    ai_summary_text = _cand
        elif isinstance(ai_insights, str):
            ai_summary_text = ai_insights
        if ai_summary_text:
            factual_claims.append(ai_summary_text)
        factual_claims = [_ensure_inline_source_claim(claim) for claim in factual_claims if str(claim or "").strip()]

        # Build the canonical portfolio_metrics payload and pass it to the
        # consistency validator so the Compact path is now contract-checked
        # the same way the Comprehensive path is.
        portfolio_metrics = cm.build_portfolio_metrics(
            ab_df=ab_data,
            csone_df=csone_norm,
            risk_profiles=risk_data,
            risk_scale=cm.RISK_SCALE_0_TO_10,
            extra_customer_frames=_extra_customer_frames or None,
            account_to_customer=account_to_customer,
        )
        # Round 25 / Phase A: pin the headline ``total_customers`` to
        # the narrow displayed-sheets universe (AB ∪ CSOne ∪ Pulse) so
        # the Compact Word headline never diverges from the Excel
        # ``Summary`` row.  Pre-Round 25 this branch widened the count
        # via ``extra_frames`` (team subs, action plans, success
        # priorities, csconsole adoption barriers) which produced
        # ``49`` in the reference Brian Frazier / 90d report while
        # Excel showed ``37`` for the same dataset.  Readers can now
        # manually reconcile the headline by counting unique customers
        # across the three detail sheets the report actually displays.
        try:
            portfolio_metrics["total_customers"] = cm.count_customers(
                ab_df=ab_data,
                csone_df=csone_norm,
                pulse_df=csconsole_customer_pulse,
            )
        except Exception as _tc_err:
            logger.debug(
                "Compact total_customers recompute failed; falling back to "
                "build_portfolio_metrics value: %s",
                _tc_err,
            )
        # Round 22 / R22-001: thread the same extras + account_to_customer
        # we used for build_portfolio_metrics through the validator so the
        # validator's customer universe matches portfolio_metrics["total_customers"]
        # exactly.  Without this the validator computes total_customers from
        # ab_data ∪ csone_norm only and raises "Portfolio metric mismatch" the
        # moment any extra-frame customer is present (e.g. action plan / pulse
        # only customers).  The leader-report path at app_simple.py:18776 has
        # done this since Round 5/6 -- this is the same fix for the compact path.
        #
        # Round 25 / Phase A: also thread ``pulse_df=csconsole_customer_pulse``
        # so the validator's narrow ``total_customers`` count uses the SAME
        # AB ∪ CSOne ∪ Pulse universe that the Excel ``Summary`` row and the
        # Word headline tile use.  ``extra_frames`` / ``account_to_customer``
        # remain wired for downstream defect-customer linkage and surface as
        # ``metrics["total_customers_with_extras"]``, but the headline
        # parity check now compares Word PM["total_customers"] (set above
        # to the narrow shape) against the validator's narrow count.
        consistency = validate_report_consistency(
            ab_data,
            csone_norm,
            portfolio_metrics=portfolio_metrics,
            risk_data=risk_data,
            factual_claims=factual_claims,
            extra_frames=_extra_customer_frames or None,
            account_to_customer=account_to_customer,
            pulse_df=csconsole_customer_pulse,
        )
        if consistency["errors"]:
            logger.error(f"[CONSISTENCY] Compact report errors: {consistency['errors']}")
        if consistency["warnings"]:
            logger.warning(f"[CONSISTENCY] Compact report warnings: {consistency['warnings']}")
        # Phase 1.2: Compact reports now block by default on consistency errors.
        # Set ADOPTIQ_NONSTRICT_COMPACT=1 to opt out (e.g. emergency hotfix).
        _compact_nonstrict = str(
            os.getenv("ADOPTIQ_NONSTRICT_COMPACT", "0")
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not consistency.get("is_valid", True) and not _compact_nonstrict:
            raise ValueError(
                "Compact report consistency checks failed: "
                + "; ".join(consistency.get("errors", []) or ["unknown"])
            )
        
        # Create title page
        formatter.create_compact_title_page(
            manager, technology, days, analysis_id, risk_summary,
            data_retrieved_at=data_retrieved_at,
        )

        # Phase 1.3b: emit a Partial Data banner BEFORE any number is shown so
        # the reader is warned about silent zeros from failed sources.
        if partial_data_warnings:
            try:
                formatter.doc.add_heading("⚠ Partial Data Warning", level=1)
                formatter.doc.add_paragraph(
                    "One or more upstream data sources failed to load for this run. "
                    "Sections that depend on the affected sources are marked "
                    "\"unavailable\" rather than rendered as zero. Rerun once the "
                    "source is reachable for a complete picture."
                )
                for _w in partial_data_warnings:
                    _ds = str(_w.get('dataset') or 'unknown')
                    _err = str(_w.get('error') or 'unknown error')
                    _kind = str(_w.get('kind') or 'runtime')
                    formatter.doc.add_paragraph(
                        f"• {_ds} ({_kind}): {_err}", style='List Bullet'
                    )
                formatter.doc.add_paragraph("")
            except Exception as _banner_err:
                logger.warning("Could not render compact partial-data banner: %s", _banner_err)

        # Add At-a-Glance Dashboard (NEW - matches example report format at the top)
        # Round 25 / Phase A: ``pulse_df`` is the canonical narrow-universe
        # customer source; the dashboard tile derives ``Total Customers``
        # from ``count_customers(ab, csone, pulse_df=)`` so Word agrees
        # with the Excel ``Summary`` row.  ``extra_customer_frames`` is
        # still threaded for downstream sections (renewal risk coverage,
        # action plan / success priority sections) but no longer enters
        # the headline count.
        formatter.add_at_a_glance_dashboard(
            ab_data,
            csone_data,
            total_customers_override=risk_summary.get('total_customers'),
            extra_customer_frames=_extra_customer_frames or None,
            account_to_customer=account_to_customer,
            pulse_df=csconsole_customer_pulse,
        )
        formatter._add_section_separator()
        
        # Add comprehensive sections matching example report quality
        formatter.add_executive_summary(risk_summary, ai_insights)
        formatter._add_section_separator()
        
        formatter.add_high_risk_customers(ab_data, csone_data, risk_data)
        formatter._add_section_separator()
        
        # Add early warning indicators (NEW - matches example report)
        formatter.add_early_warning_section(ab_data, csone_data, risk_data)
        formatter._add_section_separator()
        
        formatter.add_portfolio_health_dashboard(ab_data, csone_data, risk_summary)
        formatter._add_section_separator()
        
        # Add critical adoption barriers (WAS MISSING - function existed but wasn't called)
        formatter.add_critical_adoption_barriers(ab_data)
        formatter._add_section_separator()
        
        # Add BEMS escalation analysis (NEW - critical for example report quality)
        formatter.add_bems_escalation_section(csone_data)
        formatter._add_section_separator()
        
        # Add adoption barriers voice section (NEW - matches example report)
        formatter.add_adoption_barriers_voice_section(ab_data)
        formatter._add_section_separator()
        
        formatter.add_renewal_recommendations(risk_summary, ai_insights)
        formatter._add_section_separator()
        
        # Add Common Problems Across Portfolio (NEW - matches example report)
        formatter.add_common_problems_section(ab_data, csone_data)
        formatter._add_section_separator()
        
        # Add What We Need To Do section (NEW - matches example report)
        formatter.add_action_plan_section(ab_data, csone_data, risk_data)
        formatter._add_section_separator()
        
        # Add Predictive Risk section (NEW - matches example report)
        formatter.add_predictive_risk_section(ab_data, csone_data, risk_data)
        formatter._add_section_separator()
        
        # Add Executive Takeaway (NEW - matches example report)
        formatter.add_executive_takeaway_section(risk_summary)
        formatter._add_section_separator()
        
        # Add page break before long sections
        formatter.doc.add_page_break()
        
        # Add Top 10 by Risk / Focus Accounts
        # Round 10 / Phase 2.4: sort by ``-score`` then customer-name lower
        # so ties produce a stable, alphabetical order. Previously two
        # customers tied at the same score iterated in the underlying
        # ``risk_data`` dict's insertion order — and ``risk_data`` is built
        # from a ``set()`` in upstream callers, which is non-deterministic
        # across Python sessions. The same data therefore rendered a
        # different "top 10" between runs.
        def _focus_sort_key(item):
            name, profile = item
            try:
                score = float(profile.get('score', 0)) if isinstance(profile, dict) else 0.0
            except (TypeError, ValueError):
                score = 0.0
            return (-score, str(name).lower())
        # Round 12 / Phase 9.5: previously this section silently
        # truncated to the top 10 risk accounts with no disclosure
        # when ``len(risk_data)`` exceeded 10, so a manager reading
        # the compact briefing for an 80-account portfolio could not
        # tell from the document whether the table was the entire
        # universe or just the top-10 cut.  Compute the full count
        # before slicing and append a "+M more" footer (matching the
        # Round 11 disclosure pattern in leader_report) when there
        # are accounts that did not make the visible table.
        _r12_total_focus = len(risk_data)
        sorted_by_risk = sorted(risk_data.items(), key=_focus_sort_key)[:10]
        if sorted_by_risk:
            formatter.doc.add_heading('Top 10 Focus Accounts by Risk', level=1)
            # Round 16 / Phase 5.2: substitute the Round-15 banded
            # top-N helper for the legacy ``Table Grid`` build.  Same
            # contract (header row + ranked data rows), same content,
            # but consumers now see the Cisco-blue header treatment
            # used by the title-page executive summary, so all top-N
            # tables in the compact report carry one consistent style.
            _r16_focus_headers = ['Rank', 'Customer', 'Risk Score', 'Category']
            _r16_focus_rows = []
            for idx, (cust, info) in enumerate(sorted_by_risk, 1):
                _s = info.get('score', 0)
                _s = 0 if _s is None or (isinstance(_s, float) and np.isnan(_s)) else _s
                _r16_focus_rows.append([
                    str(idx),
                    str(cust),
                    f"{_s:.1f}/10",
                    str(info.get('category') or 'N/A'),
                ])
            focus_table = _r16_add_banded_top_n_table(
                formatter.doc,
                headers=_r16_focus_headers,
                rows=_r16_focus_rows,
            )
            if focus_table is None:
                # Defensive fallback: if the helper refused to build the
                # table (e.g. python-docx surface unavailable in a test
                # harness), keep the legacy renderer alive so the report
                # still ships the focus list rather than vanishing
                # silently.
                focus_table = formatter.doc.add_table(rows=1 + len(sorted_by_risk), cols=4)
                focus_table.style = 'Table Grid'
                hdr = focus_table.rows[0].cells
                for i, txt in enumerate(_r16_focus_headers):
                    hdr[i].text = txt
                    for para in hdr[i].paragraphs:
                        for run in para.runs:
                            run.bold = True
                for r_idx, row_vals in enumerate(_r16_focus_rows, 1):
                    row = focus_table.rows[r_idx].cells
                    for c_idx, v in enumerate(row_vals):
                        row[c_idx].text = v
            # Round 12 / Phase 9.5: see comment above the slice -- emit
            # the truncation footer only when there really are more
            # than 10 ranked accounts so the briefing never claims
            # exhaustive coverage that it cannot back up.
            if _r12_total_focus > len(sorted_by_risk):
                _trunc_para = formatter.doc.add_paragraph()
                _trunc_run = _trunc_para.add_run(
                    f"Showing top {len(sorted_by_risk)} of {_r12_total_focus} ranked accounts "
                    f"(+{_r12_total_focus - len(sorted_by_risk)} more available in the full Excel export)."
                )
                _trunc_run.italic = True
            formatter._add_section_separator()
        
        # Add data citations (NEW - matches example report)
        formatter.add_data_citations_section(ab_data, csone_data)
        formatter._add_section_separator()
        
        formatter.add_methodology_section()
        
        # Report metadata footer
        try:
            from report_utils import get_report_metadata_footer
        except ImportError:
            get_report_metadata_footer = lambda **kw: "AdoptIQ Compact Report"
        formatter.doc.add_page_break()
        formatter.doc.add_heading('Report Metadata', level=2)
        footer_para = formatter.doc.add_paragraph()
        footer_para.add_run(get_report_metadata_footer(
            report_type="Compact Executive",
            manager=manager,
            technology=technology,
            days=days,
        )).font.size = Pt(8)
        
        # Round 25 / Phase E: thread the manager + technology through
        # to ``save_document`` so the .docx Properties dialog shows a
        # meaningful ``Title`` (e.g. ``"Brian Frazier All Contact Center
        # - Executive Analysis - 2026-04-25"``) instead of an empty
        # string.  ``manager`` is the portfolio owner; ``technology``
        # narrows the scope.  Together they form the identity the
        # recipient expects to see in the file metadata.
        _r25e_subject = (
            f"AdoptIQ Compact Executive Report - {technology} - {days}d"
        )
        result_path = formatter.save_document(
            output_path,
            customer_name=f"{manager} {technology}".strip(),
            report_subject=_r25e_subject,
        )
        
        logger.info(f"Compact executive report completed: {result_path}")
        return result_path
        
    except Exception as e:
        logger.error(f"Failed to create compact executive report: {e}")
        raise


if __name__ == "__main__":
    # Test the compact formatter
    logger.info("Compact Report Formatter - Ready for integration")
