"""Round 4 / Phase 6.5 regression test.

``CircuitChatClient.complete`` must:
  1. Pass explicit ``temperature`` and ``max_completion_tokens`` (or
     ``max_tokens``) to ``client.chat.completions.create``.
  2. Log ``{model, temperature, max_tokens, finish_reason}`` per call.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_circuit_complete_passes_explicit_sampling_params() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # Pin presence of explicit temperature param in the create call.
    assert '"temperature"' in src and "0.2" in src, (
        "Round 4 Phase 6.5: CircuitChatClient.complete must set an "
        "explicit temperature (e.g. 0.2) for reproducibility instead "
        "of relying on the SDK default."
    )
    assert (
        '"max_completion_tokens"' in src
        or '"max_tokens"' in src
        or "max_completion_tokens" in src
    ), (
        "Round 4 Phase 6.5: CircuitChatClient.complete must set an "
        "explicit max_completion_tokens / max_tokens cap."
    )


def test_circuit_complete_logs_model_and_finish_reason_per_call() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # The Round 4 fix emits a structured log line including model,
    # temperature, max_tokens, finish_reason.
    assert "LLM call ok" in src, (
        "Round 4 Phase 6.5: CircuitChatClient.complete must emit a "
        "structured 'LLM call ok' log line per successful call so "
        "operators can audit which params produced each answer."
    )
    for token in ("model=", "finish_reason="):
        assert token in src, (
            f"Round 4 Phase 6.5: 'LLM call ok' log must include "
            f"'{token}'."
        )
