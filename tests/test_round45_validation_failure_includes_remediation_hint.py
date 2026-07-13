"""Round 45 / Phase 4 regression: ``DataSourceValidationError`` failure
messages surfaced in ``analysis_status['message']`` must include a
source-specific, single-line remediation hint -- NOT the legacy
"Data validation failed - see logs for details" placeholder.

Pre-Round-45, the progress page red banner said only "see logs for
details" which forced the operator to grep ``adoptiq.<pid>.log`` to
diagnose -- and the actual log line ("[ERROR] DATA SOURCE VALIDATION
FAILED for COMPACT report") was equally opaque.  Phase 4 introduces
the ``_r45_render_validation_remediation_message`` helper that maps
each missing-source kind to a concrete, actionable one-liner so the
banner can show the operator WHICH source failed and WHAT to do.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="module")
def app_simple_module():
    """Import app_simple lazily so this test doesn't pay the import
    cost when run in isolation against another module's failure."""
    return importlib.import_module("app_simple")


def test_helper_exists(app_simple_module) -> None:
    """The Phase 4 helper must be exposed under the documented name."""
    assert hasattr(app_simple_module, "_r45_render_validation_remediation_message"), (
        "Round 45 / Phase 4 regression: app_simple must expose "
        "``_r45_render_validation_remediation_message`` so the worker "
        "exception handler can render an actionable banner."
    )


def test_csone_missing_no_upload_includes_autodiscovery_path(app_simple_module) -> None:
    """When CSOne is the missing source AND the operator did not upload
    a file, the rendered message must (a) name CSOne, (b) include the
    autodiscovery path, and (c) include the remediation phrase
    'Upload a CSOne export'."""
    fn = app_simple_module._r45_render_validation_remediation_message
    msg = fn(
        report_type="compact",
        missing_sources=["csone"],
        error_details={"csone": "No CSOne support case data found."},
        csone_was_uploaded=False,
        csone_auto_path="/Users/op/OneDrive/AdoptIQ_CSOne_Reports",
    )

    # Single-line render: no embedded newlines.
    assert "\n" not in msg, (
        "Round 45 / Phase 4 regression: rendered message must be a "
        "single line so the progress banner doesn't truncate weirdly."
    )

    # Must name CSOne, the autodiscovery path, and a remediation cue.
    assert "CSOne" in msg, "Message must name the missing source"
    assert "/Users/op/OneDrive/AdoptIQ_CSOne_Reports" in msg, (
        "Round 45 / Phase 4 regression: rendered message must include "
        "the autodiscovery path so the operator can verify the env var "
        "and the OneDrive sync state."
    )
    assert "Upload a CSOne export" in msg, (
        "Round 45 / Phase 4 regression: remediation phrase missing"
    )


def test_csone_missing_with_upload_distinguishes_scope_failure(app_simple_module) -> None:
    """When CSOne IS uploaded but scopes to empty, the message must
    explain it was a scope-filter empty (NOT an autodiscovery empty).
    """
    fn = app_simple_module._r45_render_validation_remediation_message
    msg = fn(
        report_type="compact",
        missing_sources=["csone"],
        error_details={"csone": "scope filter removed all records"},
        csone_was_uploaded=True,
        csone_auto_path="/some/path",
    )

    assert "uploaded but produced 0 in-scope rows" in msg, (
        "Round 45 / Phase 4 regression: when csone_was_uploaded=True, "
        "the message must distinguish 'uploaded but empty after scope' "
        "from the autodiscovery-empty-no-upload case so the operator "
        "knows to check filters, not the upload."
    )


def test_unknown_source_falls_back_to_validator_details(app_simple_module) -> None:
    """When the validator raises with a source kind the helper doesn't
    know, the helper must still surface the per-source ``error_details``
    text rather than swallowing it."""
    fn = app_simple_module._r45_render_validation_remediation_message
    msg = fn(
        report_type="leader",
        missing_sources=["future_source_kind"],
        error_details={"future_source_kind": "Brand new source failed for reason X."},
    )
    assert "future_source_kind" in msg
    assert "Brand new source failed for reason X." in msg


def test_empty_missing_sources_falls_back_to_legacy_message(app_simple_module) -> None:
    """If the exception is malformed and carries no missing_sources, the
    helper must NOT silently render a misleading hint -- it must fall
    back to the legacy "see logs for details" text so we don't hide a
    real failure behind incorrect remediation."""
    fn = app_simple_module._r45_render_validation_remediation_message
    msg = fn(
        report_type="compact",
        missing_sources=None,
        error_details=None,
    )
    assert "see logs for details" in msg


def test_compact_worker_exception_handler_uses_helper() -> None:
    """The compact worker's ``except DataSourceValidationError`` block
    MUST call ``_r45_render_validation_remediation_message`` and assign
    the result to ``status['message']`` (NOT the legacy generic string).
    Pin the source shape so a refactor can't silently regress."""
    src = (Path(__file__).resolve().parent.parent / "app_simple.py").read_text(
        encoding="utf-8"
    )
    assert "_r45_render_validation_remediation_message(" in src, (
        "Round 45 / Phase 4 regression: validation exception handlers "
        "must call ``_r45_render_validation_remediation_message`` so "
        "the progress banner shows actionable text."
    )
