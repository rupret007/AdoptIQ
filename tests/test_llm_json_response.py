import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import adoptiq_backend as backend


def test_generate_llm_json_response_parses_valid_json(monkeypatch):
    def _fake_llm(system_prompt, briefing_book):
        return '{"executive_summary":"ok","claims":[],"actions":[],"unknowns":[]}'

    monkeypatch.setattr(backend, "generate_llm_response", _fake_llm)
    schema = {
        "type": "object",
        "required": ["executive_summary", "claims", "actions", "unknowns"],
        "properties": {
            "executive_summary": {"type": "string"},
            "claims": {"type": "array"},
            "actions": {"type": "array"},
            "unknowns": {"type": "array"},
        },
    }
    result = backend.generate_llm_json_response("sys", "brief", schema)
    assert result["ok"] is True
    assert result["data"]["executive_summary"] == "ok"


def test_generate_llm_json_response_rejects_missing_required_keys(monkeypatch):
    def _fake_llm(system_prompt, briefing_book):
        return '{"executive_summary":"ok","claims":[]}'

    monkeypatch.setattr(backend, "generate_llm_response", _fake_llm)
    schema = {
        "type": "object",
        "required": ["executive_summary", "claims", "actions", "unknowns"],
        "properties": {"executive_summary": {}, "claims": {}, "actions": {}, "unknowns": {}},
    }
    result = backend.generate_llm_json_response("sys", "brief", schema)
    assert result["ok"] is False
    assert "missing required keys" in result["error"]
