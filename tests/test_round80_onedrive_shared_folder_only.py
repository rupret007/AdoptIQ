"""Round 80 / Build 56: OneDrive corpus folder discovery is now
narrowed to the SharePoint shared-folder shortcut leaf only.

Pre-R80 ``config._csone_onedrive_candidates`` returned four paths
under ``OneDrive-Cisco/AI Projects/AdoptIQ_CSOne_Reports`` -- a tree
only the corpus owner can sync.  Brian Frazier's bug A: as a non-
owner he had clicked "Add shortcut to OneDrive" against Jeffrey's
SharePoint share, materialising the folder as
``OneDrive-Cisco/Jeffrey Story (jestory) - AdoptIQ_CSOne_Reports``,
but AdoptIQ never looked there.  Round 80 drops the four legacy
paths and probes ONLY the shared-folder leaf so every install
(owner included, since the owner now dogfoods the same shortcut
workflow) hits the same path.

Round 83 / Build 59 update: the candidate list is now FOUR entries
again -- but the new entries are the ``AI Projects/AdoptIQ_CSOne_Reports``
**owner-style** path tier 3-4 priorities, NOT the pre-R80 legacy
sweep.  R80's narrowing intent is preserved: tiers 1-2 (the shared-
folder leaf) are first-existing-wins for the 99% non-owner case,
tiers 3-4 are a defense-in-depth fallback ONLY for the corpus
owner's machine where the OneDrive desktop client routes their
shortcut back to the personal ``AI Projects`` tree (the canonical
leaf doesn't materialise on the owner's box).  Tier ordering and
narrowing intent are pinned by tests in
``test_round83_candidate_list_owner_fallback.py``.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# Round 80


def test_candidates_contain_only_shared_folder_leaf():
    """The candidate list MUST contain the R80 shared-folder leaf
    as tiers 1-2 (modern macOS + pre-Big-Sur), preserving R80's
    narrowing intent for the 99% non-owner case.

    Round 83 / Build 59 update: tiers 3-4 are now the R83 owner-style
    fallback (``AI Projects/AdoptIQ_CSOne_Reports``) so the corpus
    owner's machine resolves automatically without a manual env-var
    workaround.  Tiers 1-2 still terminate in the canonical R80
    shared-folder leaf -- so a typical user install (Brian Frazier
    style) still hits the leaf on the first probe.

    The pre-R80 ``Documents/AI Projects`` nesting and the original
    four-paths-without-shortcut shape MUST NOT appear anywhere.
    """
    config = importlib.import_module("config")
    candidates = config._csone_onedrive_candidates()
    # Round 83: list is four entries (R80 tiers 1-2 + R83 tiers 3-4).
    assert len(candidates) == 4, (
        f"Round 80 + 83: expected exactly 4 candidates "
        f"(R80 modern + R80 pre-Big-Sur + R83 owner-style modern + "
        f"R83 owner-style pre-Big-Sur), got {len(candidates)}: {candidates}"
    )
    # Tiers 1-2: R80 shared-folder leaf.
    for c in candidates[:2]:
        # No "AI Projects" segment in the R80 tier (that's the R83
        # tier).  Pin the source-shape so a future regression cannot
        # collapse R83 into R80 silently.
        assert "AI Projects" not in c, (
            f"Round 80: candidate {c!r} (R80 tier 1-2) must NOT contain "
            f"the 'AI Projects/' segment; it should terminate in the "
            f"shared-folder shortcut leaf."
        )
        # No "Documents/AI Projects" pre-R80 nesting anywhere.
        assert "Documents/AI Projects" not in c
        # R80 tiers 1-2 must end in the canonical shared-folder leaf.
        assert c.endswith("Jeffrey Story (jestory) - AdoptIQ_CSOne_Reports"), (
            f"Round 80: tier 1-2 candidate {c!r} does not terminate in the "
            f"shared-folder shortcut leaf."
        )
    # Tiers 3-4: R83 owner-style fallback. MUST end in the
    # ``AI Projects/AdoptIQ_CSOne_Reports`` leaf so the corpus
    # owner's machine resolves via this path.
    for c in candidates[2:]:
        # Pre-R80 ``Documents/`` nesting must STILL not appear.
        assert "Documents/AI Projects" not in c, (
            f"Round 83: candidate {c!r} (R83 tier 3-4) must NOT contain "
            f"the pre-R80 'Documents/AI Projects/' nesting."
        )
        assert c.endswith("AI Projects/AdoptIQ_CSOne_Reports"), (
            f"Round 83: tier 3-4 candidate {c!r} does not terminate in the "
            f"owner-style 'AI Projects/AdoptIQ_CSOne_Reports' leaf."
        )


def test_modern_macos_cloud_storage_candidate_present():
    """The modern macOS layout (Big Sur+) is
    ``~/Library/CloudStorage/OneDrive-Cisco/<leaf>``; that variant
    MUST be the FIRST candidate so a typical user install hits it
    on the first probe."""
    config = importlib.import_module("config")
    candidates = config._csone_onedrive_candidates()
    home = Path.home()
    expected = str(
        home
        / "Library"
        / "CloudStorage"
        / "OneDrive-Cisco"
        / "Jeffrey Story (jestory) - AdoptIQ_CSOne_Reports"
    )
    assert candidates[0] == expected, (
        f"Round 80: first candidate must be the modern macOS "
        f"CloudStorage layout. Got {candidates[0]!r}, expected "
        f"{expected!r}."
    )


def test_pre_big_sur_candidate_present():
    """Pre-Big-Sur macOS / Windows layout uses
    ``~/OneDrive - Cisco/<leaf>`` (the symlinked legacy mount).
    Round 80 keeps this fallback so installs that haven't migrated
    to ``CloudStorage`` still resolve."""
    config = importlib.import_module("config")
    candidates = config._csone_onedrive_candidates()
    home = Path.home()
    expected = str(
        home / "OneDrive - Cisco" / "Jeffrey Story (jestory) - AdoptIQ_CSOne_Reports"
    )
    assert expected in candidates, (
        f"Round 80: pre-Big-Sur fallback {expected!r} not in "
        f"candidate list: {candidates}"
    )


def test_env_override_still_wins(monkeypatch, tmp_path):
    """``CSONE_ONEDRIVE_FOLDER`` env override MUST still take
    precedence over the auto-discovery candidates -- power users
    who want to point AdoptIQ at a non-canonical local path still
    can.  Pre-R80 the resolver checked the env first and we
    preserve that contract."""
    # Make a synthetic local folder that "exists" so the resolver
    # doesn't fall through.
    target = tmp_path / "SharedReports"
    target.mkdir()
    monkeypatch.setenv("CSONE_ONEDRIVE_FOLDER", str(target))
    # Re-import so the env override is picked up.
    import importlib as _im
    _im.invalidate_caches()
    import config as _cfg
    _im.reload(_cfg)
    try:
        resolved = _cfg._resolve_csone_onedrive_folder()
        assert resolved == str(target), (
            f"Round 80: env override CSONE_ONEDRIVE_FOLDER must win. "
            f"Expected {target!s}, got {resolved!r}"
        )
    finally:
        # Restore the module to its default state for downstream
        # tests in the same session.
        monkeypatch.delenv("CSONE_ONEDRIVE_FOLDER", raising=False)
        _im.reload(_cfg)


def test_panel_messaging_uses_add_shortcut_to_onedrive():
    """The corpus panel JS messages MUST instruct users to
    "Add shortcut to OneDrive" rather than the misleading
    "sync AI Projects/AdoptIQ_CSOne_Reports" wording -- that path
    only the corpus owner can sync."""
    js_path = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "intel_status.js"
    )
    js_src = js_path.read_text(encoding="utf-8")
    # The actionable phrase MUST appear in the rendered panel
    # detail messages.
    assert "Add shortcut to OneDrive" in js_src, (
        "Round 80: intel_status.js panel detail messaging must carry "
        "the 'Add shortcut to OneDrive' instruction so non-owner users "
        "know how to materialise the shared folder on their Mac."
    )
    # The misleading owner-only path string MUST NOT appear in
    # user-facing panel detail copy.  Comments are allowed (and we
    # keep historical context in the code), so we scope the check
    # to the visible message bodies.  The simplest pin: the literal
    # substring 'sync \u201CAI Projects/AdoptIQ_CSOne_Reports\u201D'
    # (which is the exact pre-R80 user-visible phrase) MUST be gone.
    assert (
        'sync \u201CAI Projects/AdoptIQ_CSOne_Reports\u201D' not in js_src
    ), (
        "Round 80: intel_status.js still carries the misleading "
        "'sync AI Projects/AdoptIQ_CSOne_Reports' user-facing phrase."
    )


def test_owner_constants_match_canonical_leaf():
    """Owner-identity constants MUST resolve to the canonical
    shared-folder leaf the OneDrive desktop client materialises
    when a user clicks "Add shortcut to OneDrive" against
    Jeffrey's SharePoint share.  If owner identity ever changes,
    these constants are the single source of truth and a
    roster-driven build update is the patch path."""
    config = importlib.import_module("config")
    assert config._R80_CORPUS_OWNER_DISPLAY == "Jeffrey Story (jestory)"
    assert config._R80_CORPUS_FOLDER_NAME == "AdoptIQ_CSOne_Reports"
    assert config._R80_SHARED_FOLDER_LEAF == (
        "Jeffrey Story (jestory) - AdoptIQ_CSOne_Reports"
    )
