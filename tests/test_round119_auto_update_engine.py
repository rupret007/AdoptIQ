"""Round 119 / Build 88 -- staging/verify, swapper source-shape, apply_update.

All offline + synthetic: no real DMG mount, no real swap.  ``runner`` and
``spawn`` are injected; ``apply_update``'s TESTING short-circuit guarantees
pytest can never trigger a real self-replace.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import auto_updater


def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def _make_releases(tmp_path, *, build=88, artifact_rel="AdoptIQ/AdoptIQ-v1.0.4-build88.dmg",
                   payload=b"FAKE-DMG-BYTES", key="mac", sha=None):
    releases = tmp_path / "releases"
    art_path = releases / artifact_rel
    art_path.parent.mkdir(parents=True, exist_ok=True)
    art_path.write_bytes(payload)
    digest = sha if sha is not None else _sha256_bytes(payload)
    manifest = {
        "schema": 1, "version": "1.0.4", "build": build, "channel": "stable",
        key: {"build": build, "version": "1.0.4", "artifact": artifact_rel,
              "sha256": digest, "size_bytes": len(payload)},
    }
    (releases / "latest.json").write_text(json.dumps(manifest))
    return releases, manifest


# ---------------------------------------------------------------------------
# stage_and_verify -- sha256 gate
# ---------------------------------------------------------------------------
def test_stage_verify_sha_mismatch_raises(tmp_path):
    releases, manifest = _make_releases(tmp_path, sha="deadbeef" * 8)

    def fake_runner(*a, **k):
        class R: returncode = 0; stdout = "Valid"
        return R()

    with pytest.raises(auto_updater.UpdateError) as ei:
        auto_updater.stage_and_verify(
            manifest, releases_folder=str(releases),
            app_support_dir=str(tmp_path / "support"), key="mac", runner=fake_runner,
        )
    assert ei.value.error_kind == "sha256_mismatch"


def test_stage_verify_artifact_missing_raises(tmp_path):
    releases, manifest = _make_releases(tmp_path)
    # Delete the artifact after manifest is written.
    (releases / manifest["mac"]["artifact"]).unlink()
    with pytest.raises(auto_updater.UpdateError) as ei:
        auto_updater.stage_and_verify(
            manifest, releases_folder=str(releases),
            app_support_dir=str(tmp_path / "support"), key="mac",
            runner=lambda *a, **k: None,
        )
    assert ei.value.error_kind == "artifact_missing"


def test_stage_verify_rejects_traversal_artifact(tmp_path):
    releases, manifest = _make_releases(tmp_path)
    manifest["mac"]["artifact"] = "../../etc/passwd"
    with pytest.raises(auto_updater.UpdateError) as ei:
        auto_updater.stage_and_verify(
            manifest, releases_folder=str(releases),
            app_support_dir=str(tmp_path / "support"), key="mac",
            runner=lambda *a, **k: None,
        )
    assert ei.value.error_kind == "bad_artifact_path"


def test_stage_verify_rejects_absolute_artifact(tmp_path):
    releases, manifest = _make_releases(tmp_path)
    manifest["mac"]["artifact"] = "/etc/passwd"
    with pytest.raises(auto_updater.UpdateError) as ei:
        auto_updater.stage_and_verify(
            manifest, releases_folder=str(releases),
            app_support_dir=str(tmp_path / "support"), key="mac",
            runner=lambda *a, **k: None,
        )
    assert ei.value.error_kind == "bad_artifact_path"


def test_stage_verify_mac_codesign_gate(tmp_path, monkeypatch):
    """A failing codesign --verify MUST raise codesign_failed (no swap)."""
    releases, manifest = _make_releases(tmp_path)
    calls = []

    def fake_runner(argv, **k):
        calls.append(argv[0])

        class R:
            # hdiutil attach / ditto succeed; codesign fails.
            returncode = 1 if argv[0] == "codesign" else 0
            stdout = ""
        return R()

    # Make the mounted DMG appear to contain AdoptIQ.app + ditto produce it.
    real_mkdtemp = auto_updater.tempfile.mkdtemp

    def fake_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        (Path(d) / "AdoptIQ.app").mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(auto_updater.tempfile, "mkdtemp", fake_mkdtemp)
    # ditto is faked via runner; emulate it by creating the staged app dir.
    import shutil as _sh
    orig_copy = _sh.copy2

    with pytest.raises(auto_updater.UpdateError) as ei:
        auto_updater.stage_and_verify(
            manifest, releases_folder=str(releases),
            app_support_dir=str(tmp_path / "support"), key="mac", runner=fake_runner,
        )
    assert ei.value.error_kind in ("codesign_failed", "dmg_copy_failed", "dmg_no_app")
    assert "codesign" in calls or "ditto" in calls


# ---------------------------------------------------------------------------
# swapper source-shape
# ---------------------------------------------------------------------------
def test_swapper_macos_shape(tmp_path):
    sw = auto_updater.write_swapper_macos(
        pid=4242, staged_app=Path("/stage/AdoptIQ.app"),
        target_app=Path("/Applications/AdoptIQ.app"),
        swapper_dir=tmp_path,
    )
    body = Path(sw).read_text()
    assert "4242" in body            # waits on our pid
    assert "kill -0" in body         # poll-for-exit loop
    assert "ditto" in body           # install
    assert ".old" in body            # rollback copy kept
    assert "open " in body           # relaunch
    assert "rm -f \"$0\"" in body    # self-delete
    # Lives outside the bundle (in the injected swapper dir).
    assert str(tmp_path) in str(sw)
    # Executable bit set.
    assert Path(sw).stat().st_mode & 0o100


def test_swapper_windows_shape(tmp_path):
    sw = auto_updater.write_swapper_windows(
        pid=909, staged_exe=Path("C:/stage/AdoptIQ.exe"),
        target_exe=Path("C:/Program Files/AdoptIQ/AdoptIQ.exe"),
        swapper_dir=tmp_path,
    )
    body = Path(sw).read_text()
    assert "909" in body
    assert "tasklist" in body            # poll-for-exit
    assert "copy /Y" in body             # install
    assert ".old" in body                # rollback
    assert "start " in body              # relaunch
    assert "del /F /Q" in body           # self-delete + cleanup
    assert str(tmp_path) in str(sw)


# ---------------------------------------------------------------------------
# apply_update orchestration
# ---------------------------------------------------------------------------
def test_apply_update_testing_short_circuits(tmp_path):
    releases, manifest = _make_releases(tmp_path, build=99)
    spawned = []
    res = auto_updater.apply_update(
        manifest, releases_folder=str(releases), current_build=87, key="mac",
        spawn=lambda argv: spawned.append(argv), testing=True,
    )
    assert res["ok"] is True
    assert res["state"] == "would_update"
    assert res["latest_build"] == 99
    assert spawned == []  # never spawned a real swapper


def test_apply_update_no_update_returns_notify(tmp_path):
    releases, manifest = _make_releases(tmp_path, build=80)
    res = auto_updater.apply_update(
        manifest, releases_folder=str(releases), current_build=87, key="mac",
        testing=True,
    )
    assert res["ok"] is False
    assert res["state"] == "notify"
    assert res["error_kind"] == "no_update"


def test_apply_update_busy_defers(tmp_path):
    releases, manifest = _make_releases(tmp_path, build=99)
    res = auto_updater.apply_update(
        manifest, releases_folder=str(releases), current_build=87, key="mac",
        is_busy=lambda: True, testing=True,
    )
    assert res["ok"] is False
    assert res["state"] == "busy"


def test_apply_update_busy_check_failure_defers(tmp_path):
    releases, manifest = _make_releases(tmp_path, build=99)

    def broken_busy():
        raise RuntimeError("status map locked")

    res = auto_updater.apply_update(
        manifest, releases_folder=str(releases), current_build=87, key="mac",
        is_busy=broken_busy, testing=True,
    )
    assert res["state"] == "busy"
    assert res["error_kind"] == "busy_check_failed"


def test_apply_update_not_frozen_dev_falls_back(tmp_path, monkeypatch):
    releases, manifest = _make_releases(tmp_path, build=99)
    monkeypatch.setattr(auto_updater, "is_frozen", lambda: False)
    res = auto_updater.apply_update(
        manifest, releases_folder=str(releases), current_build=87, key="mac",
        testing=False,
    )
    assert res["ok"] is False
    assert res["state"] == "notify"
    assert res["error_kind"] == "not_frozen"


def test_apply_update_full_path_spawns_and_shuts_down(tmp_path, monkeypatch):
    """Verified path: stage+verify mocked OK -> swapper spawned + shutdown fired."""
    releases, manifest = _make_releases(tmp_path, build=99)
    monkeypatch.setattr(auto_updater, "is_frozen", lambda: True)
    monkeypatch.setattr(auto_updater, "current_install_root",
                        lambda: Path("/Applications/AdoptIQ.app"))
    staged = tmp_path / "support" / "updates" / "AdoptIQ.app"
    staged.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(auto_updater, "stage_and_verify", lambda *a, **k: staged)

    spawned = []
    shut = []
    res = auto_updater.apply_update(
        manifest, releases_folder=str(releases),
        app_support_dir=str(tmp_path / "support"), current_build=87, key="mac",
        spawn=lambda argv: spawned.append(argv),
        trigger_shutdown=lambda: shut.append(True),
        testing=False,
    )
    assert res["ok"] is True
    assert res["state"] == "applying"
    assert len(spawned) == 1
    assert shut == [True]


def test_apply_update_stage_failure_falls_back_to_notify(tmp_path, monkeypatch):
    """Any UpdateError in stage_and_verify -> notify fallback, never a swap."""
    releases, manifest = _make_releases(tmp_path, build=99)
    monkeypatch.setattr(auto_updater, "is_frozen", lambda: True)
    monkeypatch.setattr(auto_updater, "current_install_root",
                        lambda: Path("/Applications/AdoptIQ.app"))

    def boom(*a, **k):
        raise auto_updater.UpdateError("disk full", error_kind="copy_failed")

    monkeypatch.setattr(auto_updater, "stage_and_verify", boom)
    spawned = []
    res = auto_updater.apply_update(
        manifest, releases_folder=str(releases),
        app_support_dir=str(tmp_path / "support"), current_build=87, key="mac",
        spawn=lambda argv: spawned.append(argv), testing=False,
    )
    assert res["ok"] is False
    assert res["state"] == "notify"
    assert res["error_kind"] == "copy_failed"
    assert spawned == []  # never spawned


def test_apply_update_unexpected_exception_falls_back(tmp_path, monkeypatch):
    releases, manifest = _make_releases(tmp_path, build=99)
    monkeypatch.setattr(auto_updater, "is_frozen", lambda: True)
    monkeypatch.setattr(auto_updater, "current_install_root",
                        lambda: Path("/Applications/AdoptIQ.app"))

    def kaboom(*a, **k):
        raise ValueError("totally unexpected")

    monkeypatch.setattr(auto_updater, "stage_and_verify", kaboom)
    res = auto_updater.apply_update(
        manifest, releases_folder=str(releases),
        app_support_dir=str(tmp_path / "support"), current_build=87, key="mac",
        testing=False,
    )
    assert res["state"] == "notify"
    assert res["error_kind"] == "unexpected"
