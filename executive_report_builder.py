"""
ExecutiveReportBuilder - Clean Word report builder for AdoptIQ comprehensive reports.
Wraps python-docx Document with executive-ready formatting and delegates to adoptiq_backend.
"""
import logging

from docx import Document
from docx.shared import Inches

from adoptiq_backend import create_executive_title_page, append_to_word_report


logger = logging.getLogger(__name__)


# Round 7 / Phase 1.11: heuristic guards applied at the
# parse_ai_output_and_add boundary so prompt-injection attempts that
# arrive *inside* the model output are flagged and the offending
# segment is dropped before being written into the executive Word
# document.  These mirror the negative constraints that the prompt
# layer already enforces (no fabrication, no contact info, no system
# override).
_PROMPT_INJECTION_GUARDS = (
    "ignore previous instructions",
    "ignore the previous instructions",
    "disregard prior",
    "system:",
    "<<sys>>",
    "<|im_start|>",
    "you are now",
    "act as if you",
    "reveal the system prompt",
    "prompt injection",
)
_AI_OUTPUT_DELIMITER = "=== BEGIN AI_REPORT ==="
_AI_OUTPUT_END_DELIMITER = "=== END AI_REPORT ==="


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
        """Add paragraph.

        Round 7 / Phase 1.12: previously ``bold_sections`` was silently
        ignored, which let callers think they were producing bold runs
        when they weren't.  We now honor a simple, documented contract:
        ``bold_sections`` may be a list of substrings; each occurrence
        in ``text`` is rendered as a bold run, with the surrounding
        text rendered as plain runs.  Callers that don't care can omit
        the parameter (it still defaults to ``None``).
        """
        if not bold_sections:
            self.doc.add_paragraph(text)
            return

        try:
            para = self.doc.add_paragraph()
            remaining = str(text or "")
            patterns = [str(s) for s in bold_sections if s]
            while remaining:
                # Find the earliest match across all patterns; ties
                # break by longest substring so nested labels honor
                # the more specific pattern.
                next_idx = -1
                next_pat = ""
                for pat in patterns:
                    if not pat:
                        continue
                    idx = remaining.find(pat)
                    if idx < 0:
                        continue
                    if (next_idx < 0
                            or idx < next_idx
                            or (idx == next_idx and len(pat) > len(next_pat))):
                        next_idx = idx
                        next_pat = pat
                if next_idx < 0:
                    para.add_run(remaining)
                    break
                if next_idx > 0:
                    para.add_run(remaining[:next_idx])
                bold_run = para.add_run(next_pat)
                bold_run.bold = True
                remaining = remaining[next_idx + len(next_pat):]
        except Exception as _bold_err:
            logger.warning(
                "Round 7 / Phase 1.12: bold_sections rendering "
                "failed (%s); falling back to plain paragraph.",
                _bold_err,
            )
            self.doc.add_paragraph(text)

    def save(self, path: str):
        """Save document to path."""
        self.doc.save(path)

    def parse_ai_output_and_add(self, ai_output: str):
        """Parse AI markdown output and add to document with clean formatting.

        Round 7 / Phase 1.11: enforce delimiter + negative-constraint
        screening at this layer instead of trusting the upstream
        prompt to have filtered everything.  We:

        1. Strip the ``=== BEGIN AI_REPORT === ... === END AI_REPORT ===``
           fence if present so the renderer never embeds the literal
           delimiters in the document.
        2. Drop any line containing a known prompt-injection pattern
           and emit a WARNING with a hashed digest of the dropped
           segment so we can audit drift over time.
        3. Hand the cleaned text to ``append_to_word_report``.
        """
        cleaned = self._enforce_ai_output_contract(ai_output or "")
        append_to_word_report(self.doc, cleaned)

    @staticmethod
    def _enforce_ai_output_contract(ai_output: str) -> str:
        """Apply Round 7 / Phase 1.11 delimiter + negative constraints.

        Public for testing.
        """
        text = str(ai_output or "")
        # Step 1: unwrap delimiters if present.
        if _AI_OUTPUT_DELIMITER in text:
            try:
                _, _, after = text.partition(_AI_OUTPUT_DELIMITER)
                inner, _, _ = after.partition(_AI_OUTPUT_END_DELIMITER)
                text = inner if inner else after
            except Exception as _delim_err:
                logger.warning(
                    "Round 7 / Phase 1.11: AI output delimiter "
                    "stripping failed (%s); using raw output.",
                    _delim_err,
                )
        # Step 2: drop prompt-injection lines.
        kept = []
        dropped = 0
        for line in text.splitlines():
            lower = line.lower()
            if any(g in lower for g in _PROMPT_INJECTION_GUARDS):
                dropped += 1
                try:
                    import hashlib as _hl
                    _digest = _hl.sha256(line.encode("utf-8")).hexdigest()[:8]
                except Exception:
                    _digest = "unavailable"
                logger.warning(
                    "Round 7 / Phase 1.11: dropped suspected prompt-"
                    "injection line from AI output (digest=%s, len=%d).",
                    _digest, len(line),
                )
                continue
            kept.append(line)
        if dropped:
            logger.warning(
                "Round 7 / Phase 1.11: dropped %d AI output line(s) "
                "matching prompt-injection guards.", dropped,
            )
        return "\n".join(kept)

    def _add_customer_separator(self):
        """Add visual separator between customer sections (page break)."""
        self.doc.add_page_break()
