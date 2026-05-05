"""Round 80 / Build 56: corpus indexer registers
``_APP_SUPPORT/outputs/`` as a new ``local_outputs`` source so
reports the running app generates are indexed automatically, AND
flips ``CSONE_INCLUDE_USER_DOWNLOADS`` default ``true -> false``
to eliminate the macOS Files-and-Folders permission prompt that
confused users (Brian's bug B).
"""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path

import pytest

# Round 80


def test_resolve_app_support_outputs_dir_returns_existing_path(monkeypatch, tmp_path):
    """The R80 helper MUST return an absolute Path when the directory
    exists, and ``None`` when it does not -- callers in
    ``_resolve_index_sources`` rely on the ``None`` to skip
    registering an empty source."""
    monkeypatch.setenv("ADOPTIQ_OUTPUTS_DIR", str(tmp_path))
    cb = importlib.import_module("corpus_bootstrap")
    resolved = cb._r80_resolve_app_support_outputs_dir()
    assert resolved == tmp_path, (
        f"Round 80: helper must return tmp_path={tmp_path!s}, got {resolved!r}"
    )


def test_resolve_app_support_outputs_dir_returns_none_when_missing(
    monkeypatch, tmp_path
):
    """When the override path does not exist on disk the helper
    MUST return ``None`` so the indexer skips it."""
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("ADOPTIQ_OUTPUTS_DIR", str(missing))
    cb = importlib.import_module("corpus_bootstrap")
    assert cb._r80_resolve_app_support_outputs_dir() is None, (
        "Round 80: helper must return None for missing directory."
    )


def test_local_outputs_source_registered_after_onedrive(
    monkeypatch, tmp_path
):
    """The ``local_outputs`` source MUST appear in the index-source
    list immediately after ``onedrive``.  When OneDrive is missing
    (typical CI), ``local_outputs`` is the first registered source."""
    # Set up a fake outputs dir.
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setenv("ADOPTIQ_OUTPUTS_DIR", str(outputs))
    # Disable Downloads (the R80 default) and intel_uploads so the
    # source list is deterministic.
    monkeypatch.setenv("CSONE_INCLUDE_USER_DOWNLOADS", "false")

    # Force OneDrive to be unset so the onedrive source is skipped.
    cb = importlib.import_module("corpus_bootstrap")
    monkeypatch.setattr(cb.Config, "CSONE_ONEDRIVE_FOLDER", None, raising=False)
    monkeypatch.setattr(
        cb.Config, "CSONE_INCLUDE_USER_DOWNLOADS", False, raising=False
    )
    monkeypatch.setattr(
        cb.Config, "CSONE_INTEL_UPLOADS_FOLDER", None, raising=False
    )

    sources = cb._resolve_index_sources()
    labels = [s["label"] for s in sources]
    assert "local_outputs" in labels, (
        f"Round 80: local_outputs source not registered. "
        f"Got labels: {labels}"
    )

    # Filter must be 'adoptiq_named' so the walker only ingests
    # AdoptIQ-named report files.
    local = next(s for s in sources if s["label"] == "local_outputs")
    assert local["filter"] == "adoptiq_named", (
        f"Round 80: local_outputs filter must be 'adoptiq_named'; "
        f"got {local['filter']!r}"
    )
    assert Path(local["dir"]) == outputs


def test_local_outputs_appears_after_onedrive_when_both_present(
    monkeypatch, tmp_path
):
    """Priority pin: when both OneDrive and local_outputs exist,
    OneDrive MUST come first (it's the corpus walker's rebuild
    anchor; local_outputs upserts on top)."""
    od = tmp_path / "onedrive"
    od.mkdir()
    out = tmp_path / "outputs"
    out.mkdir()

    monkeypatch.setenv("ADOPTIQ_OUTPUTS_DIR", str(out))
    monkeypatch.setenv("CSONE_INCLUDE_USER_DOWNLOADS", "false")

    cb = importlib.import_module("corpus_bootstrap")
    monkeypatch.setattr(cb.Config, "CSONE_ONEDRIVE_FOLDER", str(od), raising=False)
    monkeypatch.setattr(
        cb.Config, "CSONE_INCLUDE_USER_DOWNLOADS", False, raising=False
    )
    monkeypatch.setattr(
        cb.Config, "CSONE_INTEL_UPLOADS_FOLDER", None, raising=False
    )

    sources = cb._resolve_index_sources()
    labels = [s["label"] for s in sources]
    # Both must be present.
    assert "onedrive" in labels and "local_outputs" in labels
    # OneDrive must precede local_outputs.
    od_idx = labels.index("onedrive")
    out_idx = labels.index("local_outputs")
    assert od_idx < out_idx, (
        f"Round 80: onedrive must precede local_outputs. "
        f"Got order: {labels}"
    )


def test_user_downloads_default_off_in_round_80(monkeypatch):
    """The ``CSONE_INCLUDE_USER_DOWNLOADS`` default MUST be ``False``
    in Round 80.  Pre-R80 it was ``True`` which triggered the macOS
    Files-and-Folders permission prompt that confused users."""
    # Clear any leftover env so we observe the bare default.
    monkeypatch.delenv("CSONE_INCLUDE_USER_DOWNLOADS", raising=False)
    import config
    importlib.reload(config)
    try:
        assert config.Config.CSONE_INCLUDE_USER_DOWNLOADS is False, (
            f"Round 80: CSONE_INCLUDE_USER_DOWNLOADS default must be "
            f"False (was True pre-R80). "
            f"Got {config.Config.CSONE_INCLUDE_USER_DOWNLOADS!r}"
        )
    finally:
        importlib.reload(config)


def test_deprecation_warning_emitted_when_env_opts_in(monkeypatch, caplog):
    """Setting ``CSONE_INCLUDE_USER_DOWNLOADS=true`` via env MUST
    re-enable the deprecated source for one build BUT MUST also
    emit a one-shot ``logger.warning`` so the operator sees the
    deprecation notice and the regression to the new R81 default
    is scheduled."""
    monkeypatch.setenv("CSONE_INCLUDE_USER_DOWNLOADS", "true")
    import config
    with caplog.at_level(logging.WARNING, logger="config"):
        importlib.reload(config)
    try:
        assert config.Config.CSONE_INCLUDE_USER_DOWNLOADS is True, (
            "Round 80: env=true must still opt the source in for one build."
        )
        # The warning text MUST name 'CSONE_INCLUDE_USER_DOWNLOADS',
        # 'DEPRECATED', and 'Round 81' so the operator knows what
        # is going away and when.
        msgs = [r.getMessage() for r in caplog.records]
        text = "\n".join(msgs)
        assert "CSONE_INCLUDE_USER_DOWNLOADS" in text
        assert "DEPRECATED" in text
        assert "Round 81" in text
    finally:
        monkeypatch.delenv("CSONE_INCLUDE_USER_DOWNLOADS", raising=False)
        importlib.reload(config)


def test_no_deprecation_warning_when_env_unset_or_false(monkeypatch, caplog):
    """When the env var is unset OR explicitly set to a falsy value,
    NO deprecation warning fires (we don't want every cold start
    to log a deprecation warning when nobody opted in)."""
    monkeypatch.delenv("CSONE_INCLUDE_USER_DOWNLOADS", raising=False)
    import config
    with caplog.at_level(logging.WARNING, logger="config"):
        importlib.reload(config)
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "CSONE_INCLUDE_USER_DOWNLOADS" not in msgs or "DEPRECATED" not in msgs, (
        f"Round 80: deprecation warning must NOT fire when env is unset. "
        f"Got log records: {msgs!r}"
    )

    monkeypatch.setenv("CSONE_INCLUDE_USER_DOWNLOADS", "false")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="config"):
        importlib.reload(config)
    try:
        msgs = "\n".join(r.getMessage() for r in caplog.records)
        assert "DEPRECATED" not in msgs, (
            f"Round 80: deprecation warning must NOT fire when env=false. "
            f"Got log records: {msgs!r}"
        )
    finally:
        monkeypatch.delenv("CSONE_INCLUDE_USER_DOWNLOADS", raising=False)
        importlib.reload(config)
