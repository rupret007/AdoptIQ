"""Round 125 / Build 94 (E2+E3) -- releases_folder setting + manifest merge.

* E2: ``releases_folder`` is now a first-class allow-listed settings key
  with a validator, so ``config._resolve_releases_folder`` can honour an
  operator override saved to ``settings.json`` (pre-R125 the key was
  absent from ``_SCHEMA`` so it was silently dropped on load/save).
* E3: a mac-only ``write_release_manifest`` publish MUST preserve an
  existing ``pc`` slot so Windows installs in the wild keep seeing the
  last published PC build.
"""

import importlib.util
import os

import adoptiq_settings


# --------------------------------------------------------------------------
# E2: releases_folder schema + validator
# --------------------------------------------------------------------------

def test_releases_folder_in_schema_with_empty_default():
    assert "releases_folder" in adoptiq_settings._SCHEMA
    typ, default = adoptiq_settings._SCHEMA["releases_folder"]
    assert typ is str
    assert default == ""


def test_releases_folder_has_validator():
    assert "releases_folder" in adoptiq_settings._VALIDATORS


def test_releases_folder_public_validator_accepts_absolute_and_empty():
    assert adoptiq_settings.is_valid_releases_folder("") is True
    assert adoptiq_settings.is_valid_releases_folder("/Users/me/OneDrive/OUTBOX") is True
    assert adoptiq_settings.is_valid_releases_folder("~/OneDrive/OUTBOX") is True


def test_releases_folder_public_validator_rejects_control_chars():
    assert adoptiq_settings.is_valid_releases_folder("/tmp/x\x00y") is False
    assert adoptiq_settings.is_valid_releases_folder(123) is False


def test_releases_folder_round_trips_save_load(tmp_path, monkeypatch):
    # A valid override survives save_settings -> load_settings (i.e. it is
    # NOT dropped the way an unknown key would be).
    monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
    adoptiq_settings.save_settings({"releases_folder": "/Users/me/OUTBOX"})
    loaded = adoptiq_settings.load_settings()
    assert loaded.get("releases_folder") == "/Users/me/OUTBOX"


def test_releases_folder_invalid_value_dropped_on_save(tmp_path, monkeypatch):
    monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
    adoptiq_settings.save_settings({"releases_folder": "relative/path"})
    loaded = adoptiq_settings.load_settings()
    # Invalid (not absolute / tilde / drive) -> validator rejects -> dropped.
    assert "releases_folder" not in loaded


# --------------------------------------------------------------------------
# E3: write_release_manifest mac-only publish preserves an existing pc slot
# --------------------------------------------------------------------------

def _load_manifest_writer():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "scripts", "write_release_manifest.py")
    spec = importlib.util.spec_from_file_location("write_release_manifest_r125", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mac_only_publish_preserves_existing_pc_slot():
    wm = _load_manifest_writer()
    existing = {
        "schema": 1,
        "version": "1.0.4",
        "build": 87,
        "channel": "stable",
        "pc": {
            "build": 87,
            "version": "1.0.4",
            "artifact": "AdoptIQ_PC/AdoptIQ-v1.0.4-build87.exe",
            "sha256": "deadbeef",
            "size_bytes": 123,
            "released_at_utc": "2026-05-01T00:00:00Z",
        },
    }
    merged = wm.merge_manifest(
        existing,
        platform="mac",
        version="1.0.4",
        build=94,
        artifact="AdoptIQ/AdoptIQ-v1.0.4-build94.dmg",
        sha256="cafe",
        size_bytes=456,
    )
    # PC slot is untouched.
    assert merged["pc"]["build"] == 87
    assert merged["pc"]["artifact"] == "AdoptIQ_PC/AdoptIQ-v1.0.4-build87.exe"
    # Mac slot is the new build.
    assert merged["mac"]["build"] == 94
    # Top-level build is the max across slots (informational).
    assert merged["build"] == 94


def test_mac_publish_with_no_existing_pc_slot_does_not_invent_one():
    wm = _load_manifest_writer()
    merged = wm.merge_manifest(
        {},
        platform="mac",
        version="1.0.4",
        build=94,
        artifact="AdoptIQ/AdoptIQ-v1.0.4-build94.dmg",
        sha256="cafe",
        size_bytes=0,
    )
    assert "pc" not in merged
    assert merged["mac"]["build"] == 94
    assert merged["build"] == 94
