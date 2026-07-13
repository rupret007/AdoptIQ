"""Round 3 / Phase 2.2 regression test.

When ``add_executive_summary`` cannot find any AI summary text on a
completed run, the report must say "AI summary unavailable for this
run", not "AI insights are being processed. Please check back
shortly." The latter implies a transient pipeline state and invites
readers to refresh, which is dishonest after a failed/empty LLM call.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _new_formatter():
    try:
        from executive_intelligence_formatter import (
            ExecutiveIntelligenceFormatter,
        )
    except Exception:
        pytest.skip("executive_intelligence_formatter unavailable")
    return ExecutiveIntelligenceFormatter()


def _doc_text(doc) -> str:
    return "\n".join(p.text for p in doc.paragraphs)


def test_empty_ai_insights_says_unavailable_not_being_processed():
    fmt = _new_formatter()
    fmt.add_executive_summary({})
    text = _doc_text(fmt.doc)
    assert "AI summary unavailable" in text
    assert "being processed" not in text.lower()
    assert "check back shortly" not in text.lower()


def test_failed_llm_surfaces_reason_in_doc():
    fmt = _new_formatter()
    fmt.add_executive_summary(
        {"llm_error": "CircuIT timeout after 120s"}
    )
    text = _doc_text(fmt.doc)
    assert "AI summary unavailable" in text
    assert "CircuIT timeout after 120s" in text


def test_codebase_does_not_use_being_processed_copy():
    """Hard guard: the dishonest copy must not reappear in the EI
    formatter source."""
    src = (PROJECT_ROOT / "executive_intelligence_formatter.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "being processed. Please check back shortly" not in src
