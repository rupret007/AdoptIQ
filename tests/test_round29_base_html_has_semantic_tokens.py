"""Round 29 / M1 -- base.html declares the status / accent semantic tokens.

The Round-28 plan promised ``--success-bg`` / ``--success-fg`` /
``--warning-bg`` / ``--warning-fg`` / ``--danger-bg`` / ``--danger-fg``
/ ``--info-card-border`` so child templates (``progress.html``,
``previous_reports.html``) could token-reference the status tints
instead of hardcoding ``rgba(...)`` literals derived from brand
hexes.  The tokens never landed, so those templates carried inline
``rgba`` literals and re-skinned manually under
``[data-bs-theme="dark"]``.

Round 29 adds the tokens to BOTH the ``:root`` block AND the
``[data-bs-theme="dark"]`` override block in ``base.html`` so the
toggle re-skins all three template surfaces from a single source
of truth.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE_HTML = REPO_ROOT / "templates" / "base.html"

REQUIRED_TOKENS = (
    "--success-bg",
    "--success-fg",
    "--warning-bg",
    "--warning-fg",
    "--danger-bg",
    "--danger-fg",
    "--info-card-border",
)


def _extract_block(src: str, selector: str) -> str:
    """Return the body of the FIRST CSS block whose selector text
    matches ``selector``.  Naive but sufficient for an inline
    ``<style>`` tag with hand-formatted blocks (no preprocessor /
    nested rules).
    """
    # ``selector`` is matched literally on a line that ends with ``{``.
    pattern = re.compile(
        re.escape(selector) + r"\s*\{(?P<body>.*?)\n\s*\}",
        re.DOTALL,
    )
    match = pattern.search(src)
    assert match is not None, f"Could not locate CSS block for selector {selector!r} in base.html"
    return match.group("body")


def test_root_block_declares_round29_semantic_tokens():
    src = BASE_HTML.read_text(encoding="utf-8")
    root_body = _extract_block(src, ":root")
    for token in REQUIRED_TOKENS:
        assert token + ":" in root_body, (
            "Round 29 / M1: ``base.html`` :root block must declare "
            "``%s`` so child templates (progress.html, "
            "previous_reports.html) can token-reference the status "
            "tints from a single source of truth instead of "
            "hardcoding inline rgba(...) literals." % token
        )


def test_dark_theme_block_overrides_round29_semantic_tokens():
    src = BASE_HTML.read_text(encoding="utf-8")
    dark_body = _extract_block(src, '[data-bs-theme="dark"]')
    for token in REQUIRED_TOKENS:
        assert token + ":" in dark_body, (
            "Round 29 / M1: ``base.html`` [data-bs-theme=\"dark\"] "
            "block must override ``%s`` so the dark/light toggle "
            "re-skins the status surfaces (status-box, csone-status, "
            "partial-warnings, previous-reports-strip, file-type) "
            "without each template having to dual-write its own "
            "``[data-bs-theme=\"dark\"] .selector`` rule." % token
        )
