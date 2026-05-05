"""Round 83 / Build 59 — bake_corpus.py owner-style path auto-detect.

The Round 83 / Phase C1 fix ensures ``scripts/bake_corpus.py``
auto-detects the corpus owner's owner-style OneDrive path (
``~/Library/CloudStorage/OneDrive-Cisco/AI Projects/AdoptIQ_CSOne_Reports``)
WITHOUT requiring a manual ``ADOPTIQ_BAKE_FIXTURE_DIR`` /
``ADOPTIQ_BAKE_SENTINEL_ROOT`` env-var override.

These tests pin:

* ``_resolve_source_dir`` walks ``_csone_onedrive_candidates()`` and
  returns the first existing-directory candidate.
* ``_resolve_source_dir`` honors explicit overrides (CLI flag,
  ``--offline-fixture``, ``ADOPTIQ_BAKE_FIXTURE_DIR`` env) BEFORE
  the auto-detect walk.
* ``_resolve_onedrive_sentinel_root`` walks the candidate list AFTER
  CLI / env-var overrides.
* ``_resolve_onedrive_sentinel_root`` returns ``None`` when no
  candidate exists AND ``Config.CSONE_ONEDRIVE_FOLDER`` doesn't
  exist either.

Round 83 / Build 59
"""
# Round 83
from __future__ import annotations

import argparse
import importlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _make_args(
    source: Optional[str] = None,
    offline_fixture: Optional[str] = None,
    onedrive_sentinel_root: Optional[str] = None,
) -> argparse.Namespace:
    """Round 83 helper: build the argparse.Namespace shape that
    ``bake_corpus._resolve_source_dir`` and
    ``_resolve_onedrive_sentinel_root`` consume."""
    # Round 83
    return argparse.Namespace(
        source=source,
        offline_fixture=offline_fixture,
        onedrive_sentinel_root=onedrive_sentinel_root,
    )


# ---------------------------------------------------------------------------
# 1. _resolve_source_dir
# ---------------------------------------------------------------------------


def test_resolve_source_dir_walks_candidate_list_when_no_overrides(
    monkeypatch, tmp_path,
):
    """When no CLI/env overrides are present and the canonical R80
    leaves don't exist, ``_resolve_source_dir`` walks the candidate
    list and returns the first existing-directory candidate."""
    # Round 83
    import bake_corpus
    # Build a fake candidate list pointing the FIRST entry at a
    # non-existent path and the SECOND at a real tmp_path so we can
    # assert the walker picks the first existing one.
    fake_path = tmp_path / "owner_style"
    fake_path.mkdir()
    candidates = [
        str(tmp_path / "does_not_exist_a"),
        str(tmp_path / "does_not_exist_b"),
        str(fake_path),
    ]
    monkeypatch.setenv("ADOPTIQ_BAKE_FIXTURE_DIR", "")
    with patch(
        "config._csone_onedrive_candidates",
        return_value=candidates,
    ):
        args = _make_args()
        result = bake_corpus._resolve_source_dir(args)
    assert result is not None
    assert result == fake_path


def test_resolve_source_dir_cli_source_flag_wins_over_walk(
    monkeypatch, tmp_path,
):
    """``--source <path>`` MUST take precedence over the candidate
    walk so an operator can override the auto-detect."""
    # Round 83
    import bake_corpus
    cli_path = tmp_path / "cli_source"
    cli_path.mkdir()
    other_path = tmp_path / "other_path"
    other_path.mkdir()
    monkeypatch.setenv("ADOPTIQ_BAKE_FIXTURE_DIR", "")
    with patch(
        "config._csone_onedrive_candidates",
        return_value=[str(other_path)],
    ):
        args = _make_args(source=str(cli_path))
        result = bake_corpus._resolve_source_dir(args)
    assert result == cli_path.resolve()


def test_resolve_source_dir_env_var_wins_over_walk(monkeypatch, tmp_path):
    """``ADOPTIQ_BAKE_FIXTURE_DIR`` MUST take precedence over the
    candidate walk (matches R80 / R83 design)."""
    # Round 83
    import bake_corpus
    env_path = tmp_path / "env_dir"
    env_path.mkdir()
    other_path = tmp_path / "other_dir"
    other_path.mkdir()
    monkeypatch.setenv("ADOPTIQ_BAKE_FIXTURE_DIR", str(env_path))
    with patch(
        "config._csone_onedrive_candidates",
        return_value=[str(other_path)],
    ):
        args = _make_args()
        result = bake_corpus._resolve_source_dir(args)
    assert result == env_path.resolve()


def test_resolve_source_dir_walk_skips_files(monkeypatch, tmp_path):
    """The walker MUST require ``is_dir() == True`` -- a regular
    file at a candidate path is skipped (defensive against an
    operator who accidentally created a file at the canonical
    path)."""
    # Round 83
    import bake_corpus
    file_path = tmp_path / "file_at_candidate"
    file_path.write_text("not a dir", encoding="utf-8")
    dir_path = tmp_path / "real_dir"
    dir_path.mkdir()
    monkeypatch.setenv("ADOPTIQ_BAKE_FIXTURE_DIR", "")
    with patch(
        "config._csone_onedrive_candidates",
        return_value=[str(file_path), str(dir_path)],
    ):
        args = _make_args()
        result = bake_corpus._resolve_source_dir(args)
    assert result == dir_path


def test_resolve_source_dir_falls_back_to_config_when_no_candidates_exist(
    monkeypatch, tmp_path,
):
    """When NO candidate exists and no overrides are configured,
    the resolver falls back to ``Config.CSONE_ONEDRIVE_FOLDER``
    (even when the path doesn't exist) so the caller's error
    message points at a known-canonical path."""
    # Round 83
    import bake_corpus
    fallback = tmp_path / "config_fallback_does_not_exist"
    monkeypatch.setenv("ADOPTIQ_BAKE_FIXTURE_DIR", "")
    with patch(
        "config._csone_onedrive_candidates",
        return_value=[str(tmp_path / "no_a"), str(tmp_path / "no_b")],
    ), patch.object(
        bake_corpus, "_resolve_source_dir",
        wraps=bake_corpus._resolve_source_dir,
    ):
        from config import Config
        original = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
        try:
            Config.CSONE_ONEDRIVE_FOLDER = str(fallback)
            args = _make_args()
            result = bake_corpus._resolve_source_dir(args)
            assert result is not None
            # The path doesn't exist, but the resolver returns the
            # canonical config value so the caller can emit a
            # helpful error.
            assert result == fallback.resolve()
        finally:
            if original is not None:
                Config.CSONE_ONEDRIVE_FOLDER = original


# ---------------------------------------------------------------------------
# 2. _resolve_onedrive_sentinel_root
# ---------------------------------------------------------------------------


def test_resolve_sentinel_root_walks_candidate_list(monkeypatch, tmp_path):
    """``_resolve_onedrive_sentinel_root`` walks
    ``_csone_onedrive_candidates()`` and returns the first
    existing-directory candidate when no CLI / env override is
    set."""
    # Round 83
    import bake_corpus
    fake_root = tmp_path / "owner_sentinel_root"
    fake_root.mkdir()
    monkeypatch.setenv("ADOPTIQ_BAKE_SENTINEL_ROOT", "")
    with patch(
        "config._csone_onedrive_candidates",
        return_value=[
            str(tmp_path / "does_not_exist"),
            str(fake_root),
        ],
    ):
        args = _make_args()
        result = bake_corpus._resolve_onedrive_sentinel_root(args)
    assert result == fake_root


def test_resolve_sentinel_root_cli_flag_wins_over_walk(
    monkeypatch, tmp_path,
):
    """``--onedrive-sentinel-root <path>`` MUST take precedence
    over the candidate walk."""
    # Round 83
    import bake_corpus
    cli_root = tmp_path / "cli_root"
    cli_root.mkdir()
    walk_root = tmp_path / "walk_root"
    walk_root.mkdir()
    monkeypatch.setenv("ADOPTIQ_BAKE_SENTINEL_ROOT", "")
    with patch(
        "config._csone_onedrive_candidates",
        return_value=[str(walk_root)],
    ):
        args = _make_args(onedrive_sentinel_root=str(cli_root))
        result = bake_corpus._resolve_onedrive_sentinel_root(args)
    assert result == cli_root.resolve()


def test_resolve_sentinel_root_env_var_wins_over_walk(
    monkeypatch, tmp_path,
):
    """``ADOPTIQ_BAKE_SENTINEL_ROOT`` MUST take precedence over the
    candidate walk."""
    # Round 83
    import bake_corpus
    env_root = tmp_path / "env_root"
    env_root.mkdir()
    walk_root = tmp_path / "walk_root"
    walk_root.mkdir()
    monkeypatch.setenv("ADOPTIQ_BAKE_SENTINEL_ROOT", str(env_root))
    with patch(
        "config._csone_onedrive_candidates",
        return_value=[str(walk_root)],
    ):
        args = _make_args()
        result = bake_corpus._resolve_onedrive_sentinel_root(args)
    assert result == env_root.resolve()


def test_resolve_sentinel_root_returns_none_when_nothing_exists(
    monkeypatch, tmp_path,
):
    """When no candidate exists AND no overrides are configured AND
    ``Config.CSONE_ONEDRIVE_FOLDER`` doesn't exist on disk, the
    resolver returns ``None`` so the caller can emit an honest
    fail-closed error."""
    # Round 83
    import bake_corpus
    monkeypatch.setenv("ADOPTIQ_BAKE_SENTINEL_ROOT", "")
    with patch(
        "config._csone_onedrive_candidates",
        return_value=[
            str(tmp_path / "no_a"),
            str(tmp_path / "no_b"),
            str(tmp_path / "no_c"),
        ],
    ):
        from config import Config
        original = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
        try:
            Config.CSONE_ONEDRIVE_FOLDER = str(tmp_path / "also_no")
            args = _make_args()
            result = bake_corpus._resolve_onedrive_sentinel_root(args)
            # Either None (preferred -- candidate walk + config
            # fallback both fail) OR a non-existent path. R83's
            # contract is None when nothing on disk exists.
            assert result is None
        finally:
            if original is not None:
                Config.CSONE_ONEDRIVE_FOLDER = original


def test_resolve_sentinel_root_emits_round83_log_marker(
    monkeypatch, tmp_path, caplog,
):
    """When the walker auto-detects a sentinel root, a Round 83
    log marker is emitted so the build-script log surfaces the
    auto-detect path explicitly (no silent rescue)."""
    # Round 83
    import bake_corpus
    import logging
    fake_root = tmp_path / "owner_logged"
    fake_root.mkdir()
    monkeypatch.setenv("ADOPTIQ_BAKE_SENTINEL_ROOT", "")
    with patch(
        "config._csone_onedrive_candidates",
        return_value=[
            str(tmp_path / "no_a"),
            str(fake_root),
        ],
    ):
        with caplog.at_level(logging.INFO, logger="bake_corpus"):
            args = _make_args()
            result = bake_corpus._resolve_onedrive_sentinel_root(args)
        assert result == fake_root
        # The log MUST mention "Round 83" so an operator scanning
        # build logs can confirm the auto-detect happened.
        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert "Round 83" in joined or "auto-detected" in joined
