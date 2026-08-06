"""Round 116 / Build 85: robust model heal — Gemini-by-default, nano = opt-in.

Build 84 acceptance found ``settings.json`` wedged at
``report_model_name=gpt-5-nano`` with ALL THREE one-time migration markers
(``r103_model_default_migrated`` / ``r108_model_default_migrated`` /
``r115_model_default_migrated``) already ``True`` — a state no marker-gated
migration could ever heal (each early-returns once its marker is stamped).

The robust fix retires reliance on per-round markers for coercion and keys
"deliberate nano" on a NEW pair of flags (``report_model_user_set`` /
``ask_ai_model_user_set``) set ONLY by the Preferences-dropdown POST handler:

* A stale ``gpt-5-nano`` with ``user_set`` False (or absent) heals to the
  Gemini default — both at read time (``model_resolver`` runtime coercion)
  AND on disk (``migrate_round116_model_user_set`` condition-driven heal).
* A ``gpt-5-nano`` with ``user_set`` True (a deliberate post-Build-85 pick)
  is honored — it never heals.
* Clearing the override (empty string) resets ``user_set`` to False so a
  later stale value can heal again.
"""

# Round 116 / Build 85

from __future__ import annotations
from source_shape_utils import assert_in_source

import importlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GEMINI = "gemini-3.1-flash-lite"
STALE = "gpt-5-nano"


def _read(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


def _clear_env(monkeypatch):
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)


# ---------------------------------------------------------------------------
# Section 1: schema + helper surface
# ---------------------------------------------------------------------------


def test_r116_schema_keys_present():
    import adoptiq_settings as _s

    for key in ("report_model_user_set", "ask_ai_model_user_set"):
        assert key in _s._SCHEMA, f"Round 116: {key} missing from _SCHEMA"
        assert _s._SCHEMA[key][0] is bool
        assert _s._SCHEMA[key][1] is False  # defaults to "not user-set"


def test_r116_user_set_key_map_and_exports():
    import adoptiq_settings as _s

    assert _s._R116_MODEL_USER_SET_KEYS == {
        "ask_ai_model_name": "ask_ai_model_user_set",
        "report_model_name": "report_model_user_set",
    }
    assert "is_user_set" in _s.__all__
    assert "migrate_round116_model_user_set" in _s.__all__


def test_r116_is_user_set_helper(monkeypatch, tmp_path):
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    # Default (no flag) => not user-set.
    _s.save_settings({"report_model_name": STALE})
    assert _s.is_user_set("report_model_name") is False
    # Flag set => user-set.
    _s.save_settings({"report_model_name": STALE, "report_model_user_set": True})
    assert _s.is_user_set("report_model_name") is True
    # Unknown key => False (never crashes).
    assert _s.is_user_set("not_a_model_key") is False


# ---------------------------------------------------------------------------
# Section 2: the wedge — all markers True, nano pinned, no user_set
# ---------------------------------------------------------------------------


def test_r116_wedged_nano_heals_to_gemini_at_resolver(monkeypatch, tmp_path):
    """The exact Build 84 wedge: report_model_name=nano with R103+R108+R115
    markers all True and NO user_set flag.  The resolver MUST return Gemini."""
    _clear_env(monkeypatch)
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "r103_model_default_migrated": True,
        "r108_model_default_migrated": True,
        "r115_model_default_migrated": True,
        "report_model_name": STALE,
    })

    import model_resolver as _mr

    importlib.reload(_mr)
    assert _mr.get_active_report_model() == GEMINI


def test_r116_wedged_nano_self_heals_on_disk(monkeypatch, tmp_path):
    """The condition-driven migration clears the stale value on disk so the
    persisted file + Preferences dropdown also reflect the heal."""
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "r103_model_default_migrated": True,
        "r108_model_default_migrated": True,
        "r115_model_default_migrated": True,
        "report_model_name": STALE,
        "ask_ai_model_name": STALE,
    })

    assert _s.migrate_round116_model_user_set() is True
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["report_model_name"] == ""
    assert on_disk["ask_ai_model_name"] == ""


def test_r116_self_heal_is_idempotent(monkeypatch, tmp_path):
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({"report_model_name": STALE})
    assert _s.migrate_round116_model_user_set() is True
    # Second run: value already healed -> no change.
    assert _s.migrate_round116_model_user_set() is False


def test_r116_ensure_runs_the_self_heal(monkeypatch, tmp_path):
    """The condition-driven heal MUST be wired into ensure_model_defaults_migrated."""
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "r103_model_default_migrated": True,
        "r108_model_default_migrated": True,
        "r115_model_default_migrated": True,
        "report_model_name": STALE,
    })
    assert _s.ensure_model_defaults_migrated() is True
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["report_model_name"] == ""


# ---------------------------------------------------------------------------
# Section 3: deliberate nano (user_set True) is honored
# ---------------------------------------------------------------------------


def test_r116_deliberate_nano_survives_resolver(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "report_model_name": STALE,
        "report_model_user_set": True,
    })

    import model_resolver as _mr

    importlib.reload(_mr)
    assert _mr.get_active_report_model() == STALE


def test_r116_deliberate_nano_survives_self_heal(monkeypatch, tmp_path):
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "report_model_name": STALE,
        "report_model_user_set": True,
        "ask_ai_model_name": STALE,  # ask-ai NOT user-set -> should heal
    })
    assert _s.migrate_round116_model_user_set() is True
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    # report nano is deliberate -> kept; ask-ai nano leftover -> healed.
    assert on_disk["report_model_name"] == STALE
    assert on_disk["ask_ai_model_name"] == ""


def test_r116_deliberate_nano_survives_marker_migration(monkeypatch, tmp_path):
    """The R103/R108/R115 marker migrations MUST also respect user_set so a
    deliberate nano is never flipped when its marker happens to be absent."""
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    # r115 marker absent -> R115 migration would otherwise flip nano.
    _s.save_settings({
        "r103_model_default_migrated": True,
        "r108_model_default_migrated": True,
        "report_model_name": STALE,
        "report_model_user_set": True,
    })
    _s.migrate_round115_model_defaults()
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["report_model_name"] == STALE  # honored, not flipped


# ---------------------------------------------------------------------------
# Section 4: cleared override resets the flag and re-heals
# ---------------------------------------------------------------------------


def test_r116_clearing_override_resets_flag_via_post(monkeypatch, tmp_path):
    """POSTing an empty model_name clears the override AND resets user_set to
    False so a later stale value heals again.  Exercises the real Flask
    handler so the flag-write wiring is covered end-to-end."""
    _clear_env(monkeypatch)
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)

    import app_simple

    app_simple.app.config["TESTING"] = True
    client = app_simple.app.test_client()

    # 1) Operator picks nano -> user_set True.
    monkeypatch.setattr(app_simple, "_r17_2_authorize_corpus_admin", lambda: None)
    # /api/llm/ping is not exercised here; the handler validates + persists.
    resp = client.post("/api/settings/report-model", json={"model_name": STALE})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _s.is_user_set("report_model_name") is True
    assert _s.load_settings().get("report_model_name") == STALE

    # 2) Operator clears the override -> user_set reset to False.
    resp = client.post("/api/settings/report-model", json={"model_name": ""})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _s.is_user_set("report_model_name") is False


def test_r116_post_sets_user_set_true(monkeypatch, tmp_path):
    """A successful non-empty POST persists ``report_model_user_set=True``."""
    _clear_env(monkeypatch)
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    import app_simple

    app_simple.app.config["TESTING"] = True
    monkeypatch.setattr(app_simple, "_r17_2_authorize_corpus_admin", lambda: None)
    client = app_simple.app.test_client()
    resp = client.post("/api/settings/ask-ai-model", json={"model_name": GEMINI})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    on_disk = _s.load_settings()
    assert on_disk.get("ask_ai_model_user_set") is True
    assert on_disk.get("ask_ai_model_name") == GEMINI


# ---------------------------------------------------------------------------
# Section 5: source markers
# ---------------------------------------------------------------------------


def test_r116_source_markers_present():
    settings_src = _read("adoptiq_settings.py")
    assert "Round 116 / Build 85" in settings_src
    assert "migrate_round116_model_user_set" in settings_src
    assert "_R116_MODEL_USER_SET_KEYS" in settings_src

    resolver_src = _read("model_resolver.py")
    assert "_r116_coerce_settings_value" in resolver_src
    assert "Round 116 / Build 85" in resolver_src

    app_src = _read("app_simple.py")
    assert_in_source(app_src, "_R116_MODEL_USER_SET_KEYS", label='app_src')
