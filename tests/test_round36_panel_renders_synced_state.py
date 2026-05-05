"""Round 36 / onedrive-sync-auth: pin the AdoptIQ Knowledge Corpus
panel renderer in ``static/js/intel_status.js``.

The Round 36 panel renders one of seven states based on:

* ``boot.source``         (``"baked" | "fresh"``)
* ``boot.onedrive_status``(``"synced" | "not_synced"``)
* ``boot.in_progress``    (refresh in flight)
* ``boot.last_refresh_error`` (last refresh raised)

This test pins the seven-state matrix and the labels so an editor
that re-orders the if-chain or rewords a label cannot silently break
the user-facing copy.

Static-substring / regex assertions follow the project idiom
(``test_round26_intel_status_js_poll_cadence.py``) so the test runs
without spinning up a JS runtime.

Plus: a Python re-implementation of ``classifyCorpusPanel`` (mirrors
the JS branch order) is exercised against the four cardinal payloads
to pin the contract end-to-end.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


JS_PATH = Path(__file__).parent.parent / "static" / "js" / "intel_status.js"
JS_SRC = JS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Static contract: required functions + selectors live in the bundle.
# ---------------------------------------------------------------------------


def test_intel_status_js_exports_panel_classifier():
    """The unit-test surface ``window.__adoptiqCorpusPanelState``
    must expose ``classify`` so external test harnesses can pin
    panel logic without a DOM."""
    assert "window.__adoptiqCorpusPanelState" in JS_SRC
    assert "classify: classifyCorpusPanel" in JS_SRC
    assert "label: corpusPanelLabel" in JS_SRC
    assert "pillClass: corpusPanelPillClass" in JS_SRC
    assert "detail: corpusPanelDetail" in JS_SRC


def test_intel_status_js_uses_onedrive_status_field_not_msal():
    """The classifier MUST key off ``boot.onedrive_status`` -- the
    R36 native-auth field -- and MUST NOT key off any MSAL signal
    (sharepoint signed_in / token_expires / etc).  Pin both halves
    so a future regression cannot quietly re-introduce the MSAL
    code path."""
    assert "boot.onedrive_status" in JS_SRC, (
        "classifyCorpusPanel must read boot.onedrive_status; the "
        "Round 36 daily-refresh worker uses this field to gate "
        "refreshes and the panel must agree."
    )
    # Defense-in-depth: no leftover MSAL-flavored keys.
    forbidden = (
        "boot.signed_in",
        "boot.token_expires",
        "boot.sharepoint_account",
        "sharepoint.signed_in",
    )
    for needle in forbidden:
        assert needle not in JS_SRC, (
            f"intel_status.js still references legacy MSAL key "
            f"{needle!r}; Round 36 retired the MSAL/Graph runtime path."
        )


# ---------------------------------------------------------------------------
# State matrix: each of the seven states must have a label + pill.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        "baked_synced",
        "baked_not_synced",
        "fresh_indexing",
        "fresh_not_synced",
        "refreshing",
        "refresh_failed",
        "unknown",
    ],
)
def test_panel_state_has_label_branch(state: str):
    """Each panel state MUST appear in both the classifier (the
    string the function returns) and the label switch (so the user
    sees something other than 'undefined' in the pill).

    The classifier's ``unknown`` is the default branch and is
    asserted via the trailing ``return 'unknown'`` -- any other
    state must appear as a literal in a ``return '<state>'``
    expression."""
    if state == "unknown":
        # Classifier ``unknown`` is the default branch of
        # ``classifyCorpusPanel``; assert the trailing return.
        assert "return 'unknown'" in JS_SRC, (
            "classifyCorpusPanel default branch lost; the panel "
            "would render 'undefined' on first paint before the "
            "first poll lands."
        )
        # Label ``unknown`` is the ``default:`` branch of the
        # switch in ``corpusPanelLabel`` -- pin the literal copy
        # rather than a non-existent ``case 'unknown':`` line.
        assert "default:" in JS_SRC and "checking" in JS_SRC, (
            "corpusPanelLabel default branch must return a "
            "'checking...' pill so the user sees feedback before "
            "the first /api/intel/status poll lands."
        )
    else:
        pattern = "return '" + state + "'"
        assert pattern in JS_SRC, (
            f"classifyCorpusPanel missing branch for state "
            f"{state!r}; the panel would fall through to 'unknown' "
            f"and the user would never see the corresponding label."
        )
        # Label switch: literal case for the explicit states.
        assert "case '" + state + "':" in JS_SRC, (
            f"corpusPanelLabel missing case for {state!r}; the pill "
            f"would render 'checking...' for this state."
        )


# ---------------------------------------------------------------------------
# User-facing labels.
# ---------------------------------------------------------------------------


_LABEL_FRAGMENTS = (
    # baked_synced -- the happy path; user sees "Active" pill.
    ("baked_synced", "Active"),
    ("baked_synced", "OneDrive synced"),
    # baked_not_synced -- baked snapshot active but daily refresh paused.
    ("baked_not_synced", "Active"),
    ("baked_not_synced", "baked snapshot"),
    # fresh_indexing -- legacy / dev path actively building.
    ("fresh_indexing", "Indexing OneDrive"),
    # fresh_not_synced -- the worst case: no baked snapshot AND no sync.
    ("fresh_not_synced", "OneDrive sync required"),
    # refreshing -- daily refresh pass mid-flight.
    ("refreshing", "Refreshing"),
    # refresh_failed -- last refresh raised.
    ("refresh_failed", "Last refresh failed"),
)


@pytest.mark.parametrize(("state", "fragment"), _LABEL_FRAGMENTS)
def test_panel_label_user_copy(state: str, fragment: str):
    """The user-facing copy for each pill must contain the
    expected fragment so a stray edit does not silently change
    operator-visible text (which is read in support tickets)."""
    assert fragment in JS_SRC, (
        f"corpusPanelLabel for {state!r} no longer mentions "
        f"{fragment!r}; users debugging from a support ticket "
        f"will not be able to find the new wording."
    )


def test_panel_detail_includes_onedrive_setup_instructions():
    """The two ``not_synced`` states must include the OneDrive
    setup instructions in their detail copy (sync the canonical
    'AI Projects/AdoptIQ_CSOne_Reports' folder).  Otherwise the
    user has no way to know what to do."""
    assert "AdoptIQ_CSOne_Reports" in JS_SRC, (
        "panel detail copy lost the canonical OneDrive folder name; "
        "users on baked_not_synced / fresh_not_synced will not know "
        "which OneDrive folder to sync."
    )


# ---------------------------------------------------------------------------
# Source-shape pins (R83 cleanup): replace the legacy Python mirror.
# ---------------------------------------------------------------------------
# Round 83 / Phase D7 removed ``_classify_panel`` (a stale Python mirror
# of ``classifyCorpusPanel`` that did NOT model the R53
# ``blocked_no_onedrive`` branch and would NOT have modelled the R83
# ``signed_in_no_corpus`` branch either).  The mirror was a tax on every
# round that touched the JS classifier without buying any signal that
# the same-file static-substring pins above weren't already buying --
# and worse, it advertised a contract ("ten cardinal payloads") that
# silently went stale as the JS state machine grew from seven to nine
# states.  R53 + R68 + R83 panel tests use source-shape pins (see
# ``tests/test_round83_signed_in_no_corpus_panel.py`` for the canonical
# pattern) instead of a Python mirror; this file follows suit.


def test_classifier_dispatches_all_seven_r36_baseline_states():
    """The R36 baseline classifier MUST recognise the seven cardinal
    R36 states.  R53 (``blocked_no_onedrive``) and R83
    (``signed_in_no_corpus``) add two more; their pins live in the
    R53/R83 panel tests respectively."""
    # Round 83 / D7: source-shape replacement for the retired mirror.
    classifier_start = JS_SRC.find("function classifyCorpusPanel")
    classifier_end = JS_SRC.find(
        "function corpusPanelLabel", classifier_start,
    )
    assert classifier_start != -1 and classifier_end != -1
    classifier = JS_SRC[classifier_start:classifier_end]
    # Pin the seven R36 baseline state names -- if any are dropped or
    # renamed, this test fires.
    for state in (
        "baked",          # source value (resolves to baked_synced /
                          # baked_not_synced via the OneDrive status check)
        "fresh",          # similar
        "synced",         # od_status branch
        "not_synced",     # od_status branch (used for messaging downstream)
        "in_progress",    # refreshing branch
        "last_refresh_error",  # refresh_failed branch
    ):
        assert state in classifier, (
            f"R36 baseline classifier branch for {state!r} missing -- "
            "did a refactor drop a state?"
        )
    # The classifier MUST emit the four cardinal R36 state labels.
    for label in (
        "'baked_synced'",
        "'baked_not_synced'",
        "'fresh_indexing'",
        "'fresh_not_synced'",
        "'refreshing'",
        "'refresh_failed'",
        "'unknown'",
    ):
        assert label in classifier, (
            f"R36 baseline classifier output {label!r} missing -- "
            "did a refactor drop a state?"
        )


def test_label_for_baked_synced_says_active_and_synced():
    """Pin the user copy for the happy-path label
    (``baked_synced`` -> ``Active (OneDrive synced)``).  This is
    the most-visible label in the panel; a regression here would
    confuse an operator opening a healthy install."""
    # Round 83 / D7: was previously gated on the Python mirror,
    # now uses the JS source-shape directly.
    label_block = re.search(
        r"function corpusPanelLabel\(state\)\s*\{(.+?)\n\s*\}",
        JS_SRC,
        re.DOTALL,
    )
    assert label_block is not None, "corpusPanelLabel function missing"
    body = label_block.group(1)
    case_pos = body.find("case 'baked_synced':")
    assert case_pos != -1, "label switch missing case for baked_synced"
    return_pos = body.find("return", case_pos)
    assert return_pos != -1
    return_line = body[return_pos:body.find("\n", return_pos)]
    assert "Active" in return_line and "OneDrive synced" in return_line, (
        f"corpusPanelLabel(baked_synced) returned unexpected line: "
        f"{return_line!r}"
    )
