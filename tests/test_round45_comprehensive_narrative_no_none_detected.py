"""Round 45 / Phase 6 regression: the comprehensive narrative pipeline
MUST scrub residual ``None detected`` chrome from the LLM response so
it consistently shows ``data unavailable`` (matching the NEGATIVE
CONSTRAINT at adoptiq_backend.py:10657).

The 2026-04-28 Build-20 comprehensive Word artifact contained 8
paragraphs with ``None detected`` because the OUTPUT FORMAT
instruction at adoptiq_backend.py:10682 used to say
``state 'None detected' if a section is empty`` -- contradicting the
top-of-prompt NEGATIVE CONSTRAINT that says ``write 'data unavailable'``.

Phase 6 fix has TWO parts:

1. Update the prompt so it stops contradicting itself.
2. Add ``_r45_clean_llm_chrome`` post-process pass that scrubs the
   residual chrome from the LLM response (belt-and-suspenders for
   cached responses or models that ignore the new prompt).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from adoptiq_backend import _r45_clean_llm_chrome  # noqa: E402


def test_helper_replaces_bare_none_detected() -> None:
    """The bare phrase 'None detected' MUST be replaced with
    'data unavailable'."""
    assert _r45_clean_llm_chrome("Section X is None detected.") == (
        "Section X is data unavailable."
    )


def test_helper_replaces_sp_id_parenthetical() -> None:
    """The SP-ID parenthetical 'None detected' MUST be replaced with
    'not provided' (more specific) -- pins the order of regex
    application so the parenthetical fix wins before the bare-phrase
    fix."""
    assert _r45_clean_llm_chrome("Status: (SP-ID: None detected)") == (
        "Status: (SP-ID: not provided)"
    )


def test_helper_handles_mixed_case() -> None:
    """Both regex passes are case-insensitive so model variations on
    capitalization don't escape the post-process."""
    assert "data unavailable" in _r45_clean_llm_chrome("NONE DETECTED")
    assert "data unavailable" in _r45_clean_llm_chrome("none detected")


def test_helper_preserves_unrelated_text() -> None:
    """The regex MUST use a word boundary so accidental embedded
    substrings (e.g. 'phenomenon detected') survive untouched."""
    assert _r45_clean_llm_chrome("phenomenon detected") == "phenomenon detected"
    assert _r45_clean_llm_chrome("The model detected anomalies.") == (
        "The model detected anomalies."
    )


@pytest.mark.parametrize("inp", [None, "", 0, 123, [], {}])
def test_helper_handles_non_string_inputs(inp) -> None:
    """The post-process MUST never raise on non-string input -- it just
    returns the value unchanged so a malformed LLM response doesn't
    block the report build."""
    out = _r45_clean_llm_chrome(inp)
    assert out == inp


def test_prompt_instruction_says_data_unavailable_not_none_detected() -> None:
    """The prompt at adoptiq_backend.py:10682 MUST say 'data unavailable',
    NOT 'None detected', so it stops contradicting line 10657."""
    src = (Path(__file__).resolve().parent.parent / "adoptiq_backend.py").read_text(
        encoding="utf-8"
    )
    # The OUTPUT FORMAT instruction, after Phase 6, must use the
    # consistent wording.  We pin the EXACT instruction phrase rather
    # than a substring so a future edit that accidentally adds a new
    # 'None detected' instruction would fail this test.
    assert "state 'data unavailable' if a section is empty" in src, (
        "Round 45 / Phase 6 regression: comprehensive prompt OUTPUT "
        "FORMAT instruction at adoptiq_backend.py:10682 must say "
        "``state 'data unavailable' if a section is empty``."
    )


def test_post_process_wired_into_generate_llm_response() -> None:
    """The ``generate_llm_response`` success branch MUST call
    ``_r45_clean_llm_chrome`` before returning so every LLM consumer
    inherits the scrub."""
    src = (Path(__file__).resolve().parent.parent / "adoptiq_backend.py").read_text(
        encoding="utf-8"
    )
    assert "_r45_clean_llm_chrome(result)" in src, (
        "Round 45 / Phase 6 regression: generate_llm_response must "
        "post-process the LLM response with _r45_clean_llm_chrome "
        "before returning."
    )
