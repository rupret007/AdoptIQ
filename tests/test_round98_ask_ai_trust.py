"""Round 98 Ask AI evidence/citation trust regression coverage."""

from __future__ import annotations

from pathlib import Path


def test_round98_used_evidence_records_are_built_from_allowed_source_ids() -> None:
    from ask_ai_grounded import EvidenceRecord, _r98_used_evidence_records

    records = [
        EvidenceRecord("SupportCase", "CASE-1", "Acme", "2026-01-01", "Case one text"),
        EvidenceRecord("SupportCase", "CASE-2", "Acme", "2026-01-02", "Case two text"),
    ]

    used = _r98_used_evidence_records(records, {"CASE-2"})

    assert [r["source_id"] for r in used] == ["CASE-2"]
    assert used[0]["snippet"] == "Case two text"


def test_round98_corpus_allowed_ids_are_added_to_evidence_records() -> None:
    from ask_ai_grounded import _r98_corpus_evidence_records

    block = """
<corpus>
CORPUS_PLAYBOOK_CHUNKS (BM25-ranked; cite by SourceID):
  - [CORPUS:001] technology=Contact Center; theme=Escalations; customer=Acme; score=0.950; text=Escalate stalled APs.
  - [CORPUS:002] technology=Meetings; theme=Training; customer=Beta; score=0.800; text=Training gap.
</corpus>
"""

    records = _r98_corpus_evidence_records(block, ("CORPUS:001",))

    assert [r["source_id"] for r in records] == ["CORPUS:001"]
    assert records[0]["source_type"] == "Corpus"
    assert "Escalate stalled APs" in records[0]["text"]


def test_round98_supported_findings_render_sources_marker() -> None:
    from ask_ai_grounded import compose_grounded_answer

    answer, rejected = compose_grounded_answer(
        {
            "executive_summary": "",
            "claims": [{"statement": "Acme has one P1 case.", "citations": ["CASE-1"]}],
            "actions": [],
            "unknowns": [],
        },
        {"CASE-1"},
    )

    assert rejected == 0
    assert "[Sources: CASE-1]" in answer


def test_round98_ask_ai_js_accepts_source_sources_and_sourceid_markers() -> None:
    src = (Path(__file__).resolve().parent.parent / "static" / "js" / "ask_ai.js").read_text(encoding="utf-8")

    assert "(?:Source|Sources|SourceID)" in src
    assert "text.indexOf('[Source')" in src
