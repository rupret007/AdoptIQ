"""Round 161 — Leader prefetch meta threads source freshness into both facts builders."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from app_simple import _r147_compact_prefetch_freshness


_SOURCE_CLOCK = "2026-08-11T17:38:30+00:00"
_EVAL_CLOCK = datetime(2026, 8, 11, 18, 0, 0, tzinfo=timezone.utc)


def test_round161_leader_generator_build_report_facts_threads_freshness() -> None:
    from leader_report_generator import LeaderReportGenerator

    meta = {
        "attempted_at": "2026-08-11T17:30:00Z",
        "data_retrieved_at": _SOURCE_CLOCK,
        "outcome": "success",
    }
    gen = LeaderReportGenerator(
        MagicMock(),
        [("Brian Frazier", "Alex Rivera", "alex@example.com")],
        data_retrieved_at=_EVAL_CLOCK,
        leader_prefetch_meta=meta,
    )
    fresh = gen._r161_resolve_leader_freshness()
    assert fresh["data_as_of_state"] == "available"
    assert fresh["data_as_of_utc"].startswith("2026-08-11")

    gen_path = Path(__file__).resolve().parents[1] / "leader_report_generator.py"
    body = gen_path.read_text(encoding="utf-8")
    assert "_r161_fact_kwargs" in body
    assert '"data_as_of_utc": _r161_fresh.get' in body
    assert '"retrieval_attempted_at_utc": _r161_fresh.get' in body
    assert "if getattr(self, \"_leader_prefetch_meta\", None) is not None:" in body


def test_round161_app_simple_leader_worker_wires_prefetch_meta() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app_simple.py"
    body = app_path.read_text(encoding="utf-8")
    assert "_leader_prefetch_meta" in body
    assert "leader_prefetch_meta=_leader_prefetch_meta" in body
    assert "_r161_leader_freshness = _r147_compact_prefetch_freshness(" in body
    assert 'status["csone_raw_rows"]' in body
    assert "data_as_of_utc=_r161_leader_freshness.get" in body


def test_round161_leader_generator_does_not_stamp_freshness_from_evaluation_only() -> None:
    """Negative control: evaluation clock alone must not become public as-of."""
    meta = {"attempted_at": "2026-08-11T17:30:00Z", "outcome": "attempting"}
    resolved = _r147_compact_prefetch_freshness(
        meta,
        outcome=meta.get("outcome"),
        evaluation_clock=_EVAL_CLOCK,
    )
    assert not resolved.get("data_as_of_utc")
    assert resolved.get("data_as_of_state") != "available"


def test_round161_collect_team_data_stamps_prefetch_meta() -> None:
    gen_path = Path(__file__).resolve().parents[1] / "leader_report_generator.py"
    body = gen_path.read_text(encoding="utf-8")
    assert "_r167_stamp_leader_prefetch_success" in body
    assert "Round 161" in body


def test_round167_leader_success_preserves_explicit_source_clock() -> None:
    from leader_report_generator import LeaderReportGenerator

    meta = {
        "attempted_at": "2026-08-13T04:20:00Z",
        "outcome": "attempting",
    }
    source_clock = datetime(2026, 8, 3, 21, 0, 0, tzinfo=timezone.utc)
    generator = LeaderReportGenerator(
        MagicMock(),
        [("Dana Manager", "Alex Rivera", "alex@example.com")],
        data_retrieved_at=source_clock,
        leader_prefetch_meta=meta,
    )

    generator._r167_stamp_leader_prefetch_success()
    freshness = generator._r161_resolve_leader_freshness()

    assert meta["data_retrieved_at"] == source_clock.isoformat()
    assert meta["outcome"] == "success"
    assert freshness["data_as_of_utc"] == "2026-08-03T21:00:00Z"


def test_round167_leader_render_time_fallback_is_not_verified_freshness() -> None:
    from leader_report_generator import LeaderReportGenerator

    meta = {
        "attempted_at": "2026-08-13T04:20:00Z",
        "data_retrieved_at": "2026-08-13T04:20:01Z",
        "outcome": "attempting",
    }
    generator = LeaderReportGenerator(
        MagicMock(),
        [("Dana Manager", "Alex Rivera", "alex@example.com")],
        data_retrieved_at=None,
        leader_prefetch_meta=meta,
    )

    generator._r167_stamp_leader_prefetch_success()
    freshness = generator._r161_resolve_leader_freshness()

    assert "data_retrieved_at" not in meta
    assert freshness["data_as_of_utc"] == ""
    assert freshness["data_as_of_state"] != "available"
