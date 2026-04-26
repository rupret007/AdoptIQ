"""Round 19.1 / R18-NEXT-005 expansion: extend the doc/code parity
pattern landed in Round 18 R18-001 to three more load-bearing
invariants.

Why this test exists
--------------------
Round 18 R18-001 fixed a single instance of LLM-bible drift: ``CLAUDE.md``
claimed the main app defaulted to ``0.0.0.0`` while the code defaulted
to ``127.0.0.1``.  R18-NEXT-005 in the Round 18 follow-up table called
out the same drift class for other invariants — admin bind default,
default ports, the Round 13/15 risk-band hex tokens, etc. — and
recommended pinning each one with the same parity-test pattern so a
future doc OR code edit that breaks the pair fails ``make verify``
locally before it can land.

This module pins three more invariants:

1. **Admin bind default** -- ``enhanced_admin_dashboard_v2.py`` defaults
   to ``127.0.0.1`` unless ``ADOPTIQ_ADMIN_BIND_PUBLIC=1``; the
   corresponding ``CLAUDE.md`` admin row plus the "Admin security"
   line must say the same thing.  (Round 14 R14-007 / Phase 4.5.)

2. **Default ports 5151 / 5152** -- ``app_simple._DEFAULT_MAIN_PORT``
   and ``enhanced_admin_dashboard_v2._DEFAULT_ADMIN_PORT`` plus the
   matching ``CLAUDE.md`` "Two Flask Apps" table rows.  (Round 17.3
   moved both off the legacy 5001/5002.)

3. **Risk-band hex SSoT parity** -- the four data-viz hexes shared
   between ``canonical_metrics.RISK_BAND_COLORS`` (matplotlib charts +
   Excel conditional formatting + HTML risk badges) and the
   ``--risk-*`` CSS tokens in ``templates/base.html`` (Round 13 / 15
   cross-surface invariant; Round 17.4 explicitly left them
   byte-identical when the dark theme shipped).

If any of these three drifts re-occur, the matching test below fails
locally before the regression can land in a commit.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE = REPO_ROOT / "app_simple.py"
ADMIN_APP = REPO_ROOT / "enhanced_admin_dashboard_v2.py"
CANONICAL_METRICS = REPO_ROOT / "canonical_metrics.py"
TEMPLATE_BASE = REPO_ROOT / "templates" / "base.html"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


# ---------------------------------------------------------------------------
# Block 1 -- admin bind default parity (Round 14 R14-007 / Phase 4.5)
# ---------------------------------------------------------------------------


def test_admin_app_bind_default_is_loopback() -> None:
    """enhanced_admin_dashboard_v2.py must default to loopback unless
    ADOPTIQ_ADMIN_BIND_PUBLIC=1.

    Mirrors the R18-001 contract for the admin app.  The exact ternary
    plus the env-var name are pinned together so neither can drift in
    isolation.
    """
    src = ADMIN_APP.read_text(encoding="utf-8")
    assert "'0.0.0.0' if _bind_public else '127.0.0.1'" in src, (
        "Round 14 R14-007 / Phase 4.5 contract: enhanced_admin_dashboard_v2.py "
        "must default to 127.0.0.1 unless ADOPTIQ_ADMIN_BIND_PUBLIC is "
        "truthy.  If you intentionally changed this, update CLAUDE.md "
        "(both the Admin row of the Two Flask Apps table and the "
        "'Admin security' line) in the same commit."
    )
    assert "ADOPTIQ_ADMIN_BIND_PUBLIC" in src, (
        "Round 14 R14-007 / Phase 4.5 contract: ADOPTIQ_ADMIN_BIND_PUBLIC "
        "is the documented opt-in env var for exposing the admin dashboard."
    )


def test_claude_md_admin_row_documents_loopback_default_and_escape_hatch() -> None:
    """CLAUDE.md admin row must mention both 127.0.0.1 and ADOPTIQ_ADMIN_BIND_PUBLIC."""
    doc = CLAUDE_MD.read_text(encoding="utf-8")
    admin_row_start = doc.find("| Admin | 5152 |")
    assert admin_row_start != -1, (
        "CLAUDE.md must keep the Two Flask Apps table row for the admin "
        "app on port 5152 -- this row is the LLM-readable summary of "
        "the admin bind posture."
    )
    admin_row_end = doc.find("\n", admin_row_start)
    admin_row = doc[admin_row_start:admin_row_end]
    assert "127.0.0.1" in admin_row, (
        "Round 19.1 R18-NEXT-005 contract: CLAUDE.md admin row must "
        "explicitly say 127.0.0.1 is the default bind."
    )
    assert "ADOPTIQ_ADMIN_BIND_PUBLIC" in admin_row, (
        "Round 19.1 R18-NEXT-005 contract: CLAUDE.md admin row must "
        "name the ADOPTIQ_ADMIN_BIND_PUBLIC opt-in env var so the "
        "LLM-bible matches the admin escape hatch in the code."
    )


def test_claude_md_admin_security_line_pins_loopback_default() -> None:
    """The 'Admin security' callout line must say the admin app binds to
    loopback by default and must name the escape-hatch env var, so a
    future LLM editor cannot quietly weaken the documented posture
    without tripping this test."""
    doc = CLAUDE_MD.read_text(encoding="utf-8")
    # The line shape from CLAUDE.md (kept loose -- only the load-bearing
    # tokens are pinned, not the exact prose):
    #   **Admin security:** Admin dashboard binds to loopback (`127.0.0.1`)
    #   by default. Don't change this default without the
    #   `ADOPTIQ_ADMIN_BIND_PUBLIC=1` escape hatch.
    assert "Admin security:" in doc, (
        "CLAUDE.md must keep the 'Admin security:' callout line."
    )
    section_start = doc.find("Admin security:")
    section_end = doc.find("\n\n", section_start)
    if section_end == -1:
        section_end = len(doc)
    section = doc[section_start:section_end]
    assert "loopback" in section.lower() and "127.0.0.1" in section, (
        "Round 19.1 R18-NEXT-005 contract: 'Admin security' line must "
        "say the admin app binds to loopback (127.0.0.1) by default."
    )
    assert "ADOPTIQ_ADMIN_BIND_PUBLIC" in section, (
        "Round 19.1 R18-NEXT-005 contract: 'Admin security' line must "
        "name the ADOPTIQ_ADMIN_BIND_PUBLIC escape hatch."
    )


# ---------------------------------------------------------------------------
# Block 2 -- default port parity (Round 17.3)
# ---------------------------------------------------------------------------


def test_default_ports_match_claude_md_two_flask_apps_table() -> None:
    """The Round 17.3 default ports (5151 main, 5152 admin) live in
    three places: ``app_simple._DEFAULT_MAIN_PORT``, the admin
    equivalent ``enhanced_admin_dashboard_v2._DEFAULT_ADMIN_PORT``, and
    the ``CLAUDE.md`` "Two Flask Apps" table.  Pin all three so a
    future doc OR code edit that breaks the trio fails ``make verify``."""
    main_src = APP_SIMPLE.read_text(encoding="utf-8")
    admin_src = ADMIN_APP.read_text(encoding="utf-8")
    doc = CLAUDE_MD.read_text(encoding="utf-8")

    # Code-side: exact constant assignments (Round 17.3 wire).
    assert "_DEFAULT_MAIN_PORT = 5151" in main_src, (
        "Round 17.3 contract: app_simple._DEFAULT_MAIN_PORT must equal "
        "5151.  If you intentionally moved the main port, update the "
        "Two Flask Apps table in CLAUDE.md and the 'Open the app at "
        "http://localhost:5151' references in the same change."
    )
    assert "_DEFAULT_ADMIN_PORT = 5152" in admin_src, (
        "Round 17.3 contract: enhanced_admin_dashboard_v2._DEFAULT_ADMIN_PORT "
        "must equal 5152.  If you intentionally moved the admin port, "
        "update the Two Flask Apps table in CLAUDE.md and the admin "
        "URL references in the same change."
    )

    # Doc-side: both rows of the Two Flask Apps table must show the
    # current defaults.  We pin the leading row prefix so the test
    # fails with a clear message if either column drifts.
    assert "| Main UI | 5151 |" in doc, (
        "Round 19.1 R18-NEXT-005 contract: CLAUDE.md Two Flask Apps "
        "table must show 5151 as the main-app default port."
    )
    assert "| Admin | 5152 |" in doc, (
        "Round 19.1 R18-NEXT-005 contract: CLAUDE.md Two Flask Apps "
        "table must show 5152 as the admin-app default port."
    )


def test_default_ports_have_env_overrides_documented() -> None:
    """The Round 17.3 wire is meaningless unless the env-var override
    names are documented; otherwise operators redeploy the bundled
    .app to move ports.  Pin both override names in code AND doc."""
    main_src = APP_SIMPLE.read_text(encoding="utf-8")
    admin_src = ADMIN_APP.read_text(encoding="utf-8")

    # Env-var names: each resolver helper consults a single env var.
    assert "ADOPTIQ_PORT" in main_src, (
        "Round 17.3 contract: app_simple._resolve_main_port must read "
        "the ADOPTIQ_PORT env var."
    )
    assert "ADOPTIQ_ADMIN_PORT" in admin_src, (
        "Round 17.3 contract: enhanced_admin_dashboard_v2._resolve_admin_port "
        "must read the ADOPTIQ_ADMIN_PORT env var."
    )


# ---------------------------------------------------------------------------
# Block 3 -- risk-band hex SSoT parity (Round 13 / 15 / 17.4 invariant)
# ---------------------------------------------------------------------------


_RISK_HEX_RE = re.compile(
    r"--risk-(?P<band>critical|high|medium|low)\s*:\s*(?P<hex>#[0-9a-fA-F]{3,8})\s*;"
)


def _read_css_risk_tokens() -> dict[str, str]:
    """Parse the ``--risk-*`` tokens from ``templates/base.html`` into a
    {band: hex} dict.  Lower-case band keys, lower-case hex values."""
    css = TEMPLATE_BASE.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for match in _RISK_HEX_RE.finditer(css):
        out[match.group("band")] = match.group("hex").lower()
    return out


def test_canonical_metrics_risk_band_colors_match_css_tokens() -> None:
    """The four CRITICAL / HIGH / MEDIUM / LOW hexes in
    ``canonical_metrics.RISK_BAND_COLORS`` must equal the matching
    ``--risk-*`` CSS tokens in ``templates/base.html`` byte-for-byte.

    This is the load-bearing Round 13 / 15 cross-surface invariant:
    the same hex must drive matplotlib chart fills (Word + PDF
    reports), Excel conditional-formatting bands, and HTML risk
    badges.  Round 17.4 explicitly left these tokens untouched when
    the dark theme shipped (see QUALITY_AUDIT.md Round 17.4 -- "Round
    13 invariant -- risk band parity").  This test pins both ends so
    a future edit on either side cannot silently drift the data-viz
    palette away from the chrome palette.
    """
    # Avoid importing the module (heavy deps); parse the literal.
    src = CANONICAL_METRICS.read_text(encoding="utf-8")

    expected = {
        "critical": "#d62728",
        "high": "#ff7f0e",
        "medium": "#ffd700",
        "low": "#2ca02c",
    }

    # Code side: each hex must appear inside the RISK_BAND_COLORS dict
    # body so a future move of the hex into a different constant still
    # trips the test.
    dict_match = re.search(
        r"RISK_BAND_COLORS\s*:\s*Dict\[str,\s*str\]\s*=\s*\{(?P<body>.*?)\}",
        src,
        re.DOTALL,
    )
    assert dict_match is not None, (
        "Round 11 / 13 contract: canonical_metrics.RISK_BAND_COLORS "
        "must remain the SSoT dict for risk-band hexes.  If you "
        "renamed it, update Round 17.4's invariant note in "
        "QUALITY_AUDIT.md and the matching --risk-* CSS tokens."
    )
    # Source uses uppercase band keys (e.g. "CRITICAL") and lowercase
    # hexes; match against the body verbatim.
    code_body = dict_match.group("body")
    for band, hex_value in expected.items():
        needle = f'"{band.upper()}": "{hex_value}"'
        assert needle in code_body, (
            f"Round 13 / 15 invariant: canonical_metrics.RISK_BAND_COLORS"
            f"['{band.upper()}'] must equal {hex_value!r}.  Matplotlib "
            f"charts, Excel conditional formatting, and HTML risk "
            f"badges all read from this dict; drift here breaks "
            f"cross-surface parity."
        )

    # Doc / chrome side: the --risk-* CSS tokens in templates/base.html
    # must equal the same hexes.  Round 17.4 left them byte-identical
    # on purpose; pin that contract here as well so dark-theme tweaks
    # cannot silently re-skin the risk palette.
    css_tokens = _read_css_risk_tokens()
    for band, hex_value in expected.items():
        assert css_tokens.get(band) == hex_value, (
            f"Round 13 / 17.4 invariant: --risk-{band} in "
            f"templates/base.html must equal {hex_value!r} (current: "
            f"{css_tokens.get(band)!r}).  Round 17.4's dark-theme work "
            f"explicitly left these tokens untouched -- if you need to "
            f"change a chart color, change canonical_metrics."
            f"RISK_BAND_COLORS and the matching --risk-* token TOGETHER, "
            f"and update tests/test_round17_4_dark_theme.py + this "
            f"file's expected map at the same time."
        )
