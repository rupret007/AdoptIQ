"""Round 119 / Build 88 -- manifest writer + read/validate + build-compare.

Offline coverage for:
  * scripts/write_release_manifest.py merge logic (mac/pc slots, atomic write,
    top-level build = max, bad-build raise, corrupt-existing fallback).
  * auto_updater.read_latest_manifest / validate_manifest (schema gate).
  * auto_updater.is_update_available (>, ==, <, channel mismatch, missing slot,
    non-numeric build).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_writer():
    """Import scripts/write_release_manifest.py as a module (flat repo, no pkg)."""
    path = PROJECT_ROOT / "scripts" / "write_release_manifest.py"
    spec = importlib.util.spec_from_file_location("write_release_manifest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def writer():
    return _load_writer()


# ---------------------------------------------------------------------------
# scripts/write_release_manifest.py
# ---------------------------------------------------------------------------
def test_merge_creates_mac_slot(writer):
    m = writer.merge_manifest({}, platform="mac", version="1.0.4", build=88,
                              artifact="AdoptIQ/AdoptIQ-v1.0.4-build88.dmg",
                              sha256="ABC123", size_bytes=10)
    assert m["schema"] == writer.SCHEMA_VERSION
    assert m["mac"]["build"] == 88
    assert m["mac"]["artifact"] == "AdoptIQ/AdoptIQ-v1.0.4-build88.dmg"
    assert m["mac"]["sha256"] == "abc123"  # lower-cased
    assert m["build"] == 88  # top-level max
    assert m["channel"] == "stable"


def test_merge_preserves_other_platform_slot(writer):
    """Writing the mac slot MUST NOT clobber an existing pc slot."""
    existing = writer.merge_manifest({}, platform="pc", version="1.0.4", build=87,
                                     artifact="AdoptIQ_PC/AdoptIQ-v1.0.4-build87.exe",
                                     sha256="deadbeef", size_bytes=5)
    merged = writer.merge_manifest(existing, platform="mac", version="1.0.4", build=88,
                                   artifact="AdoptIQ/AdoptIQ-v1.0.4-build88.dmg",
                                   sha256="cafef00d", size_bytes=9)
    assert merged["pc"]["build"] == 87
    assert merged["pc"]["artifact"] == "AdoptIQ_PC/AdoptIQ-v1.0.4-build87.exe"
    assert merged["mac"]["build"] == 88
    # top-level build = max of the two slots.
    assert merged["build"] == 88


def test_merge_top_level_build_is_max(writer):
    existing = writer.merge_manifest({}, platform="mac", version="1.0.4", build=90,
                                     artifact="AdoptIQ/x.dmg", sha256="a", size_bytes=1)
    merged = writer.merge_manifest(existing, platform="pc", version="1.0.4", build=88,
                                   artifact="AdoptIQ_PC/x.exe", sha256="b", size_bytes=1)
    assert merged["build"] == 90


def test_merge_unknown_platform_raises(writer):
    with pytest.raises(ValueError):
        writer.merge_manifest({}, platform="linux", version="1.0.4", build=1,
                              artifact="x", sha256="y", size_bytes=0)


def test_merge_non_integer_build_raises(writer):
    with pytest.raises(ValueError):
        writer.merge_manifest({}, platform="mac", version="1.0.4", build="notanum",
                              artifact="x", sha256="y", size_bytes=0)


def test_write_atomic_round_trip(writer, tmp_path):
    # Dedicated subdir so the autouse settings fixture's home dir does not
    # pollute the leftover-temp-file assertion.
    out = tmp_path / "outbox"
    out.mkdir()
    p = out / "latest.json"
    m = writer.merge_manifest({}, platform="mac", version="1.0.4", build=88,
                              artifact="AdoptIQ/x.dmg", sha256="abc", size_bytes=3)
    writer.write_atomic(str(p), m)
    assert p.is_file()
    loaded = json.loads(p.read_text())
    assert loaded["mac"]["build"] == 88
    # No leftover temp files in the directory (atomic replace cleaned up).
    assert [c.name for c in out.iterdir()] == ["latest.json"]


def test_load_existing_corrupt_returns_empty(writer, tmp_path):
    p = tmp_path / "latest.json"
    p.write_text("{not valid json")
    assert writer.load_existing(str(p)) == {}


def test_load_existing_missing_returns_empty(writer, tmp_path):
    assert writer.load_existing(str(tmp_path / "absent.json")) == {}


def test_main_cli_writes_manifest(writer, tmp_path):
    p = tmp_path / "latest.json"
    rc = writer.main([
        "--manifest", str(p), "--platform", "pc", "--version", "1.0.4",
        "--build", "88", "--artifact", "AdoptIQ_PC/x.exe", "--sha256", "ABCD",
        "--size", "42",
    ])
    assert rc == 0
    data = json.loads(p.read_text())
    assert data["pc"]["build"] == 88
    assert data["pc"]["size_bytes"] == 42


def test_consumer_publication_requires_explicit_approval(writer, tmp_path):
    manifest = tmp_path / "latest.json"
    with pytest.raises(SystemExit):
        writer.main([
            "--manifest", str(manifest), "--platform", "pc",
            "--version", "1.0.4", "--build", "115",
            "--artifact", "AdoptIQ_PC/candidate.exe", "--sha256", "abcd",
            "--size", "42", "--publication-root", str(tmp_path),
        ])
    assert not manifest.exists()


def test_consumer_publication_uses_shared_cross_platform_lock(writer, tmp_path):
    manifest = tmp_path / "latest.json"
    artifact = tmp_path / "AdoptIQ_PC" / "candidate.exe"
    artifact.parent.mkdir()
    artifact.write_bytes(b"candidate")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    lock_dir = tmp_path / ".adoptiq-release-manifest.lock"
    lock_dir.mkdir()
    with pytest.raises(ValueError, match="already locked"):
        writer.main([
            "--manifest", str(manifest), "--platform", "pc",
            "--version", "1.0.4", "--build", "115",
            "--artifact", "AdoptIQ_PC/candidate.exe", "--sha256", digest,
            "--size", str(artifact.stat().st_size), "--publication-root", str(tmp_path),
            "--source-artifact", str(artifact),
            "--publication-approved",
        ])
    assert not manifest.exists()


def test_consumer_publication_releases_lock_after_atomic_write(writer, tmp_path):
    root = tmp_path / "releases"
    (root / "AdoptIQ_PC").mkdir(parents=True)
    manifest = root / "latest.json"
    source = tmp_path / "staged-candidate.exe"
    source.write_bytes(b"candidate")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert writer.main([
        "--manifest", str(manifest), "--platform", "pc",
        "--version", "1.0.4", "--build", "115",
        "--artifact", "AdoptIQ_PC/candidate.exe", "--sha256", digest,
        "--size", str(source.stat().st_size), "--publication-root", str(root),
        "--source-artifact", str(source),
        "--publication-approved",
    ]) == 0
    assert json.loads(manifest.read_text())["pc"]["build"] == 115
    assert (root / "AdoptIQ_PC" / "candidate.exe").read_bytes() == b"candidate"
    assert not (root / ".adoptiq-release-manifest.lock").exists()


def test_consumer_publication_rejects_corrupt_existing_manifest(writer, tmp_path):
    artifact = tmp_path / "AdoptIQ_PC" / "candidate.exe"
    artifact.parent.mkdir()
    artifact.write_bytes(b"candidate")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = tmp_path / "latest.json"
    manifest.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON"):
        writer.main([
            "--manifest", str(manifest), "--platform", "pc",
            "--version", "1.0.4", "--build", "115",
            "--artifact", "AdoptIQ_PC/candidate.exe", "--sha256", digest,
            "--size", str(artifact.stat().st_size),
            "--publication-root", str(tmp_path),
            "--source-artifact", str(artifact), "--publication-approved",
        ])
    assert manifest.read_text(encoding="utf-8") == "{corrupt"


def test_consumer_publication_rejects_stale_or_same_build_different_bytes(
    writer, tmp_path
):
    artifact = tmp_path / "AdoptIQ_PC" / "candidate.exe"
    artifact.parent.mkdir()
    artifact.write_bytes(b"candidate")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = tmp_path / "latest.json"
    manifest.write_text(json.dumps({
        "schema": 1,
        "pc": {
            "build": 116,
            "version": "1.0.4",
            "artifact": "AdoptIQ_PC/newer.exe",
            "sha256": "f" * 64,
            "size_bytes": 99,
        },
    }), encoding="utf-8")
    argv = [
        "--manifest", str(manifest), "--platform", "pc",
        "--version", "1.0.4", "--build", "115",
        "--artifact", "AdoptIQ_PC/candidate.exe", "--sha256", digest,
        "--size", str(artifact.stat().st_size),
        "--publication-root", str(tmp_path),
        "--source-artifact", str(artifact), "--publication-approved",
    ]
    before = manifest.read_bytes()
    with pytest.raises(ValueError, match="newer"):
        writer.main(argv)
    assert manifest.read_bytes() == before

    payload = json.loads(before)
    payload["pc"]["build"] = 115
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    before = manifest.read_bytes()
    with pytest.raises(ValueError, match="different bytes"):
        writer.main(argv)
    assert manifest.read_bytes() == before


# ---------------------------------------------------------------------------
# auto_updater manifest read / validate / availability
# ---------------------------------------------------------------------------
def test_validate_manifest_rejects_non_dict():
    import auto_updater
    with pytest.raises(auto_updater.UpdateError):
        auto_updater.validate_manifest(["not", "a", "dict"])


def test_validate_manifest_rejects_bad_schema():
    import auto_updater
    with pytest.raises(auto_updater.UpdateError):
        auto_updater.validate_manifest({"schema": 999, "build": 88})


def test_validate_manifest_rejects_missing_build():
    import auto_updater
    with pytest.raises(auto_updater.UpdateError):
        auto_updater.validate_manifest({"schema": 1})


def test_validate_manifest_accepts_good():
    import auto_updater
    data = {"schema": 1, "build": 88}
    assert auto_updater.validate_manifest(data) is data


def _manifest(build_mac=88, channel="stable"):
    return {
        "schema": 1, "version": "1.0.4", "build": build_mac, "channel": channel,
        "mac": {"build": build_mac, "version": "1.0.4",
                "artifact": "AdoptIQ/x.dmg", "sha256": "abc", "size_bytes": 1},
    }


def test_is_update_available_newer():
    import auto_updater
    assert auto_updater.is_update_available(_manifest(90), current_build=87, key="mac") is True


def test_is_update_available_equal_false():
    import auto_updater
    assert auto_updater.is_update_available(_manifest(87), current_build=87, key="mac") is False


def test_is_update_available_older_false():
    import auto_updater
    assert auto_updater.is_update_available(_manifest(80), current_build=87, key="mac") is False


def test_is_update_available_channel_mismatch_false():
    import auto_updater
    m = _manifest(99, channel="beta")
    assert auto_updater.is_update_available(m, current_build=87, key="mac") is False


def test_is_update_available_missing_slot_false():
    import auto_updater
    m = _manifest(99)
    assert auto_updater.is_update_available(m, current_build=87, key="pc") is False


def test_is_update_available_non_numeric_build_false():
    import auto_updater
    m = _manifest(99)
    m["mac"]["build"] = "lots"
    assert auto_updater.is_update_available(m, current_build=87, key="mac") is False


def test_is_update_available_no_artifact_false():
    import auto_updater
    m = _manifest(99)
    m["mac"]["artifact"] = ""
    assert auto_updater.is_update_available(m, current_build=87, key="mac") is False


def test_is_update_available_none_manifest_false():
    import auto_updater
    assert auto_updater.is_update_available(None, current_build=87, key="mac") is False


def test_read_latest_manifest_missing_folder_returns_none(tmp_path):
    import auto_updater
    missing = tmp_path / "no_such_releases"
    assert auto_updater.read_latest_manifest(str(missing)) is None


def test_read_latest_manifest_corrupt_raises(tmp_path):
    import auto_updater
    (tmp_path / "latest.json").write_text("{bad json")
    with pytest.raises(auto_updater.UpdateError):
        auto_updater.read_latest_manifest(str(tmp_path))


def test_read_latest_manifest_valid(tmp_path):
    import auto_updater
    (tmp_path / "latest.json").write_text(json.dumps(_manifest(88)))
    m = auto_updater.read_latest_manifest(str(tmp_path))
    assert m["build"] == 88
