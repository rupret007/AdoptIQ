"""Round 4 / Phase 6.6 regression test.

``generate_llm_json_response`` must validate per-claim shape and FAIL
CLOSED when claims are malformed (missing ``text``/``statement``,
non-list ``citations``, or an entry that is not a dict).  Pre-Round-4
only top-level keys were validated, so a ``claims`` array of strings
returned ``ok: True`` with empty findings.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _patch_llm(monkeypatch, fake_text: str):
    import adoptiq_backend
    monkeypatch.setattr(
        adoptiq_backend, "generate_llm_response",
        lambda sys_p, user_p: fake_text,
    )


def test_malformed_claim_string_is_rejected(monkeypatch):
    import adoptiq_backend
    _patch_llm(monkeypatch, '{"executive_summary":"x","claims":["bad string instead of object"]}')
    schema = {
        "properties": {
            "executive_summary": {"type": "string"},
            "claims": {"type": "array"},
        },
        "required": ["executive_summary", "claims"],
    }
    out = adoptiq_backend.generate_llm_json_response("sys", "brief", schema)
    assert out["ok"] is False, (
        "Round 4 Phase 6.6: a claim entry that is a bare string must "
        "fail closed, not return ok:True."
    )
    assert "malformed" in out["error"].lower() or "claim" in out["error"].lower()


def test_malformed_citations_non_list_is_rejected(monkeypatch):
    import adoptiq_backend
    _patch_llm(monkeypatch, (
        '{"executive_summary":"x","claims":['
        '{"text":"some claim","citations":"AB-001"}'  # citations must be a LIST
        ']}'
    ))
    schema = {
        "properties": {
            "executive_summary": {"type": "string"},
            "claims": {"type": "array"},
        },
        "required": ["executive_summary", "claims"],
    }
    out = adoptiq_backend.generate_llm_json_response("sys", "brief", schema)
    assert out["ok"] is False, (
        "Round 4 Phase 6.6: a claim with non-list citations must "
        "fail closed."
    )


def test_well_formed_claim_passes(monkeypatch):
    import adoptiq_backend
    _patch_llm(monkeypatch, (
        '{"executive_summary":"x","claims":['
        '{"text":"a real claim","citations":["AB-001","TAC-002"]}'
        ']}'
    ))
    schema = {
        "properties": {
            "executive_summary": {"type": "string"},
            "claims": {"type": "array"},
        },
        "required": ["executive_summary", "claims"],
    }
    out = adoptiq_backend.generate_llm_json_response("sys", "brief", schema)
    assert out["ok"] is True, (
        f"Well-formed claims must pass validation; got {out!r}"
    )
