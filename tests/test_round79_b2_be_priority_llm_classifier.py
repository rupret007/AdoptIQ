"""Round 79 / Build 55 / Phase 2 (B2): LLM classifier tests.

Pins the JSON-array contract, ID-FK validation (drops hallucinated
IDs), top-N cap, kill-switch behavior, and the per-record diagnostic
shape.  Every test injects ``llm_callable`` so the suite never makes
a real network call.

Round 79 / Phase 2 (B2).  Made-with: Cursor.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

import be_priority_llm_classifier as bpl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ab_frame(n: int = 3) -> pd.DataFrame:
    """Build a synthetic top-N AB frame already enriched with be_priority_score."""
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "ID": f"ABRR-{1000 + i}",
            "title": f"Production issue #{i}",
            "description": f"Customer reports outage in feature {i}",
            "severity_norm": "High",
            "open_age_days": 30 + i,
            "AB_ESCALATE_C": False,
            "customer_name": "ACME" if i % 2 else "BETA",
            "be_priority_score": 50.0 + i,
        })
    return pd.DataFrame(rows)


def _good_response(ids: list[str]) -> str:
    """Fake a well-formed LLM JSON-array response."""
    return json.dumps([
        {
            "id": ab_id,
            "class": "TRUE_BLOCKER" if i % 3 == 0 else "TRAINING_GAP",
            "reason": f"Reason for {ab_id}",
            "confidence": "high" if i % 2 == 0 else "medium",
        }
        for i, ab_id in enumerate(ids)
    ])


# ---------------------------------------------------------------------------
# Tier 1: empty / kill-switch / fallback
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_frame():
    out, diag = bpl.classify_top_n_be_barriers(pd.DataFrame())
    assert out.empty
    assert diag["input_count"] == 0
    assert diag["classified_count"] == 0


def test_none_input_returns_empty_frame():
    out, diag = bpl.classify_top_n_be_barriers(None)
    assert out.empty
    assert diag["input_count"] == 0


def test_use_llm_false_kill_switch():
    """``use_llm=False`` -> every row UNCLASSIFIED, no LLM call."""
    df = _ab_frame(3)

    def boom(_system, _briefing):
        raise AssertionError("LLM should NOT be called when use_llm=False")

    out, diag = bpl.classify_top_n_be_barriers(
        df, use_llm=False, llm_callable=boom,
    )
    assert (out["be_llm_class"] == "UNCLASSIFIED").all()
    assert "LLM disabled" in out["be_llm_reason"].iloc[0]
    assert diag["llm_disabled"] is True
    assert diag["classified_count"] == 0


def test_llm_callable_returns_error_yields_unclassified():
    """LLM returns ``ERROR: ...`` -> every row UNCLASSIFIED with diag."""
    df = _ab_frame(2)

    def fake_llm(_system, _briefing):
        return "ERROR: rate_limited: too many requests"

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert (out["be_llm_class"] == "UNCLASSIFIED").all()
    assert "ERROR" in out["be_llm_reason"].iloc[0]
    assert "ERROR" in diag["llm_error"]


def test_llm_callable_raises_yields_unclassified():
    """LLM raises -> every row UNCLASSIFIED + diag captures exception kind."""
    df = _ab_frame(2)

    def fake_llm(_system, _briefing):
        raise RuntimeError("network blew up")

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert (out["be_llm_class"] == "UNCLASSIFIED").all()
    assert "RuntimeError" in out["be_llm_reason"].iloc[0]
    assert "RuntimeError" in diag["llm_error"]


def test_llm_callable_returns_empty_string():
    """Empty LLM body -> UNCLASSIFIED + diag tags the failure."""
    df = _ab_frame(2)
    out, diag = bpl.classify_top_n_be_barriers(
        df, llm_callable=lambda s, b: "",
    )
    assert (out["be_llm_class"] == "UNCLASSIFIED").all()
    assert diag["llm_error"]


# ---------------------------------------------------------------------------
# Tier 2: happy-path JSON parsing
# ---------------------------------------------------------------------------


def test_happy_path_classifies_every_row():
    df = _ab_frame(3)
    ids = df["ID"].tolist()

    def fake_llm(_system, _briefing):
        return _good_response(ids)

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert diag["input_count"] == 3
    assert diag["classified_count"] == 3
    assert (out["be_llm_class"] != "UNCLASSIFIED").all()
    assert all(c in {"TRUE_BLOCKER", "TRAINING_GAP"} for c in out["be_llm_class"])


def test_response_with_markdown_fence_still_parses():
    """LLMs sometimes wrap JSON in ```json ... ```; helper should recover."""
    df = _ab_frame(2)
    ids = df["ID"].tolist()

    def fake_llm(_system, _briefing):
        return f"```json\n{_good_response(ids)}\n```"

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert diag["classified_count"] == 2


def test_response_with_prose_prefix_still_parses():
    """LLM emits a short rationale before the array."""
    df = _ab_frame(2)
    ids = df["ID"].tolist()

    def fake_llm(_system, _briefing):
        return f"Here is the triage:\n{_good_response(ids)}"

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert diag["classified_count"] == 2


# ---------------------------------------------------------------------------
# Tier 3: validation / drops
# ---------------------------------------------------------------------------


def test_id_not_in_input_drops_record():
    """Hallucinated IDs are dropped + counted in diag."""
    df = _ab_frame(2)

    def fake_llm(_system, _briefing):
        return json.dumps([
            {"id": df["ID"].iloc[0], "class": "TRUE_BLOCKER", "reason": "real", "confidence": "high"},
            {"id": "ABRR-99999", "class": "TRUE_BLOCKER", "reason": "halluc", "confidence": "low"},
        ])

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert diag["llm_dropped_by_reason"]["id_not_in_input"] == 1
    assert diag["classified_count"] == 1


def test_class_not_in_allowlist_drops_record():
    """Non-enum class values are dropped."""
    df = _ab_frame(2)
    ids = df["ID"].tolist()

    def fake_llm(_system, _briefing):
        return json.dumps([
            {"id": ids[0], "class": "TRUE_BLOCKER", "reason": "ok", "confidence": "high"},
            {"id": ids[1], "class": "MAYBE_BLOCKER", "reason": "huh", "confidence": "low"},
        ])

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert diag["llm_dropped_by_reason"]["class_not_in_allowlist"] == 1
    assert diag["classified_count"] == 1


def test_missing_required_keys_drops_record():
    df = _ab_frame(2)
    ids = df["ID"].tolist()

    def fake_llm(_system, _briefing):
        return json.dumps([
            {"id": ids[0], "class": "TRUE_BLOCKER", "reason": "ok", "confidence": "high"},
            {"class": "FEATURE_REQUEST"},  # missing id
        ])

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert diag["llm_dropped_by_reason"]["missing_keys"] == 1


def test_duplicate_id_drops_second_record():
    """Two records pointing at the same input ID -> only first counted."""
    df = _ab_frame(1)
    ab_id = df["ID"].iloc[0]

    def fake_llm(_system, _briefing):
        return json.dumps([
            {"id": ab_id, "class": "TRUE_BLOCKER", "reason": "first", "confidence": "high"},
            {"id": ab_id, "class": "TRAINING_GAP", "reason": "duplicate", "confidence": "low"},
        ])

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert diag["llm_dropped_by_reason"]["duplicate_id"] == 1
    assert diag["classified_count"] == 1
    # First record won
    assert out["be_llm_class"].iloc[0] == "TRUE_BLOCKER"


def test_unparseable_json_yields_unclassified():
    df = _ab_frame(2)

    def fake_llm(_system, _briefing):
        return "this is not JSON"

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert (out["be_llm_class"] == "UNCLASSIFIED").all()
    assert diag["llm_error"] == "json_parse_error"


# ---------------------------------------------------------------------------
# Tier 4: enum normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("TRUE_BLOCKER", "TRUE_BLOCKER"),
        ("true_blocker", "TRUE_BLOCKER"),
        ("True Blocker", "TRUE_BLOCKER"),
        ("TRUE-BLOCKER", "TRUE_BLOCKER"),
        ("TRAINING_GAP", "TRAINING_GAP"),
        ("not_a_barrier", "NOT_A_BARRIER"),
        ("BLOCKER", ""),  # not in allowlist
        ("", ""),
        (None, ""),
    ],
)
def test_normalise_class(raw, expected):
    """Enum normalisation: case-insensitive, dash/space tolerant, allowlist."""
    assert bpl._normalise_class(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("high", "high"),
        ("HIGH", "high"),
        ("medium", "medium"),
        ("low", "low"),
        ("very high", "unknown"),
        (None, "unknown"),
        ("", "unknown"),
    ],
)
def test_normalise_confidence(raw, expected):
    assert bpl._normalise_confidence(raw) == expected


# ---------------------------------------------------------------------------
# Tier 5: top-N cap
# ---------------------------------------------------------------------------


def test_top_n_cap_truncates_input():
    """``n=2`` on a 5-row frame -> only 2 rows reach the LLM."""
    df = _ab_frame(5)
    captured = {}

    def fake_llm(_system, briefing):
        captured["briefing"] = briefing
        # Return classifications for the first 2 IDs
        return _good_response(df["ID"].head(2).tolist())

    out, diag = bpl.classify_top_n_be_barriers(df, n=2, llm_callable=fake_llm)
    assert diag["input_count"] == 2
    assert diag["classified_count"] == 2
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Tier 6: per-record diagnostic + PII redaction
# ---------------------------------------------------------------------------


def test_per_record_diag_uses_id_digest_not_raw_id():
    """Round 79 / B2: per-record diag entries digest the AB ID so the
    persisted analysis_status carries no raw IDs (R65/C-3 pattern)."""
    df = _ab_frame(2)
    ids = df["ID"].tolist()

    def fake_llm(_system, _briefing):
        return _good_response(ids)

    _, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    for rec in diag["records"]:
        # Each record's id_digest is an 8-char SHA256 prefix; never the raw ID
        assert isinstance(rec["id_digest"], str)
        assert len(rec["id_digest"]) == 8
        assert all(ab_id != rec["id_digest"] for ab_id in ids)


def test_diag_records_when_use_llm_false():
    """Kill-switch path still records per-record diag entries."""
    df = _ab_frame(3)
    _, diag = bpl.classify_top_n_be_barriers(df, use_llm=False, llm_callable=None)
    assert len(diag["records"]) == 3
    assert all(r["outcome"] == "skipped" for r in diag["records"])


# ---------------------------------------------------------------------------
# Tier 7: module exports + system prompt shape
# ---------------------------------------------------------------------------


def test_module_exports():
    assert "classify_top_n_be_barriers" in bpl.__all__


def test_system_prompt_lists_six_enum_values():
    """The system prompt MUST name all 6 enum values verbatim."""
    sp = bpl._SYSTEM_PROMPT
    for enum_val in [
        "TRUE_BLOCKER", "TRAINING_GAP", "FEATURE_REQUEST",
        "DUPLICATE", "AMBIGUOUS", "NOT_A_BARRIER",
    ]:
        assert enum_val in sp, f"system prompt missing enum: {enum_val}"


def test_system_prompt_specifies_json_array():
    """The system prompt MUST instruct the LLM to return a JSON ARRAY."""
    assert "JSON ARRAY" in bpl._SYSTEM_PROMPT
    assert '"id"' in bpl._SYSTEM_PROMPT
    assert '"class"' in bpl._SYSTEM_PROMPT
    assert '"reason"' in bpl._SYSTEM_PROMPT
    assert '"confidence"' in bpl._SYSTEM_PROMPT


def test_briefing_truncates_long_descriptions():
    """A description longer than 600 chars is truncated before the LLM
    sees it so a single noisy AB cannot blow the prompt budget."""
    df = pd.DataFrame([{
        "ID": "ABRR-1",
        "title": "x" * 1500,
        "description": "y" * 2000,
        "severity_norm": "High",
        "open_age_days": 10,
        "be_priority_score": 50.0,
        "customer_name": "ACME",
    }])
    briefing = bpl._format_briefing(df)
    assert len(briefing) < 4000  # well below the upper bound
    assert "..." in briefing  # truncation marker present


def test_id_digest_stable_across_calls():
    """The same AB ID always digests to the same 8 chars."""
    a = bpl._digest_id("ABRR-12345")
    b = bpl._digest_id("ABRR-12345")
    assert a == b
    assert a != bpl._digest_id("ABRR-12346")
