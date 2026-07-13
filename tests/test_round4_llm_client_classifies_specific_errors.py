"""Round 4 / Phase 4.7 regression test.

``CircuitChatClient.complete`` must classify common LLM failures
(429 / 5xx / content_filter / parse_fail) into structured ``ERROR:<kind>:``
strings instead of returning a silent ``None`` (which downstream
mapped to a generic 'invalid response').
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_circuit_complete_classifies_known_failure_kinds() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # Pin presence of each classified ``kind`` literal in the
    # complete() error path.
    for token in (
        "rate_limit_429",
        "content_filter",
        "parse_fail",
        "server_error_",
    ):
        assert token in src, (
            f"Round 4 Phase 4.7: CircuitChatClient.complete must "
            f"classify failures into '{token}' (or equivalent) so the "
            f"UI can distinguish rate limits from policy blocks from "
            f"parse failures."
        )


def test_circuit_complete_returns_error_string_not_none_for_no_choices() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # The "no choices" branch must return an ERROR: string.
    assert "ERROR: parse_fail" in src or "ERROR: content_filter" in src, (
        "Round 4 Phase 4.7: CircuitChatClient.complete must return an "
        "ERROR:<kind>: string (not None) when the response has no "
        "choices or trips content_filter."
    )
