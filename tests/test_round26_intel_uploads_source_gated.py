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


def _common_two_source_setup(monkeypatch, tmp_path: Path):
    """Round 36: set up OneDrive + Downloads so the base source list
    is the canonical 2-entry shape (the legacy ``sharepoint_csone``
    entry was retired with the MSAL/Graph runtime path).  Tests then
    layer on the intel_uploads expectations.

    Patches BOTH the live ``config.Config`` and the
    ``corpus_bootstrap.Config`` references to be robust against
    ``importlib.reload(config)`` pollution from sibling tests
    (``tests/test_round35_corpus_url_hardcoded.py``) -- the bootstrap
    module captures ``Config`` at import time and otherwise reads
    stale defaults under that ordering.
    """
    import config as _live_config
    import corpus_bootstrap as _cb

    od_dir = tmp_path / "od"
    dl_dir = tmp_path / "dl"
    od_dir.mkdir()
    dl_dir.mkdir()

    overrides = {
        "CSONE_ONEDRIVE_FOLDER": str(od_dir),
        "CSONE_INCLUDE_USER_DOWNLOADS": True,
        "CSONE_USER_DOWNLOADS_DIR": str(dl_dir),
    }
    for name, value in overrides.items():
        monkeypatch.setattr(_live_config.Config, name, value, raising=False)
        monkeypatch.setattr(_cb.Config, name, value, raising=False)


# Backward-compat alias (older test names referenced this helper).
_common_three_source_setup = _common_two_source_setup


def test_intel_uploads_absent_when_flag_off_and_dir_missing(
    monkeypatch, tmp_path: Path
):
    """Disabled installs that never pre-seeded the directory keep
    the original 3-source ordering.  This is the production default."""
    import config as _live_config
    import corpus_bootstrap as cb

    _common_three_source_setup(monkeypatch, tmp_path)

    overrides = {
        "ADOPTIQ_INTEL_UPLOAD_ENABLED": False,
        "CSONE_INTEL_UPLOADS_FOLDER": str(tmp_path / "intel-uploads-absent"),
    }
    for name, value in overrides.items():
        monkeypatch.setattr(_live_config.Config, name, value, raising=False)
        monkeypatch.setattr(cb.Config, name, value, raising=False)

    labels = [s["label"] for s in cb._resolve_index_sources()]
    assert "intel_uploads" not in labels
    # Round 36: sharepoint_csone source retired.
    assert labels == ["onedrive", "user_downloads"]


def test_intel_uploads_picked_up_when_flag_off_but_admin_pre_seeded(
    monkeypatch, tmp_path: Path
):
    """Admin can drop files into the directory by hand without
    enabling the user-facing upload route; the walker still
    indexes them.  This is the documented escape hatch in the
    ``_resolve_index_sources`` docstring."""
    import config as _live_config
    import corpus_bootstrap as cb

    _common_three_source_setup(monkeypatch, tmp_path)

    intel_dir = tmp_path / "intel-uploads-preseeded"
    intel_dir.mkdir()

    overrides = {
        "ADOPTIQ_INTEL_UPLOAD_ENABLED": False,
        "CSONE_INTEL_UPLOADS_FOLDER": str(intel_dir),
    }
    for name, value in overrides.items():
        monkeypatch.setattr(_live_config.Config, name, value, raising=False)
        monkeypatch.setattr(cb.Config, name, value, raising=False)

    labels = [s["label"] for s in cb._resolve_index_sources()]
    # Round 36: sharepoint_csone source retired.
    assert labels == [
        "onedrive",
        "user_downloads",
        "intel_uploads",
    ]


# ---------------------------------------------------------------------------
# Round 26 - review (R26-OPEN-001): _index_stats_to_dict marshalling
# ---------------------------------------------------------------------------
#
# Originally Round 17 stripped error filenames before they reached the
# admin tile (PII).  Internal-only deployment lifts that constraint, so
# the marshaller now exposes:
#
#   * ``errors``       -- list of {"file", "reason"} dicts (<=5 entries)
#   * ``errors_total`` -- full count for "N of M shown" rendering
#
# These tests pin the new contract.  They live next to the gating tests
# so the Round 26 review follow-up has one obvious test home.


def test_index_stats_to_dict_marshals_first_five_errors_and_total():
    import corpus_bootstrap as cb
    from corpus_indexer import IndexStats

    stats = IndexStats()
    # Indexer appends "<filename>: <ExceptionClass>" strings; reproduce
    # that exact shape here so the partition path fires.
    for i in range(12):
        stats.errors.append(f"file_{i}.xlsx: BadZipFile")

    payload = cb._index_stats_to_dict(stats)

    assert isinstance(payload["errors"], list)
    assert payload["errors_total"] == 12
    assert len(payload["errors"]) == 5
    # Order preservation: first 5 errors should appear in input order.
    expected_files = [f"file_{i}.xlsx" for i in range(5)]
    assert [row["file"] for row in payload["errors"]] == expected_files
    assert all(row["reason"] == "BadZipFile" for row in payload["errors"])


def test_index_stats_to_dict_errors_total_matches_list_when_under_cap():
    """When stats has <=5 errors, the rendered list equals the full count
    and ``errors_total`` matches ``len(errors)``."""
    import corpus_bootstrap as cb
    from corpus_indexer import IndexStats

    stats = IndexStats()
    stats.errors.append("only_one.xlsx: KeyError")

    payload = cb._index_stats_to_dict(stats)
    assert payload["errors_total"] == 1
    assert len(payload["errors"]) == 1
    assert payload["errors"][0] == {"file": "only_one.xlsx", "reason": "KeyError"}


def test_index_stats_to_dict_handles_no_colon_edge_case():
    """Conn=None / fallback path: indexer appends a bare string with no
    colon.  Marshaller must render it as ``{"file": "?", "reason": <s>}``
    so the template doesn't crash on missing keys."""
    import corpus_bootstrap as cb
    from corpus_indexer import IndexStats

    stats = IndexStats()
    stats.errors.append("indexer aborted before file open")

    payload = cb._index_stats_to_dict(stats)
    assert payload["errors_total"] == 1
    assert payload["errors"][0]["file"] == "?"
    assert "indexer aborted" in payload["errors"][0]["reason"]


def test_index_stats_to_dict_empty_errors_list():
    """Steady-state happy path: no errors -> empty list + zero count."""
    import corpus_bootstrap as cb
    from corpus_indexer import IndexStats

    payload = cb._index_stats_to_dict(IndexStats())
    assert payload["errors"] == []
    assert payload["errors_total"] == 0


def test_intel_uploads_present_when_flag_on_and_dir_exists(
    monkeypatch, tmp_path: Path
):
    """The happy path: operator enabled the upload endpoint and
    the bootstrap walker exposes the 4-entry source list."""
    import config as _live_config
    import corpus_bootstrap as cb

    _common_three_source_setup(monkeypatch, tmp_path)

    intel_dir = tmp_path / "intel-uploads-enabled"
    intel_dir.mkdir()

    overrides = {
        "ADOPTIQ_INTEL_UPLOAD_ENABLED": True,
        "CSONE_INTEL_UPLOADS_FOLDER": str(intel_dir),
    }
    for name, value in overrides.items():
        monkeypatch.setattr(_live_config.Config, name, value, raising=False)
        monkeypatch.setattr(cb.Config, name, value, raising=False)

    labels = [s["label"] for s in cb._resolve_index_sources()]
    # Round 36: sharepoint_csone source retired.
    assert labels == [
        "onedrive",
        "user_downloads",
        "intel_uploads",
    ]
