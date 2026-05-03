"""Round 4 / Phase 4.1 regression test.

``run_intel_grounded_ask_ai`` must not crash when
``intel['fetch_errors']`` is a *dict* (the production shape).  The
prior list-of-dicts mock hid a TypeError at ``intel_fetch_errors[:20]``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_intel_grounded_ask_ai_accepts_dict_fetch_errors(monkeypatch):
    import ask_ai_grounded as aag

    captured = {}

    def fake_get_all_external_intel(days_back: int = 365):
        # Provide at least one intel ID so run_intel_grounded_ask_ai
        # does not short-circuit on "No intelligence IDs available".
        # The point of this test is the dict-shaped fetch_errors path.
        return {
            "incidents": [
                {
                    "id": "INC-TEST-1",
                    "title": "Test incident",
                    "status": "resolved",
                    "incident_type": "incident",
                    "started_at": "2025-01-01T00:00:00Z",
                    "resolved_at": "2025-01-01T01:00:00Z",
                    "components_csv": "",
                    "regions_csv": "",
                    "summary": "test",
                    "url": "https://example.com",
                }
            ],
            "bugs": [],
            "maintenances": [],
            "fetch_errors": {
                "help.webex.com": "503 Service Unavailable",
                "status.webex.com": "DNS failure",
            },
            "list_truncated": {},
        }

    def fake_llm(sys_prompt, user_prompt, schema, **kwargs):
        # Round 69 / Build 43: ``**kwargs`` accepts the new
        # ``model_name=`` keyword that production threads through.
        captured["user"] = user_prompt
        return {
            "ok": True,
            "data": {
                "executive_summary": "ok",
                "claims": [],
                "actions": [],
                "unknowns": [],
            },
        }

    monkeypatch.setattr(
        aag, "get_all_external_intel", fake_get_all_external_intel, raising=False
    )
    import incident_storage
    monkeypatch.setattr(
        incident_storage, "get_all_external_intel", fake_get_all_external_intel
    )
    import adoptiq_backend
    monkeypatch.setattr(adoptiq_backend, "generate_llm_json_response", fake_llm)

    # Must not raise TypeError.
    result = aag.run_intel_grounded_ask_ai("Any incidents?", days=30)

    assert result.get("ok") is True, (
        "Round 4 Phase 4.1: run_intel_grounded_ask_ai must complete "
        "successfully even when intel['fetch_errors'] is a dict."
    )
    user = captured.get("user", "")
    assert "help.webex.com" in user and "status.webex.com" in user, (
        "Round 4 Phase 4.1: dict-shaped fetch_errors must be serialized "
        "into the LLM prompt (one line per failed source)."
    )
