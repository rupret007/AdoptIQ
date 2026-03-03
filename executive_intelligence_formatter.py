"""
Executive Intelligence Formatter for AdoptIQ
Creates executive-ready compact reports with intelligence-driven insights.
Used for Compact Analysis Reports.
"""

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from datetime import datetime
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
import logging
import re

logger = logging.getLogger(__name__)

# Professional color palette
CISCO_BLUE = RGBColor(0x00, 0x7B, 0xC7)
CISCO_DARK_BLUE = RGBColor(0x00, 0x4F, 0x8B)
CISCO_LIGHT_BLUE = RGBColor(0x5C, 0xC7, 0xF0)
CISCO_GRAY = RGBColor(0x58, 0x59, 0x5B)
CISCO_LIGHT_GRAY = RGBColor(0xF5, 0xF5, 0xF5)

# Status colors
EXCELLENT_GREEN = RGBColor(0x00, 0x8B, 0x00)
SUCCESS_GREEN = RGBColor(0x28, 0xA7, 0x45)
WARNING_ORANGE = RGBColor(0xFF, 0x8C, 0x00)
DANGER_RED = RGBColor(0xDC, 0x14, 0x3C)
CRITICAL_RED = RGBColor(0x8B, 0x00, 0x00)


class ExecutiveIntelligenceFormatter:
    """
    Executive Intelligence Formatter for compact, insight-driven reports.
    Focuses on actionable intelligence and risk assessment.
    """
    
    def __init__(self, output_path: str = None):
        """Initialize the formatter"""
        self.doc = Document()
        self.output_path = output_path
        self._setup_document_settings()
        self._create_professional_styles()
    
    def _setup_document_settings(self):
        """Configure document-wide settings"""
        sections = self.doc.sections
        for section in sections:
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)
            section.left_margin = Inches(1.0)
            section.right_margin = Inches(1.0)
    
    def _create_professional_styles(self):
        """Create professional document styles"""
        styles = self.doc.styles
        
        # Executive Title
        if 'Executive Title' not in [s.name for s in styles]:
            title_style = styles.add_style('Executive Title', WD_STYLE_TYPE.PARAGRAPH)
            title_style.font.name = 'Segoe UI'
            title_style.font.size = Pt(24)
            title_style.font.bold = True
            title_style.font.color.rgb = CISCO_BLUE
            title_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
            title_style.paragraph_format.space_after = Pt(20)
        
        # Section Header
        if 'Section Header' not in [s.name for s in styles]:
            header_style = styles.add_style('Section Header', WD_STYLE_TYPE.PARAGRAPH)
            header_style.font.name = 'Segoe UI'
            header_style.font.size = Pt(14)
            header_style.font.bold = True
            header_style.font.color.rgb = CISCO_DARK_BLUE
            header_style.paragraph_format.space_before = Pt(16)
            header_style.paragraph_format.space_after = Pt(8)
    
    def _clean_text(self, text: str) -> str:
        """Clean markdown and formatting from text"""
        if not text:
            return ""
        
        # Remove markdown
        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        text = re.sub(r'\*(.*?)\*', r'\1', text)
        text = re.sub(r'`(.*?)`', r'\1', text)
        text = re.sub(r'---+\s*', '', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        return text.strip()
    
    def add_title_page(self, manager: str, technology: str, days: int):
        """Add executive title page"""
        # Logo/Branding
        logo_para = self.doc.add_paragraph()
        logo_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        logo_run = logo_para.add_run("● CISCO AdoptIQ")
        logo_run.font.name = 'Segoe UI'
        logo_run.font.size = Pt(16)
        logo_run.font.color.rgb = CISCO_BLUE
        logo_run.font.bold = True
        
        self.doc.add_paragraph()
        
        # Main Title
        title_para = self.doc.add_paragraph()
        title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_run = title_para.add_run(f"Executive Intelligence Report")
        title_run.font.name = 'Segoe UI'
        title_run.font.size = Pt(24)
        title_run.font.bold = True
        title_run.font.color.rgb = CISCO_BLUE
        
        # Subtitle
        subtitle_para = self.doc.add_paragraph()
        subtitle_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        subtitle_run = subtitle_para.add_run(f"{manager} | {technology} | {days} Day Analysis")
        subtitle_run.font.name = 'Segoe UI'
        subtitle_run.font.size = Pt(14)
        subtitle_run.font.color.rgb = CISCO_GRAY
        
        # Date
        self.doc.add_paragraph()
        date_para = self.doc.add_paragraph()
        date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        date_run = date_para.add_run(f"Generated: {datetime.now().strftime('%B %d, %Y')}")
        date_run.font.name = 'Segoe UI'
        date_run.font.size = Pt(11)
        date_run.font.color.rgb = CISCO_GRAY
    
    def add_executive_dashboard(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame,
                               risk_scores: Dict[str, Any], risk_summary: Dict[str, Any],
                               team_subs_df: pd.DataFrame = None,
                               csconsole_action_plans: pd.DataFrame = None,
                               csconsole_customer_pulse: pd.DataFrame = None,
                               csconsole_success_priorities: pd.DataFrame = None,
                               csconsole_adoption_barriers: pd.DataFrame = None,
                               software_defects: Dict = None, psirt_vulns: Dict = None):
        """Add executive dashboard with key metrics using ALL data sources"""
        self.doc.add_paragraph()
        
        # Dashboard header
        header = self.doc.add_heading('At-a-Glance Dashboard', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = CISCO_BLUE
        
        # Create metrics table - expanded to include software defects and vulnerabilities (7 columns total)
        metrics_table = self.doc.add_table(rows=2, cols=7)
        metrics_table.style = 'Light Grid Accent 1'
        
        # CRITICAL FIX: Use comprehensive function to get ALL customers from ALL available data sources
        # This ensures consistent customer counts across all report types
        # team_subs_df is the PRIMARY source (unfiltered, contains all customers assigned to manager)
        try:
            from app_simple import _get_all_customers_from_all_sources
            all_customers_set = _get_all_customers_from_all_sources(
                ab_norm=ab_data if ab_data is not None else pd.DataFrame(),
                csone_df=csone_data if csone_data is not None else pd.DataFrame(),
                team_subs_df=team_subs_df if team_subs_df is not None else pd.DataFrame(),
                csconsole_action_plans=csconsole_action_plans if csconsole_action_plans is not None else pd.DataFrame(),
                csconsole_customer_pulse=csconsole_customer_pulse if csconsole_customer_pulse is not None else pd.DataFrame(),
                csconsole_success_priorities=csconsole_success_priorities if csconsole_success_priorities is not None else pd.DataFrame(),
                csconsole_adoption_barriers=csconsole_adoption_barriers if csconsole_adoption_barriers is not None else pd.DataFrame()
            )
            total_customers = len(all_customers_set)
            logger.info(f"[[CUSTOMER_COUNT]] Executive Intelligence Report - Total unique customers from all data sources: {total_customers}")
            logger.info(f"[[CUSTOMER_COUNT]] Team subscriptions (PRIMARY SOURCE): {len(team_subs_df['BU_NAME'].unique()) if not team_subs_df.empty and 'BU_NAME' in team_subs_df.columns else 0} customers")
            logger.info(f"[[CUSTOMER_COUNT]] Team subscriptions DataFrame has {len(team_subs_df)} rows")
            if not team_subs_df.empty and 'BU_NAME' in team_subs_df.columns:
                sample_customers = team_subs_df['BU_NAME'].dropna().unique()[:5].tolist()
                logger.info(f"[[CUSTOMER_COUNT]] Sample customers from team_subs_df: {sample_customers}")
        except (ImportError, AttributeError) as e:
            logger.warning(f"[[WARNING]] Could not import _get_all_customers_from_all_sources, using team_subs_df count: {e}")
            # Fallback: Use team_subs_df as primary source, then AB, then CSOne
            if team_subs_df is not None and not team_subs_df.empty and 'BU_NAME' in team_subs_df.columns:
                total_customers = len(team_subs_df['BU_NAME'].dropna().unique())
            elif ab_data is not None and not ab_data.empty and 'customer_name' in ab_data.columns:
                total_customers = len(ab_data['customer_name'].unique())
            else:
                total_customers = 0
        total_cases = len(csone_data) if not csone_data.empty else 0
        
        p1_count = 0
        p2_count = 0
        bems_count = 0
        
        if not csone_data.empty:
            severity_col = None
            for col in ['Severity', 'severity', 'Priority', 'priority']:
                if col in csone_data.columns:
                    severity_col = col
                    break
            
            if severity_col:
                p1_count = len(csone_data[csone_data[severity_col].astype(str).str.contains('P1|1|Critical', case=False, na=False)])
                p2_count = len(csone_data[csone_data[severity_col].astype(str).str.contains('P2|2|High', case=False, na=False)])
            
            # Use centralized BEMS detection
            try:
                from app_simple import detect_bems_escalations
            except ImportError:
                try:
                    from adoptiq_backend import detect_bems_escalations
                except ImportError:
                    try:
                        from bems_escalation_analyzer import detect_bems_escalations
                    except ImportError:
                        # Fallback: simple BEMS detection
                        def detect_bems_escalations(df):
                            return pd.DataFrame(), 0
            bems_cases, bems_count = detect_bems_escalations(csone_data)
        
        # Extract software defects and PSIRT vulnerabilities counts
        defect_count = 0
        vuln_count = 0
        if software_defects:
            defect_count = software_defects.get('total_defects', 0)
        if psirt_vulns:
            vuln_count = psirt_vulns.get('total_vulnerabilities', 0)
        
        # Header row - expanded to include software defects and vulnerabilities
        headers = ['Total Customers', 'Support Cases', 'Critical (P1)', 'High (P2)', 'BEMS Escalations', 'Software Defects', 'Security Vulnerabilities']
        for i, header_text in enumerate(headers):
            cell = metrics_table.rows[0].cells[i]
            cell.text = header_text
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True
                    run.font.size = Pt(10)
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Values row - expanded to include software defects and vulnerabilities
        values = [str(total_customers), str(total_cases), str(p1_count), str(p2_count), str(bems_count), str(defect_count), str(vuln_count)]
        colors = [None, None, DANGER_RED if p1_count > 0 else None, WARNING_ORANGE if p2_count > 0 else None, CRITICAL_RED if bems_count > 0 else None, WARNING_ORANGE if defect_count > 0 else None, DANGER_RED if vuln_count > 0 else None]
        
        for i, (value, color) in enumerate(zip(values, colors)):
            cell = metrics_table.rows[1].cells[i]
            cell.text = value
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(16)
                    run.bold = True
                    if color:
                        run.font.color.rgb = color
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        self.doc.add_paragraph()
        
        # Risk Summary
        if risk_summary:
            risk_para = self.doc.add_paragraph()
            risk_para.add_run('Risk Summary: ').font.bold = True
            risk_para.add_run(f"Overall Risk Score: {risk_summary.get('overall_risk_score', 'N/A')} | ")
            risk_para.add_run(f"High Risk Customers: {risk_summary.get('high_risk_customers', 0)} | ")
            risk_para.add_run(f"Moderate Risk: {risk_summary.get('moderate_risk_customers', 0)}")
    
    def add_executive_summary(self, ai_insights: Dict[str, Any]):
        """Add AI-generated executive summary"""
        header = self.doc.add_heading('Executive Summary', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = CISCO_BLUE
        
        summary_text = ""
        if ai_insights:
            if 'executive_summary' in ai_insights:
                summary_text = ai_insights['executive_summary']
            elif 'portfolio_summary' in ai_insights and 'executive_summary' in ai_insights['portfolio_summary']:
                summary_text = ai_insights['portfolio_summary']['executive_summary']
            elif 'raw_response' in ai_insights:
                summary_text = ai_insights['raw_response']
        
        if summary_text:
            clean_text = self._clean_text(summary_text)
            self._parse_and_add_content(clean_text)
        else:
            para = self.doc.add_paragraph()
            para.add_run("AI-generated insights are being processed. Please check back shortly.")
    
    def _parse_and_add_content(self, text: str):
        """Parse text content and add to document with proper formatting"""
        if not text:
            return
        
        sections = text.split('\n\n')
        
        for section in sections:
            if not section.strip():
                continue
            
            lines = section.strip().split('\n')
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Section headers
                if line.endswith(':') and len(line) < 80:
                    header_text = line.rstrip(':')
                    header = self.doc.add_heading(header_text, level=2)
                    if header.runs:
                        header.runs[0].font.color.rgb = CISCO_DARK_BLUE
                        header.runs[0].font.size = Pt(12)
                
                # Bullet points
                elif line.startswith(('•', '-', '*')):
                    bullet_text = line.lstrip('•-* ')
                    para = self.doc.add_paragraph(style='List Bullet')
                    run = para.add_run(bullet_text)
                    run.font.name = 'Segoe UI'
                    run.font.size = Pt(11)
                
                # Numbered items
                elif re.match(r'^\d+[\.\)]\s', line):
                    number_text = re.sub(r'^\d+[\.\)]\s*', '', line)
                    para = self.doc.add_paragraph(style='List Number')
                    run = para.add_run(number_text)
                    run.font.name = 'Segoe UI'
                    run.font.size = Pt(11)
                
                # Regular paragraph
                else:
                    para = self.doc.add_paragraph()
                    run = para.add_run(line)
                    run.font.name = 'Segoe UI'
                    run.font.size = Pt(11)
    
    def add_risk_analysis_section(self, risk_scores: Dict[str, Any], ab_data: pd.DataFrame, csone_data: pd.DataFrame):
        """Add detailed risk analysis section"""
        header = self.doc.add_heading('Risk Analysis', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = CISCO_BLUE
        
        # High-risk customers
        high_risk = {k: v for k, v in risk_scores.items() if isinstance(v, dict) and v.get('score', 0) >= 6}
        
        if high_risk:
            subheader = self.doc.add_heading('High-Risk Customers Requiring Immediate Attention', level=2)
            if subheader.runs:
                subheader.runs[0].font.color.rgb = DANGER_RED
            
            # Create table for high-risk customers
            if len(high_risk) > 0:
                table = self.doc.add_table(rows=1, cols=3)
                table.style = 'Light Grid Accent 1'
                
                # Header
                headers = ['Customer', 'Risk Score', 'Key Issues']
                for i, h in enumerate(headers):
                    table.rows[0].cells[i].text = h
                    table.rows[0].cells[i].paragraphs[0].runs[0].font.bold = True
                
                # FIXED: Show ALL high-risk customers
                for customer, data in sorted(high_risk.items(), key=lambda x: x[1].get('score', 0), reverse=True):
                    row = table.add_row().cells
                    row[0].text = str(customer)  # Full customer name
                    row[1].text = f"{data.get('score', 'N/A')}/10"
                    
                    # Key issues
                    issues = []
                    if data.get('ab_count', 0) > 0:
                        issues.append(f"{data.get('ab_count')} barriers")
                    if data.get('case_count', 0) > 0:
                        issues.append(f"{data.get('case_count')} cases")
                    row[2].text = ', '.join(issues) if issues else 'N/A'
        else:
            para = self.doc.add_paragraph()
            para.add_run("No high-risk customers identified in this analysis period.")
    
    def add_bems_escalation_section(self, csone_data: pd.DataFrame):
        """Add BEMS escalation analysis section - CRITICAL for executive visibility"""
        header = self.doc.add_heading('🚨 BEMS Escalation Analysis', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = DANGER_RED
        
        # Intro paragraph
        intro = self.doc.add_paragraph()
        intro.add_run("BEMS (Back-End Engineering Management System) escalations indicate complex technical issues "
                     "requiring specialized backend engineering attention. These are critical indicators of customer risk.")
        
        # Extract BEMS data
        total_bems = 0
        bems_by_customer = {}
        bems_ids = []
        
        if csone_data is not None and not csone_data.empty:
            bems_mask = pd.Series([False] * len(csone_data), index=csone_data.index)
            
            # Check Transaction ID column (primary BEMS source)
            if 'Transaction ID' in csone_data.columns:
                bems_mask |= csone_data['Transaction ID'].astype(str).str.contains('BEMS', case=False, na=False)
            
            # Check bemscsc_refs column (secondary BEMS source)
            if 'bemscsc_refs' in csone_data.columns:
                bems_mask |= csone_data['bemscsc_refs'].astype(str).str.contains('BEMS', case=False, na=False)
            
            bems_cases = csone_data[bems_mask]
            total_bems = len(bems_cases)
            
            # Group by customer
            if not bems_cases.empty and 'customer_name' in bems_cases.columns:
                bems_by_customer = bems_cases.groupby('customer_name').size().to_dict()
                
                # Extract BEMS IDs
                if 'Transaction ID' in bems_cases.columns:
                    for tid in bems_cases['Transaction ID'].dropna():
                        if 'BEMS' in str(tid).upper():
                            bems_ids.append(str(tid))
        
        # Summary metrics
        metrics_para = self.doc.add_paragraph()
        metrics_para.add_run('Summary Metrics:\n').bold = True
        metrics_para.add_run(f'• Total BEMS Escalations: ')
        count_run = metrics_para.add_run(f'{total_bems}')
        count_run.bold = True
        if total_bems > 0:
            count_run.font.color.rgb = DANGER_RED
        metrics_para.add_run(f'\n• Customers Affected: {len(bems_by_customer)}\n')
        
        if bems_ids:
            metrics_para.add_run(f'• BEMS IDs: ')
            # Format with brackets for citation like [BEMS01916938] - show all IDs
            ids_run = metrics_para.add_run(', '.join([f'[{bid}]' for bid in bems_ids]))
            ids_run.font.color.rgb = CISCO_GRAY
        
        # Customer breakdown
        if bems_by_customer:
            self.doc.add_paragraph()
            breakdown_para = self.doc.add_paragraph()
            breakdown_para.add_run('BEMS Escalations by Customer:\n').bold = True
            
            # FIXED: Show ALL customers with BEMS escalations
            for customer, count in sorted(bems_by_customer.items(), key=lambda x: x[1], reverse=True):
                breakdown_para.add_run(f'• {customer}: ')
                count_run = breakdown_para.add_run(f'{count} escalation(s)')
                count_run.font.color.rgb = DANGER_RED if count > 1 else WARNING_ORANGE
                breakdown_para.add_run('\n')
        else:
            no_bems = self.doc.add_paragraph()
            no_bems.add_run('✅ No BEMS escalations detected in this analysis period - positive indicator.')
    
    def add_software_defects_section(self, software_defects: Dict):
        """Add Software Defects section - BST/CSC IDs extracted from data"""
        header = self.doc.add_heading('Software Defects (BST/CSC IDs)', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = DANGER_RED
        
        defect_count = software_defects.get('total_defects', 0)
        cases_with_defects = software_defects.get('total_cases_with_defects', 0)
        
        summary_para = self.doc.add_paragraph()
        summary_para.add_run(f'Total Software Defects Identified: ').bold = True
        summary_para.add_run(f'{defect_count} unique BST/CSC defects found in {cases_with_defects} support cases.')
        
        defect_by_customer = software_defects.get('defect_by_customer', {})
        if defect_by_customer:
            self.doc.add_paragraph()
            for customer, defects in defect_by_customer.items():
                defect_list = sorted(set(defects))
                customer_para = self.doc.add_paragraph()
                customer_para.add_run(f'• {customer}: ').bold = True
                customer_para.add_run(', '.join([f'[{d}]' for d in defect_list]))
    
    def add_psirt_vulnerabilities_section(self, psirt_vulns: Dict):
        """Add PSIRT Vulnerabilities section - CVEs and PSIRT advisories extracted from data"""
        header = self.doc.add_heading('Security Vulnerabilities (CVEs & PSIRT)', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = DANGER_RED
        
        vuln_count = psirt_vulns.get('total_vulnerabilities', 0)
        cve_ids = psirt_vulns.get('cve_ids', set())
        psirt_advisories = psirt_vulns.get('psirt_advisories', set())
        
        summary_para = self.doc.add_paragraph()
        summary_para.add_run(f'Total Vulnerabilities Identified: ').bold = True
        summary_para.add_run(f'{vuln_count} ({len(cve_ids)} CVEs, {len(psirt_advisories)} PSIRT advisories).')
        
        if cve_ids:
            self.doc.add_paragraph()
            cve_para = self.doc.add_paragraph()
            cve_para.add_run('CVE IDs: ').bold = True
            cve_para.add_run(', '.join([f'[{cve}]' for cve in sorted(cve_ids)]))
        
        if psirt_advisories:
            self.doc.add_paragraph()
            psirt_para = self.doc.add_paragraph()
            psirt_para.add_run('PSIRT Advisories: ').bold = True
            psirt_para.add_run(', '.join([f'[{psirt}]' for psirt in sorted(psirt_advisories)]))
        
        vulnerability_by_customer = psirt_vulns.get('vulnerability_by_customer', {})
        if vulnerability_by_customer:
            self.doc.add_paragraph()
            for customer, vulns in vulnerability_by_customer.items():
                vuln_list = sorted(set(vulns))
                customer_para = self.doc.add_paragraph()
                customer_para.add_run(f'• {customer}: ').bold = True
                customer_para.add_run(', '.join([f'[{v}]' for v in vuln_list]))
    
    def add_known_defects_section(self, ext_bugs: List = None):
        """Add Known Software Defects section - CRITICAL for matching example reports"""
        header = self.doc.add_heading('Known Software Defects (help.webex.com)', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = WARNING_ORANGE
        
        intro = self.doc.add_paragraph()
        intro.add_run("Known software defects from help.webex.com that may be impacting portfolio customers:")
        
        if ext_bugs and len(ext_bugs) > 0:
            # Summary
            summary_para = self.doc.add_paragraph()
            summary_para.add_run(f'Total Known Defects: ').bold = True
            summary_para.add_run(f'{len(ext_bugs)}')
            
            # FIXED: Display ALL defects for complete visibility
            self.doc.add_paragraph()
            for bug in ext_bugs:
                bug_id = bug.get('bug_id', 'Unknown')
                title = bug.get('title', 'No description available')
                source = bug.get('source_url', '')
                
                bug_para = self.doc.add_paragraph()
                # Format defect ID for easy citation and verification
                bug_run = bug_para.add_run(f'• [{bug_id}]: ')
                bug_run.bold = True
                bug_run.font.color.rgb = CISCO_BLUE
                bug_para.add_run(title)
                if source:
                    bug_para.add_run(f'\n  Source: {source}').font.size = Pt(9)
        else:
            no_defects = self.doc.add_paragraph()
            no_defects.add_run('✅ No publicly documented software defects detected affecting this portfolio.')
    
    def add_service_incidents_section(self, ext_incidents: List = None):
        """Add Service Incidents section from status.webex.com"""
        header = self.doc.add_heading('Recent Service Incidents', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = WARNING_ORANGE
        
        intro = self.doc.add_paragraph()
        intro.add_run("Recent service incidents from status.webex.com that may have impacted portfolio customers:")
        
        if ext_incidents and len(ext_incidents) > 0:
            # Summary
            summary_para = self.doc.add_paragraph()
            summary_para.add_run(f'Total Incidents: ').bold = True
            summary_para.add_run(f'{len(ext_incidents)}')
            
            # FIXED: Display ALL incidents for complete visibility
            self.doc.add_paragraph()
            for incident in ext_incidents:
                inc_id = incident.get('id', incident.get('pub_id', 'Unknown'))
                title = incident.get('title', 'No description')
                date = incident.get('published', incident.get('date', 'Unknown date'))
                
                inc_para = self.doc.add_paragraph()
                inc_run = inc_para.add_run(f'• {inc_id}: ')
                inc_run.bold = True
                inc_para.add_run(f'{title} ({date})')
        else:
            no_incidents = self.doc.add_paragraph()
            no_incidents.add_run('✅ No significant service incidents detected in this analysis period.')
    
    def add_recommendations_section(self, ai_insights: Dict[str, Any]):
        """Add strategic recommendations section"""
        header = self.doc.add_heading('Strategic Recommendations', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = CISCO_BLUE
        
        # Default recommendations
        recommendations = [
            "Prioritize resolution of high-severity adoption barriers",
            "Implement proactive outreach for high-risk customers",
            "Schedule executive business reviews for customers with BEMS escalations",
            "Develop targeted success plans for moderate-risk accounts",
            "Monitor renewal dates and initiate early engagement strategy"
        ]
        
        for rec in recommendations:
            para = self.doc.add_paragraph(style='List Bullet')
            run = para.add_run(rec)
            run.font.name = 'Segoe UI'
            run.font.size = Pt(11)
    
    def add_data_citations_section(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame):
        """Add Data Citations section – uses canonical data sources (same across all AdoptIQ reports)."""
        header = self.doc.add_heading('Report Data Sources', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = CISCO_BLUE
        
        intro = self.doc.add_paragraph()
        intro.add_run("All data in this report is traceable. Same canonical sources used across all AdoptIQ reports.")
        
        try:
            from report_utils import get_data_sources_paragraph_text, get_data_sources_list
        except ImportError:
            get_data_sources_paragraph_text = lambda: (
                'Adoption Barriers: CSConsole/Snowflake. Support Cases: CSOne (TAC). '
                'BEMS: CSOne. Service Incidents: status.webex.com. Software Defects: help.webex.com. '
                'Customer Pulse, Action Plans, Success Priorities: CSConsole.'
            )
            get_data_sources_list = lambda: [
                ('Adoption Barriers', 'CSConsole / Snowflake C360_CS_TASK_C_VW', 'Query by Record ID'),
                ('Support Cases (TAC)', 'CSOne (TAC case data)', 'Query by Case Number in CSOne'),
                ('BEMS Escalations', 'CSOne (Transaction ID, bemscsc_refs)', 'BEMS IDs verifiable in CSOne'),
                ('Service Incidents', 'status.webex.com', 'Public RSS feed'),
                ('Software Defects', 'help.webex.com, CSC/BST refs', 'Defect IDs verifiable'),
                ('Customer Pulse, Action Plans, Success Priorities', 'CSConsole', 'By customer in CSConsole'),
            ]
        sources_para = self.doc.add_paragraph()
        sources_para.add_run(get_data_sources_paragraph_text())
        self.doc.add_paragraph()
        rec_para = self.doc.add_paragraph()
        rec_para.add_run("Record counts in this run:").bold = True
        citations = [
            f"• CSOne TAC Cases: {len(csone_data) if csone_data is not None and not csone_data.empty else 0} records",
            f"• Adoption Barriers: {len(ab_data) if ab_data is not None and not ab_data.empty else 0} records",
            "• BEMS: Extracted from CSOne Transaction ID / bemscsc_refs",
            "• Service Incidents: status.webex.com RSS feed",
            "• AI Insights: CircuIT AI using above data sources"
        ]
        for cite in citations:
            cite_para = self.doc.add_paragraph()
            cite_para.add_run(cite)
            cite_para.paragraph_format.left_indent = Inches(0.25)
        self.doc.add_paragraph()
        note = self.doc.add_paragraph()
        note.add_run("🔗 Verifiable IDs: ").bold = True
        note.add_run("All [BEMSxxxxxxxx], [CSCxxxxxxx], and TAC case numbers can be validated in their source systems.")
    
    def save(self, filepath: str = None):
        """Save the document"""
        save_path = filepath or self.output_path
        if save_path:
            self.doc.save(save_path)
            logger.info(f"Executive Intelligence Report saved to: {save_path}")
            return save_path
        else:
            raise ValueError("No output path specified")


def create_executive_intelligence_report(analysis_id: str, manager: str, technology: str, days: int,
                                        ab_data: pd.DataFrame, csone_data: pd.DataFrame,
                                        ai_insights: Dict[str, Any], ext_bugs: List = None,
                                        ext_incidents: List = None, risk_scores: Dict = None,
                                        risk_summary: Dict = None, output_path: str = None,
                                        arr_data: pd.DataFrame = None, arr_impact: Dict = None,
                                        chart_paths: List[str] = None, feature_requests: Dict = None,
                                        team_subs_df: pd.DataFrame = None,
                                        csconsole_action_plans: pd.DataFrame = None,
                                        csconsole_customer_pulse: pd.DataFrame = None,
                                        csconsole_success_priorities: pd.DataFrame = None,
                                        csconsole_adoption_barriers: pd.DataFrame = None,
                                        software_defects: Dict = None, psirt_vulns: Dict = None) -> str:
    """
    Create an Executive Intelligence Report.
    
    Args:
        analysis_id: Unique analysis identifier
        manager: Manager name
        technology: Technology focus
        days: Analysis period in days
        ab_data: Adoption barriers DataFrame
        csone_data: CSOne cases DataFrame
        ai_insights: AI-generated insights dictionary
        ext_bugs: External bugs list (optional)
        ext_incidents: External incidents list (optional)
        risk_scores: Risk scores dictionary (optional)
        risk_summary: Risk summary dictionary (optional)
        output_path: Output file path
        arr_data: ARR data DataFrame (optional)
        arr_impact: ARR impact dictionary (optional)
        chart_paths: Chart image paths (optional)
        feature_requests: Feature requests dictionary (optional)
    
    Returns:
        Path to the saved report
    """
    formatter = ExecutiveIntelligenceFormatter(output_path)
    
    # Add title page
    formatter.add_title_page(manager, technology, days)
    
    # Add page break after title
    formatter.doc.add_page_break()
    
    # Add executive dashboard with all data sources for accurate customer counting
    formatter.add_executive_dashboard(ab_data, csone_data, risk_scores or {}, risk_summary or {},
                                     team_subs_df=team_subs_df if team_subs_df is not None else pd.DataFrame(),
                                     csconsole_action_plans=csconsole_action_plans if csconsole_action_plans is not None else pd.DataFrame(),
                                     csconsole_customer_pulse=csconsole_customer_pulse if csconsole_customer_pulse is not None else pd.DataFrame(),
                                     csconsole_success_priorities=csconsole_success_priorities if csconsole_success_priorities is not None else pd.DataFrame(),
                                     csconsole_adoption_barriers=csconsole_adoption_barriers if csconsole_adoption_barriers is not None else pd.DataFrame(),
                                     software_defects=software_defects if software_defects is not None else {'total_defects': 0, 'total_cases_with_defects': 0, 'defect_by_customer': {}},
                                     psirt_vulns=psirt_vulns if psirt_vulns is not None else {'total_vulnerabilities': 0, 'cve_ids': set(), 'psirt_advisories': set(), 'vulnerability_by_customer': {}})
    
    # Add executive summary
    formatter.add_executive_summary(ai_insights)
    
    # Add risk analysis
    if risk_scores:
        formatter.add_risk_analysis_section(risk_scores, ab_data, csone_data)
    
    # Add BEMS escalation analysis (CRITICAL section that was missing)
    formatter.add_bems_escalation_section(csone_data)
    
    # Add Software Defects section (extracted from data - BST/CSC IDs)
    if software_defects and software_defects.get('total_defects', 0) > 0:
        formatter.add_software_defects_section(software_defects)
    
    # Add PSIRT Vulnerabilities section (extracted from data - CVEs/PSIRT)
    if psirt_vulns and psirt_vulns.get('total_vulnerabilities', 0) > 0:
        formatter.add_psirt_vulnerabilities_section(psirt_vulns)
    
    # Add Known Defects section (from help.webex.com)
    formatter.add_known_defects_section(ext_bugs)
    
    # Add Service Incidents section (from status.webex.com)
    formatter.add_service_incidents_section(ext_incidents)
    
    # Add recommendations
    formatter.add_recommendations_section(ai_insights)
    
    # Add Data Citations section (NEW - enables data verification)
    formatter.add_data_citations_section(ab_data, csone_data)
    
    # Save and return
    return formatter.save(output_path)

