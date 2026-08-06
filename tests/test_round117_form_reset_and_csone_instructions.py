"""Round 117 / Build 86: post-success form reset + CSOne export instructions.

Pins two operator-UX changes layered on top of the Round 91 single-window flow:

* After a job successfully starts, the analyze + leader forms reset to a
  fresh-page-load state (full reset) WITHOUT breaking the R91 contract
  (still records the job, still does NOT redirect to ``/progress/``).
* Both forms and the Help page document how to generate the CSOne ``.xlsx``
  export themselves, linking to ``Config.CSONE_REPORT_URL``.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import os
from pathlib import Path

import app_simple
from config import Config


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# A. Post-success full form reset (R91 contract preserved)                    #
# --------------------------------------------------------------------------- #

def test_analyze_form_resets_after_successful_submit():
    html = _read("templates/analyze.html")
    # The reset helper exists and is invoked from the success branch.
    assert_in_source(html, "resetAnalysisFormAfterSubmit", label='html')
    # Helper performs a native reset + clears the custom widgets + radios.
    assert_in_source(html, "elements.form.reset()", label='html')
    assert_in_source(html, "hideFilePreview()", label='html')
    assert_in_source(html, "getElementById('leader')", label='html')
    assert_in_source(html, "syncReportTypeRequirements()", label='html')


def test_analyze_reset_preserves_round91_contract():
    html = _read("templates/analyze.html")
    # Still records the started job (R91) ...
    assert_in_source(html, "AdoptIQReportJobs.recordStartedJob", label='html')
    # ... and still never redirects to the progress page.
    assert "window.location.href = finalRedirectUrl" not in html
    # The reset is called in the same success path that records the job.
    success_idx = html.index("recordStartedJob")
    reset_idx = html.index("resetAnalysisFormAfterSubmit()")
    assert reset_idx > success_idx


def test_leader_form_resets_after_successful_submit():
    html = _read("templates/leader_report_form.html")
    assert_in_source(html, "form.reset()", label='html')
    # R91 contract preserved on the leader form too.
    assert_in_source(html, "AdoptIQReportJobs.recordStartedJob", label='html')
    assert "window.location.href = url" not in html
    # Reset runs after the job is recorded.
    success_idx = html.index("recordStartedJob")
    reset_idx = html.index("form.reset()")
    assert reset_idx > success_idx


# --------------------------------------------------------------------------- #
# B. File-input selector hardening (#csone_file, not the intel-upload input)  #
# --------------------------------------------------------------------------- #

def test_analyze_file_input_targets_csone_field_by_id():
    html = _read("templates/analyze.html")
    # The CSOne field is pinned by id so the intel-upload input (which renders
    # first when intel_upload_enabled) is never grabbed by a generic selector.
    assert_in_source(html, "elements.fileInput = document.getElementById('csone_file')", label='html')
    # The bare generic selector for fileInput is gone.
    assert "elements.fileInput = document.querySelector('input[type=\"file\"]')" not in html


# --------------------------------------------------------------------------- #
# C. CSOne "generate it yourself" instructions on both forms + Help           #
# --------------------------------------------------------------------------- #

def test_analyze_form_has_csone_export_instructions():
    html = _read("templates/analyze.html")
    assert_in_source(html, "How do I generate this report?", label='html')
    assert_in_source(html, 'data-bs-target="#csoneHowToCollapse"', label='html')
    # CSP-safe: collapse via data attributes, no inline onclick on the toggle.
    assert_in_source(html, "csone_report_url", label='html')
    assert_in_source(html, "<strong>Export</strong>", label='html')
    assert_in_source(html, "<strong>Standard</strong>", label='html')


def test_leader_form_has_csone_export_instructions():
    html = _read("templates/leader_report_form.html")
    assert_in_source(html, "How do I generate this report?", label='html')
    assert_in_source(html, 'data-bs-target="#csoneHowToCollapseLeader"', label='html')
    assert_in_source(html, "csone_report_url", label='html')


def test_help_page_documents_csone_export_steps():
    html = _read("templates/help.html")
    assert_in_source(html, 'data-help-section="data-sources"', label='html')
    assert_in_source(html, "Generate the CSOne export yourself", label='html')
    assert_in_source(html, "csone_report_url", label='html')
    assert_in_source(html, "<strong>Export</strong>", label='html')
    assert_in_source(html, "<strong>Standard</strong>", label='html')


# --------------------------------------------------------------------------- #
# D. Config default + context exposure                                        #
# --------------------------------------------------------------------------- #

def test_config_csone_report_url_default():
    # Baked default points at the shared CSOne AdoptIQ Export report.
    assert Config.CSONE_REPORT_URL
    assert Config.CSONE_REPORT_URL.startswith("https://")
    assert "csone.lightning.force.com" in Config.CSONE_REPORT_URL
    assert "00OfX000001Nnh2UAC" in Config.CSONE_REPORT_URL


def test_config_csone_report_url_env_overridable(monkeypatch):
    # The default is computed at class-definition time, so assert the env hook
    # exists in source rather than re-importing the module.
    src = _read("config.py")
    assert_in_source(src, "os.environ.get('CSONE_REPORT_URL')", label='src')


def test_context_processor_exposes_csone_report_url():
    with app_simple.app.test_request_context("/"):
        ctx = app_simple.inject_version()
    assert "csone_report_url" in ctx
    assert ctx["csone_report_url"] == app_simple.app.config.get("CSONE_REPORT_URL", "")


def test_analyze_page_renders_csone_report_link(client):
    # End-to-end: the baked default URL flows through to the rendered page.
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert_in_source(body, "How do I generate this report?", label='body')
    assert Config.CSONE_REPORT_URL in body


# --------------------------------------------------------------------------- #
# E. Audit follow-up: Compact stub-bullet parity with Comprehensive (R78/B1)  #
# --------------------------------------------------------------------------- #

def test_compact_formatter_stub_bullet_helper_matches_ssot_pattern():
    import executive_intelligence_formatter as eif

    # The Compact executive formatter now mirrors the Comprehensive R78 filter.
    assert eif._r117_is_stub_bullet("Industry Benchmarking: Data unavailable.")
    assert eif._r117_is_stub_bullet("**Technical Competency:** Data unavailable")
    assert eif._r117_is_stub_bullet("Adoption Velocity: data unavailable.")
    # Substantive bullets with content after the marker are PRESERVED.
    assert not eif._r117_is_stub_bullet(
        "Operational Disruption: Data unavailable. No active incidents."
    )
    # Generic prose is preserved.
    assert not eif._r117_is_stub_bullet("3 open adoption barriers across 2 customers.")
    assert not eif._r117_is_stub_bullet("")


def test_compact_formatter_skips_stub_bullets_but_keeps_substantive():
    import executive_intelligence_formatter as eif

    fmt = eif.ExecutiveIntelligenceFormatter()
    before = len(fmt.doc.paragraphs)
    fmt._parse_and_add_content(
        "- Industry Benchmarking: Data unavailable.\n"
        "- 3 open adoption barriers need attention.\n"
        "- Competitive Positioning: Data unavailable"
    )
    added = [p.text for p in fmt.doc.paragraphs[before:]]
    # The two pure stub bullets are dropped; the substantive one survives.
    assert any("3 open adoption barriers" in t for t in added)
    assert not any("Data unavailable" in t for t in added)


def test_compact_formatter_uses_backend_ssot_regex():
    # Source-shape: the helper prefers the adoptiq_backend SSoT regex.
    src = _read("executive_intelligence_formatter.py")
    assert_in_source(src, "from adoptiq_backend import _R78_STUB_RE", label='src')
    assert_in_source(src, "_r117_is_stub_bullet(bullet_text)", label='src')
