"""Round 3 / Phase 2.1 regression test.

`add_recommendations_section` must either render content from the
provided ``ai_insights`` dict, or — when no AI recommendations are
present — explicitly label the canned playbook block as static so a
reader cannot mistake the boilerplate for AI output.
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


def test_uses_ai_insights_when_provided():
    fmt = _new_formatter()
    fmt.add_recommendations_section(
        {
            "recommendations": [
                "Schedule QBR with Acme by Friday",
                "Open ticket TAC-42 for Beta",
            ]
        }
    )
    text = _doc_text(fmt.doc)
    assert "Schedule QBR with Acme by Friday" in text
    assert "Open ticket TAC-42 for Beta" in text
    assert "Static playbook" not in text


def test_falls_back_to_labelled_static_playbook_when_no_ai():
    fmt = _new_formatter()
    fmt.add_recommendations_section({})
    text = _doc_text(fmt.doc)
    assert "Static playbook" in text, (
        "static playbook must be explicitly labelled when no AI recs"
    )


def test_pulls_recommendations_from_portfolio_summary_nest():
    fmt = _new_formatter()
    fmt.add_recommendations_section(
        {
            "portfolio_summary": {
                "next_steps": ["Run BST audit on top 5 customers"],
            }
        }
    )
    text = _doc_text(fmt.doc)
    assert "Run BST audit on top 5 customers" in text
    assert "Static playbook" not in text
