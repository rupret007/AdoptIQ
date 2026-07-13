"""Round 18 / R18-001 regression test: CLAUDE.md must not drift from
the actual main-app bind default.

Why this test exists
--------------------
CLAUDE.md is the LLM-bible: every Cursor + Claude Code session reads
it as ground truth before touching this repo.  Round 14 / R14-007
changed the main app bind default from ``0.0.0.0`` to ``127.0.0.1``
(opt-in public bind via ``ADOPTIQ_BIND_PUBLIC=1``), but the CLAUDE.md
"Two Flask Apps" table at line 48 was left saying the default was
``0.0.0.0``.  Every future LLM session that reads the doc would have
been silently misled into believing the app was open to the LAN by
default, which is the opposite of the security posture R14-007 shipped.

This test pins the doc + code in lockstep so the next time anyone
edits either side without updating the other, ``make verify`` fails
locally before the regression can land.

What this test pins
-------------------
1. The actual bind default in ``app_simple.py`` is loopback
   (``127.0.0.1``) and the public-bind escape hatch is
   ``ADOPTIQ_BIND_PUBLIC=1``.
2. CLAUDE.md does NOT claim the main app default is ``0.0.0.0``.
3. CLAUDE.md mentions both the loopback default and the
   ``ADOPTIQ_BIND_PUBLIC`` escape hatch so future LLM readers cannot
   misunderstand the security posture.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE = REPO_ROOT / "app_simple.py"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


def test_app_simple_bind_default_is_loopback() -> None:
    """app_simple.py must default to loopback unless ADOPTIQ_BIND_PUBLIC=1."""
    src = APP_SIMPLE.read_text(encoding="utf-8")
    # The exact ternary that picks the bind host.  Both halves are pinned:
    # the loopback default AND the public-opt-in env var name.
    assert "'0.0.0.0' if _bind_public else '127.0.0.1'" in src, (
        "Round 14 R14-007 contract: app_simple.py must default to "
        "127.0.0.1 unless ADOPTIQ_BIND_PUBLIC is truthy.  If you "
        "intentionally changed this, update CLAUDE.md and "
        "QUALITY_AUDIT.md in the same change."
    )
    assert "ADOPTIQ_BIND_PUBLIC" in src, (
        "Round 14 R14-007 contract: ADOPTIQ_BIND_PUBLIC is the "
        "documented opt-in env var for exposing the main app."
    )


def test_claude_md_does_not_claim_public_bind_default() -> None:
    """CLAUDE.md must not say the main app default is 0.0.0.0."""
    doc = CLAUDE_MD.read_text(encoding="utf-8")
    # The historical drift: the "Two Flask Apps" table used to say
    # "default 0.0.0.0" for the main app row.  That's wrong post-R14-007.
    forbidden_phrase = "default `0.0.0.0`"
    main_ui_row_start = doc.find("| Main UI | 5151 |")
    assert main_ui_row_start != -1, (
        "CLAUDE.md must keep the Two Flask Apps table row for the "
        "main UI on port 5151 -- this row is the LLM-readable "
        "summary of the bind posture."
    )
    main_ui_row_end = doc.find("\n", main_ui_row_start)
    main_ui_row = doc[main_ui_row_start:main_ui_row_end]
    assert forbidden_phrase not in main_ui_row, (
        "Round 18 R18-001 contract: CLAUDE.md main-app row must not "
        "claim the default bind is 0.0.0.0 -- Round 14 R14-007 changed "
        "the default to 127.0.0.1.  Misleading the LLM-bible silently "
        "regresses the documented security posture."
    )


def test_claude_md_documents_loopback_default_and_escape_hatch() -> None:
    """CLAUDE.md must mention both the loopback default and the
    ADOPTIQ_BIND_PUBLIC escape hatch on the main-app row, so future
    LLM sessions cannot mistake the security posture."""
    doc = CLAUDE_MD.read_text(encoding="utf-8")
    main_ui_row_start = doc.find("| Main UI | 5151 |")
    main_ui_row_end = doc.find("\n", main_ui_row_start)
    main_ui_row = doc[main_ui_row_start:main_ui_row_end]
    assert "127.0.0.1" in main_ui_row, (
        "Round 18 R18-001 contract: CLAUDE.md main-app row must "
        "explicitly say 127.0.0.1 is the default."
    )
    assert "ADOPTIQ_BIND_PUBLIC" in main_ui_row, (
        "Round 18 R18-001 contract: CLAUDE.md main-app row must name "
        "the ADOPTIQ_BIND_PUBLIC opt-in env var so the LLM-bible "
        "matches the documented escape hatch in app_simple.py."
    )
