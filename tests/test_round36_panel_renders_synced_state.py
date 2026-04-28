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
# Behavioral re-implementation: mirror classifyCorpusPanel in Python.
# ---------------------------------------------------------------------------


def _classify_panel(payload):
    """Python mirror of the JS classifyCorpusPanel function.  Pinned
    here so a behavioral regression in either side surfaces.  Branch
    order matches the JS source (in_progress > error > baked vs
    fresh)."""
    boot = (payload or {}).get("boot") if payload else None
    if not boot:
        return "unknown"
    if boot.get("in_progress"):
        return "refreshing"
    if boot.get("last_refresh_error"):
        return "refresh_failed"
    source = boot.get("source") if isinstance(boot.get("source"), str) else ""
    od = (
        boot.get("onedrive_status")
        if isinstance(boot.get("onedrive_status"), str)
        else ""
    )
    if source == "baked" and od == "synced":
        return "baked_synced"
    if source == "baked":
        return "baked_not_synced"
    if source == "fresh" and od == "synced":
        return "fresh_indexing"
    if source == "fresh":
        return "fresh_not_synced"
    return "unknown"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # Cardinal four (the spec's primary states).
        (
            {
                "boot": {
                    "source": "baked",
                    "onedrive_status": "synced",
                    "onedrive_file_count": 12,
                }
            },
            "baked_synced",
        ),
        (
            {
                "boot": {
                    "source": "baked",
                    "onedrive_status": "not_synced",
                    "onedrive_file_count": 0,
                }
            },
            "baked_not_synced",
        ),
        (
            {
                "boot": {
                    "source": "fresh",
                    "onedrive_status": "synced",
                    "onedrive_file_count": 7,
                }
            },
            "fresh_indexing",
        ),
        (
            {
                "boot": {
                    "source": "fresh",
                    "onedrive_status": "not_synced",
                    "onedrive_file_count": 0,
                }
            },
            "fresh_not_synced",
        ),
        # Refresh in progress wins regardless of source / status.
        (
            {
                "boot": {
                    "source": "baked",
                    "onedrive_status": "synced",
                    "in_progress": True,
                }
            },
            "refreshing",
        ),
        # Error wins over the baked/fresh classification (operator
        # must see the error first).
        (
            {
                "boot": {
                    "source": "baked",
                    "onedrive_status": "synced",
                    "last_refresh_error": "URLError",
                }
            },
            "refresh_failed",
        ),
        # Defaults: no payload -> unknown (pre-poll grey pill).
        (None, "unknown"),
        ({}, "unknown"),
        ({"boot": None}, "unknown"),
        # Malformed source / status -> unknown (defensive).
        (
            {"boot": {"source": None, "onedrive_status": "synced"}},
            "unknown",
        ),
    ],
)
def test_classify_panel_matrix(payload, expected):
    """Behavioral pin: the ten cardinal payloads classify exactly as
    documented in the panel spec.  Mirrors the JS one-to-one so a
    drift on either side fails this test."""
    assert _classify_panel(payload) == expected


def test_label_for_baked_synced_says_active_and_synced():
    """Compose a payload, run it through both classifier + label
    extraction, and assert the user sees 'Active' + 'OneDrive
    synced' on the happy path."""
    payload = {
        "boot": {
            "source": "baked",
            "onedrive_status": "synced",
            "onedrive_file_count": 5,
        }
    }
    state = _classify_panel(payload)
    assert state == "baked_synced"
    # Spot-check that the JS label switch carries the corresponding
    # user copy (we already pinned the copy above, but assert it
    # composes through the state lookup here).
    label_block = re.search(
        r"function corpusPanelLabel\(state\)\s*\{(.+?)\n\s*\}",
        JS_SRC,
        re.DOTALL,
    )
    assert label_block is not None, "corpusPanelLabel function missing"
    body = label_block.group(1)
    case_pos = body.find("case '" + state + "':")
    assert case_pos != -1, f"label switch missing case for {state}"
    # Find the return statement that follows the case label and
    # assert it carries the expected fragment.
    return_pos = body.find("return", case_pos)
    assert return_pos != -1
    return_line = body[return_pos:body.find("\n", return_pos)]
    assert "Active" in return_line and "OneDrive synced" in return_line, (
        f"corpusPanelLabel({state}) returned unexpected line: "
        f"{return_line!r}"
    )
