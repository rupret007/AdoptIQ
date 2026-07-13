"""Round 4 / Phase 6.1 regression test.

``_strip_uncited_digit_sentences`` must NOT strip benign sentences
that mention the analysis window / display caps when those numbers
are passed in via ``canonical_numbers``.  In addition, the intel
grounding path must pass its own ``canonical_numbers`` set so the
window (e.g. "30 days") and caps survive.
"""
from __future__ import annotations

import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_strip_uncited_digit_preserves_canonical_numbers() -> None:
    from ask_ai_grounded import _strip_uncited_digit_sentences

    text = (
        "The analysis covers the last 30 days and lists up to 120 "
        "incidents per feed. Acme Corp had 4 P1 cases."
    )
    canonical = {"30", "120"}
    cleaned, dropped = _strip_uncited_digit_sentences(
        text, allowed_ids=set(), canonical_numbers=canonical
    )
    assert "30 days" in cleaned, (
        "Round 4 Phase 6.1: a sentence mentioning the analysis-window "
        "integer (30) MUST survive stripping when '30' is in "
        "canonical_numbers."
    )
    assert "120" in cleaned, (
        "Round 4 Phase 6.1: cap disclosure (120) MUST survive when "
        "'120' is in canonical_numbers."
    )


def test_intel_path_passes_canonical_numbers_to_compose() -> None:
    src = (REPO_ROOT / "ask_ai_grounded.py").read_text(encoding="utf-8")
    # Pin the marker variable used in the Round 4 fix.
    assert "_intel_canonical_numbers" in src, (
        "Round 4 Phase 6.1: run_intel_grounded_ask_ai must build an "
        "_intel_canonical_numbers set (containing the analysis "
        "window, display caps, and whitelist cap) and pass it into "
        "compose_grounded_answer."
    )
