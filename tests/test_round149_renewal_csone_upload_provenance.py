"""Round 149 — renewal CSOne validation uses upload provenance, not autodiscovery path."""

from pathlib import Path


def test_start_analysis_status_includes_csone_upload_provenance_markers() -> None:
    text = Path("app_simple.py").read_text(encoding="utf-8")
    assert '"csone_file_was_uploaded": bool(csone_file_explicit)' in text
    assert '"csone_autodiscovery_path": csone_file_autopicked or ""' in text


def test_renewal_validation_reads_csone_file_was_uploaded_not_locals_path() -> None:
    text = Path("app_simple.py").read_text(encoding="utf-8")
    assert "status.get(\"csone_file_was_uploaded\")" in text
    # Pre-R149 bug: truthy csone_path treated autodiscovery as explicit upload.
    assert "_csone_file_provided = bool(_scope_locals.get(\"csone_file\")" not in text
