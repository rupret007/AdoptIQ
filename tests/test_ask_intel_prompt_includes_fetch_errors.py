"""Round 3 / Phase 2.7 regression test.

``run_intel_grounded_ask_ai`` must serialize ``fetch_errors`` and
``list_truncated`` from ``get_all_external_intel`` into the LLM
prompt. Without that, failed feeds look like "no incidents/bugs" to
the model and the answer can confidently assert silence.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_run_intel_grounded_ask_ai_emits_intel_data_warnings(monkeypatch):
    import ask_ai_grounded as aag

    captured = {}

    def fake_get_all_external_intel(days_back: int = 365):
        return {
            "incidents": [
                {
                    "id": "INC-100",
                    "title": "API outage",
                    "status": "resolved",
                    "impact_level": "high",
                    "description": "Brief outage",
                    "published": "2026-04-01T00:00:00Z",
                }
            ],
            "bugs": [],
            "maintenances": [],
            # Round 4 / Phase 4.1: production ``get_all_external_intel``
            # returns ``fetch_errors`` as a *dict* keyed by source.
            # The prior list-of-dicts test shape masked a TypeError in
            # the prompt builder for real failures.
            "fetch_errors": {
                "help.webex.com": "503 Service Unavailable",
                "status.webex.com": "DNS failure",
            },
            "list_truncated": {"bugs": True},
        }

    def fake_llm(sys_prompt, user_prompt, schema, **kwargs):
        # Round 69 / Build 43: ``**kwargs`` accepts the new
        # ``model_name=`` keyword that production threads through.
        captured["system"] = sys_prompt
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
    # Patch the lazily imported helper so the function uses our stub.
    import incident_storage

    monkeypatch.setattr(
        incident_storage, "get_all_external_intel", fake_get_all_external_intel
    )
    import adoptiq_backend

    monkeypatch.setattr(
        adoptiq_backend, "generate_llm_json_response", fake_llm
    )

    result = aag.run_intel_grounded_ask_ai("Are there any open incidents?", days=30)

    assert result.get("ok") is True
    user_prompt = captured.get("user", "")
    assert "INTEL_DATA_WARNINGS" in user_prompt
    assert "help.webex.com" in user_prompt
    assert "503 Service Unavailable" in user_prompt
    assert "status.webex.com" in user_prompt
    assert "bugs" in user_prompt and "truncated" in user_prompt.lower()
