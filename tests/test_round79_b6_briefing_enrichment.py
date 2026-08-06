"""Round 79 / Build 55 / Phase 6 (B6): briefing book BE-priority enrichment.

Pins the ``adoptiq_backend._create_briefing_book`` Snowflake AB detail
loop emitting the four new Round 79 fields (``CSConsole_Severity``,
``Independent_BE_Priority``, ``Independent_BE_Class``, ``Days_Open``) so
the per-customer narrative LLM can reason about deterministic priority
on top of the LLM classification verdict. Also pins the source-shape
contract that ``ab_norm`` is enriched with ``be_priority_score`` and
``be_llm_class`` BEFORE the per-customer briefing loop runs in the
comprehensive flow (otherwise the briefing would never see the new
fields).

Round 79 / Phase 6 (B6).  Made-with: Cursor.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

import pandas as pd
import pytest

import adoptiq_backend


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"
_ADOPTIQ_BACKEND = _REPO_ROOT / "adoptiq_backend.py"


def _ab_df_with_be_priority() -> pd.DataFrame:
    """A small AB DataFrame with the BE-priority enrichment columns."""
    return pd.DataFrame(
        [
            {
                "ID": "AB001",
                "title": "Login MFA loop blocks daily standup",
                "description": "End users hit infinite MFA prompts every morning when SSO refresh fails.",
                "customer_name": "Acme Corp",
                "ab_category_final": "Login",
                "sub_technology": "Webex",
                "severity_norm": "P1",
                "open_age_days": 47,
                "be_priority_score": 88.4,
                "be_top_signal": "Severity",
                "be_llm_class": "TRUE_BLOCKER",
            },
            {
                "ID": "AB002",
                "title": "Calendar invite sync mismatches",
                "description": "Outlook recurring invites surface as separate single events in the room calendar.",
                "customer_name": "Globex",
                "ab_category_final": "Calendar",
                "sub_technology": "Webex",
                "severity_norm": "P3",
                "open_age_days": 12,
                "be_priority_score": 41.2,
                "be_top_signal": "Customer Risk",
                "be_llm_class": "FEATURE_REQUEST",
            },
        ]
    )


def _ab_df_legacy_no_be_columns() -> pd.DataFrame:
    """Legacy shape -- no BE-priority columns at all."""
    return pd.DataFrame(
        [
            {
                "ID": "AB100",
                "title": "Old style barrier",
                "description": "Pre-R79 shape, no BE-priority columns.",
                "customer_name": "Old Customer",
                "ab_category_final": "Other",
                "sub_technology": "Webex",
            },
        ]
    )


# ---------------------------------------------------------------------------
# Briefing emission tests
# ---------------------------------------------------------------------------


class TestBriefingEnrichment:
    @staticmethod
    def _call_briefing(scope: str, ab_df: pd.DataFrame) -> str:
        return adoptiq_backend._create_briefing_book(
            scope,
            ab_df,
            pd.DataFrame(),
            [],
            [],
            [],
            pd.DataFrame(),
            None,  # db_profile -- not needed for AB-detail tests
        )

    def test_briefing_emits_csconsole_severity_when_severity_norm_present(self) -> None:
        ab_df = _ab_df_with_be_priority()
        briefing = self._call_briefing("Acme Corp", ab_df)
        assert "CSConsole_Severity" in briefing
        assert "P1" in briefing
        assert "P3" in briefing

    def test_briefing_emits_independent_be_priority_with_one_decimal(self) -> None:
        ab_df = _ab_df_with_be_priority()
        briefing = self._call_briefing("Acme Corp", ab_df)
        assert "Independent_BE_Priority" in briefing
        assert "88.4" in briefing
        assert "41.2" in briefing

    def test_briefing_emits_independent_be_class_when_classified(self) -> None:
        ab_df = _ab_df_with_be_priority()
        briefing = self._call_briefing("Acme Corp", ab_df)
        assert "Independent_BE_Class" in briefing
        assert "TRUE_BLOCKER" in briefing
        assert "FEATURE_REQUEST" in briefing

    def test_briefing_emits_days_open_as_integer(self) -> None:
        ab_df = _ab_df_with_be_priority()
        briefing = self._call_briefing("Acme Corp", ab_df)
        assert "Days_Open:" in briefing
        assert "47" in briefing
        assert "12" in briefing

    def test_briefing_skips_unclassified_be_class(self) -> None:
        """``UNCLASSIFIED`` is the classifier's default sentinel for
        rows the LLM did not classify (kill-switch / dropped / not in
        response). The briefing should NOT emit ``Independent_BE_Class``
        for such rows -- presenting ``UNCLASSIFIED`` to the per-customer
        LLM would be noise."""
        ab_df = _ab_df_with_be_priority().copy()
        ab_df.loc[ab_df["ID"] == "AB002", "be_llm_class"] = "UNCLASSIFIED"
        briefing = self._call_briefing("Acme Corp", ab_df)
        assert "FEATURE_REQUEST" not in briefing
        assert briefing.count("Independent_BE_Class") == 1

    def test_briefing_skips_blank_be_class(self) -> None:
        """Blank ``be_llm_class`` (the post-pipeline projection of
        ``UNCLASSIFIED``) MUST not surface in the briefing."""
        ab_df = _ab_df_with_be_priority().copy()
        ab_df.loc[ab_df["ID"] == "AB001", "be_llm_class"] = ""
        ab_df.loc[ab_df["ID"] == "AB002", "be_llm_class"] = ""
        briefing = self._call_briefing("Acme Corp", ab_df)
        assert "Independent_BE_Class" not in briefing

    def test_briefing_legacy_shape_emits_no_be_priority_fields(self) -> None:
        """Legacy ``ab_df`` (compact / renewal that don't run the
        BE-priority scorer) MUST render the original 4-line block
        with no extra Round 79 fields."""
        ab_df = _ab_df_legacy_no_be_columns()
        briefing = self._call_briefing("Old Customer", ab_df)
        assert "Independent_BE_Priority" not in briefing
        assert "Independent_BE_Class" not in briefing
        # severity_norm + open_age_days are also absent from the legacy
        # shape so neither ``CSConsole_Severity`` nor ``Days_Open``
        # appears either.
        assert "CSConsole_Severity" not in briefing
        assert "Days_Open:" not in briefing
        # but the original 4-line block is still there
        assert "CSConsole Record: AB100" in briefing
        assert "Old style barrier" in briefing

    def test_briefing_handles_nan_severity_gracefully(self) -> None:
        """Rows with NaN ``severity_norm`` MUST not emit the field."""
        ab_df = _ab_df_with_be_priority().copy()
        ab_df.loc[ab_df["ID"] == "AB001", "severity_norm"] = float("nan")
        briefing = self._call_briefing("Acme Corp", ab_df)
        # AB001's severity field is gone but AB002's P3 is still emitted
        assert briefing.count("CSConsole_Severity") == 1
        assert "P3" in briefing

    def test_briefing_handles_negative_days_open_gracefully(self) -> None:
        """Negative ``open_age_days`` should not emit the Days_Open
        field (defensive: anchor-shifted timestamps could produce
        negative deltas; we don't want to confuse the LLM)."""
        ab_df = _ab_df_with_be_priority().copy()
        ab_df.loc[ab_df["ID"] == "AB001", "open_age_days"] = -5
        briefing = self._call_briefing("Acme Corp", ab_df)
        # AB001's Days_Open field is gone but AB002's 12 is still
        # emitted.
        assert briefing.count("Days_Open:") == 1
        assert "12" in briefing

    def test_briefing_handles_string_score_value_gracefully(self) -> None:
        """A non-numeric ``be_priority_score`` should not crash the
        briefing builder (defensive: legacy callers may pass a
        rendered string instead of a float)."""
        ab_df = _ab_df_with_be_priority().copy()
        ab_df["be_priority_score"] = ab_df["be_priority_score"].astype(object)
        ab_df.loc[ab_df["ID"] == "AB001", "be_priority_score"] = "not-a-number"
        briefing = self._call_briefing("Acme Corp", ab_df)
        # AB002's 41.2 still rendered
        assert "41.2" in briefing
        # AB001 still has its other fields
        assert "Login MFA loop blocks daily standup" in briefing

    def test_briefing_kill_switch_falls_back_to_legacy_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Config.BE_PRIORITY_BRIEFING_ENABLED=False`` MUST fall back
        to the pre-Round-79 4-line briefing shape (CSConsole Record /
        Customer / Title / Full Description). The R79 fields must NOT
        appear, even when the enriched columns are present on the row."""
        from config import Config

        monkeypatch.setattr(
            Config, "BE_PRIORITY_BRIEFING_ENABLED", False, raising=False
        )
        ab_df = _ab_df_with_be_priority()
        briefing = self._call_briefing("Acme Corp", ab_df)
        # Legacy fields still present
        assert "CSConsole Record: AB001" in briefing
        assert "Login MFA loop blocks daily standup" in briefing
        # Round 79 fields must be skipped under kill-switch
        assert "CSConsole_Severity" not in briefing
        assert "Independent_BE_Priority" not in briefing
        assert "Independent_BE_Class" not in briefing
        assert "Days_Open:" not in briefing
        # Both records still rendered with the legacy 4-line block
        # (kill-switch must NOT skip records, only skip the BE-priority
        # context fields appended after them).
        assert "CSConsole Record: AB001" in briefing
        assert "CSConsole Record: AB002" in briefing
        assert "Calendar invite sync mismatches" in briefing


# ---------------------------------------------------------------------------
# Source-shape pins: comprehensive flow hoists pipeline before the loop
# ---------------------------------------------------------------------------


class TestComprehensivePipelineHoist:
    """Pins the R79/B6 source-shape: ``app_simple.run_comprehensive_analysis``
    builds the BE-priority canonical DataFrames BEFORE the per-customer
    narrative loop AND merges ``be_llm_class`` + ``be_priority_score``
    back into ``ab_norm`` so per-customer ``cust_ab`` slices inherit
    the columns when the briefing book reads them.

    These tests do NOT execute the full comprehensive flow (that
    requires Snowflake, CSConsole, LLM credentials). Instead they pin
    the source-shape so a future refactor that drops the hoist gets
    flagged immediately."""

    def test_comprehensive_hoists_pipeline_before_per_customer_loop(self) -> None:
        text = _APP_SIMPLE.read_text(encoding="utf-8")
        # The R79 hoist comment header lives BEFORE the per-customer loop.
        idx_hoist = text.find(
            "Round 79 / Build 55 (B2/B3/B5/B6): build the BE-priority outputs"
        )
        idx_loop = text.find("for i, customer_name in enumerate(all_customers, 1):")
        assert idx_hoist != -1, "missing R79 hoist comment header"
        assert idx_loop != -1, "missing per-customer loop"
        assert idx_hoist < idx_loop, (
            "R79 hoist must be BEFORE the per-customer loop (otherwise "
            "the briefing book never sees the BE-priority columns)"
        )

    def test_comprehensive_merges_be_columns_into_ab_norm(self) -> None:
        text = _APP_SIMPLE.read_text(encoding="utf-8")
        # The merge writes both ``be_priority_score`` and
        # ``be_llm_class`` columns onto ``ab_norm``.
        assert_in_source(text, 'ab_norm["be_priority_score"]', label='text')
        assert_in_source(text, 'ab_norm["be_llm_class"]', label='text')
        assert "Barrier_ID" in text  # the join key

    def test_comprehensive_skips_merge_on_provenance_only_canonical(self) -> None:
        """When the canonical DataFrame is provenance-only (broken
        pipeline, empty input), the merge MUST be skipped so we don't
        zero out every barrier's score with map().fillna(0)."""
        text = _APP_SIMPLE.read_text(encoding="utf-8")
        assert_in_source(text, "_r79_provenance_only", label='text')
        assert_in_source(text, "_adoptiq_provenance_row", label='text')

    def test_comprehensive_pipeline_pre_loop_block_has_no_duplicate_post_loop(
        self,
    ) -> None:
        """The pre-loop hoist replaced the post-loop pipeline-call.
        We must NOT have two ``build_be_priority_outputs`` call sites
        in the comprehensive flow (one before the loop, one after)
        because that would burn an extra LLM call and produce drift
        between the two canonical DataFrames."""
        text = _APP_SIMPLE.read_text(encoding="utf-8")
        # Walk to the leader-report sentinel marker so we only count
        # comprehensive-flow occurrences.
        idx_leader = text.find("def run_leader_report_generation(")
        comprehensive_text = text[:idx_leader] if idx_leader != -1 else text
        # Exactly one call to ``build_be_priority_outputs`` in the
        # comprehensive scope (the hoisted block before the loop).
        assert (
            comprehensive_text.count(
                "_r79_be_pipeline.build_be_priority_outputs("
            )
            == 1
        ), (
            "Comprehensive flow must have exactly one BE-priority "
            "pipeline call (the R79/B6 pre-loop hoist) -- a duplicate "
            "post-loop call would burn an extra LLM call AND cause the "
            "Word section + XLSX to disagree."
        )


# ---------------------------------------------------------------------------
# Source-shape pins: briefing book renders the four Round 79 fields
# ---------------------------------------------------------------------------


class TestBriefingSourceShape:
    def test_briefing_emits_round_79_field_labels(self) -> None:
        text = _ADOPTIQ_BACKEND.read_text(encoding="utf-8")
        # All four canonical labels MUST appear in the briefing emitter.
        for label in (
            "CSConsole_Severity",
            "Independent_BE_Priority",
            "Independent_BE_Class",
            "Days_Open",
        ):
            assert label in text, (
                f"adoptiq_backend.py briefing emitter must include "
                f"'{label}' label (R79/B6 contract)"
            )

    def test_briefing_skips_unclassified_label(self) -> None:
        """The briefing source MUST short-circuit on the
        ``UNCLASSIFIED`` token so it never leaks into the LLM prompt
        as Independent_BE_Class."""
        text = _ADOPTIQ_BACKEND.read_text(encoding="utf-8")
        assert_in_source(text, 'UNCLASSIFIED', label='text')
        assert_in_source(text, 'Independent_BE_Class', label='text')
