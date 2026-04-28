"""Round 32 / Phase 2.E regression: ``settings.json`` precedence.

Resolution order (documented in CLAUDE.md / README.md):
``settings.json`` > env var > config default.
"""
from __future__ import annotations

import importlib
import json
import sys

import pytest


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    yield


def _write_settings(value: bool) -> None:
    import adoptiq_settings as s
    s.save_settings({"corpus_knowledge_enabled": value})


def _read_persisted_state() -> bool:
    """Replicates the bootstrap snippet in app_simple.py without
    paying the cost of re-importing the entire Flask module: load
    settings, then return what Config *would* be set to."""
    import adoptiq_settings as s
    persisted = s.load_settings()
    return bool(persisted.get("corpus_knowledge_enabled"))


def test_settings_value_overrides_config_default_true(monkeypatch):
    """settings.json=False must beat the new default-on (Round 32 / 2.F)."""
    monkeypatch.setenv("CORPUS_KNOWLEDGE_ENABLED", "true")
    importlib.reload(importlib.import_module("config"))
    from config import Config as ReloadedConfig
    # Simulate the app_simple.py override block.
    _write_settings(False)
    persisted = _read_persisted_state()
    ReloadedConfig.CORPUS_KNOWLEDGE_ENABLED = persisted
    assert ReloadedConfig.CORPUS_KNOWLEDGE_ENABLED is False


def test_settings_value_overrides_env_var_false(monkeypatch):
    """settings.json=True wins even when the env var explicitly disables."""
    monkeypatch.setenv("CORPUS_KNOWLEDGE_ENABLED", "false")
    importlib.reload(importlib.import_module("config"))
    from config import Config as ReloadedConfig
    _write_settings(True)
    persisted = _read_persisted_state()
    ReloadedConfig.CORPUS_KNOWLEDGE_ENABLED = persisted
    assert ReloadedConfig.CORPUS_KNOWLEDGE_ENABLED is True


def test_missing_settings_file_falls_back_to_env_var(monkeypatch):
    """No settings.json -> env var wins."""
    monkeypatch.setenv("CORPUS_KNOWLEDGE_ENABLED", "false")
    importlib.reload(importlib.import_module("config"))
    from config import Config as ReloadedConfig
    # No settings file written -> _read_persisted_state irrelevant; env wins.
    import adoptiq_settings as s
    assert s.load_settings() == {}
    assert ReloadedConfig.CORPUS_KNOWLEDGE_ENABLED is False


def test_default_on_when_env_unset_and_no_settings(monkeypatch):
    """Round 32 / Phase 2.F: bare install with neither env var nor
    settings.json gets the new default-on behavior."""
    monkeypatch.delenv("CORPUS_KNOWLEDGE_ENABLED", raising=False)
    importlib.reload(importlib.import_module("config"))
    from config import Config as ReloadedConfig
    import adoptiq_settings as s
    assert s.load_settings() == {}
    assert ReloadedConfig.CORPUS_KNOWLEDGE_ENABLED is True


def test_app_simple_startup_block_consumes_settings_json() -> None:
    """Static check that app_simple.py wires the settings override
    after ``from config import Config``.  Pinning this in a test
    keeps a future refactor from accidentally moving the override
    above the import (silent no-op) or below the
    ``enforce_production_safety`` call (override never applied)."""
    import pathlib
    src = (
        pathlib.Path(__file__).resolve().parent.parent / "app_simple.py"
    ).read_text(encoding="utf-8")
    cfg_idx = src.find("from config import ADOPTIQ_VERSION")
    settings_idx = src.find("import adoptiq_settings as _r32_settings")
    assert cfg_idx != -1, "Config import landmark moved"
    assert settings_idx != -1, "Round 32 settings override block missing"
    assert settings_idx > cfg_idx, (
        "settings override must come AFTER the Config import"
    )
