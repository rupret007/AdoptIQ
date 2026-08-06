"""Round 6 / Phase 2.2 regression test.

``ask_ai.html`` ``formatAnswer`` must not assign untrusted HTML to
``innerHTML``; build via DOM APIs (or pass through DOMPurify).
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_ask_ai_no_innerhtml() -> None:
    # Round 8 / Phase 5.2: the inline ``<script>`` block was extracted
    # from ``templates/ask_ai.html`` to ``static/js/ask_ai.js`` so the
    # page-level CSP no longer needs ``script-src 'unsafe-inline'``.
    # The Round 6 / Phase 2.2 ``createElement`` / ``textContent``
    # builder is preserved verbatim in the extracted JS file, so we
    # assert the marker against the new location.
    src = (REPO_ROOT / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 2.2", label='src')
    assert_in_source(src, "textContent", label='src')
    assert "answerContent.innerHTML" not in src
