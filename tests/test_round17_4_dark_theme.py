"""Round 17.4 / Dark Theme + Light/Dark Toggle regression tests.

These assertions pin the *contract* of the dark-by-default UI shipped
in Round 17.4 so future edits cannot silently regress:

1. ``<html>`` carries ``data-bs-theme="dark"`` -- dark is the
   advertised default, not just a CSS opinion.
2. An early-paint inline ``<script>`` in ``<head>`` reads
   ``adoptiq-theme`` from ``localStorage`` and applies it to the
   ``<html>`` element BEFORE first paint.  Without this guard a
   returning user sees a flash of the wrong theme (FOUC).
3. The new semantic theme tokens (``--adoptiq-orange``, ``--bg-base``,
   ``--bg-surface``, ``--text-primary``, ``--accent-primary``) are
   defined in ``:root`` so per-page rules can rely on them.
4. A ``[data-bs-theme="light"]`` override block exists, proving the
   toggle is reversible -- not a one-way trip into dark mode.
5. **Round 13 invariant pin**: the four ``--risk-*`` tokens
   (``--risk-critical``, ``--risk-high``, ``--risk-medium``,
   ``--risk-low``) are byte-identical with the Tableau / matplotlib /
   Excel band colors driven by ``canonical_metrics.RISK_BAND_COLORS``.
   The whole point of Round 13 was that HTML risk badges, chart fills,
   and Excel conditional formatting all share the SAME hex.  The dark
   theme MUST NOT have moved them.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_HTML = REPO_ROOT / "templates" / "base.html"


@pytest.fixture(scope="module")
def base_html_text() -> str:
    """Read templates/base.html once for all assertions in this module."""
    assert BASE_HTML.exists(), f"missing template: {BASE_HTML}"
    return BASE_HTML.read_text(encoding="utf-8")


def test_dark_theme_is_advertised_default(base_html_text: str) -> None:
    """Round 17.4: <html> opening tag must declare data-bs-theme="dark"."""
    assert 'data-bs-theme="dark"' in base_html_text, (
        "Round 17.4 contract: <html> must carry data-bs-theme=\"dark\" so "
        "Bootstrap 5.3 paints the dark UI as the default."
    )
    assert '<html lang="en" data-bs-theme="dark">' in base_html_text, (
        "The dark-by-default attribute must live on the opening <html> tag, "
        "not somewhere lower in the document."
    )


def test_early_paint_script_prevents_fouc(base_html_text: str) -> None:
    """Round 17.4: <head> must run a small inline script that applies the
    persisted theme BEFORE first paint, otherwise returning users see a
    flash of the wrong theme."""
    head_chunk = base_html_text.split("</head>", 1)[0]
    assert "adoptiq-theme" in head_chunk, (
        "Early-paint bootstrap must reference the localStorage key "
        "'adoptiq-theme' (same key used by the toggle) inside <head>."
    )
    assert "localStorage" in head_chunk, (
        "Early-paint bootstrap must call window.localStorage to read the "
        "user's persisted theme choice before first paint."
    )
    assert "setAttribute('data-bs-theme'" in head_chunk, (
        "Early-paint bootstrap must apply the resolved theme by setting "
        "data-bs-theme on the documentElement before first paint."
    )


def test_semantic_theme_tokens_are_defined(base_html_text: str) -> None:
    """Round 17.4: the new semantic tokens must live in :root so per-page
    rules can rely on them without redefining colors."""
    expected_tokens = (
        "--adoptiq-orange",
        "--bg-base",
        "--bg-surface",
        "--text-primary",
        "--accent-primary",
    )
    for token in expected_tokens:
        assert token in base_html_text, (
            f"Round 17.4 token contract: '{token}' must be defined in :root "
            f"in templates/base.html so the dark/light toggle can pivot "
            f"the chrome consistently."
        )


def test_light_theme_override_block_exists(base_html_text: str) -> None:
    """Round 17.4: the toggle must be reversible -- a
    [data-bs-theme="light"] override block has to exist so clicking the
    toggle from dark -> light actually restores light chrome."""
    assert '[data-bs-theme="light"]' in base_html_text, (
        "Round 17.4 reversibility contract: a [data-bs-theme=\"light\"] "
        "override block must exist in templates/base.html so the toggle "
        "is a true two-way switch."
    )


def test_risk_band_hexes_are_byte_identical_round_13_pin(base_html_text: str) -> None:
    """Round 13 invariant: --risk-* tokens must match the chart/Excel
    palette byte-for-byte.  Dark theme MUST NOT have moved these.

    Why this is locked: ``canonical_metrics.RISK_BAND_COLORS`` drives the
    matplotlib chart fills and the Excel conditional formatting, AND the
    HTML badges in the report.  The whole Round 13 audit was about making
    those three surfaces agree.  If the dark theme silently tweaks these
    hexes for "better dark contrast", the badges in the HTML report stop
    matching the chart colors and the Excel cells, which is exactly the
    cross-surface drift Round 13 was supposed to eliminate.
    """
    expected_risk_hexes = {
        "--risk-critical": "#d62728",
        "--risk-high": "#ff7f0e",
        "--risk-medium": "#ffd700",
        "--risk-low": "#2ca02c",
    }
    for token, expected_hex in expected_risk_hexes.items():
        declaration = f"{token}: {expected_hex};"
        assert declaration in base_html_text, (
            f"Round 13 invariant violated: '{declaration}' must be "
            f"byte-identical in templates/base.html.  This token is "
            f"shared with chart palettes and Excel conditional "
            f"formatting; if you changed it, the dark UI badges no "
            f"longer match the chart/Excel colors and Round 13's "
            f"cross-surface parity is broken."
        )
