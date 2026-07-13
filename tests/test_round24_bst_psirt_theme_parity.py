"""Round 24 -- BST / PSIRT search template theme parity.

Marker tests for the post-fix shape of
``templates/bst_psirt_search.html`` after Round 24 Phase B
tokenization.  This template was the noisiest theme-parity hotspot
in the audit (40 hex/rgb literals in inline ``<style>``); most of
them are intentional brand-paired colors (white text on a gradient,
Bootstrap-default alert hexes paired with explicit
``[data-bs-theme="dark"]`` overrides, translucent overlays on
gradients).  The Round 24 plan flagged exactly two literals as
needing a token-driven swap:

1. ``.classification-restricted { color: #333 }`` -> ``var(--text-primary)``
2. ``.classification-internal { background: #ff9800 }`` -> ``var(--cisco-warning)``

These are pinned below as exact strings.  An audit-pass dark-mode
override on ``.classification-restricted`` is also pinned because
``--cisco-warning`` is a non-flipping brand color (Round 13
invariant), so swapping ``#333`` for the auto-flipping
``var(--text-primary)`` would otherwise produce off-white text on
bright orange in dark mode.

This file is brittle-by-design: it pins source-text shape rather
than computed style.  A future re-skin that legitimately moves
these classes should update the test in lockstep.
"""

from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_BST_TEMPLATE = _REPO_ROOT / "templates" / "bst_psirt_search.html"


def _read_template() -> str:
    return _BST_TEMPLATE.read_text(encoding="utf-8")


def test_classification_restricted_uses_text_primary_token() -> None:
    """``.classification-restricted`` body text must read from the
    semantic ``--text-primary`` token after Round 24 (was hardcoded
    ``#333`` pre-fix).
    """
    text = _read_template()
    block = _extract_rule_block(text, ".classification-restricted")
    assert "color: var(--text-primary)" in block, (
        ".classification-restricted no longer uses var(--text-primary) "
        "for its color -- did the Round 24 token swap get reverted?  "
        "Block was:\n" + block
    )
    assert "color: #333" not in block, (
        ".classification-restricted re-introduced a hardcoded #333 "
        "color; Round 24 replaced this with var(--text-primary).  "
        "If you genuinely need to pin the badge text, do it in the "
        "[data-bs-theme=...] override block instead."
    )


def test_classification_restricted_has_dark_mode_color_override() -> None:
    """Because the badge background is the non-flipping brand orange
    ``--cisco-warning``, the auto-flipping ``var(--text-primary)``
    needs an explicit dark-mode override that pins the text to a
    dark hex; otherwise dark mode renders off-white text on bright
    orange (poor contrast).  Pin the existence of that override.
    """
    text = _read_template()
    pattern = re.compile(
        r'\[data-bs-theme="dark"\]\s+\.classification-restricted\s*\{[^}]*color\s*:\s*[^;}]+;',
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match is not None, (
        "missing ``[data-bs-theme=\"dark\"] .classification-restricted "
        "{ color: ... }`` override -- Round 24 added this so the badge "
        "stays readable on the non-flipping orange background.  If "
        "you removed it, the contrast regression must be fixed some "
        "other way (e.g. switching the badge background to a "
        "theme-aware token)."
    )


def test_classification_internal_uses_cisco_warning_token() -> None:
    """``.classification-internal`` background was a hardcoded
    ``#ff9800`` (Material Design orange) duplicating the Cisco brand
    orange next door; Round 24 consolidated to ``var(--cisco-warning)``.
    """
    text = _read_template()
    block = _extract_rule_block(text, ".classification-internal")
    assert "background: var(--cisco-warning)" in block, (
        ".classification-internal no longer uses var(--cisco-warning) "
        "for its background -- did the Round 24 consolidation get "
        "reverted?  Block was:\n" + block
    )
    assert "background: #ff9800" not in block, (
        ".classification-internal re-introduced the duplicate Material "
        "Design orange #ff9800; Round 24 consolidated this with the "
        "Cisco brand orange via var(--cisco-warning)."
    )


def test_template_has_no_unpaired_white_card_background() -> None:
    """Audit-pass guard: no rule should declare a literal
    ``background: #fff`` (or ``#ffffff``) directly on a card-like
    surface in this template -- every white card surface should
    route through ``var(--bg-surface, #fff)`` so the fallback only
    applies if the variable cannot be loaded.

    This deliberately ALLOWS ``var(--bg-surface, #fff)`` and
    ``var(--bg-surface-raised, ...)`` patterns; it only rejects a
    naked ``background: #fff`` declaration.
    """
    text = _read_template()
    naked_white_pattern = re.compile(
        r"^\s*background\s*:\s*#fff(?:fff)?\s*;",
        re.MULTILINE | re.IGNORECASE,
    )
    matches = naked_white_pattern.findall(text)
    assert not matches, (
        f"templates/bst_psirt_search.html declares {len(matches)} "
        "naked ``background: #fff;`` rule(s) -- route them through "
        "var(--bg-surface, #fff) so the surface pivots with the "
        "theme.  Found: " + "; ".join(repr(m) for m in matches)
    )


def test_paired_alert_box_overrides_remain_intact() -> None:
    """The Bootstrap-default alert hexes for ``.warning-box`` and
    ``.error-box`` are paired with explicit dark-mode overrides
    (Round 17 / Phase 6.5).  The Round 24 audit pass verified
    these pairings, so this test pins them as a regression guard.
    """
    text = _read_template()
    pairs = (
        (".warning-box", "background: #fff3cd"),
        (".error-box", "background: #f8d7da"),
    )
    for selector, light_decl in pairs:
        light_block = _extract_rule_block(text, selector)
        assert light_decl in light_block, (
            f"expected light-mode {selector} to declare {light_decl!r} "
            f"(Bootstrap alert hex paired with a dark override).  "
            f"Block was:\n{light_block}"
        )
        # Find the next [data-bs-theme="dark"] selector for the
        # same class within a reasonable window.
        dark_pattern = re.compile(
            r'\[data-bs-theme="dark"\]\s+'
            + re.escape(selector)
            + r"\s*\{[^}]*background\s*:[^;}]+;",
            re.DOTALL,
        )
        assert dark_pattern.search(text), (
            f"missing dark-mode override for {selector} -- the "
            f"Bootstrap-default light hex is unpaired.  Round 24 "
            f"audit pass requires every paired alert keeps both "
            f"branches in sync."
        )


def test_round24_marker_comments_present() -> None:
    """Sanity check that the Round 24 marker comments are still in
    the file -- if they all disappeared, somebody bulk-reverted the
    Phase B edits without telling pytest.
    """
    text = _read_template()
    occurrences = text.count("Round 24 / theme-parity")
    assert occurrences >= 3, (
        f"expected at least 3 ``Round 24 / theme-parity`` marker "
        f"comments in templates/bst_psirt_search.html, found "
        f"{occurrences}.  These markers document the Phase B edits "
        f"and act as a tripwire for accidental reverts."
    )


def _extract_rule_block(text: str, selector: str) -> str:
    """Extract the body of a CSS rule whose selector contains *selector*
    exactly (no descendant chain).  Returns the substring between
    the matching ``{`` and ``}``.
    """
    # Match selector at the start of a rule, NOT preceded by ``]``
    # (which would catch ``[data-bs-theme="dark"] .classification-...``).
    # We anchor on a line that starts with optional whitespace + the
    # selector + optional whitespace + ``{``.
    pattern = re.compile(
        r"^[ \t]*"
        + re.escape(selector)
        + r"\s*\{",
        re.MULTILINE,
    )
    match = pattern.search(text)
    assert match, f"could not locate CSS rule for selector {selector!r}"
    start = match.end()
    depth = 1
    i = start
    while i < len(text) and depth > 0:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    raise AssertionError(f"unterminated rule block for {selector!r}")
