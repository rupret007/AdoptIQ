"""
Round 26 - review (R26-OPEN-002): regression coverage for the
``intel_uploads`` startup pre-create gate and the resulting
``corpus_bootstrap._resolve_index_sources`` shape.

Original Round 26 / Phase D code unconditionally pre-created
``Config.CSONE_INTEL_UPLOADS_FOLDER`` at startup, which meant
``_resolve_index_sources`` always returned 4 entries even on installs
that never enabled the user-facing upload route.  The R26-OPEN-002
fix gates the pre-create on ``ADOPTIQ_INTEL_UPLOAD_ENABLED`` and
updates the docstring.

These tests pin the new contract:

  * Flag OFF + dir absent -> 3-source list (no ``intel_uploads``).
  * Flag OFF + admin pre-seeded the dir -> 4-source list.
  * Flag ON + dir present -> 4-source list.

The startup pre-create itself is not directly covered by a pytest
fixture (it runs at module import time on the live process); we
exercise the same effective code path by toggling
``Config.ADOPTIQ_INTEL_UPLOAD_ENABLED`` and the directory's
existence and asserting that the bootstrap source list reflects
the toggle.
"""
from __future__ import annotations

from pathlib import Path


def _common_three_source_setup(monkeypatch, tmp_path: Path):
    """Set up SharePoint + OneDrive + Downloads so the base
    source list is the canonical 3-entry shape.  Tests then
    layer on the intel_uploads expectations."""
    from config import Config

    sp_cache = tmp_path / "sp_cache"
    od_dir = tmp_path / "od"
    dl_dir = tmp_path / "dl"
    sp_cache.mkdir()
    od_dir.mkdir()
    dl_dir.mkdir()

    monkeypatch.setattr(
        Config, "ADOPTIQ_SHAREPOINT_ENABLED", True, raising=False
    )
    monkeypatch.setattr(
        Config,
        "ADOPTIQ_SHAREPOINT_FOLDER_URL",
        "https://example.com/share",
        raising=False,
    )
    monkeypatch.setattr(
        Config, "ADOPTIQ_SHAREPOINT_CACHE_DIR", str(sp_cache), raising=False
    )
    monkeypatch.setattr(
        Config, "CSONE_ONEDRIVE_FOLDER", str(od_dir), raising=False
    )
    monkeypatch.setattr(
        Config, "CSONE_INCLUDE_USER_DOWNLOADS", True, raising=False
    )
    monkeypatch.setattr(
        Config, "CSONE_USER_DOWNLOADS_DIR", str(dl_dir), raising=False
    )


def test_intel_uploads_absent_when_flag_off_and_dir_missing(
    monkeypatch, tmp_path: Path
):
    """Disabled installs that never pre-seeded the directory keep
    the original 3-source ordering.  This is the production default."""
    import corpus_bootstrap as cb
    from config import Config

    _common_three_source_setup(monkeypatch, tmp_path)

    monkeypatch.setattr(
        Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", False, raising=False
    )
    monkeypatch.setattr(
        Config,
        "CSONE_INTEL_UPLOADS_FOLDER",
        str(tmp_path / "intel-uploads-absent"),
        raising=False,
    )

    labels = [s["label"] for s in cb._resolve_index_sources()]
    assert "intel_uploads" not in labels
    assert labels == ["sharepoint_csone", "onedrive", "user_downloads"]


def test_intel_uploads_picked_up_when_flag_off_but_admin_pre_seeded(
    monkeypatch, tmp_path: Path
):
    """Admin can drop files into the directory by hand without
    enabling the user-facing upload route; the walker still
    indexes them.  This is the documented escape hatch in the
    ``_resolve_index_sources`` docstring."""
    import corpus_bootstrap as cb
    from config import Config

    _common_three_source_setup(monkeypatch, tmp_path)

    intel_dir = tmp_path / "intel-uploads-preseeded"
    intel_dir.mkdir()

    monkeypatch.setattr(
        Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", False, raising=False
    )
    monkeypatch.setattr(
        Config,
        "CSONE_INTEL_UPLOADS_FOLDER",
        str(intel_dir),
        raising=False,
    )

    labels = [s["label"] for s in cb._resolve_index_sources()]
    assert labels == [
        "sharepoint_csone",
        "onedrive",
        "user_downloads",
        "intel_uploads",
    ]


def test_intel_uploads_present_when_flag_on_and_dir_exists(
    monkeypatch, tmp_path: Path
):
    """The happy path: operator enabled the upload endpoint and
    the bootstrap walker exposes the 4-entry source list."""
    import corpus_bootstrap as cb
    from config import Config

    _common_three_source_setup(monkeypatch, tmp_path)

    intel_dir = tmp_path / "intel-uploads-enabled"
    intel_dir.mkdir()

    monkeypatch.setattr(
        Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True, raising=False
    )
    monkeypatch.setattr(
        Config,
        "CSONE_INTEL_UPLOADS_FOLDER",
        str(intel_dir),
        raising=False,
    )

    labels = [s["label"] for s in cb._resolve_index_sources()]
    assert labels == [
        "sharepoint_csone",
        "onedrive",
        "user_downloads",
        "intel_uploads",
    ]
