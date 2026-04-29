"""Round 44 / Phase 7 regression test.

Pin the two leader-Word per-engagement Subject/Title cells AND the
renewal customer-TAC bullet so they NEVER render leftover Markdown
chrome (``**bold**``, ``__italic__``, ``***``).  Round 42 / Phase 6
wired the ``_strip_markdown_chrome`` helper at the AB / AP / CP body-
bullet sites only; the audited Build-20 leader artifact still rendered
``**Classic Calabrio***delete old report - Calabrio WFO# 00179474`` in
two leader Word table cells (T73R20C2 + T75R36C2) and one renewal
Word paragraph (per-customer TAC bullet) with the asterisks intact.

Round 44 / Phase 7 extends the strip to:

  - ``leader_report_generator.py:5180`` (T73 col 2: per-engagement
    Subject/Title).
  - ``leader_report_generator.py:6102`` (T75 col 1: per-CSSM TAC
    Subject/Title).
  - ``app_simple.py`` renewal customer-TAC bullet ``p.add_run(...)``
    at the ``TAC {case_num}: ...`` site.
"""

from __future__ import annotations

from pathlib import Path

import leader_report_generator as lrg


REPO_ROOT = Path(__file__).resolve().parent.parent
LRG_PATH = REPO_ROOT / "leader_report_generator.py"
APP_SIMPLE_PATH = REPO_ROOT / "app_simple.py"


def test_strip_markdown_chrome_helper_strips_classic_calabrio_artifact() -> None:
    """The exact audited Build-20 artifact MUST round-trip stripped --
    no asterisks survive."""

    raw = "**Classic Calabrio***delete old report - Calabrio WFO# 00179474"
    out = lrg._strip_markdown_chrome(raw)
    assert "*" not in out, f"asterisks survived strip: {out!r}"
    # Subject content must remain readable.
    assert "Classic Calabrio" in out
    assert "delete old report" in out
    assert "Calabrio WFO# 00179474" in out


def test_strip_markdown_chrome_handles_underscore_emphasis() -> None:
    """Markdown ``__italic__`` runs at word boundaries are stripped, but
    legitimate ``snake_case`` identifiers survive (single underscore
    embedded inside an identifier is not Markdown emphasis)."""

    assert lrg._strip_markdown_chrome("__italic__ word") == "italic word"
    # Single underscore inside identifier preserved (Round 42 contract).
    assert (
        lrg._strip_markdown_chrome("snake_case identifier")
        == "snake_case identifier"
    )


def test_leader_subject_t73_uses_strip_helper() -> None:
    """The leader per-engagement Subject/Title cell at line ~5180
    (T73R*C2) MUST route through ``_strip_markdown_chrome``."""

    src = LRG_PATH.read_text(encoding="utf-8")
    # Anchor on the new line shape introduced by Round 44 / Phase 7.
    assert (
        "row_cells[2].text = _strip_markdown_chrome(subject) or 'N/A'"
        in src
    ), (
        "Round 44 / Phase 7: leader per-engagement Subject/Title cell "
        "(row_cells[2]) must route through _strip_markdown_chrome."
    )


def test_leader_subject_t75_uses_strip_helper() -> None:
    """The leader per-CSSM TAC Subject/Title cell at line ~6102
    (T75R*C1) MUST route through ``_strip_markdown_chrome``."""

    src = LRG_PATH.read_text(encoding="utf-8")
    assert (
        "row_cells[1].text = _strip_markdown_chrome(subject) or 'N/A'"
        in src
    ), (
        "Round 44 / Phase 7: leader per-CSSM TAC Subject/Title cell "
        "(row_cells[1]) must route through _strip_markdown_chrome."
    )


def test_renewal_customer_tac_bullet_uses_strip_helper() -> None:
    """The renewal customer-TAC bullet in ``app_simple.py`` MUST route
    the title through the same Round 42 / Phase 6 helper (re-imported
    as ``_r44_strip_markdown_chrome``)."""

    src = APP_SIMPLE_PATH.read_text(encoding="utf-8")
    assert (
        "from leader_report_generator import _strip_markdown_chrome "
        "as _r44_strip_markdown_chrome" in src
    ), (
        "Round 44 / Phase 7: app_simple.py must import _strip_markdown_"
        "chrome from leader_report_generator under the local alias "
        "_r44_strip_markdown_chrome."
    )
    assert (
        "_r44_strip_markdown_chrome(title_text)" in src
    ), (
        "Round 44 / Phase 7: renewal customer-TAC bullet must call "
        "_r44_strip_markdown_chrome(title_text) before rendering the "
        "TAC subject."
    )
