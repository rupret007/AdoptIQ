"""Round 115 / Build 84: stale-nano report-model re-migration.

Build 82/83 acceptance found installs pinned to ``report_model_name=
gpt-5-nano`` while BOTH ``r103_model_default_migrated`` and
``r108_model_default_migrated`` were already ``True`` -- so the earlier
migrations early-returned and the resolver treated the stale value as a
deliberate selection, leaving report narratives on nano across DMG
upgrades.  R115 re-stomps that exact value once more, keyed to a new
marker, while preserving a genuinely-deliberate post-R115 nano choice.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GEMINI = "gemini-3.1-flash-lite"
STALE = "gpt-5-nano"


def _read(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


def test_r115_flips_stale_nano_when_both_prior_markers_set(monkeypatch, tmp_path):
    """The exact dev-box / upgraded-install state: r103 + r108 set, nano pinned."""
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "r103_model_default_migrated": True,
        "r108_model_default_migrated": True,
        "ask_ai_model_name": "",
        "report_model_name": STALE,
    })

    assert _s.migrate_round115_model_defaults() is True
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["report_model_name"] == GEMINI
    assert on_disk["ask_ai_model_name"] == ""  # empty sentinel untouched
    assert on_disk["r115_model_default_migrated"] is True


def test_r115_is_idempotent_once_marker_set(monkeypatch, tmp_path):
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({"report_model_name": STALE})

    assert _s.migrate_round115_model_defaults() is True
    # Second run: marker is set, so the migration no-ops (returns False)
    # even though a later deliberate nano write would survive.
    assert _s.migrate_round115_model_defaults() is False


def test_r115_leaves_non_stale_values_untouched(monkeypatch, tmp_path):
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "ask_ai_model_name": "gemini-3.1-flash-lite",
        "report_model_name": "gemini-2.5-pro",
    })

    # No stale value present -> changed_model is False, but the marker is
    # still stamped so the migration does not re-run.
    assert _s.migrate_round115_model_defaults() is False
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["ask_ai_model_name"] == "gemini-3.1-flash-lite"
    assert on_disk["report_model_name"] == "gemini-2.5-pro"
    assert on_disk["r115_model_default_migrated"] is True


def test_r115_deliberate_nano_after_migration_survives(monkeypatch, tmp_path):
    """A DELIBERATE nano pick survives — but under the Round 116 contract a
    deliberate pick is keyed on the ``report_model_user_set`` flag, NOT the
    one-time marker.  Without ``user_set`` the value is treated as leftover
    and heals to Gemini (covered by the R116 suite); WITH it, nano persists.
    """
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "r103_model_default_migrated": True,
        "r108_model_default_migrated": True,
        "r115_model_default_migrated": True,
        "report_model_name": STALE,
        "report_model_user_set": True,  # Round 116: deliberate operator pick
    })

    import model_resolver as _mr

    importlib.reload(_mr)
    assert _mr.get_active_report_model() == STALE


def test_r115_resolver_flips_pinned_nano_to_gemini(monkeypatch, tmp_path):
    """End-to-end: resolver runs all migrations and report model becomes Gemini."""
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "r103_model_default_migrated": True,
        "r108_model_default_migrated": True,
        "report_model_name": STALE,
    })

    import model_resolver as _mr

    importlib.reload(_mr)
    assert _mr.get_active_report_model() == GEMINI
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["r115_model_default_migrated"] is True


def test_r115_ensure_runs_all_three_migrations(monkeypatch, tmp_path):
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "r103_model_default_migrated": True,
        "r108_model_default_migrated": True,
        "report_model_name": STALE,
    })

    assert _s.ensure_model_defaults_migrated() is True
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["report_model_name"] == GEMINI
    assert on_disk["r115_model_default_migrated"] is True


def test_r115_schema_and_exports_present():
    import adoptiq_settings as _s

    assert "r115_model_default_migrated" in _s._SCHEMA
    assert _s._SCHEMA["r115_model_default_migrated"][0] is bool
    assert "migrate_round115_model_defaults" in _s.__all__
    assert _s._R115_MODEL_MIGRATION_KEY == "r115_model_default_migrated"


def test_r115_source_markers_present():
    src = _read("adoptiq_settings.py")
    assert "Round 115 / Build 84" in src
    assert "migrate_round115_model_defaults" in src
    resolver_src = _read("model_resolver.py")
    assert "R115" in resolver_src
