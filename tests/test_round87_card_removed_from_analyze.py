"""Round 87 / Phase 4: regression guard for the corpus-share-URL card relocation.

Build 60 (R84) introduced the operator-configurable corpus share URL
card on ``templates/analyze.html``.  Build 61 (R85) rotated the
shipping default URL and ALSO added the card to
``templates/preferences.html`` so the Preferences hub had a
canonical admin-config surface.

Round 87 / Phase 4 finishes the migration: the card is removed from
``templates/analyze.html`` (admin-config doesn't belong on the
per-run report tile -- mirrors the R73 UX-2 split that moved the
report-narrative model picker off the analyze page for the same
reason) and the canonical surface becomes the Preferences hub.

This file pins the source-shape contract so a future regression that
re-adds the card to ``analyze.html`` (or removes it from
``preferences.html``) fails LOUD.

The R85 tests (``tests/test_round85_url_refresh_and_preferences_card.py``)
already cover the markers / wiring on preferences.html; the R84
file (``tests/test_round84_corpus_share_url_ui_source_shape.py``) was
repurposed by Phase 4 to validate the same surface.  This file is
the "negative-control" guard -- the explicit assertion that the
analyze page no longer carries the card.

Pinned by these tests:
    * ``test_r87_analyze_html_does_not_carry_card_section_id`` --
      the R84 container ID ``adoptiq-corpus-share-url-card`` is gone
      from analyze.html.
    * ``test_r87_analyze_html_does_not_carry_data_card_marker`` --
      the R84 ``[data-corpus-share-url-card]`` marker is gone from
      analyze.html.
    * ``test_r87_analyze_html_does_not_load_corpus_share_url_js`` --
      the ``static/js/corpus_share_url.js`` script tag is gone from
      analyze.html.
    * ``test_r87_analyze_html_carries_relocation_marker`` -- the
      Phase 4 comment block stays on analyze.html so a future round
      that re-introduces the card has to consciously delete the
      marker (not just paste the section back in).
    * ``test_r87_preferences_html_still_carries_card_section_id`` --
      negative-control on the destination side: the Preferences hub
      MUST still carry the card.
    * ``test_r87_preferences_html_still_loads_corpus_share_url_js`` --
      same: the script tag MUST still be wired on preferences.html.

Source-shape only -- no Flask, no rendering, no behavior assertions.
The R84 endpoint behavior + the R85 URL contracts are covered by
their respective test files.
"""

# Round 87 / Phase 4 -- regression guard for the card relocation.

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures: read the two templates as plain text (filesystem only).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def analyze_html_text() -> str:
    """Return the raw ``templates/analyze.html`` text.

    Used for the negative-control assertions: the R84 card MUST be
    gone from this surface after Phase 4.
    """
    path = Path(__file__).resolve().parent.parent / "templates" / "analyze.html"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def preferences_html_text() -> str:
    """Return the raw ``templates/preferences.html`` text.

    Used for the destination-side assertions: the R84 card MUST
    still live on the Preferences hub after Phase 4 (R85 added it
    here; Phase 4 just removes the analyze-page duplicate).
    """
    path = (
        Path(__file__).resolve().parent.parent / "templates" / "preferences.html"
    )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Negative-control assertions on analyze.html
# ---------------------------------------------------------------------------


def test_r87_analyze_html_does_not_carry_card_section_id(analyze_html_text):
    """The R84 container ID ``adoptiq-corpus-share-url-card`` MUST
    NOT appear in analyze.html.  A future regression that re-adds
    the card via copy-paste from the R84 source will trip on this
    assertion -- the R84 ID is the most distinctive needle.

    The destination on preferences.html uses ``prefs-corpus-share-url-card``
    (a different ID) so this assertion does not collide with the
    R85 Preferences-hub card."""
    assert "adoptiq-corpus-share-url-card" not in analyze_html_text, (
        "R87 / Phase 4 regression: the corpus-share-URL card was "
        "re-added to analyze.html.  Admin-config belongs on the "
        "Preferences hub (preferences.html); the analyze page is "
        "for running reports."
    )


def test_r87_analyze_html_does_not_carry_data_card_marker(analyze_html_text):
    """The ``[data-corpus-share-url-card]`` data-attribute marker
    MUST NOT appear in analyze.html.  The JS module
    (``static/js/corpus_share_url.js``) binds against this marker
    via ``document.querySelector`` so its presence on analyze.html
    would mean the card is live on that surface -- exactly the
    Phase 4 regression we're guarding against."""
    assert "data-corpus-share-url-card" not in analyze_html_text, (
        "R87 / Phase 4 regression: the corpus-share-URL card "
        "data-marker was re-added to analyze.html.  The card's JS "
        "handler binds against [data-corpus-share-url-card] on "
        "page load -- adding it back here would re-bind the card "
        "on the analyze page."
    )


def test_r87_analyze_html_does_not_load_corpus_share_url_js(analyze_html_text):
    """The ``static/js/corpus_share_url.js`` ``<script>`` tag MUST
    NOT appear in analyze.html.  The script is loaded by
    preferences.html instead.

    Loading it here would silently re-bind any future
    ``[data-corpus-share-url-card]`` markup that drifted back onto
    the analyze page -- a defense-in-depth pin against the
    full-card regression captured by the previous two tests."""
    forbidden_substrings = [
        "filename='js/corpus_share_url.js'",
        'filename="js/corpus_share_url.js"',
    ]
    for needle in forbidden_substrings:
        assert needle not in analyze_html_text, (
            f"R87 / Phase 4 regression: analyze.html loads "
            f"corpus_share_url.js via {needle!r}.  The script "
            f"belongs on preferences.html; loading it here would "
            f"re-bind the card on the analyze page if any of the "
            f"data-attribute markers drifted back."
        )


def test_r87_analyze_html_carries_relocation_marker(analyze_html_text):
    """analyze.html MUST keep the Phase 4 relocation comment block
    so a future round that re-introduces the card has to consciously
    delete the marker (not just paste the section back in).

    The marker is the canonical Round 87 anchor for ``git diff
    analyze.html | grep 'Round 87'`` -- removing it silently would
    drop the per-file footprint for this Phase 4 work."""
    assert "Round 87 / Phase 4" in analyze_html_text, (
        "R87 / Phase 4 regression: the relocation comment block "
        "was deleted from analyze.html.  The block is the "
        "canonical anchor for the per-file footprint and the "
        "documented reason the card moved off this surface."
    )


# ---------------------------------------------------------------------------
# Destination-side assertions on preferences.html
# (the R85 contract -- pinned here as a defense-in-depth so a
# future round can't drop the destination AND the source at once)
# ---------------------------------------------------------------------------


def test_r87_preferences_html_still_carries_card_section_id(preferences_html_text):
    """preferences.html MUST still carry the
    ``prefs-corpus-share-url-card`` container ID.  R85 added it;
    Phase 4 must NOT drop it -- otherwise the operator has no
    surface to configure the share URL.

    This is the destination-side guard that pairs with the
    negative-control on analyze.html."""
    assert 'id="prefs-corpus-share-url-card"' in preferences_html_text, (
        "R87 / Phase 4 regression: the Preferences-hub container "
        "ID is missing.  The Phase 4 migration removed the card "
        "from analyze.html on the assumption that preferences.html "
        "would still carry it; without this ID the operator has no "
        "configurable surface for the share URL at all."
    )


def test_r87_preferences_html_still_loads_corpus_share_url_js(preferences_html_text):
    """preferences.html MUST still load
    ``static/js/corpus_share_url.js``.  The card's UI is inert
    without the IIFE that binds the save / clear / test handlers
    against the data-attribute markers."""
    candidates = [
        "filename='js/corpus_share_url.js'",
        'filename="js/corpus_share_url.js"',
    ]
    assert any(c in preferences_html_text for c in candidates), (
        "R87 / Phase 4 regression: preferences.html no longer "
        "loads corpus_share_url.js.  The card markup is present "
        "but the JS handler is gone -- the card's input / save / "
        "test buttons are inert."
    )
