"""Round 32 / Phase 2.E regression for ``adoptiq_settings``: persistent
settings live under the platform Application Support directory with
strict permissions and atomic writes.
"""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest

import adoptiq_settings as s


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Re-route the settings dir into a per-test tmp_path so we never
    touch the developer's real settings.json."""
    monkeypatch.setenv("HOME", str(tmp_path))
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    yield


def test_default_load_is_empty_when_no_file_exists() -> None:
    assert s.load_settings() == {}


def test_round_trip_preserves_allow_listed_keys() -> None:
    s.save_settings({"corpus_knowledge_enabled": True})
    assert s.load_settings() == {"corpus_knowledge_enabled": True}
    s.save_settings({"corpus_knowledge_enabled": False})
    assert s.load_settings() == {"corpus_knowledge_enabled": False}


def test_unknown_keys_are_dropped_on_save() -> None:
    s.save_settings({
        "corpus_knowledge_enabled": True,
        "evil_unknown_key": "should be dropped",
        "another_one": 42,
    })
    on_disk = json.loads(s._settings_path().read_text(encoding="utf-8"))
    assert on_disk == {"corpus_knowledge_enabled": True}


def test_file_mode_is_0600() -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX-mode permissions don't apply on Windows")
    s.save_settings({"corpus_knowledge_enabled": True})
    mode = stat.S_IMODE(os.stat(s._settings_path()).st_mode)
    assert mode == 0o600, oct(mode)


def test_parent_directory_mode_is_0700() -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX-mode permissions don't apply on Windows")
    s.save_settings({"corpus_knowledge_enabled": True})
    parent_mode = stat.S_IMODE(os.stat(s._settings_path().parent).st_mode)
    assert parent_mode == 0o700, oct(parent_mode)


def test_atomic_write_no_temp_files_on_disk_after_success(tmp_path) -> None:
    s.save_settings({"corpus_knowledge_enabled": True})
    parent = s._settings_path().parent
    leftovers = [p.name for p in parent.iterdir() if p.name.startswith(".settings.")]
    assert leftovers == [], leftovers


def test_malformed_json_returns_empty_without_raising() -> None:
    parent = s._app_support_dir()
    parent.mkdir(parents=True, exist_ok=True)
    (parent / s.SETTINGS_FILENAME).write_text("not json {", encoding="utf-8")
    assert s.load_settings() == {}


def test_non_object_top_level_returns_empty() -> None:
    parent = s._app_support_dir()
    parent.mkdir(parents=True, exist_ok=True)
    (parent / s.SETTINGS_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
    assert s.load_settings() == {}


def test_get_returns_default_for_unknown_key() -> None:
    assert s.get("not_a_real_key", default="sentinel") == "sentinel"


def test_set_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        s.set("not_a_real_key", True)


def test_set_single_key_preserves_other_values() -> None:
    s.save_settings({"corpus_knowledge_enabled": True})
    s.set("corpus_knowledge_enabled", False)
    assert s.load_settings() == {"corpus_knowledge_enabled": False}


def test_schema_keys_returns_tuple_of_known_keys() -> None:
    keys = s.schema_keys()
    assert isinstance(keys, tuple)
    assert "corpus_knowledge_enabled" in keys
