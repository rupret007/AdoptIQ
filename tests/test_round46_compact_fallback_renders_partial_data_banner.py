"""Round 46 / F-COMP-DQ-BANNER regression test.

Why this exists:
- The compact (single-manager) executive report has TWO render paths:
  1) Primary: ``create_executive_intelligence_report`` (executive_intelligence_formatter.py)
  2) Fallback: ``app_simple._create_enhanced_compact_report``
- Both paths must surface ``partial_data_warnings`` to the reader so a
  "successful" docx built on partial data (column-policy block, schema drift,
  fetch error) doesn't silently mislead.
- Pre-Round 46 the fallback path silently dropped these warnings: the
  function signature didn't accept them and there was no banner-rendering
  code. Confirmed against
  ``Brian_Frazier_All_Contact_Center_90d_1777438120``: status had 3
  warnings, docx body had zero "Partial Data Warning" text.
- Round 46 threads ``partial_data_warnings`` into the fallback signature
  and renders a banner mirroring the primary-path banner shape.

This test asserts:
  a) ``_create_enhanced_compact_report`` accepts ``partial_data_warnings``.
  b) When passed, the produced docx contains a "Partial Data Warning"
     heading and one bullet per warning entry naming the dataset and the
     human-readable error.
  c) When ``partial_data_warnings`` is None or empty, no banner appears
     (so the happy-path docx stays clean).
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import pytest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read('word/document.xml').decode('utf-8', errors='replace')
    text = re.sub(r'<[^>]+>', ' ', xml)
    return re.sub(r'\s+', ' ', text).strip()


def test_round46_signature_accepts_partial_data_warnings() -> None:
    """``_create_enhanced_compact_report`` must accept the new kw-arg."""
    from app_simple import _create_enhanced_compact_report

    sig = inspect.signature(_create_enhanced_compact_report)
    assert 'partial_data_warnings' in sig.parameters, (
        "Round 46 / F-COMP-DQ-BANNER: _create_enhanced_compact_report must "
        "accept partial_data_warnings so the fallback executive-report path "
        "can surface schema drift / fetch errors to the reader."
    )
    # The kw-arg must be Optional (None is the no-warnings default).
    p = sig.parameters['partial_data_warnings']
    assert p.default is None, (
        "partial_data_warnings must default to None so existing callers stay "
        "behaviourally unchanged when no warnings exist."
    )


def test_round46_banner_renders_when_warnings_present(tmp_path: Path) -> None:
    """When warnings are passed, the docx must contain the banner + bullets."""
    from app_simple import _create_enhanced_compact_report

    warnings = [
        {
            'dataset': 'schema:EDW_SALES_ETL_DB.SS.ESA_C360_CS_TASK__C',
            'error': 'Snowflake table blocked by policy: EDW_SALES_ETL_DB.SS.ESA_C360_CS_TASK__C',
            'kind': 'column_introspection_failure',
        },
        {
            'dataset': 'customer_pulse',
            'error': "schema_drift:customer_pulse: missing slot(s) rating on a non-empty result (87 row(s))",
            'kind': 'schema_drift',
        },
        {
            'dataset': 'adoption_barriers',
            'error': "schema_drift:adoption_barriers: missing slot(s) customer on a non-empty result (68 row(s))",
            'kind': 'schema_drift',
        },
    ]

    base = str(tmp_path / "round46_compact_banner")
    out = _create_enhanced_compact_report(
        base_path=base,
        manager="Brian Frazier",
        technology="All Contact Center",
        days=90,
        ai_insights={},
        csone_df=pd.DataFrame(),
        ab_norm=pd.DataFrame(),
        arr_data=pd.DataFrame(),
        arr_impact={},
        chart_paths=[],
        feature_requests={},
        team_subs_df=pd.DataFrame({'BU_NAME': ['ACME', 'WIDGETS']}),
        partial_data_warnings=warnings,
    )

    assert out and os.path.exists(out), (
        "_create_enhanced_compact_report must return a path to the saved docx"
    )

    text = _docx_text(Path(out))

    assert 'Partial Data Warning' in text, (
        "Round 46 / F-COMP-DQ-BANNER: docx must include the 'Partial Data "
        f"Warning' heading. Body excerpt: {text[:400]!r}"
    )
    # Each warning's dataset name + kind + error must appear so the reader
    # can correlate the report against the runtime status entry.
    for w in warnings:
        assert w['dataset'] in text, (
            f"Banner missing dataset '{w['dataset']}'. Body excerpt: {text[:400]!r}"
        )
        assert w['kind'] in text, (
            f"Banner missing kind '{w['kind']}'. Body excerpt: {text[:400]!r}"
        )
        # Error string is long; assert a stable substring (the dataset name)
        # plus a short tail unique to that warning.
        tail = w['error'].split(':')[-1].strip()[:30]
        assert tail in text, (
            f"Banner missing error tail '{tail}'. Body excerpt: {text[:400]!r}"
        )


def test_round46_no_banner_when_warnings_absent(tmp_path: Path) -> None:
    """Happy path (no warnings) must not render the banner."""
    from app_simple import _create_enhanced_compact_report

    base = str(tmp_path / "round46_compact_no_banner")
    out = _create_enhanced_compact_report(
        base_path=base,
        manager="Brian Frazier",
        technology="All Contact Center",
        days=90,
        ai_insights={},
        csone_df=pd.DataFrame(),
        ab_norm=pd.DataFrame(),
        arr_data=pd.DataFrame(),
        arr_impact={},
        chart_paths=[],
        feature_requests={},
        team_subs_df=pd.DataFrame({'BU_NAME': ['ACME']}),
        partial_data_warnings=None,
    )
    assert out and os.path.exists(out)
    text = _docx_text(Path(out))
    assert 'Partial Data Warning' not in text, (
        "Round 46: happy-path docx (no warnings) must not render the banner."
    )

    # Same check for empty list
    out2 = _create_enhanced_compact_report(
        base_path=base + "_empty",
        manager="Brian Frazier",
        technology="All Contact Center",
        days=90,
        ai_insights={},
        csone_df=pd.DataFrame(),
        ab_norm=pd.DataFrame(),
        arr_data=pd.DataFrame(),
        arr_impact={},
        chart_paths=[],
        feature_requests={},
        team_subs_df=pd.DataFrame({'BU_NAME': ['ACME']}),
        partial_data_warnings=[],
    )
    text2 = _docx_text(Path(out2))
    assert 'Partial Data Warning' not in text2, (
        "Empty list of warnings must also skip the banner."
    )
