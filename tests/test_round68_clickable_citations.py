"""Round 68 / Build 42 (C7): pin the clickable citation badges.

Pre-R68 the only signal an operator had for an LLM citation was
the inline ``[Source: ID]`` text, which was opaque -- they could
not see which record actually backed the claim without filing a
support ticket and waiting for an engineer to query SQLite.

R68 adds:
  * Server-side ``evidence_index`` field in the
    ``/api/ask-ai-portfolio`` response, listing each
    ``{source_id, source_type, customer, timestamp, snippet}`` for
    every ranked record that fed the LLM context.
  * Client-side renderer that detects ``[Source: ID]`` markers in
    the answer text, replaces each ID with a clickable badge, and
    pops the snippet inline on click.
  * Multi-id citations like ``[Source: A, B]`` produce one badge
    per id (preserves the LLM's compound-claim grouping).
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import ast
from pathlib import Path
from unittest.mock import patch

import pytest


_ROOT = Path(__file__).resolve().parent.parent
_ASK_AI_JS = _ROOT / "static" / "js" / "ask_ai.js"
_ASK_AI_GROUNDED = _ROOT / "ask_ai_grounded.py"
_APP_SIMPLE = _ROOT / "app_simple.py"


def _ask_ai_js() -> str:
    return _ASK_AI_JS.read_text(encoding="utf-8")


def _ask_ai_grounded() -> str:
    return _ASK_AI_GROUNDED.read_text(encoding="utf-8")


def _app_simple() -> str:
    return _APP_SIMPLE.read_text(encoding="utf-8")


# --- Server-side evidence_index field ----------------------------------------


def test_r68_evidence_index_built_from_ranked_records() -> None:
    """The server must build ``evidence_index`` from
    ``_ranked_for_diag`` (the records that actually fed the LLM
    context), not from the full record set.  Including unranked
    records would surface evidence the LLM never saw."""
    src = _ask_ai_grounded()
    assert "_r98_used_evidence_records(" in src and "_ranked_for_diag or []" in src, (
        "evidence_index/evidence_records built from a different source than _ranked_for_diag"
    )


def test_r68_evidence_index_capped_to_used_records() -> None:
    """The build loop must respect the ``used_records`` cap so the
    index is consistent with the cap published in
    ``evidence_records_used``.  Otherwise the operator could see
    snippets for records the LLM was budget-dropped from."""
    src = _ask_ai_grounded()
    assert "allowed_ids" in src and "_r98_used_evidence_records(" in src, (
        "evidence_index doesn't respect the actual allowed SourceID set"
    )


def test_r68_evidence_index_snippet_bounded() -> None:
    """Each snippet must be capped (~280 chars) so the response
    payload doesn't explode on a verbose evidence record."""
    src = _ask_ai_grounded()
    assert "if len(snippet_text) > 280" in src, (
        "evidence_index snippet not capped -- payload could explode"
    )


def test_r68_evidence_index_capped_to_200_entries() -> None:
    """A defensive total cap so the payload stays bounded even if
    ``used_records`` grows (current default 200)."""
    src = _ask_ai_grounded()
    assert "if len(evidence_index) >= 200:" in src, (
        "no defensive 200-entry cap on evidence_index -- payload could explode"
    )


def test_r68_evidence_index_returned_in_payload() -> None:
    """The build site must return ``evidence_index`` in the
    grounded result payload."""
    src = _ask_ai_grounded()
    assert '"evidence_index": evidence_index' in src, (
        "grounded result doesn't include evidence_index"
    )


def test_r68_evidence_index_passed_through_to_client() -> None:
    """Sync and SSE must publish a projected, bounded evidence index."""
    tree = ast.parse(_app_simple())
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def projected_index_calls(function_name: str) -> list[ast.Call]:
        function = functions[function_name]
        calls = []
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "_r147_public_ai_evidence_rows":
                continue
            if not node.args or not any(
                isinstance(part, ast.Constant) and part.value == "evidence_index"
                for part in ast.walk(node.args[0])
            ):
                continue
            if not any(
                keyword.arg == "index_only"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            ):
                continue
            calls.append(node)
        return calls

    assert projected_index_calls("ask_ai_portfolio"), (
        "the synchronous portfolio response must project evidence_index "
        "through _r147_public_ai_evidence_rows"
    )
    assert projected_index_calls("_r74_run_grounded_for_streaming"), (
        "the SSE portfolio response must project evidence_index through "
        "_r147_public_ai_evidence_rows"
    )


def test_r68_evidence_index_dedupes_source_ids() -> None:
    """The build loop must dedupe ``source_id`` so the index
    doesn't carry the same id twice (e.g. when the LLM cites the
    same record from multiple paragraphs)."""
    src = _ask_ai_grounded()
    assert "_seen_ids: set" in src and "_seen_ids.add(sid)" in src, (
        "evidence_index doesn't dedupe source_ids"
    )


# --- JS rendering ------------------------------------------------------------


def test_r68_set_evidence_index_function_present() -> None:
    src = _ask_ai_js()
    assert "function _r68SetEvidenceIndex(" in src, (
        "_r68SetEvidenceIndex helper missing"
    )


def test_r68_evidence_index_populated_before_format() -> None:
    """The evidence index must be populated BEFORE
    ``formatAnswerInto`` runs, otherwise the citation badge
    renderer (called from ``appendInline``) would see an empty
    index and fall through to the no-snippet placeholder for
    every badge."""
    src = _ask_ai_js()
    # The set-evidence-index call must come before formatAnswerInto
    # in the success branch.
    set_idx = src.find("_r68SetEvidenceIndex(data.evidence_index)")
    # Round 127: success path renders into chat bubble via _r127AnswerTarget().
    fmt_idx = src.find(
        "formatAnswerInto(_r127AnswerTarget(), (data.answer && data.answer.trim())",
    )
    assert set_idx != -1, "_r68SetEvidenceIndex not called from success branch"
    assert fmt_idx != -1, "formatAnswerInto call site not found"
    assert set_idx < fmt_idx, (
        "_r68SetEvidenceIndex called AFTER formatAnswerInto -- badges would"
        " render with an empty index"
    )


def test_r68_build_citation_badge_function_present() -> None:
    src = _ask_ai_js()
    assert "function _r68BuildCitationBadge(" in src, (
        "_r68BuildCitationBadge missing"
    )


def test_r68_citation_badge_uses_textContent_not_innerHTML() -> None:
    """XSS guard -- the snippet text comes from server data and
    MUST use textContent, never innerHTML."""
    src = _ask_ai_js()
    body_start = src.find("function _r68BuildCitationBadge(")
    assert body_start != -1
    # Function ends at the next column-4 closing brace.
    body_end = src.find("\n    }\n", body_start)
    body = src[body_start:body_end]
    assert "innerHTML" not in body, (
        "_r68BuildCitationBadge uses innerHTML (XSS sink)"
    )
    assert ".textContent" in body, (
        "_r68BuildCitationBadge must use textContent"
    )


def test_r68_appendInline_regex_matches_source_pattern() -> None:
    """The regex that drives inline rendering must include the
    ``[Source: ...]`` arm so citation markers get rewritten to
    badges instead of staying as plain text."""
    src = _ask_ai_js()
    # The new regex shape includes the citation arm.
    assert r"(?:Source|Sources|SourceID)" in src, (
        "appendInline regex doesn't match source citation markers"
    )


def test_r68_multi_id_citations_split_to_multiple_badges() -> None:
    """``[Source: A, B, C]`` must produce three badges, not one
    badge containing the comma-joined string."""
    src = _ask_ai_js()
    # Look at the citation branch of appendInline.
    fn_idx = src.find("function appendInline(parent, text)")
    assert fn_idx != -1
    body = src[fn_idx : fn_idx + 4000]
    assert "var ids = String(m[4]).split(',')" in body, (
        "multi-id citations not split into individual badges"
    )


def test_r68_citation_badge_handles_missing_index_entry() -> None:
    """If the LLM cites an ID not in the evidence_index (e.g.
    rejected by the validator), the badge must still render but
    with a fallback message rather than crashing."""
    src = _ask_ai_js()
    body_start = src.find("function _r68BuildCitationBadge(")
    body_end = src.find("\n    }\n", body_start)
    body = src[body_start:body_end]
    # The function checks ``if (rec) {`` and has an ``else`` path.
    assert_in_source(body, "if (rec)" in body or "if (rec) {", label='body')
    assert "Evidence record not in this answer" in body, (
        "no fallback message for missing evidence_index entry"
    )


def test_r68_only_one_popover_open_at_a_time() -> None:
    """Clicking a second badge must close the first popover so the
    page doesn't end up with multiple stacked popovers blocking
    the answer text."""
    src = _ask_ai_js()
    body_start = src.find("function _r68BuildCitationBadge(")
    body_end = src.find("\n    }\n", body_start)
    body = src[body_start:body_end]
    assert "querySelectorAll('.r68-citation-popover')" in body, (
        "click handler doesn't close other popovers -- multi-popover stacking"
    )


def test_r68_citation_badge_class_namespaced() -> None:
    """The badge / popover CSS class names must be namespaced
    (``r68-`` prefix) so other UI scripts on the page can't
    accidentally style or remove them."""
    src = _ask_ai_js()
    assert "r68-citation-badge" in src, "badge class not namespaced"
    assert "r68-citation-popover" in src, "popover class not namespaced"
    assert "r68-citation-wrapper" in src, "wrapper class not namespaced"


# --- Smoke -------------------------------------------------------------------


def test_r68_module_imports_cleanly() -> None:
    import ask_ai_grounded  # noqa: F401
    import app_simple  # noqa: F401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
