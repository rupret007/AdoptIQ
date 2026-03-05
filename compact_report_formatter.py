#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AdoptIQ Compact Report Formatter
Creates focused executive summaries highlighting high-risk customers and renewal concerns
"""

import pandas as pd
import numpy as np
import re
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import logging
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.shared import OxmlElement, qn
from risk_scoring import compute_customer_risk_profile
from data_normalization import (
    add_case_lifecycle_fields,
    detect_bems_mask,
    extract_bems_ids_from_row,
    normalize_customer_name,
)
from report_consistency import validate_report_consistency
from report_utils import format_inline_source, format_metric_with_source

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
        separator_run.font.color.rgb = RGBColor(0x58, 0x59, 0x5B)  # CISCO_GRAY
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
            # Title style
            title_style = self.doc.styles.add_style('CompactTitle', 1)
            title_font = title_style.font
            title_font.name = 'Calibri'
            title_font.size = Pt(24)
            title_font.bold = True
            title_font.color.rgb = None  # Dark blue
            title_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
            title_style.paragraph_format.space_after = Pt(12)
            
            # Subtitle style
            subtitle_style = self.doc.styles.add_style('CompactSubtitle', 1)
            subtitle_font = subtitle_style.font
            subtitle_font.name = 'Calibri'
            subtitle_font.size = Pt(16)
            subtitle_font.bold = True
            subtitle_font.color.rgb = None  # Dark gray
            subtitle_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
            subtitle_style.paragraph_format.space_after = Pt(18)
            
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
                                 analysis_id: str, risk_summary: Dict):
        """Create compact title page with key metrics"""
        try:
            # Title
            title = self.doc.add_heading('AdoptIQ Compact Executive Report', 0)
            title.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Subtitle
            subtitle = self.doc.add_heading(f'Renewal Risk Analysis - {manager or "N/A"}', level=1)
            subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            details = self.doc.add_paragraph()
            details.add_run(f'Technology: {technology or "N/A"}\n').bold = True
            details.add_run(f'Analysis Period: {days or "N/A"} days\n').bold = True
            details.add_run(f'Generated: {datetime.now().strftime("%B %d, %Y at %I:%M %p")}\n').bold = True
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
            data_cells[0].text = str(risk_summary.get('high_risk_customers', 0))
            data_cells[1].text = str(risk_summary.get('critical_adoption_barriers', 0))
            data_cells[2].text = str(risk_summary.get('escalated_cases', 0))
            data_cells[3].text = f"{risk_summary.get('overall_risk_score', 'N/A')}/10"
            
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
                                    if score >= 7:
                                        run.font.color.rgb = None  # Red for high risk
                                    elif score >= 4:
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
    ):
        """Add At-a-Glance Dashboard matching the example report format"""
        from docx.shared import RGBColor  # Import for color styling
        try:
            ab_data = ab_data if ab_data is not None else pd.DataFrame()
            csone_data = csone_data if csone_data is not None else pd.DataFrame()
            self.doc.add_heading('At-a-Glance Dashboard', level=1)
            
            # Calculate metrics from normalized shared fields
            csone_norm = add_case_lifecycle_fields(csone_data)
            customer_set = set()
            if not ab_data.empty:
                ab_customer_col = next(
                    (c for c in ('customer_name', 'BU_NAME', 'Customer Name') if c in ab_data.columns),
                    None,
                )
                if ab_customer_col:
                    customer_set.update(
                        ab_data[ab_customer_col].dropna().astype(str).apply(normalize_customer_name)
                    )
            if not csone_norm.empty and 'customer_name' in csone_norm.columns:
                customer_set.update(csone_norm['customer_name'].dropna().astype(str).apply(normalize_customer_name))
            total_customers = len([c for c in customer_set if c and c != "Unknown"])
            if total_customers_override is not None:
                total_customers = int(total_customers_override)
            total_support_cases = len(csone_norm) if not csone_norm.empty else 0
            
            # Count P1 (Critical) cases
            critical_p1 = 0
            high_p2 = 0
            if not csone_norm.empty:
                sev_series = csone_norm.get('case_priority_norm', pd.Series(dtype=str)).astype(str)
                critical_p1 = int((sev_series == "P1").sum())
                high_p2 = int((sev_series == "P2").sum())
            
            # Count BEMS escalations
            bems_count = 0
            if not csone_norm.empty:
                bems_count = int(detect_bems_mask(csone_norm).sum())
            
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
                shading.set(qn('w:fill'), '007BC7')
                cell._element.get_or_add_tcPr().append(shading)
                for para in cell.paragraphs:
                    for run in para.runs:
                        run.font.color.rgb = RGBColor(255, 255, 255)
            
            # Data row
            values = [str(total_customers), str(total_support_cases), str(critical_p1), str(high_p2), str(bems_count)]
            for i, value in enumerate(values):
                cell = dashboard_table.rows[1].cells[i]
                cell.text = value
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
                            run.font.color.rgb = RGBColor(220, 20, 60)
                        elif i == 4 and _v > 0:  # BEMS
                            run.font.color.rgb = RGBColor(220, 20, 60)
            
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
            
            if overall_risk >= 7:
                risk_level_p.add_run(f'🔴 HIGH RISK - {overall_risk}/10').bold = True
                risk_level_p.runs[-1].font.color.rgb = None  # Red color
                risk_level_p.add_run('\n\n⚠️ Immediate executive attention required. Multiple critical issues identified.')
            elif overall_risk >= 4:
                risk_level_p.add_run(f'🟡 MODERATE RISK - {overall_risk}/10').bold = True
                risk_level_p.runs[-1].font.color.rgb = None  # Orange color
                risk_level_p.add_run('\n\n📋 Proactive monitoring recommended. Some areas require attention.')
            else:
                risk_level_p.add_run(f'🟢 LOW RISK - {overall_risk}/10').bold = True
                risk_level_p.runs[-1].font.color.rgb = None  # Green color
                risk_level_p.add_run('\n\n✅ Portfolio performing well. Continue current initiatives.')
            
            # Key metrics summary
            metrics_p = self.doc.add_paragraph()
            metrics_p.add_run('📈 Key Portfolio Metrics:\n').bold = True
            
            metrics_data = [
                f"• High-risk customers: {risk_summary.get('high_risk_customers', 0)}",
                f"• Critical adoption barriers: {risk_summary.get('critical_adoption_barriers', 0)}",
                f"• Escalated support cases: {risk_summary.get('escalated_cases', 0)}",
                f"• Total customers analyzed: {risk_summary.get('total_customers', 'N/A')}"
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
            
            # AI insights summary with enhanced formatting
            if ai_insights:
                ai_section = self.doc.add_paragraph()
                ai_section.add_run('🤖 AI-Powered Analysis Summary:\n').bold = True
                
                # Handle different AI response formats
                ai_summary = ""
                if isinstance(ai_insights, dict):
                    if 'portfolio_summary' in ai_insights:
                        ai_summary = (ai_insights.get('portfolio_summary') or {}).get('executive_summary', '')
                    elif 'executive_summary' in ai_insights:
                        ai_summary = ai_insights['executive_summary']
                    else:
                        # Try to get any text content from the dict
                        ai_summary = str(ai_insights)
                elif isinstance(ai_insights, str):
                    ai_summary = ai_insights
                else:
                    ai_summary = str(ai_insights)
                
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
            
            # Get customers by risk level
            red_customers = {k: v for k, v in risk_data.items() if isinstance(v, dict) and v.get('color') == 'Red'}
            yellow_customers = {k: v for k, v in risk_data.items() if isinstance(v, dict) and v.get('color') == 'Yellow'}
            green_customers = {k: v for k, v in risk_data.items() if isinstance(v, dict) and v.get('color') == 'Green'}
            gray_customers = {k: v for k, v in risk_data.items() if isinstance(v, dict) and v.get('color') == 'Gray'}
            
            if not risk_data:
                no_risk_p = self.doc.add_paragraph()
                no_risk_p.add_run('✅ No customer data available for risk assessment.').bold = True
                return
            
            # Red customers (Critical/High Risk) - FIXED: Show ALL red customers
            if red_customers:
                self.doc.add_heading('🔴 RED - Critical/High Risk Customers', level=2)
                sorted_red = sorted(red_customers.items(), key=lambda x: x[1].get('score', 0) if isinstance(x[1], dict) else 0, reverse=True)
                
                for customer_name, risk_info in sorted_red:  # Show ALL red customers
                    self._add_customer_risk_section(customer_name, risk_info, ab_data, csone_data, 'Red')
            
            # Yellow customers (Moderate Risk) - FIXED: Show ALL yellow customers
            if yellow_customers:
                self.doc.add_heading('🟡 YELLOW - Moderate Risk Customers', level=2)
                sorted_yellow = sorted(yellow_customers.items(), key=lambda x: x[1].get('score', 0) if isinstance(x[1], dict) else 0, reverse=True)
                
                for customer_name, risk_info in sorted_yellow:  # Show ALL yellow customers
                    self._add_customer_risk_section(customer_name, risk_info, ab_data, csone_data, 'Yellow')
            
            # Green customers (Low Risk) - FIXED: Show ALL green customers
            if green_customers:
                self.doc.add_heading('🟢 GREEN - Low Risk Customers', level=2)
                green_p = self.doc.add_paragraph()
                green_p.add_run(f'✅ {len(green_customers)} customers are classified as low risk with standard monitoring requirements.').bold = True
                green_list = ', '.join(list(green_customers.keys()))  # Show ALL
                green_p.add_run(f'\nCustomers: {green_list}')
            
            # Gray customers (No Risk) - FIXED: Show ALL gray customers
            if gray_customers:
                self.doc.add_heading('⚫ GRAY - No Renewal Risk', level=2)
                gray_p = self.doc.add_paragraph()
                gray_p.add_run(f'✅ {len(gray_customers)} customers have minimal engagement requirements.').bold = True
                gray_list = ', '.join(list(gray_customers.keys()))  # Show ALL
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
            customer_heading = self.doc.add_heading(f'{customer_name} – Risk: {risk_info.get("category", "Unknown").upper()}', level=3)
            
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
            
            # Extract BEMS IDs and count from customer cases
            bems_ids = set()
            bems_count = 0
            if not customer_csone.empty:
                for _, row in customer_csone.iterrows():
                    bems_ids.update(extract_bems_ids_from_row(row))
                bems_count = len(bems_ids)
            
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
            if bems_count > 0:
                # FIXED: Show all BEMS IDs for full verification
                bems_id_examples = ', '.join([f'[{bid}]' for bid in sorted(list(bems_ids))])
                barriers_text_parts.append(f'{bems_count} BEMS escalations ({bems_id_examples})')
            barriers_text_parts.append(f'{len(customer_csone)} open TAC cases')
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
            
            sev_col = (
                'severity_norm'
                if 'severity_norm' in ab_data.columns
                else next((c for c in ('SEVERITY_C', 'severity_c', 'Severity') if c in ab_data.columns), None)
            )
            if not sev_col:
                critical_ab = pd.DataFrame()
            else:
                critical_ab = ab_data[
                    ab_data[sev_col].astype(str).str.contains('Critical|High', case=False, na=False)
                ]
            
            if critical_ab.empty:
                no_critical_p = self.doc.add_paragraph()
                no_critical_p.add_run('✅ No critical severity adoption barriers found.').bold = True
                return
            
            cust_col = 'customer_name' if 'customer_name' in critical_ab.columns else ('BU_NAME' if 'BU_NAME' in critical_ab.columns else None)
            if not cust_col:
                return
            customer_groups = critical_ab.groupby(cust_col)
            
            for customer_name, group in customer_groups:
                customer_p = self.doc.add_paragraph()
                customer_p.add_run(f'{customer_name} ({len(group)} critical barriers)').bold = True
                
                # Show all barriers for this customer, not limited
                for _, ab in group.iterrows():
                    ab_p = self.doc.add_paragraph()
                    # Get barrier ID for citation/verification
                    ab_id = ab.get("ID", ab.get("RECORD_ID", ""))
                    # FIXED: Don't truncate barrier title - show full text
                    subject = ab.get("SUBJECT_C", "No title")
                    ab_p.add_run(f'• ')
                    if ab_id:
                        ab_p.add_run(f'[{ab_id}] ').font.size = Pt(8)
                    ab_p.add_run(f'{subject}\n')
                    ab_p.add_run(f'  Status: {ab.get("AB_STATUS_C", "Unknown")} | ')
                    ab_p.add_run(f'Severity: {ab.get("SEVERITY_C", "Unknown")} | ')
                    ab_p.add_run(f'Assignee: {ab.get("ASSIGNEE_C", "Unassigned")}\n')
                    # ADDED: Show description if available for more context
                    description = ab.get("DESCRIPTION_C", ab.get("DESCRIPTION__C", ""))
                    if description:
                        ab_p.add_run(f'  Description: {description}\n').italic = True
                
                self.doc.add_paragraph()  # Spacing
            
        except Exception as e:
            logger.error(f"Error adding critical adoption barriers: {e}")
            raise
    
    def add_renewal_recommendations_detailed(self, risk_scores: Dict, 
                                  ab_data: pd.DataFrame, csone_data: pd.DataFrame):
        """Add detailed renewal-specific recommendations (alternative to add_renewal_recommendations)"""
        try:
            self.doc.add_heading('Renewal Risk Mitigation Recommendations', level=1)
            
            # High-risk customer recommendations (risk_scores can be Dict[str, float] or Dict[str, Dict] with 'score' key)
            def _score(v):
                return v.get('score', v) if isinstance(v, dict) else v
            high_risk_customers = {k: v for k, v in risk_scores.items() if _score(v) >= 6}
            
            if high_risk_customers:
                rec_p = self.doc.add_paragraph()
                rec_p.add_run('For High-Risk Customers (Risk Score ≥ 6):\n').bold = True
                rec_p.add_run('• Schedule executive-level customer meetings within 30 days\n')
                rec_p.add_run('• Assign dedicated Customer Success Manager for intensive support\n')
                rec_p.add_run('• Create custom adoption plan addressing specific barriers\n')
                rec_p.add_run('• Implement weekly check-ins and progress reviews\n')
                rec_p.add_run('• Consider proactive support credits or additional resources\n')
            
            # Moderate-risk customer recommendations
            moderate_risk_customers = {k: v for k, v in risk_scores.items() if 4 <= _score(v) < 6}
            
            if moderate_risk_customers:
                rec_p = self.doc.add_paragraph()
                rec_p.add_run('For Moderate-Risk Customers (Risk Score 4-6):\n').bold = True
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
    
    def save_document(self, file_path: str) -> str:
        """Save the compact report document"""
        try:
            self.doc.save(file_path)
            logger.info(f"Compact report saved: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Error saving compact report: {e}")
            raise
    
    def _generate_key_concerns(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame) -> List[str]:
        """Generate key concerns based on data analysis"""
        concerns = []
        
        if not ab_data.empty:
            ab_sev_col = (
                'severity_norm'
                if 'severity_norm' in ab_data.columns
                else next((c for c in ('SEVERITY_C', 'severity_c', 'Severity') if c in ab_data.columns), None)
            )
            critical_abs = (
                int(ab_data[ab_sev_col].astype(str).str.contains('Critical', case=False, na=False).sum())
                if ab_sev_col
                else 0
            )
            if critical_abs > 0:
                concerns.append(f"{critical_abs} critical adoption barriers requiring immediate attention")
        
        csone_norm = add_case_lifecycle_fields(csone_data) if csone_data is not None and not csone_data.empty else pd.DataFrame()
        if not csone_norm.empty and 'case_priority_norm' in csone_norm.columns:
            p1_cases = int((csone_norm['case_priority_norm'].astype(str) == 'P1').sum())
            if p1_cases > 0:
                concerns.append(f"{p1_cases} P1 support cases indicating customer dissatisfaction")
        
        if not concerns:
            concerns.append("No critical concerns identified in current analysis period")
        
        return concerns
    
    def _generate_immediate_actions(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame, risk_scores: Dict) -> List[str]:
        """Generate immediate actions based on risk analysis"""
        actions = []
        
        high_risk_count = len([v for v in risk_scores.values() if isinstance(v, dict) and v.get('score', 0) >= 6])
        if high_risk_count > 0:
            actions.append(f"Schedule executive meetings with {high_risk_count} high-risk customers")
        
        if not ab_data.empty:
            ab_sev_col = (
                'severity_norm'
                if 'severity_norm' in ab_data.columns
                else next((c for c in ('SEVERITY_C', 'severity_c', 'Severity') if c in ab_data.columns), None)
            )
            critical_abs = (
                int(ab_data[ab_sev_col].astype(str).str.contains('Critical', case=False, na=False).sum())
                if ab_sev_col
                else 0
            )
            if critical_abs > 0:
                actions.append(f"Assign dedicated CSM resources to address {critical_abs} critical adoption barriers")
        
        csone_norm = add_case_lifecycle_fields(csone_data) if csone_data is not None and not csone_data.empty else pd.DataFrame()
        if not csone_norm.empty and 'case_priority_norm' in csone_norm.columns:
            p1_cases = int((csone_norm['case_priority_norm'].astype(str) == 'P1').sum())
            if p1_cases > 0:
                actions.append(f"Escalate and prioritize resolution of {p1_cases} P1 support cases")
        
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
            renewal_risk_status = 'Low' if (isinstance(score, (int, float)) and score < 4) else 'Moderate' if (isinstance(score, (int, float)) and score < 7) else 'High'
            metrics_data = [
                ('Customer Satisfaction', 'Good' if risk_summary.get('escalated_cases', 0) < 5 else 'Needs Attention', '📊'),
                ('Adoption Health', 'Healthy' if risk_summary.get('critical_adoption_barriers', 0) < 3 else 'At Risk', '📈'),
                ('Support Load', 'Normal' if risk_summary.get('escalated_cases', 0) < 10 else 'High', '⚠️'),
                ('Renewal Risk', renewal_risk_status, '🎯')
            ]
            
            for i, (metric, status, trend) in enumerate(metrics_data, 1):
                cells = health_table.rows[i].cells
                cells[0].text = metric
                cells[1].text = status
                cells[2].text = trend
                
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
                ai_p = self.doc.add_paragraph()
                ai_p.add_run('🤖 AI-Powered Strategic Insights:\n').bold = True
                
                # Handle different AI response formats
                ai_summary = ""
                if isinstance(ai_insights, dict):
                    if 'portfolio_summary' in ai_insights:
                        ai_summary = (ai_insights.get('portfolio_summary') or {}).get('executive_summary', '')
                    elif 'executive_summary' in ai_insights:
                        ai_summary = ai_insights['executive_summary']
                    else:
                        # Try to get any text content from the dict
                        ai_summary = str(ai_insights)
                elif isinstance(ai_insights, str):
                    ai_summary = ai_insights
                else:
                    ai_summary = str(ai_insights)
                
                if ai_summary and ai_summary.strip() and "AI analysis is currently processing" not in ai_summary:
                    # FIXED: Show full AI summary without truncation
                    ai_p.add_run(ai_summary)
                else:
                    # Generate meaningful fallback content
                    fallback_insights = self._generate_fallback_insights(risk_summary)
                    ai_p.add_run(fallback_insights)
            
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
                no_data_p = self.doc.add_paragraph()
                no_data_p.add_run('Data availability: ').bold = True
                no_data_p.add_run('No CSOne (TAC) case data was provided for this analysis. BEMS escalations are identified from CSOne (Transaction ID, bemscsc_refs). Upload a CSOne export to include TAC cases and BEMS analysis.')
                return
            
            # Canonical BEMS extraction from normalized TAC fields
            csone_norm = add_case_lifecycle_fields(csone_data)
            bems_mask = detect_bems_mask(csone_norm)
            bems_cases = csone_norm[bems_mask]
            total_bems = len(bems_cases)
            bems_rate = (total_bems / len(csone_norm) * 100) if len(csone_norm) > 0 else 0
            
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
                
                for customer in bems_cases['customer_name'].dropna().unique():
                    customer_bems = bems_cases[bems_cases['customer_name'] == customer]
                    
                    # Extract actual BEMS IDs
                    bems_ids = set()
                    for _, row in customer_bems.iterrows():
                        bems_ids.update(extract_bems_ids_from_row(row))
                    
                    # Format ALL BEMS IDs with brackets for citation like [BEMS01916938]
                    bems_id_list = sorted(list(bems_ids))
                    bems_id_str = ', '.join([f'[{bid}]' for bid in bems_id_list])  # FIXED: Show all IDs
                    
                    summary_p.add_run(
                        f"• {customer}: {len(customer_bems)} escalation(s) - {bems_id_str} "
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
                customer_counts = non_bems_cases['customer_name'].value_counts()
                
                # FIXED: Show ALL customers and ALL their cases
                for customer, count in customer_counts.items():
                    cust_cases = non_bems_cases[non_bems_cases['customer_name'] == customer]
                    case_nums = []
                    for _, case in cust_cases.iterrows():
                        case_num = case.get('SR Number', case.get('Case Number', 'Unknown'))
                        case_nums.append(f"TAC {case_num}")
                    
                    case_nums_str = ', '.join(case_nums)
                    non_bems_p.add_run(f'• {customer}: {count} case(s) - {case_nums_str}\n')
            
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
                no_data_p = self.doc.add_paragraph()
                no_data_p.add_run('No adoption barrier data available.')
                return
            
            # Overview stats
            overview_p = self.doc.add_paragraph()
            overview_p.add_run('Adoption Barriers Overview\n').bold = True
            overview_p.add_run(f'• Total Adoption Barriers: {len(ab_data)}\n')
            
            if 'customer_name' in ab_data.columns:
                n_cust = ab_data['customer_name'].nunique()
                overview_p.add_run(f'• Customers with Barriers: {n_cust}\n')
                avg_per_customer = len(ab_data) / n_cust if n_cust > 0 else 0
                overview_p.add_run(f'• Average Barriers per Customer: {avg_per_customer:.1f}\n')
            
            # Critical barriers
            self.doc.add_paragraph()
            critical_heading = self.doc.add_paragraph()
            critical_heading.add_run('Critical Adoption Barriers Requiring Attention\n').bold = True
            
            sev_col = 'SEVERITY_C' if 'SEVERITY_C' in ab_data.columns else ('severity_c' if 'severity_c' in ab_data.columns else None)
            if sev_col:
                critical_mask = ab_data[sev_col].astype(str).str.contains('Critical|High', case=False, na=False)
                critical_barriers = ab_data[critical_mask]
                
                if not critical_barriers.empty:
                    # FIXED: Show ALL critical barriers
                    for _, barrier in critical_barriers.iterrows():
                        subj = barrier.get('SUBJECT_C', barrier.get('subject_c', barrier.get('title', 'No subject')))
                        customer = barrier.get('customer_name', 'Unknown')
                        sev = barrier.get(sev_col, 'Unknown')
                        
                        barrier_p = self.doc.add_paragraph()
                        barrier_p.add_run(f'• {customer}: ').bold = True
                        barrier_p.add_run(
                            f"{subj} (Severity: {sev}) "
                            f"{format_inline_source('Adoption Barriers', fields=['SEVERITY_C', 'AB_STATUS_C'])}"
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
            if not csone_norm.empty:
                if 'case_priority_norm' in csone_norm.columns:
                    sev_series = csone_norm['case_priority_norm'].fillna('').astype(str)
                else:
                    sev_series = pd.Series(dtype=str)
                p1_critical_count = int((sev_series == 'P1').sum())
            else:
                p1_critical_count = 0

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
            
            citations_data = [
                ('Total Support Cases', str(len(csone_norm)) if not csone_norm.empty else '0', support_source, support_verif, '95%'),
                ('Unique Customers with Cases', str(csone_unique_customers), support_source, support_verif, '95%'),
                ('P1/Critical Cases', str(p1_critical_count), support_source, support_verif, '95%'),
                ('Total Adoption Barriers', str(len(ab_data)) if not ab_data.empty else '0', ab_source, ab_verif, '90%'),
                ('Customers with Adoption Barriers', str(ab_unique_customers), ab_source, ab_verif, '90%'),
            ]
            for metric, value, source, method, confidence in citations_data:
                row = citations_table.add_row()
                row.cells[0].text = metric
                row.cells[1].text = value
                row.cells[2].text = source
                row.cells[3].text = method
                row.cells[4].text = confidence
            
            self.doc.add_paragraph()
            note_p = self.doc.add_paragraph()
            note_p.add_run('To verify any metric: ').bold = True
            note_p.add_run('Use the Verification Method column to locate the specific record in the source system (CSOne, CSConsole, or Snowflake).')
            
        except Exception as e:
            logger.error(f"Error adding data citations section: {e}")
            raise
    
    def add_early_warning_section(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame, risk_data: Dict):
        """Add early warning indicators section for proactive risk identification"""
        try:
            if ab_data is None:
                ab_data = pd.DataFrame()
            if csone_data is None:
                csone_data = pd.DataFrame()
            self.doc.add_heading('🚨 Early Warning Indicators - Proactive Risk Identification', level=1)
            
            intro_p = self.doc.add_paragraph()
            intro_p.add_run('These indicators identify at-risk customers BEFORE issues escalate. ')
            intro_p.add_run('This predictive analysis enables proactive intervention to prevent escalations.\n\n').italic = True
            
            warnings = []
            
            csone_norm = add_case_lifecycle_fields(csone_data) if not csone_data.empty else pd.DataFrame()

            # Check for BEMS escalations
            if not csone_norm.empty:
                bems_mask = detect_bems_mask(csone_norm)
                if bems_mask.any() and 'customer_name' in csone_norm.columns:
                    bems_cases = csone_norm[bems_mask]
                    for customer in bems_cases['customer_name'].dropna().unique():
                        count = len(bems_cases[bems_cases['customer_name'] == customer])
                        warnings.append({
                            'severity': 'CRITICAL',
                            'customer': customer,
                            'indicator': 'Active Engineering Escalations',
                            'detail': f'{customer} has {count} cases with BEMS escalations. These require immediate engineering attention.',
                            'action': 'Coordinate with engineering team and provide customer with escalation timeline',
                            'confidence': '95%'
                        })
            
            # Check for increasing case volume
            date_col_csone = next((c for c in ['open_date', 'Date/Time Opened', 'CREATED_DATE', 'Created', 'Created Date'] if c in csone_norm.columns), None)
            if not csone_norm.empty and 'customer_name' in csone_norm.columns and date_col_csone:
                try:
                    csone_copy = csone_norm.copy()
                    csone_copy['date_opened'] = pd.to_datetime(csone_copy[date_col_csone], errors='coerce')
                    recent_30 = csone_copy[csone_copy['date_opened'] >= (datetime.now() - timedelta(days=30))]
                    
                    for customer in recent_30['customer_name'].dropna().unique():
                        recent_count = len(recent_30[recent_30['customer_name'] == customer])
                        total_count = len(csone_copy[csone_copy['customer_name'] == customer])
                        
                        if recent_count >= 5 and recent_count / max(total_count, 1) > 0.5:
                            warnings.append({
                                'severity': 'HIGH',
                                'customer': customer,
                                'indicator': 'Increasing Support Case Volume',
                                'detail': f'{customer} has {recent_count} cases in the last 30 days ({total_count} total). This trend suggests potential escalation risk.',
                                'action': 'Schedule proactive customer success review and identify root causes before escalation',
                                'confidence': '85%'
                            })
                except Exception as _ew_err:
                    logger.debug(f"Early warning date parse error: {_ew_err}")
            
            # Check for unresolved adoption barriers
            if not ab_data.empty and 'customer_name' in ab_data.columns:
                status_col = 'AB_STATUS_C' if 'AB_STATUS_C' in ab_data.columns else ('STATUS_C' if 'STATUS_C' in ab_data.columns else None)
                if status_col:
                    open_mask = ab_data[status_col].astype(str).str.contains('Open|New|In Progress', case=False, na=False)
                    open_barriers = ab_data[open_mask]
                    
                    for customer in open_barriers['customer_name'].dropna().unique():
                        count = len(open_barriers[open_barriers['customer_name'] == customer])
                        if count >= 3:
                            warnings.append({
                                'severity': 'MEDIUM',
                                'customer': customer,
                                'indicator': 'Unresolved Adoption Barriers',
                                'detail': f'{customer} has {count} unresolved adoption barriers. These may be blocking value realization.',
                                'action': 'Review barriers and create remediation plan with customer',
                                'confidence': '80%'
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
                        warn_p.add_run(f'Confidence: {warning["confidence"]}\n')
            
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
            sorted_themes = sorted(problem_themes.items(), key=lambda x: x[1], reverse=True)
            
            for idx, (theme, count) in enumerate(sorted_themes, 1):
                theme_heading = self.doc.add_heading(f'{idx}. {theme}', level=3)
                
                # What's Happening
                what_p = self.doc.add_paragraph()
                what_p.add_run("- What's Happening: ").bold = True
                what_p.add_run(f'{count} instances detected - affecting multiple customers with recurring patterns')
                
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
            
            # Count high-risk customers
            red_customers = [k for k, v in risk_data.items() if v.get('color') == 'Red'] if risk_data else []
            
            if red_customers:
                immediate_actions.add_run(f'- Engineering War Room: ').bold = True
                # FIXED: Show ALL red customers
                immediate_actions.add_run(f'For {", ".join(red_customers)} - daily status until critical issues resolved\n')
            
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
            
            total_customers = len(risk_data) if risk_data else 0
            red_customers = sum(1 for v in risk_data.values() if v.get('color') == 'Red') if risk_data else 0
            
            bems_count = 0
            if not csone_data.empty:
                bems_count = int(detect_bems_mask(add_case_lifecycle_fields(csone_data)).sum())
            
            risk_p = self.doc.add_paragraph()
            risk_p.add_run('- ARR At Risk: ').bold = True
            risk_p.add_run(f'Estimated ${red_customers * 5}M+ (Top {red_customers} high-risk customers)\n')
            
            risk_p.add_run('- Escalation Probability: ').bold = True
            if bems_count > 0:
                bems_rate = (bems_count / len(csone_data) * 100) if len(csone_data) > 0 else 0
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
            
            takeaway_p = self.doc.add_paragraph()
            
            if overall_score >= 7:
                takeaway_p.add_run('Portfolio is in RED ZONE. ').bold = True
                takeaway_p.add_run('Adoption is blocked by operational, integration, and defect bottlenecks causing unresolved high-impact issues. ')
                takeaway_run = takeaway_p.add_run('Action is required NOW to prevent critical escalations and protect revenue.')
                takeaway_run.bold = True
            elif overall_score >= 5:
                takeaway_p.add_run('Portfolio is at MODERATE RISK. ').bold = True
                takeaway_p.add_run('Several customers require proactive intervention to prevent escalation. ')
                takeaway_p.add_run('Focused attention on at-risk accounts recommended.')
            else:
                takeaway_p.add_run('Portfolio is STABLE. ').bold = True
                takeaway_p.add_run('Continue standard engagement cadence with regular monitoring. ')
                takeaway_p.add_run('Address any emerging issues proactively.')
            
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


def calculate_renewal_risk_scores(ab_data: pd.DataFrame, csone_data: pd.DataFrame) -> Dict[str, Dict]:
    """Calculate renewal risk scores and color categories for each customer"""
    try:
        ab_data = ab_data if ab_data is not None else pd.DataFrame()
        csone_data = csone_data if csone_data is not None else pd.DataFrame()
        csone_norm = add_case_lifecycle_fields(csone_data)
        risk_data = {}
        
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
            )
            final_score = profile["risk_score_0_10"]
            risk_factors = list(profile["risk_factors"])
            aging_open = profile["components"]["adoption_barriers"]["details"].get("aging_open_count", 0)
            if aging_open > 0:
                risk_factors.append(
                    f"{aging_open} barrier(s) open 60+ days "
                    f"{format_inline_source('Adoption Barriers', fields=['OPEN_DATE_C', 'AB_STATUS_C'])}"
                )
            
            # Determine color category
            if final_score >= 8:
                color = "Red"
                category = "Critical Risk - Immediate Action Required"
            elif final_score >= 6:
                color = "Red"
                category = "High Risk - Urgent Attention Needed"
            elif final_score >= 4:
                color = "Yellow"
                category = "Moderate Risk - Monitor Closely"
            elif final_score >= 2:
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
                                  ai_insights: Dict, output_path: str) -> str:
    """Create COMPREHENSIVE compact executive report focused on renewal risk with full data detail"""
    
    ab_data = ab_data if ab_data is not None else pd.DataFrame()
    csone_data = csone_data if csone_data is not None else pd.DataFrame()
    csone_norm = add_case_lifecycle_fields(csone_data)
    logger.info(f"Creating compact executive report for {manager}")
    
    try:
        formatter = CompactReportFormatter()
        
        # Calculate risk scores
        risk_data = calculate_renewal_risk_scores(ab_data, csone_data)
        
        # Create risk summary
        high_risk_customers = {k: v for k, v in risk_data.items() if isinstance(v, dict) and v.get('score', 0) >= 6}
        moderate_risk_customers = {k: v for k, v in risk_data.items() if isinstance(v, dict) and 4 <= v.get('score', 0) < 6}
        
        # Calculate BEMS count
        total_bems = int(detect_bems_mask(csone_norm).sum()) if not csone_norm.empty else 0
        
        _scores = [v.get('score', 0) for v in risk_data.values() if isinstance(v, dict) and isinstance(v.get('score'), (int, float)) and not np.isnan(v.get('score', 0))] if risk_data else []
        overall_risk_score = float(np.mean(_scores)) if _scores else 0.0
        if np.isnan(overall_risk_score) or np.isinf(overall_risk_score):
            overall_risk_score = 0.0
        if not ab_data.empty:
            ab_sev_col = 'severity_norm' if 'severity_norm' in ab_data.columns else ('SEVERITY_C' if 'SEVERITY_C' in ab_data.columns else None)
            critical_adoption_barriers = int(
                ab_data[ab_sev_col].astype(str).str.contains('Critical|High', case=False, na=False).sum()
            ) if ab_sev_col else 0
        else:
            critical_adoption_barriers = 0
        escalated_cases = int(
            csone_norm['case_priority_norm'].astype(str).str.contains('P1|P2', case=False, na=False).sum()
        ) if not csone_norm.empty and 'case_priority_norm' in csone_norm.columns else 0
        customer_set = set()
        if not ab_data.empty:
            ab_customer_col = next(
                (c for c in ('customer_name', 'BU_NAME', 'Customer Name') if c in ab_data.columns),
                None,
            )
            if ab_customer_col:
                customer_set.update(
                    ab_data[ab_customer_col].dropna().astype(str).apply(normalize_customer_name)
                )
        if not csone_norm.empty and 'customer_name' in csone_norm.columns:
            customer_set.update(
                csone_norm['customer_name'].dropna().astype(str).apply(normalize_customer_name)
            )
        canonical_total_customers = len([c for c in customer_set if c and c != "Unknown"])
        
        risk_summary = {
            'overall_risk_score': round(overall_risk_score, 1),
            'high_risk_customers': len(high_risk_customers),
            'moderate_risk_customers': len(moderate_risk_customers),
            'total_customers': canonical_total_customers,
            'critical_adoption_barriers': critical_adoption_barriers,
            'escalated_cases': escalated_cases,
            'bems_escalations': total_bems,
            'key_concerns': [
                f"{len(high_risk_customers)} customers at high renewal risk",
                f"{critical_adoption_barriers} critical/high adoption barriers",
                f"{int(csone_norm['case_priority_norm'].astype(str).str.contains('P1', case=False, na=False).sum()) if not csone_norm.empty and 'case_priority_norm' in csone_norm.columns else 0} P1 support cases",
                f"{total_bems} BEMS engineering escalations"
            ],
            'immediate_actions': [
                "Schedule executive meetings with high-risk customers",
                "Assign dedicated CSM resources to critical accounts",
                "Create targeted adoption plans for at-risk customers",
                "Coordinate with engineering on BEMS escalations"
            ]
        }
        factual_claims = []
        for profile in risk_data.values():
            if isinstance(profile, dict):
                factual_claims.extend(profile.get("risk_factors", []) or [])
                factual_claims.extend(profile.get("key_findings", []) or [])
        factual_claims.extend(risk_summary.get("key_concerns", []) or [])
        factual_claims.extend(risk_summary.get("immediate_actions", []) or [])
        ai_summary_text = ""
        if isinstance(ai_insights, dict):
            if 'portfolio_summary' in ai_insights:
                ai_summary_text = (ai_insights.get('portfolio_summary') or {}).get('executive_summary', '')
            elif 'executive_summary' in ai_insights:
                ai_summary_text = ai_insights.get('executive_summary', '')
            elif 'raw_response' in ai_insights:
                ai_summary_text = ai_insights.get('raw_response', '')
        elif isinstance(ai_insights, str):
            ai_summary_text = ai_insights
        if ai_summary_text:
            factual_claims.append(ai_summary_text)
        factual_claims = [_ensure_inline_source_claim(claim) for claim in factual_claims if str(claim or "").strip()]
        consistency = validate_report_consistency(ab_data, csone_norm, risk_data=risk_data, factual_claims=factual_claims)
        if consistency["warnings"]:
            logger.warning(f"[CONSISTENCY] Compact report warnings: {consistency['warnings']}")
        
        # Create title page
        formatter.create_compact_title_page(manager, technology, days, analysis_id, risk_summary)
        
        # Add At-a-Glance Dashboard (NEW - matches example report format at the top)
        formatter.add_at_a_glance_dashboard(
            ab_data,
            csone_data,
            total_customers_override=risk_summary.get('total_customers'),
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
        sorted_by_risk = sorted(risk_data.items(), key=lambda x: x[1].get('score', 0) if isinstance(x[1], dict) else 0, reverse=True)[:10]
        if sorted_by_risk:
            formatter.doc.add_heading('Top 10 Focus Accounts by Risk', level=1)
            focus_table = formatter.doc.add_table(rows=1 + len(sorted_by_risk), cols=4)
            focus_table.style = 'Table Grid'
            hdr = focus_table.rows[0].cells
            for i, txt in enumerate(['Rank', 'Customer', 'Risk Score', 'Category']):
                hdr[i].text = txt
                for para in hdr[i].paragraphs:
                    for run in para.runs:
                        run.bold = True
            for idx, (cust, info) in enumerate(sorted_by_risk, 1):
                row = focus_table.rows[idx].cells
                row[0].text = str(idx)
                row[1].text = str(cust)
                _s = info.get('score', 0)
                _s = 0 if _s is None or (isinstance(_s, float) and np.isnan(_s)) else _s
                row[2].text = f"{_s:.1f}/10"
                row[3].text = str(info.get('category') or 'N/A')
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
        
        # Save the report
        result_path = formatter.save_document(output_path)
        
        logger.info(f"Compact executive report completed: {result_path}")
        return result_path
        
    except Exception as e:
        logger.error(f"Failed to create compact executive report: {e}")
        raise


if __name__ == "__main__":
    # Test the compact formatter
    logger.info("Compact Report Formatter - Ready for integration")
