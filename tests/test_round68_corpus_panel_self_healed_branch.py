"""Round 68 / Build 42 (B2): pin the new ``self_healed_baked`` branch
of ``classifyCorpusPanel`` so the JS state machine remains in sync
with the Python-side ``corpus_bootstrap._STATE.source = "self_healed_baked"``
emission.

Without a JS runtime in CI, we pin the JS source shape:

* The ``classifyCorpusPanel`` function MUST contain a branch that maps
  ``boot.source === 'self_healed_baked'`` to the ``self_healed_baked``
  state (NOT silently to ``baked_synced`` / ``unknown``).
* The ``corpusPanelLabel``, ``corpusPanelPillClass``, and
  ``corpusPanelDetail`` switches MUST each carry a ``self_healed_baked``
  case so the panel renders something distinct.
* The Python emitter MUST still emit the literal
  ``"self_healed_baked"`` value (Round 39 contract preserved).

Mirrors ``tests/test_round36_panel_renders_synced_state.py`` so the
file naming convention is consistent.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEL_STATUS_JS = REPO_ROOT / "static" / "js" / "intel_status.js"
CORPUS_BOOTSTRAP_PY = REPO_ROOT / "corpus_bootstrap.py"


@pytest.fixture(scope="module")
def js_source() -> str:
    assert INTEL_STATUS_JS.exists(), f"intel_status.js missing at {INTEL_STATUS_JS}"
    return INTEL_STATUS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def bootstrap_source() -> str:
    assert CORPUS_BOOTSTRAP_PY.exists(), f"corpus_bootstrap.py missing at {CORPUS_BOOTSTRAP_PY}"
    return CORPUS_BOOTSTRAP_PY.read_text(encoding="utf-8")


def test_classify_corpus_panel_handles_self_healed_baked_source(js_source: str) -> None:
    assert "source === 'self_healed_baked'" in js_source, (
        "Round 68 / Build 42 (B2): classifyCorpusPanel MUST branch on "
        "boot.source === 'self_healed_baked' so a self-healed install "
        "renders a distinct pill instead of silently mapping to "
        "'unknown' or 'baked_synced'."
    )


def test_panel_label_has_self_healed_baked_case(js_source: str) -> None:
    assert "case 'self_healed_baked'" in js_source, (
        "Round 68 / Build 42 (B2): corpusPanelLabel MUST carry a "
        "'self_healed_baked' case so the panel pill text is honest."
    )


def test_panel_pill_class_has_self_healed_baked_case(js_source: str) -> None:
    assert js_source.count("case 'self_healed_baked'") >= 3, (
        "Round 68 / Build 42 (B2): all three render switches "
        "(label, pill class, detail) MUST carry a 'self_healed_baked' "
        "case -- got fewer than 3 occurrences in intel_status.js."
    )


def test_python_side_still_emits_self_healed_baked(bootstrap_source: str) -> None:
    assert '_STATE.source = "self_healed_baked"' in bootstrap_source, (
        "Round 68 / Build 42 (B2): the Round 39 emitter at "
        "corpus_bootstrap._run_index_pass MUST still set "
        "_STATE.source = 'self_healed_baked' so the JS branch above "
        "actually fires.  If this assertion is failing, both sides "
        "are out of sync and the panel will mis-render."
    )


def test_panel_state_table_documents_eight_states(js_source: str) -> None:
    """The big block comment above ``classifyCorpusPanel`` must list
    all eight states (baked_synced, baked_not_synced, fresh_indexing,
    fresh_not_synced, refreshing, refresh_failed, blocked_no_onedrive,
    self_healed_baked) so future maintainers don't repeat the
    pre-R68 mistake of bolting on a state without documenting it.
    """
    expected_state_names = [
        "baked_synced",
        "baked_not_synced",
        "fresh_indexing",
        "fresh_not_synced",
        "refreshing",
        "refresh_failed",
        "blocked_no_onedrive",
        "self_healed_baked",
    ]
    for state in expected_state_names:
        assert f"* {state}" in js_source, (
            f"Round 68 / Build 42 (B2): the classifyCorpusPanel "
            f"docblock MUST list every state -- '{state}' is missing."
        )


def test_template_documents_self_healed_branch() -> None:
    """The R36 docblock in templates/analyze.html now also lists
    ``self_healed_baked`` so the template <-> JS state machine
    documentation stays in sync.
    """
    template = REPO_ROOT / "templates" / "analyze.html"
    src = template.read_text(encoding="utf-8")
    assert "self_healed_baked" in src, (
        "Round 68 / Build 42 (B3): templates/analyze.html docblock "
        "MUST mention self_healed_baked alongside the other 7 corpus "
        "panel states so the JS state machine reference in the HTML "
        "comment stays canonical."
    )
