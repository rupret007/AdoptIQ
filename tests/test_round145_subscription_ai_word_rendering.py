"""Round 145 subscription AI Word rendering contract."""

from __future__ import annotations

import inspect

import app_simple


def test_subscription_ai_uses_markdown_to_word_renderer() -> None:
    source = inspect.getsource(app_simple.run_subscription_analysis)

    assert "append_to_word_report(doc, _r71_safe_ai_response)" in source
    assert "ai_p.add_run(_r71_safe_ai_response)" not in source
