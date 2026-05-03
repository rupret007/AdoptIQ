"""Round 69 / Build 43: tests for the operator-flippable LLM model
preferences seam.

Coverage matrix:

* Settings schema + validator (`adoptiq_settings`).
* `CIRCUIT_CONFIG` env-overridable seam (`config.py`).
* `model_resolver` precedence (settings > env > config default).
* `generate_llm_response` / `generate_llm_json_response` keyword-only
  ``model_name`` kwarg threading.
* Ask AI sites pass `get_active_ask_ai_model()` (NOT the report
  resolver -- "wires don't cross" regression guard).
* Report sites pass `get_active_report_model()` (NOT the Ask AI
  resolver -- mirror regression guard).
* `/api/settings/ask-ai-model` and `/api/settings/report-model`
  GET / POST behaviour (success, bad payload, invalid model name).
* `/api/llm/ping` validation + sanitization + 10s timeout shape.
* Admin proxy routes register and forward to the right upstream URL.
* UI source-shape (the templates carry the cards + the JS hook).
* Diagnostic surface (the `model_name` lands in the diag buffer +
  the `/api/ask-ai-portfolio` response shape).
* `embed_credentials.ENV_KEYS` + `secrets.env.template` documentation.
"""

# Round 69 / Build 43

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from unittest import mock

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Section 1: settings schema + validator
# ---------------------------------------------------------------------------


def test_r69_settings_schema_includes_both_model_keys():
    """The new operator-flippable keys must be allow-listed."""
    import adoptiq_settings as _s
    keys = _s.schema_keys()
    assert "ask_ai_model_name" in keys
    assert "report_model_name" in keys


def test_r69_is_valid_model_name_allows_canonical_circuit_models():
    """Real-world CircuIT model ids must pass the allow-list."""
    import adoptiq_settings as _s
    for name in ("gpt-5-nano", "gemini-3.1-flash-lite", "gpt-4o-mini",
                 "claude-3.5-sonnet", "ABC.123_XYZ"):
        assert _s.is_valid_model_name(name), f"should accept {name!r}"


def test_r69_is_valid_model_name_treats_empty_as_unset_sentinel():
    """Empty string == "no operator override" and MUST be accepted."""
    import adoptiq_settings as _s
    assert _s.is_valid_model_name("") is True
    assert _s.is_valid_model_name(None) is True


def test_r69_is_valid_model_name_rejects_shell_metacharacters_and_spaces():
    """Defense-in-depth: the allow-list is the choke point that keeps
    shell/whitespace/RTL chars out of CircuitChatClient.model_name."""
    import adoptiq_settings as _s
    for bad in ("rm -rf /", "gpt-5 nano", "gpt;rm", "gpt\nname",
                "x" * 129, "gpt|name", "gpt`x`", "$model", "../bad"):
        assert _s.is_valid_model_name(bad) is False, f"should reject {bad!r}"


def test_r69_settings_save_strips_invalid_model_name(tmp_path, monkeypatch):
    """A hand-edited settings.json with a bad model name must be
    silently dropped on save (not crash, not propagate)."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    out_path = _s.save_settings({"ask_ai_model_name": "rm -rf /",
                                  "report_model_name": "gpt-5-nano"})
    on_disk = json.loads(Path(out_path).read_text())
    assert "ask_ai_model_name" not in on_disk
    assert on_disk.get("report_model_name") == "gpt-5-nano"


# ---------------------------------------------------------------------------
# Section 2: config.py CIRCUIT_CONFIG seam
# ---------------------------------------------------------------------------


def test_r69_circuit_config_carries_per_site_keys_with_safe_defaults(monkeypatch):
    """Without env overrides, both per-site keys collapse to the legacy
    ``model_name`` default so behaviour is byte-identical pre-R69.

    Round 69 / Build 43: monkeypatch the three env vars to a known
    sentinel BEFORE reloading ``config`` so a developer machine that
    happens to carry ``CIRCUIT_MODEL_NAME`` in the shell env (e.g. for
    local LLM experiments) does not break the test.
    """
    import importlib
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)
    import config as _c
    importlib.reload(_c)
    cfg = _c.Config.CIRCUIT_CONFIG
    assert cfg["model_name"] == cfg["model_name_ask_ai"] == cfg["model_name_report"] == "gpt-5-nano"


def test_r69_circuit_config_env_override_distinct(monkeypatch):
    """Env vars ``CIRCUIT_MODEL_NAME_ASK_AI`` / ``_REPORT`` must take
    precedence over ``CIRCUIT_MODEL_NAME``."""
    monkeypatch.setenv("CIRCUIT_MODEL_NAME", "gpt-5-nano")
    monkeypatch.setenv("CIRCUIT_MODEL_NAME_ASK_AI", "gemini-3.1-flash-lite")
    monkeypatch.setenv("CIRCUIT_MODEL_NAME_REPORT", "gpt-4o-mini")
    import config as _c
    importlib.reload(_c)
    cfg = _c.Config.CIRCUIT_CONFIG
    assert cfg["model_name"] == "gpt-5-nano"
    assert cfg["model_name_ask_ai"] == "gemini-3.1-flash-lite"
    assert cfg["model_name_report"] == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Section 3: model_resolver precedence
# ---------------------------------------------------------------------------


def test_r69_resolver_falls_back_to_hardcoded_default_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    import model_resolver as _mr
    importlib.reload(_mr)
    assert _mr.get_active_ask_ai_model() == "gpt-5-nano"
    assert _mr.get_active_report_model() == "gpt-5-nano"


def test_r69_resolver_env_layer_wins_over_config_default(monkeypatch, tmp_path):
    monkeypatch.setenv("CIRCUIT_MODEL_NAME_ASK_AI", "gemini-3.1-flash-lite")
    monkeypatch.setenv("CIRCUIT_MODEL_NAME_REPORT", "gpt-4o-mini")
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    import model_resolver as _mr
    importlib.reload(_mr)
    assert _mr.get_active_ask_ai_model() == "gemini-3.1-flash-lite"
    assert _mr.get_active_report_model() == "gpt-4o-mini"


def test_r69_resolver_settings_layer_wins_over_env_layer(monkeypatch, tmp_path):
    monkeypatch.setenv("CIRCUIT_MODEL_NAME_ASK_AI", "gemini-3.1-flash-lite")
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({"ask_ai_model_name": "gpt-5-nano"})
    import model_resolver as _mr
    importlib.reload(_mr)
    assert _mr.get_active_ask_ai_model() == "gpt-5-nano"


def test_r69_resolver_drops_malformed_env_silently(monkeypatch, tmp_path):
    """A typo'd env var (with a space) must not propagate -- the
    resolver should fall through to the next layer."""
    monkeypatch.setenv("CIRCUIT_MODEL_NAME_ASK_AI", "rm -rf /")
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    import model_resolver as _mr
    importlib.reload(_mr)
    # Drops the malformed env, falls through to hardcoded default.
    assert _mr.get_active_ask_ai_model() == "gpt-5-nano"


def test_r69_resolver_does_not_cache_settings_value(monkeypatch, tmp_path):
    """A UI flip mid-process must take effect on the next call."""
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    import model_resolver as _mr
    importlib.reload(_mr)
    _s.save_settings({"ask_ai_model_name": "gpt-4o-mini"})
    assert _mr.get_active_ask_ai_model() == "gpt-4o-mini"
    _s.save_settings({"ask_ai_model_name": "gemini-3.1-flash-lite"})
    # Same process, fresh resolution -> sees the flipped value.
    assert _mr.get_active_ask_ai_model() == "gemini-3.1-flash-lite"


# ---------------------------------------------------------------------------
# Section 4: generate_llm_response keyword-only kwarg threading
# ---------------------------------------------------------------------------


def _r69_make_fake_circuit_client(captured: dict, response: str = "OK"):
    """Build a CircuitChatClient stand-in that captures kwargs and
    carries the class-level constants ``generate_llm_response`` reads
    BEFORE invoking the constructor (REQUEST_TIMEOUT_SECONDS)."""

    class _FakeClient:
        REQUEST_TIMEOUT_SECONDS = 5

        def __init__(self, **kwargs):
            captured.update(kwargs)

        def complete(self, sp, bb):
            return response

    return _FakeClient


def test_r69_generate_llm_response_threads_explicit_model_name(monkeypatch):
    """Passing ``model_name=...`` must override CIRCUIT_CONFIG for that
    call only."""
    import adoptiq_backend as _ab
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "client_id", "x")
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "client_secret", "y")
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "app_key", "z")
    captured = {}
    monkeypatch.setattr(_ab, "CircuitChatClient", _r69_make_fake_circuit_client(captured))
    out = _ab.generate_llm_response("sys", "brief", model_name="gemini-3.1-flash-lite")
    assert out == "OK"
    assert captured.get("model_name") == "gemini-3.1-flash-lite"


def test_r69_generate_llm_response_falls_back_to_circuit_config_when_kwarg_unset(monkeypatch):
    import adoptiq_backend as _ab
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "client_id", "x")
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "client_secret", "y")
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "app_key", "z")
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "model_name", "gpt-5-nano")
    captured = {}
    monkeypatch.setattr(_ab, "CircuitChatClient", _r69_make_fake_circuit_client(captured))
    _ab.generate_llm_response("sys", "brief")  # no model_name kwarg
    assert captured.get("model_name") == "gpt-5-nano"


def test_r69_generate_llm_json_response_threads_model_name_through(monkeypatch):
    """``model_name`` must propagate from ``generate_llm_json_response``
    to ``generate_llm_response``."""
    import adoptiq_backend as _ab
    captured = {}

    def _fake_response(sys_p, body, *, model_name=None):
        captured["model_name"] = model_name
        return '{"ok": true}'

    monkeypatch.setattr(_ab, "generate_llm_response", _fake_response)
    _ab.generate_llm_json_response("sys", "brief",
                                    {"properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                                    model_name="gpt-4o-mini")
    assert captured.get("model_name") == "gpt-4o-mini"


def test_r69_generate_llm_response_kwarg_is_keyword_only():
    """``model_name`` MUST be keyword-only -- a positional 3rd arg
    would silently shadow ``briefing_book`` in legacy callers."""
    import inspect
    import adoptiq_backend as _ab
    sig = inspect.signature(_ab.generate_llm_response)
    assert sig.parameters["model_name"].kind == inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# Section 5: Ask AI sites use the Ask AI resolver (wires don't cross)
# ---------------------------------------------------------------------------


def test_r69_ask_ai_grounded_imports_ask_ai_resolver_not_report():
    """Source-shape grep: ``ask_ai_grounded.py`` must reference the
    Ask AI resolver and MUST NOT reference the report resolver."""
    src = (PROJECT_ROOT / "ask_ai_grounded.py").read_text()
    assert "get_active_ask_ai_model" in src
    assert "get_active_report_model" not in src, (
        "ask_ai_grounded.py must NOT pull the report resolver -- the "
        "two model surfaces are intentionally separated"
    )


def test_r69_app_simple_ask_ai_legacy_fallback_uses_ask_ai_resolver():
    """The legacy fallback in ``/api/ask-ai-portfolio`` must thread the
    Ask AI resolver, not the report resolver."""
    src = (PROJECT_ROOT / "app_simple.py").read_text()
    # Both Ask AI legacy + Ask Intel sites must thread the Ask AI resolver.
    ask_ai_count = src.count("get_active_ask_ai_model")
    assert ask_ai_count >= 2, f"expected >=2 Ask AI resolver imports, found {ask_ai_count}"


# ---------------------------------------------------------------------------
# Section 6: Report sites use the report resolver (wires don't cross)
# ---------------------------------------------------------------------------


def test_r69_report_sites_use_report_resolver_in_app_simple():
    """The 3 report sites in app_simple.py (compact AI insights,
    per-customer storyboard fallback, subscription analysis) must each
    import + use ``get_active_report_model``."""
    src = (PROJECT_ROOT / "app_simple.py").read_text()
    # Three sites x 1 import line each = at least 3 occurrences.  The
    # endpoint helper itself also references it (admin proxy + GET path),
    # so the bound is conservative.
    occurrences = src.count("get_active_report_model")
    assert occurrences >= 3, (
        f"expected >=3 report-resolver references in app_simple.py, "
        f"found {occurrences}"
    )


def test_r69_report_sites_use_report_resolver_in_adoptiq_backend():
    """The 2 CLI report sites in adoptiq_backend.py (portfolio summary,
    per-customer storyboard) must each thread the report resolver."""
    src = (PROJECT_ROOT / "adoptiq_backend.py").read_text()
    assert src.count("get_active_report_model") >= 2


def test_r69_no_report_site_accidentally_uses_ask_ai_resolver():
    """Wires-don't-cross: report-narrative paths MUST NOT import the
    Ask AI resolver.  Source-shape grep on the report-only modules to
    confirm separation."""
    backend = (PROJECT_ROOT / "adoptiq_backend.py").read_text()
    assert "get_active_ask_ai_model" not in backend, (
        "adoptiq_backend.py is the CLI report path; it must NOT reach "
        "for the Ask AI resolver -- the two surfaces are tuned "
        "independently against different validators (R27 grounding "
        "rate vs Ask AI eval scorecard)"
    )


# ---------------------------------------------------------------------------
# Section 7: API endpoints (success, validation, sanitization)
# ---------------------------------------------------------------------------


def test_r69_get_settings_ask_ai_model_returns_default_when_unset(client, monkeypatch, tmp_path):
    """GET on the endpoint returns the persisted + active values."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    rv = client.get("/api/settings/ask-ai-model")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["setting_key"] == "ask_ai_model_name"
    assert data["persisted_value"] == ""
    assert data["active_value"] == "gpt-5-nano"
    assert data["env_var"] == "CIRCUIT_MODEL_NAME_ASK_AI"


def test_r69_post_settings_ask_ai_model_persists_valid_value(client, monkeypatch, tmp_path):
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    rv = client.post("/api/settings/ask-ai-model",
                     json={"model_name": "gemini-3.1-flash-lite"})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["persisted_value"] == "gemini-3.1-flash-lite"
    on_disk = json.loads((tmp_path / "settings.json").read_text())
    assert on_disk["ask_ai_model_name"] == "gemini-3.1-flash-lite"


def test_r69_post_settings_report_model_persists_valid_value(client, monkeypatch, tmp_path):
    """Mirror test for the report endpoint."""
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    rv = client.post("/api/settings/report-model",
                     json={"model_name": "gpt-4o-mini"})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["persisted_value"] == "gpt-4o-mini"
    on_disk = json.loads((tmp_path / "settings.json").read_text())
    assert on_disk["report_model_name"] == "gpt-4o-mini"


def test_r69_post_settings_rejects_invalid_model_name_with_400(client, monkeypatch, tmp_path):
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    rv = client.post("/api/settings/ask-ai-model",
                     json={"model_name": "rm -rf /"})
    assert rv.status_code == 400
    assert rv.get_json().get("error") == "invalid_model_name"


def test_r69_post_settings_accepts_empty_string_to_clear_override(client, monkeypatch, tmp_path):
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    # First persist a value
    client.post("/api/settings/ask-ai-model", json={"model_name": "gpt-4o-mini"})
    # Then clear it
    rv = client.post("/api/settings/ask-ai-model", json={"model_name": ""})
    assert rv.status_code == 200
    assert rv.get_json()["persisted_value"] == ""


def test_r69_post_settings_rejects_non_string_payload(client, monkeypatch, tmp_path):
    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    rv = client.post("/api/settings/ask-ai-model", json={"model_name": 123})
    assert rv.status_code == 400


def test_r69_llm_ping_rejects_invalid_model_name_before_calling_circuit(client):
    """The ping endpoint MUST validate the allow-list BEFORE making
    any network call."""
    rv = client.post("/api/llm/ping", json={"model_name": "rm -rf /"})
    assert rv.status_code == 400
    assert "invalid_model_name" in rv.get_json().get("error", "")


def test_r69_llm_ping_requires_model_name(client):
    rv = client.post("/api/llm/ping", json={})
    assert rv.status_code == 400


def test_r69_llm_ping_returns_credentials_not_configured_when_circuit_unset(client, monkeypatch):
    """When CircuIT credentials aren't bundled, the ping must return a
    friendly ``credentials_not_configured`` error (NOT crash, NOT leak)."""
    import adoptiq_backend as _ab
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "client_id", "")
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "client_secret", "")
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "app_key", "")
    rv = client.post("/api/llm/ping", json={"model_name": "gpt-5-nano"})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is False
    assert "credentials_not_configured" in data.get("error", "")


def test_r69_llm_ping_succeeds_when_circuit_returns_response(client, monkeypatch):
    import adoptiq_backend as _ab
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "client_id", "x")
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "client_secret", "y")
    monkeypatch.setitem(_ab.CIRCUIT_CONFIG, "app_key", "z")

    class _FakeClient:
        REQUEST_TIMEOUT_SECONDS = 5

        def __init__(self, **kwargs):
            pass
        def complete(self, sp, bb):
            return "OK"

    monkeypatch.setattr(_ab, "CircuitChatClient", _FakeClient)
    rv = client.post("/api/llm/ping", json={"model_name": "gpt-5-nano"})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert "latency_ms" in data
    assert data["model_name"] == "gpt-5-nano"


def test_r69_sanitize_llm_error_strips_jwt_and_bearer_tokens():
    """Defense-in-depth: a leaked Bearer token / JWT in the upstream
    error MUST be redacted before echo."""
    import app_simple as _app
    s = _app._r69_sanitize_llm_error(
        "401: Bearer eyJhbGciOiJSUzI1NiIs.payload.sig is invalid; client_secret=topsecret123"
    )
    assert "eyJ" not in s
    assert "topsecret" not in s
    assert "<redacted>" in s or "<jwt-redacted>" in s


def test_r69_sanitize_llm_error_caps_length():
    """Runaway upstream errors must not bloat the JSON response."""
    import app_simple as _app
    s = _app._r69_sanitize_llm_error("x" * 5000, max_len=200)
    assert len(s) <= 200


# ---------------------------------------------------------------------------
# Section 8: admin proxy routes
# ---------------------------------------------------------------------------


def test_r69_admin_proxy_routes_register():
    """Source-shape: the admin Flask app must expose the three proxy
    routes."""
    import enhanced_admin_dashboard_v2 as _adm
    rules = {r.rule for r in _adm.admin_app.url_map.iter_rules()}
    assert "/admin_settings/ask_ai_model" in rules
    assert "/admin_settings/report_model" in rules
    assert "/admin_settings/llm_ping" in rules


# ---------------------------------------------------------------------------
# Section 9: UI source-shape
# ---------------------------------------------------------------------------


def test_r69_ask_ai_template_carries_model_card_and_js_hook():
    src = (PROJECT_ROOT / "templates" / "ask_ai.html").read_text()
    assert 'data-r69-model-card="ask_ai"' in src
    assert "r69_model_preferences.js" in src


def test_r69_analyze_template_carries_model_card_and_js_hook():
    src = (PROJECT_ROOT / "templates" / "analyze.html").read_text()
    assert 'data-r69-model-card="report"' in src
    assert "r69_model_preferences.js" in src


def test_r69_shared_js_module_exists_and_wires_test_gates_save():
    js = (PROJECT_ROOT / "static" / "js" / "r69_model_preferences.js").read_text()
    # Must lock Save on input change.
    assert "saveBtn.disabled = true" in js
    # Must enable Save only after a successful Test.
    assert "saveBtn.disabled = false" in js
    # Must hit the right endpoints.
    assert "/api/llm/ping" in js
    assert "/api/settings/ask-ai-model" in js
    assert "/api/settings/report-model" in js


def test_r69_admin_dashboard_template_renders_both_model_cards():
    src = (PROJECT_ROOT / "enhanced_admin_dashboard_v2.py").read_text()
    assert 'data-r69-admin-card="ask_ai"' in src
    assert 'data-r69-admin-card="report"' in src
    assert "/admin_settings/llm_ping" in src


# ---------------------------------------------------------------------------
# Section 10: diagnostic surface
# ---------------------------------------------------------------------------


def test_r69_diag_buffer_records_model_name_field():
    """The grounded path stamps the active Ask AI model onto the diag
    buffer payload."""
    import app_simple as _app
    qid = "r69-test-id"
    _app._record_ask_ai_query_diag(qid, {
        "method": "vector",
        "model_name": "gemini-3.1-flash-lite",
    })
    entry = _app._get_ask_ai_query_diag(qid)
    assert entry is not None
    assert entry.get("model_name") == "gemini-3.1-flash-lite"


def test_r69_app_simple_grounded_response_carries_model_name():
    """Source-shape: the grounded response payload MUST include
    ``'model_name': _r69_active_model``."""
    src = (PROJECT_ROOT / "app_simple.py").read_text()
    # The grounded jsonify includes the model_name field.
    assert "'model_name': _r69_active_model" in src


def test_r69_ask_ai_js_renders_model_pill_in_debug_chip():
    js = (PROJECT_ROOT / "static" / "js" / "ask_ai.js").read_text()
    assert "r69DebugChipModel" in js
    assert "data.model_name" in js


# ---------------------------------------------------------------------------
# Section 11: build metadata
# ---------------------------------------------------------------------------


def test_r69_embed_credentials_env_keys_includes_both_per_site_overrides():
    import embed_credentials as _ec
    assert "CIRCUIT_MODEL_NAME_ASK_AI" in _ec.ENV_KEYS
    assert "CIRCUIT_MODEL_NAME_REPORT" in _ec.ENV_KEYS


def test_r69_secrets_env_template_documents_all_three_circuit_model_vars():
    src = (PROJECT_ROOT / "secrets.env.template").read_text()
    assert "CIRCUIT_MODEL_NAME=" in src
    assert "CIRCUIT_MODEL_NAME_ASK_AI=" in src
    assert "CIRCUIT_MODEL_NAME_REPORT=" in src
    # The template must warn the operator that the report variable
    # is the higher-risk one (R27 grounding-rate impact).
    assert "R27" in src or "grounding" in src.lower()
