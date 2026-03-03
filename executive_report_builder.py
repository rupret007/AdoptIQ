"""
ExecutiveReportBuilder - Clean Word report builder for AdoptIQ comprehensive reports.
Wraps python-docx Document with executive-ready formatting and delegates to adoptiq_backend.
"""
from docx import Document
from docx.shared import Inches

from adoptiq_backend import create_executive_title_page, append_to_word_report


class ExecutiveReportBuilder:
    """Builds executive Word reports with clean formatting (no markdown artifacts)."""

    def __init__(self):
        self.doc = Document()

    def add_title_page(self, manager: str, tech: str, days: int, portfolio_metrics: dict):
        """Add professional executive title page."""
        create_executive_title_page(self.doc, manager, tech, days, portfolio_metrics)

    def add_page_break(self):
        """Add page break."""
        self.doc.add_page_break()

    def add_heading(self, text: str, level: int = 1):
        """Add heading."""
        self.doc.add_heading(text, level=level)

    def add_paragraph(self, text: str = "", bold_sections=None):
        """Add paragraph. bold_sections is accepted for API compatibility but formatting is simple."""
        self.doc.add_paragraph(text)

    def save(self, path: str):
        """Save document to path."""
        self.doc.save(path)

    def parse_ai_output_and_add(self, ai_output: str):
        """Parse AI markdown output and add to document with clean formatting (no ** symbols)."""
        append_to_word_report(self.doc, ai_output)

    def _add_customer_separator(self):
        """Add visual separator between customer sections (page break)."""
        self.doc.add_page_break()
