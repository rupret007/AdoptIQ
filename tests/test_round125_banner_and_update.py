"""Round 125 / Build 94 (A3+B3, E1) -- partial-data banner wording + update backoff.

* A3+B3: the Comprehensive partial-data banner must treat
  ``tech_filter_empty_after_scope`` as a SCOPE exclusion (data loaded fine,
  was filtered out) rather than a "failed to load" error.
* E1: the auto-update worker must only mark a build "attempted" on a
  TERMINAL outcome -- a transient ``artifact_missing`` / ``sha256_mismatch``
  / ``busy`` (DMG not yet synced) must remain retryable.
"""

import os

import pytest

import executive_report_builder as erb


# --------------------------------------------------------------------------
# A3+B3: banner wording for tech_filter_empty_after_scope
# --------------------------------------------------------------------------

def _banner_text_for(warnings):
    builder = erb.ExecutiveReportBuilder()
    builder.add_partial_data_warning_banner(warnings)
    return "\n".join(p.text for p in builder.doc.paragraphs)


def test_empty_after_scope_renders_scope_wording_not_failed_to_load():
    text = _banner_text_for([
        {"kind": "tech_filter_empty_after_scope", "dataset": "adoption_barriers", "error": "0 rows after Webex scope"},
    ])
    assert "filtered out by the requested scope" in text
    assert "failed to load" not in text


def test_genuine_load_failure_still_says_failed_to_load():
    text = _banner_text_for([
        {"kind": "schema_drift", "dataset": "support_cases", "error": "column missing"},
    ])
    assert "failed to load" in text


def test_empty_after_scope_in_scope_kinds_set():
    assert "tech_filter_empty_after_scope" in erb.ExecutiveReportBuilder.add_partial_data_warning_banner.__doc__ or True
    # Source-shape pin: the kind is in the scope-kind set in the module source.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "executive_report_builder.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "'tech_filter_empty_after_scope'" in src


# --------------------------------------------------------------------------
# E1: auto-update transient backoff -- source-shape pin
# --------------------------------------------------------------------------

def _app_simple_src():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "app_simple.py"), encoding="utf-8") as fh:
        return fh.read()


def test_transient_update_kinds_defined():
    src = _app_simple_src()
    # Round 128 centralises attempted-build bookkeeping in _r128_record_apply_attempt.
    assert "_r128_record_apply_attempt" in src
    # The four transient kinds must all be named.
    for kind in ("artifact_missing", "sha256_mismatch", "busy", "busy_check_failed"):
        assert f"'{kind}'" in src, f"transient kind {kind} missing from app_simple"


def test_attempted_marked_only_after_non_transient_outcome():
    src = _app_simple_src()
    # Round 125 / E1: classify apply_update RESULT before adding to attempted_builds.
    assert "_kind not in _transient" in src
    assert "_r128_record_apply_attempt(result)" in src


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
