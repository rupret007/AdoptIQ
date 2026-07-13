"""Round 87 / Phase 5: source-shape pin for the indexing-safe clarifier.

Build 62 acceptance feedback flagged a UX gap: while the AdoptIQ
Intelligence panel showed "Indexing CSOne reports..." with no
secondary signal, the operator was about to run reports and had no
indication whether report generation was blocked by the index pass.
The contract is that core reports (Comprehensive, Compact, Renewal,
Leader) do NOT depend on the corpus index -- only Ask AI grounding
does -- but the panel didn't surface that fact.

Round 87 / Phase 5 adds a small italic clarifier line right under
the "Indexing CSOne reports..." summary that says:

    "Reports remain safe to run during indexing -- only Ask AI
    grounding waits for the index pass to finish."

The clarifier is server-rendered with ``hidden`` and unhidden by
``intel_status.js::paintBanner`` only when the panel state is
``running`` (the same ``boot.in_progress`` signal that drives the
summary text), so the line never appears outside an active index
pass.

Pinned by these tests:
    * ``test_r87_analyze_html_carries_indexing_clarifier_marker`` --
      the ``[data-intel-indexing-clarifier]`` attribute is present
      on the analyze.html banner, so the JS can find the element.
    * ``test_r87_analyze_html_clarifier_hidden_by_default`` --
      the element ships with ``hidden`` so SSR markup never shows
      the clarifier outside an active index pass (defense-in-depth
      for users with JS disabled).
    * ``test_r87_analyze_html_clarifier_text_is_factual`` -- the
      visible text mentions reports + indexing + Ask AI so a future
      copy-edit cannot silently drop the substantive content.
    * ``test_r87_intel_status_js_toggles_clarifier_on_running`` --
      ``paintBanner`` toggles the ``hidden`` attribute based on the
      ``state === 'running'`` branch, mirroring the
      ``boot.in_progress`` SSR conditional in analyze.html.

Source-shape only -- no DOM, no Flask, no rendering.
"""

# Round 87 / Phase 5 -- corpus indexing UX subtitle source-shape pin.

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def analyze_html_text() -> str:
    """Return the raw ``templates/analyze.html`` text."""
    path = Path(__file__).resolve().parent.parent / "templates" / "analyze.html"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def intel_status_js_text() -> str:
    """Return the raw ``static/js/intel_status.js`` text."""
    path = (
        Path(__file__).resolve().parent.parent / "static" / "js" / "intel_status.js"
    )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Section A: analyze.html source-shape
# ---------------------------------------------------------------------------


def test_r87_analyze_html_carries_indexing_clarifier_marker(analyze_html_text):
    """The ``[data-intel-indexing-clarifier]`` attribute MUST be
    present on analyze.html so ``intel_status.js::paintBanner`` can
    find the element via ``banner.querySelector(...)``.

    A future round that renames the attribute MUST update both the
    template AND the JS module; this test fails LOUD on either-side
    drift."""
    assert "data-intel-indexing-clarifier" in analyze_html_text, (
        "R87 / Phase 5: analyze.html no longer carries the "
        "[data-intel-indexing-clarifier] marker.  The JS-side "
        "toggle in intel_status.js cannot find the element to "
        "show/hide."
    )


def test_r87_analyze_html_clarifier_hidden_by_default(analyze_html_text):
    """The clarifier element MUST be server-rendered with the
    ``hidden`` attribute so users with JS disabled (or the
    pre-first-poll SSR window) never see the line outside an
    active index pass.

    Defense-in-depth: the JS module unhides only when state is
    ``running``, so a missing or buggy JS path leaves the
    clarifier hidden by default rather than visible."""
    # Find the clarifier element and check the attribute appears
    # within its tag.  We don't pin attribute ordering; just that
    # both ``data-intel-indexing-clarifier`` and ``hidden`` appear
    # on the same element.
    marker_idx = analyze_html_text.find("data-intel-indexing-clarifier")
    assert marker_idx > -1, (
        "R87 / Phase 5: clarifier marker missing (precondition "
        "for the hidden-default test)."
    )
    # Walk back to the opening ``<p`` of this element and forward
    # to its closing ``>`` so we can assert both attributes are on
    # the same tag.
    open_tag_start = analyze_html_text.rfind("<p", 0, marker_idx)
    open_tag_end = analyze_html_text.find(">", marker_idx)
    assert open_tag_start > -1 and open_tag_end > -1, (
        "R87 / Phase 5: could not bracket the clarifier <p> tag "
        "around the data-attribute marker."
    )
    open_tag = analyze_html_text[open_tag_start : open_tag_end + 1]
    assert "hidden" in open_tag, (
        "R87 / Phase 5: the clarifier <p> tag is missing the "
        f"``hidden`` attribute.  Tag: {open_tag!r}.  Without "
        "``hidden``, SSR markup would show the line outside an "
        "active index pass for users with JS disabled."
    )


def test_r87_analyze_html_clarifier_text_is_factual(analyze_html_text):
    """The clarifier's visible text MUST mention reports + indexing
    + Ask AI so a future copy-edit cannot silently drop the
    substantive content (e.g. shrink to "Indexing in progress" --
    which would be redundant with the summary line above and lose
    the user-actionable signal).

    The exact wording is allowed to drift between rounds; the
    three concept anchors are not."""
    marker_idx = analyze_html_text.find("data-intel-indexing-clarifier")
    assert marker_idx > -1, (
        "R87 / Phase 5: clarifier marker missing (precondition "
        "for the factual-text test)."
    )
    # Find the closing ``</p>`` for the clarifier and pull the
    # text body so we can assert the three concept anchors.
    close_idx = analyze_html_text.find("</p>", marker_idx)
    assert close_idx > -1, "R87 / Phase 5: clarifier <p> not closed."
    body = analyze_html_text[marker_idx:close_idx].lower()
    for needle in ("reports", "indexing", "ask ai"):
        assert needle in body, (
            f"R87 / Phase 5: clarifier text is missing the "
            f"substantive anchor {needle!r}.  The line MUST "
            f"name reports + indexing + Ask AI so the user "
            f"knows core reports are unblocked while the index "
            f"pass runs."
        )


# ---------------------------------------------------------------------------
# Section B: intel_status.js source-shape
# ---------------------------------------------------------------------------


def test_r87_intel_status_js_toggles_clarifier_on_running(intel_status_js_text):
    """``intel_status.js::paintBanner`` MUST toggle the clarifier's
    ``hidden`` attribute based on ``state === 'running'`` so the
    line appears only during an active index pass.  The branch
    must call BOTH ``removeAttribute('hidden')`` (running) AND
    ``setAttribute('hidden', '')`` (every other state) so the
    line is hidden cleanly when the panel transitions out of
    ``running``.

    A future refactor that drops the toggle logic OR changes the
    state predicate (e.g. uses ``boot.in_progress`` directly
    without going through ``classifyState``) will fail this
    assertion -- the SSR conditional in analyze.html is keyed on
    ``boot.in_progress`` so the JS-side classifier MUST pass that
    same signal through ``state === 'running'`` for the surfaces
    to agree."""
    # Pin the querySelector hook for the clarifier element.
    assert "data-intel-indexing-clarifier" in intel_status_js_text, (
        "R87 / Phase 5: paintBanner does not query for the "
        "clarifier element.  The toggle hook is missing."
    )
    # Pin the running-branch unhide.
    assert (
        "removeAttribute('hidden')" in intel_status_js_text
        or 'removeAttribute("hidden")' in intel_status_js_text
    ), (
        "R87 / Phase 5: paintBanner does not call "
        "removeAttribute('hidden') anywhere -- the clarifier "
        "cannot be unhidden during an active index pass."
    )
    # Pin the not-running-branch re-hide.
    assert (
        "setAttribute('hidden', '')" in intel_status_js_text
        or 'setAttribute("hidden", "")' in intel_status_js_text
    ), (
        "R87 / Phase 5: paintBanner does not call "
        "setAttribute('hidden', '') anywhere -- once the "
        "clarifier is unhidden it would stay visible after the "
        "index pass completes."
    )
    # Pin the predicate.  We accept either single or double quoted
    # ``running`` literals.
    assert (
        "state === 'running'" in intel_status_js_text
        or 'state === "running"' in intel_status_js_text
    ), (
        "R87 / Phase 5: paintBanner toggle is not gated on "
        "state === 'running' -- the JS-side state classifier "
        "MUST agree with the SSR ``boot.in_progress`` "
        "conditional in analyze.html."
    )
