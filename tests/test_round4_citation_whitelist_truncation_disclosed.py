"""Round 4 / Phase 6.7 regression test.

When the citation whitelist exceeds 400 IDs, the rendered prompt
fragment must include a disclosure line like
``... and N additional IDs (whitelist truncated; cite from the
Evidence rows below if needed)`` so the model knows the visible list
is partial.  Pre-Round-4 we silently sliced ``[:400]`` and the model
saw an apparently complete list.
"""
from __future__ import annotations

from ask_ai_grounded import _render_citation_whitelist


def test_whitelist_under_cap_is_rendered_in_full() -> None:
    ids = {f"AB-{i:03d}" for i in range(50)}
    rendered = _render_citation_whitelist(ids, cap=400)
    assert "additional IDs" not in rendered, (
        "Round 4 Phase 6.7: when whitelist is below the cap, no "
        "truncation disclosure should be added."
    )
    for i in range(50):
        assert f"AB-{i:03d}" in rendered


def test_whitelist_over_cap_appends_disclosure() -> None:
    ids = {f"AB-{i:04d}" for i in range(450)}
    rendered = _render_citation_whitelist(ids, cap=400)
    assert "additional IDs" in rendered, (
        "Round 4 Phase 6.7: when whitelist exceeds the cap, the "
        "rendered prompt fragment MUST include an "
        "'... and N additional IDs ...' disclosure."
    )
    assert "50" in rendered, (
        "Round 4 Phase 6.7: disclosure must include the actual count "
        "(450 - 400 = 50)."
    )
    assert "truncated" in rendered.lower(), (
        "Round 4 Phase 6.7: disclosure must mention truncation so the "
        "model understands the visible list is partial."
    )
