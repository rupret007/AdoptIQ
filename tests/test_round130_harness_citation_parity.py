"""Round 130 — harness strict vs R114/R115 source-caption parity.

Build 98 June-3 Renewal/Leader DOCX flagged ``Sources: ...`` caption
paragraphs as uncited numeric claims.  The quality gate must treat those
captions as source-backed (empty numeric-token list), matching the R114/R115
injector contract.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from report_iteration_loop import (
    _MATRIX_SOURCE_CAPTION_PREFIX,
    _numeric_tokens_requiring_source,
    evaluate_report_quality,
)


def test_sources_caption_with_90_days_label_has_no_numeric_tokens() -> None:
    line = (
        "Sources: Support Cases (Last 90 Days), BEMS Escalations - "
        "Snowflake CSOne; Total Customers - Snowflake EDW Sales subscriptions"
    )
    assert line.lower().startswith(_MATRIX_SOURCE_CAPTION_PREFIX.lower())
    assert _numeric_tokens_requiring_source(line) == []


def test_sources_caption_with_aging_bucket_ranges_has_no_numeric_tokens() -> None:
    line = (
        "Sources: 0-7 days, 8-30 days, 31-60 days, 61+ days, Total Open - "
        "AdoptIQ Report Data Sources"
    )
    assert _numeric_tokens_requiring_source(line) == []


@pytest.mark.parametrize(
    "docx_env,scenario",
    [
        (
            "ADOPTIQ_R130_RENEWAL_DOCX",
            "renewal",
        ),
        (
            "ADOPTIQ_R130_LEADER_DOCX",
            "leader",
        ),
    ],
)
def test_june3_build98_quality_gate_passes_when_artifacts_present(
    docx_env: str,
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional live-artifact pin — skipped when env path unset or file missing."""
    raw = Path(os.environ.get(docx_env, "")).expanduser()
    if not raw or not raw.is_file():
        pytest.skip(f"{docx_env} not set or file missing: {raw!s}")

    payload, passed = evaluate_report_quality(
        docx_path=raw,
        xlsx_path=None,
        scenario_key=scenario,
        strict=True,
    )
    assert passed, payload.get("unbacked_paragraphs") or payload
