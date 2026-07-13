"""Round 24 -- Admin dashboard light-mode theme parity with main app.

Pins that ``enhanced_admin_dashboard_v2.py``'s inline ``:root`` block
resolves to the same hex / rgba strings as ``templates/base.html``'s
``:root`` block for the 9 semantic theme tokens that drive light-mode
chrome.  The two files used to drift (admin had ``#0076CE`` page bg +
``#3498db`` accent; main app had ``#f8f9fa`` page bg + ``#00bceb``
accent), which made toggling between ports 5151 and 5152 in light mode
reveal two different palettes.  A failure here means a future change
re-introduced the drift.

The test resolves ``var(--cisco-...)`` indirections in ``base.html``
to brand hexes before comparing, so the assertion is on *effective*
colour values rather than literal source strings (the admin file uses
hex literals directly, the main app uses the indirection layer).

Dark-mode tokens in both files were already aligned in Round 17.4
Phase 6.6 and are NOT re-checked here -- that pre-existing alignment
is verified by ``tests/test_round17_4_theme_toggle.py`` and the
manual smoke step in the Round 24 plan.

Round 13 invariant: ``--risk-*`` tokens (chart / Excel band fills)
are NOT included in this parity check -- they must stay byte-identical
across themes per ``canonical_metrics.RISK_BAND_COLORS`` and are
intentionally absent from the admin :root block.
"""

from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_BASE_HTML = _REPO_ROOT / "templates" / "base.html"
_ADMIN_PY = _REPO_ROOT / "enhanced_admin_dashboard_v2.py"


# Round 24 / theme-parity: the 9 semantic tokens that must agree
# between the admin dashboard's inline :root and base.html's :root in
# light mode.  Brand tokens (--cisco-*, --adoptiq-orange*) and
# main-app-only tokens (--accent-secondary, --navbar-bg, --hero-bg,
# --footer-bg, --card-header-bg) are NOT cross-checked because the
# admin file does not declare them.
_PARITY_TOKENS: tuple[str, ...] = (
    "--bg-base",
    "--bg-surface",
    "--bg-surface-raised",
    "--border-subtle",
    "--text-primary",
    "--text-secondary",
    "--text-muted",
    "--accent-primary",
    "--accent-primary-hover",
)

# Round 24: optional bonus tokens both files declare in light mode.
# These are checked separately so a future divergence (e.g. one file
# tweaks the glow alpha) is flagged with a clear assertion message
# rather than buried in the main parity test.
_BONUS_PARITY_TOKENS: tuple[str, ...] = (
    "--accent-glow",
    "--accent-glow-soft",
)


_TOKEN_RE = re.compile(r"--[a-z0-9-]+", re.IGNORECASE)
_DECL_RE = re.compile(
    r"(?P<name>--[a-z0-9-]+)\s*:\s*(?P<value>[^;]+);",
    re.IGNORECASE,
)


def _extract_root_block(text: str) -> str:
    """Return the contents of the FIRST ``:root { ... }`` block in *text*.

    Both files declare light-mode tokens in their *first* ``:root``
    block; the dark-mode override lives in
    ``[data-bs-theme="dark"] { ... }`` which we ignore here on
    purpose (Round 17.4 Phase 6.6 already aligned dark mode).
    """
    match = re.search(r":root\s*\{", text)
    assert match, "could not locate :root selector"
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
    raise AssertionError("unterminated :root block")


def _parse_declarations(block: str) -> dict[str, str]:
    """Parse ``--name: value;`` pairs from a CSS block, stripping
    comments.  Returns the LAST declaration wins (matches CSS
    cascade semantics within a single rule).
    """
    # Strip ``/* ... */`` comments before parsing so commented-out
    # declarations aren't picked up.
    no_comments = re.sub(r"/\*.*?\*/", "", block, flags=re.DOTALL)
    decls: dict[str, str] = {}
    for match in _DECL_RE.finditer(no_comments):
        name = match.group("name").strip()
        value = match.group("value").strip()
        decls[name] = value
    return decls


def _resolve_var_references(
    decls: dict[str, str], max_passes: int = 4
) -> dict[str, str]:
    """Iteratively resolve ``var(--x)`` references inside *decls*.

    The base.html ``:root`` defines tokens like
    ``--bg-base: var(--cisco-gray-100)`` where ``--cisco-gray-100``
    is *also* declared in the same block as ``#f8f9fa``.  This walks
    the table a few times until no further substitutions happen so
    the parity check compares effective colour values.
    """
    resolved = dict(decls)
    for _ in range(max_passes):
        changed = False
        for name, value in list(resolved.items()):
            new_value = _substitute_vars(value, resolved)
            if new_value != value:
                resolved[name] = new_value
                changed = True
        if not changed:
            break
    return resolved


def _substitute_vars(value: str, table: dict[str, str]) -> str:
    """Replace every ``var(--X)`` and ``var(--X, fallback)`` in *value*
    with the looked-up hex from *table*; leave unresolved references
    alone (they'll surface as a parity-test failure with a clear
    diff).
    """
    pattern = re.compile(r"var\(\s*(--[a-z0-9-]+)(?:\s*,\s*[^)]+)?\s*\)", re.IGNORECASE)

    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        return table.get(token, match.group(0))

    return pattern.sub(_replace, value)


def _read_light_mode_tokens(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    block = _extract_root_block(text)
    decls = _parse_declarations(block)
    return _resolve_var_references(decls)


def test_admin_light_mode_tokens_match_base_html() -> None:
    """Each of the 9 semantic light-mode tokens declared in the admin
    dashboard's inline ``:root`` resolves to the same value as the
    matching token in ``templates/base.html``'s ``:root``.

    Failure modes this catches:
    1. Someone re-edits ``enhanced_admin_dashboard_v2.py`` and
       reverts a token to a Tableau-blue hex.
    2. Someone tweaks ``base.html``'s default and forgets to update
       the admin counterpart.
    """
    admin = _read_light_mode_tokens(_ADMIN_PY)
    base = _read_light_mode_tokens(_BASE_HTML)

    for token in _PARITY_TOKENS:
        assert token in admin, (
            f"admin :root is missing required token {token!r} -- did "
            "Round 24 token alignment get reverted?"
        )
        assert token in base, (
            f"base.html :root is missing required token {token!r} -- "
            "this would mean the indirection layer was removed, "
            "audit needed"
        )
        admin_value = admin[token].lower()
        base_value = base[token].lower()
        assert admin_value == base_value, (
            f"token {token!r} drifted: admin={admin_value!r} but "
            f"base.html={base_value!r}.  Update "
            f"enhanced_admin_dashboard_v2.py :root to match "
            f"templates/base.html :root, or update both together if "
            f"the brand palette is changing."
        )


def test_admin_light_mode_bonus_tokens_match_base_html() -> None:
    """``--accent-glow`` / ``--accent-glow-soft`` are also declared
    in both files and should agree.  Pinned separately so a glow
    drift surfaces with a targeted message.
    """
    admin = _read_light_mode_tokens(_ADMIN_PY)
    base = _read_light_mode_tokens(_BASE_HTML)

    for token in _BONUS_PARITY_TOKENS:
        if token not in admin or token not in base:
            # Tolerated: not strictly required for parity, but if
            # both sides declare it they must agree.
            continue
        admin_value = admin[token].lower().replace(" ", "")
        base_value = base[token].lower().replace(" ", "")
        assert admin_value == base_value, (
            f"glow token {token!r} drifted: admin={admin_value!r} "
            f"but base.html={base_value!r}"
        )


def test_admin_root_does_not_redeclare_bg_page() -> None:
    """``--bg-page`` was declared in the admin :root but never
    referenced anywhere in the inline stylesheet (Round 24 cleanup
    consolidated it with ``--bg-base``).  Re-introducing it would
    suggest the consolidation was reverted; flag it so reviewers
    decide intentionally.
    """
    text = _ADMIN_PY.read_text(encoding="utf-8")
    # Allow the explanatory comment that documents the removal, but
    # not a live declaration.  We look for a ``--bg-page:`` declaration
    # specifically (CSS property) rather than the bare token name.
    declarations = re.findall(r"^\s*--bg-page\s*:", text, flags=re.MULTILINE)
    assert not declarations, (
        "enhanced_admin_dashboard_v2.py re-introduced a --bg-page "
        "declaration; Round 24 consolidated this with --bg-base.  "
        "If you genuinely need a separate page-vs-base token, update "
        "templates/base.html to declare it too and re-add it to "
        "tests/test_round24_admin_theme_parity.py::_PARITY_TOKENS."
    )


def test_admin_body_background_fallback_matches_main_app() -> None:
    """``body { background: var(--bg-base, #f8f9fa); }`` -- the
    fallback hex must match the resolved value of ``--bg-base`` in
    light mode so a CSS-variable-load failure (extremely rare, but
    possible on very old browsers) doesn't render the historic
    saturated-blue ``#0076CE`` page background.
    """
    text = _ADMIN_PY.read_text(encoding="utf-8")
    match = re.search(
        r"body\s*\{[^}]*background:\s*var\(--bg-base,\s*(#[0-9a-fA-F]{3,6})\)",
        text,
        flags=re.DOTALL,
    )
    assert match, (
        "could not find ``body { background: var(--bg-base, #...) }`` "
        "in enhanced_admin_dashboard_v2.py -- has the body rule been "
        "rewritten?  If yes, update this test to match the new shape."
    )
    fallback = match.group(1).lower()
    base = _read_light_mode_tokens(_BASE_HTML)
    expected = base["--bg-base"].lower()
    assert fallback == expected, (
        f"body background fallback {fallback!r} does not match "
        f"base.html --bg-base {expected!r} -- update the inline "
        f"``var(--bg-base, ...)`` fallback hex"
    )
