"""Round 83 / Build 59 — _csone_onedrive_candidates() owner fallback.

Pins the 4-candidate shape introduced in Round 83 / Build 59:

  1. R80 modern macOS canonical leaf
  2. R80 pre-Big-Sur / Windows canonical leaf
  3. R83 modern macOS owner-style path (``AI Projects/...``)
  4. R83 pre-Big-Sur / Windows owner-style path

Round 80's "no arbitrary subdirectory walks under
``OneDrive-Cisco/``" intent is preserved -- the owner-style
fallback adds two specific paths, NOT a generic walk. R80 leaves
remain priority 1-2 so the 99% non-owner case is unchanged.

Round 83 / Build 59
"""
# Round 83
from __future__ import annotations

import os

import pytest

import config


# ---------------------------------------------------------------------------
# 1. Shape: exactly 4 entries
# ---------------------------------------------------------------------------


def test_candidates_have_four_entries():
    """Round 83 expanded the candidate list from 2 (R80) to 4."""
    # Round 83
    cands = config._csone_onedrive_candidates()
    assert len(cands) == 4, (
        f"Round 83 contract violated: expected 4 candidates, got "
        f"{len(cands)}: {cands}"
    )


def test_owner_style_leaf_constant_shape():
    """``_R83_OWNER_STYLE_LEAF`` is the path-joined 'AI Projects/<corpus>'
    string."""
    # Round 83
    expected = os.path.join("AI Projects", config._R80_CORPUS_FOLDER_NAME)
    assert config._R83_OWNER_STYLE_LEAF == expected


# ---------------------------------------------------------------------------
# 2. Order: R80 first, R83 second
# ---------------------------------------------------------------------------


def test_r80_canonical_leaves_come_first():
    """The first 2 candidates are the R80 canonical leaves."""
    # Round 83
    cands = config._csone_onedrive_candidates()
    assert config._R80_SHARED_FOLDER_LEAF in cands[0]
    assert config._R80_SHARED_FOLDER_LEAF in cands[1]


def test_r83_owner_style_paths_come_third_and_fourth():
    """The last 2 candidates are the R83 owner-style paths."""
    # Round 83
    cands = config._csone_onedrive_candidates()
    assert "AI Projects" in cands[2]
    assert "AI Projects" in cands[3]
    # Defensive: owner-style paths use the OWNER_STYLE_LEAF constant.
    assert config._R83_OWNER_STYLE_LEAF in cands[2]
    assert config._R83_OWNER_STYLE_LEAF in cands[3]


def test_modern_macos_first_in_each_pair():
    """``Library/CloudStorage/`` comes BEFORE the pre-Big-Sur
    fallback within each R80/R83 pair."""
    # Round 83
    cands = config._csone_onedrive_candidates()
    assert "Library/CloudStorage/OneDrive-Cisco" in cands[0]  # R80 modern
    assert "Library/CloudStorage" not in cands[1]            # R80 legacy
    assert "Library/CloudStorage/OneDrive-Cisco" in cands[2]  # R83 modern
    assert "Library/CloudStorage" not in cands[3]            # R83 legacy


# ---------------------------------------------------------------------------
# 3. R80 narrowing intent preserved (no arbitrary subdirectory walks)
# ---------------------------------------------------------------------------


def test_no_candidate_walks_arbitrary_subdirectory():
    """No candidate accepts a wildcard / parent-only path. Each
    candidate references a specific named leaf (either R80 canonical
    or R83 owner-style)."""
    # Round 83
    cands = config._csone_onedrive_candidates()
    for c in cands:
        # Must end with a named subdirectory, not bare OneDrive-Cisco.
        assert c.rstrip(os.sep) != os.path.expanduser(
            "~/Library/CloudStorage/OneDrive-Cisco",
        ), f"Candidate {c} would walk OneDrive-Cisco/ root"
        assert c.rstrip(os.sep) != os.path.expanduser(
            "~/OneDrive - Cisco",
        ), f"Candidate {c} would walk OneDrive - Cisco/ root"


def test_each_candidate_resolves_to_unique_path():
    """All 4 candidates are distinct paths."""
    # Round 83
    cands = config._csone_onedrive_candidates()
    assert len(cands) == len(set(cands))


# ---------------------------------------------------------------------------
# 4. _resolve_csone_onedrive_folder() walks the new candidates
# ---------------------------------------------------------------------------


def test_resolver_picks_first_existing_candidate(tmp_path, monkeypatch):
    """When only the R83 owner-style path exists on disk, the
    resolver picks it (and skips the missing R80 canonical leaves)."""
    # Round 83
    home = tmp_path
    owner_leaf = home / "Library" / "CloudStorage" / "OneDrive-Cisco" \
        / config._R83_OWNER_STYLE_LEAF
    owner_leaf.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(home))
    monkeypatch.delenv("CSONE_ONEDRIVE_FOLDER", raising=False)
    resolved = config._resolve_csone_onedrive_folder()
    # The resolver should return a string ending in the owner-style
    # leaf since that's the only candidate that exists.
    assert config._R83_OWNER_STYLE_LEAF in resolved


def test_resolver_prefers_canonical_leaf_when_both_present(tmp_path, monkeypatch):
    """When BOTH the R80 canonical leaf AND the R83 owner-style path
    exist, the canonical leaf wins (priority 1 > priority 3)."""
    # Round 83
    home = tmp_path
    canonical = home / "Library" / "CloudStorage" / "OneDrive-Cisco" \
        / config._R80_SHARED_FOLDER_LEAF
    owner = home / "Library" / "CloudStorage" / "OneDrive-Cisco" \
        / config._R83_OWNER_STYLE_LEAF
    canonical.mkdir(parents=True, exist_ok=True)
    owner.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(home))
    monkeypatch.delenv("CSONE_ONEDRIVE_FOLDER", raising=False)
    resolved = config._resolve_csone_onedrive_folder()
    assert config._R80_SHARED_FOLDER_LEAF in resolved
    # Defensive: must NOT have picked the owner-style path when the
    # canonical was also available.
    assert config._R83_OWNER_STYLE_LEAF not in resolved \
        or config._R80_SHARED_FOLDER_LEAF in resolved.replace(
            config._R83_OWNER_STYLE_LEAF, "",
        )


def test_resolver_falls_back_to_first_candidate_when_none_exist(monkeypatch):
    """When NONE of the 4 candidates exist on disk, the resolver
    returns the first candidate (the modern macOS canonical leaf)
    so error messages still point at the right "expected" path."""
    # Round 83
    monkeypatch.delenv("CSONE_ONEDRIVE_FOLDER", raising=False)
    monkeypatch.setattr(
        os.path, "expanduser",
        lambda p: "/this/path/does/not/exist",
    )
    resolved = config._resolve_csone_onedrive_folder()
    # First candidate is always the R80 modern macOS path.
    assert config._R80_SHARED_FOLDER_LEAF in resolved
    assert "Library/CloudStorage/OneDrive-Cisco" in resolved


# ---------------------------------------------------------------------------
# 5. CSONE_ONEDRIVE_FOLDER env override still wins
# ---------------------------------------------------------------------------


def test_env_override_bypasses_candidate_list(tmp_path, monkeypatch):
    """``CSONE_ONEDRIVE_FOLDER`` env var bypasses the candidate
    walk entirely (preserves the Round 17.2 power-user contract)."""
    # Round 83
    target = tmp_path / "custom"
    target.mkdir()
    monkeypatch.setenv("CSONE_ONEDRIVE_FOLDER", str(target))
    resolved = config._resolve_csone_onedrive_folder()
    assert resolved == str(target)
