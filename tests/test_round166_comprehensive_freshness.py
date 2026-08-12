"""Round 166 Comprehensive freshness + partition scope regressions."""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

import app_simple as app_mod
import decision_report_delivery as delivery
from tests.test_round142_decision_report_delivery import _team_fixture
from tests.test_round147_compact_freshness import SOURCE_CLOCK, ATTEMPT_CLOCK, EVALUATION_CLOCK


def test_comprehensive_prefetch_freshness_delegates_to_compact_helper() -> None:
    expected = app_mod._r147_compact_prefetch_freshness(
        {
            "data_retrieved_at": SOURCE_CLOCK,
            "attempted_at": ATTEMPT_CLOCK,
        },
        outcome="success",
        evaluation_clock=EVALUATION_CLOCK,
    )
    actual = app_mod._r166_comprehensive_prefetch_freshness(
        {
            "data_retrieved_at": SOURCE_CLOCK,
            "attempted_at": ATTEMPT_CLOCK,
        },
        outcome="success",
        evaluation_clock=EVALUATION_CLOCK,
    )
    assert actual == expected


def test_comprehensive_freshness_reaches_facts_and_status_fields() -> None:
    freshness = app_mod._r166_comprehensive_prefetch_freshness(
        {
            "data_retrieved_at": SOURCE_CLOCK,
            "attempted_at": ATTEMPT_CLOCK,
        },
        outcome="success",
        evaluation_clock=EVALUATION_CLOCK,
    )
    facts = delivery.build_report_facts(
        _team_fixture(),
        report_type="Comprehensive",
        scope_type="team",
        scope_value="Brian Frazier team",
        manager_name="Brian Frazier",
        days=90,
        as_of=freshness["evaluation_as_of_utc"],
        data_as_of_utc=freshness["data_as_of_utc"],
        data_as_of_state=freshness["data_as_of_state"],
        data_as_of_detail=freshness["data_as_of_detail"],
        retrieval_attempted_at_utc=freshness["retrieval_attempted_at"],
    )
    assert facts["as_of_utc"] == SOURCE_CLOCK.replace("Z", "+00:00")
    assert facts["data_as_of_state"] == "available"
    assert facts["evaluation_as_of_utc"] == SOURCE_CLOCK.replace("Z", "+00:00")


def test_comprehensive_status_threads_public_source_clock_after_freshness() -> None:
    source = Path(app_mod.__file__).read_text(encoding="utf-8")
    anchor = source.index("_r142_comp_facts = _r142_build_facts(")
    region = source[anchor : anchor + 2500]
    assert 'data_as_of_utc=_r166_comp_freshness["data_as_of_utc"]' in region
    status_anchor = source.index('status["data_as_of_utc"] = _r142_comp_facts.get("as_of_utc")')
    status_region = source[status_anchor : status_anchor + 500]
    assert 'status["data_as_of_state"]' in status_region
    assert 'status["retrieval_attempted_at_utc"]' in status_region


def test_comprehensive_partition_uses_authorized_roster_not_scoped_slice() -> None:
    source = Path(app_mod.__file__).read_text(encoding="utf-8")
    assert_in_source(
        source,
        "_r142_partition_subscriptions = _comprehensive_fetch_subs_df",
        label="comprehensive partition roster",
    )
    partition = source.index("_r142_team_data = _r142_partition_members(")
    region = source[partition : partition + 500]
    assert "_r142_partition_subscriptions" in region
    assert "_r142_scoped_subscriptions" not in region
