"""Round 145 subscription AI Word rendering contract."""

from __future__ import annotations
from source_shape_utils import assert_in_source

import inspect

import app_simple


def test_subscription_ai_uses_markdown_to_word_renderer() -> None:
    source = inspect.getsource(app_simple.run_subscription_analysis)

    assert_in_source(
        source,
        '_r71_rendered_ai_claim = str(_r71_safe_ai_response or "").strip()',
        label="source",
    )
    assert_in_source(
        source,
        '"[Source: grounded subscription briefing book]"',
        label="source",
    )
    assert_in_source(
        source,
        "append_to_word_report(doc, _r71_rendered_ai_claim)",
        label="source",
    )
    assert "ai_p.add_run(_r71_safe_ai_response)" not in source
