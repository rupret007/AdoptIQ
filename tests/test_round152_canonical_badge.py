"""Round 152 / B4 -- canonical cross-check must be visible to the user.

``ask_ai_grounded._r95_apply_canonical_corrections`` deletes any sentence
whose KPI contradicts ``canonical_metrics`` and appends a
"### Canonical Metrics" section carrying the true values.  It returns a
structured record of what was corrected (``canonical_corrections``) and of
what was checked and agreed (``canonical_verified``), and BOTH the sync
route and the SSE route already forward those fields.

Nothing in ``static/js`` read either of them.  The user saw a bare
"Canonical Metrics" heading with no indication that a correction had
occurred -- and, when the answer was fully verified, no indication of that
either.  The single strongest trust signal the product computes was
transported to the browser and discarded.
"""

from __future__ import annotations

import re

import pytest

from tests.source_shape_utils import assert_in_source, read_repo_file


def _band_js() -> str:
    return read_repo_file("static/js/r152_canonical_badge.js")


def test_round152_canonical_badge_element_exists() -> None:
    body = read_repo_file("templates/ask_ai.html")
    match = re.search(r'<span class="badge[^"]*"\s*\n?\s*id="r152CanonicalBadge"', body)
    assert match, "Round 152 / B4: canonical badge element missing from ask_ai.html"
    tail = body[body.index("r152CanonicalBadge"):][:260]
    assert 'aria-live="polite"' in tail, "the badge changes asynchronously; it must be announced"


def test_round152_classify_canonical_is_exported() -> None:
    body = _band_js()
    assert_in_source(body, "classifyCanonical: classifyCanonical")
    assert_in_source(body, "renderCanonicalBadge: renderCanonicalBadge")
    assert_in_source(body, "window.AdoptIQCanonicalBadge")


def test_round152_corrections_take_priority_over_verified() -> None:
    """A correction is the signal that matters; it must not be masked."""
    body = _band_js()
    corrections_at = body.index("if (corrections.length) {")
    verified_at = body.index("if (verified.length) {")
    assert corrections_at < verified_at


def test_round152_badge_reports_both_stated_and_canonical_values() -> None:
    """"3 corrected" alone is not actionable -- name the delta."""
    body = _band_js()
    assert_in_source(body, "answer said ")
    assert_in_source(body, ", canonical is ")


def test_round152_badge_uses_text_content_not_inner_html() -> None:
    """Correction payloads carry server-derived KPI labels."""
    body = _band_js()
    render = body[body.index("function renderCanonicalBadge"):]
    # Strip comments before checking -- the rationale comment names the API
    # it deliberately avoids.
    code = "\n".join(
        line for line in render.splitlines() if not line.lstrip().startswith("//")
    )
    assert "el.textContent" in code
    assert "innerHTML" not in code


@pytest.mark.parametrize("needle", ["canonical: ' + corrections.length + ' corrected", "canonical: ' + verified.length + ' verified"])
def test_round152_badge_states_are_distinguishable(needle: str) -> None:
    assert_in_source(_band_js(), needle)


def test_round152_no_applicable_state_is_neutral_not_green() -> None:
    """An answer with no cross-checkable KPI must not read as verified."""
    body = _band_js()
    tail = body[body.index("canonical: not applicable") - 400: body.index("canonical: not applicable") + 200]
    assert "bg-secondary-subtle" in tail, (
        "Round 152 / B4: 'not applicable' must be neutral; showing it green "
        "would fabricate a verification that never happened."
    )


def test_round152_badge_rendered_on_both_transports() -> None:
    """Sync and SSE must reach the same trust state."""
    body = read_repo_file("static/js/ask_ai.js")
    assert body.count("renderCanonicalBadge(") >= 2, (
        "Round 152 / B4: the canonical badge must render on both the sync and "
        "the SSE answer paths, matching the existing confidence-band parity."
    )
    assert_in_source(body, "renderCanonicalBadge(data)")
    assert_in_source(body, "renderCanonicalBadge(metaPayload)")


def test_round152_badge_is_cleared_between_questions() -> None:
    """A stale verdict must not linger over a new question."""
    assert_in_source(
        read_repo_file("static/js/ask_ai.js"),
        "if (_r152CanonicalBadge) { _r152CanonicalBadge.style.display = 'none'; }",
    )


def test_round152_server_still_returns_the_fields_the_badge_reads() -> None:
    """Pin the contract on the producing side too."""
    body = read_repo_file("ask_ai_grounded.py")
    assert_in_source(body, '"canonical_corrections": _r95_cross_check.corrections')
    assert_in_source(body, '"canonical_verified": _r95_cross_check.verified')


def test_round152_badge_lives_outside_the_confidence_band_module() -> None:
    """Round 95 / Phase D requires the confidence band to derive its level
    ONLY from the server trust score, and pins that as a source-shape
    contract over ``r95_confidence_band.js``.  Keeping the canonical badge in
    its own module means that contract stays literally true of that file
    instead of being loosened to accommodate a second, unrelated badge."""
    band = read_repo_file("static/js/r95_confidence_band.js")
    assert "canonical_corrections" not in band
    assert "renderCanonicalBadge" not in band


def test_round152_badge_module_is_loaded_by_the_ask_ai_page() -> None:
    assert_in_source(
        read_repo_file("templates/ask_ai.html"),
        "js/r152_canonical_badge.js",
    )
