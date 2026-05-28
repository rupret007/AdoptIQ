"""Round 112 / Build 81 - Build 80 acceptance audit fix loop.

Pins the six fixes Round 112 landed in response to the Build 80
acceptance audit (P0 LLM error sanitizer leak + 4 P1 functional
regressions + 1 P2 cosmetic).  Each test names the audit finding ID
(F1-F6) and the SSoT module the fix targeted.  The full audit
trail lives in ``QUALITY_AUDIT.md`` under
``## Round 112 - handoff 2026-05-28``.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

import pytest


# ----------------------------------------------------------------------
# F1 (P0) - LLM error sanitizer leak (Compact P25 leaked appkey + session_id)
# ----------------------------------------------------------------------


class TestF1LLMErrorSanitizerLeak:
    """Round 112 / F1 / P0: LLM error sanitizer must redact CircuIT-
    specific identifiers (``appkey``, ``session_id``, ``'user': '{...}'``
    JSON blob) AND must be applied in the
    ``_generate_fallback_insights`` emit path.

    Pre-R112 the Compact docx P25 leaked the raw 429 body verbatim
    (Build 80 acceptance) -- enough for an attacker to attempt session-
    replay against the upstream API.
    """

    def test_sanitizer_redacts_appkey(self) -> None:
        from app_simple import _r69_sanitize_llm_error

        raw = (
            "Error code: 429 - {'error': {'message': '...'}, "
            "'user': '{\"appkey\": \"egai-prd-cx-020054933-summarize-1753732881229\"}'}"
        )
        sanitized = _r69_sanitize_llm_error(raw, max_len=500)
        assert "egai-prd-cx-020054933" not in sanitized
        # The literal string ``appkey`` (token name) is fine; the value is what matters.
        assert "1753732881229" not in sanitized

    def test_sanitizer_redacts_session_id(self) -> None:
        from app_simple import _r69_sanitize_llm_error

        raw = "..., \"session_id\": \"9403c41f-3828-40c3-84ca-4c32ac1d4ee6-1779993229030607727\", ..."
        sanitized = _r69_sanitize_llm_error(raw, max_len=500)
        assert "9403c41f-3828-40c3-84ca-4c32ac1d4ee6" not in sanitized
        assert "1779993229030607727" not in sanitized

    def test_sanitizer_redacts_circuit_user_json_blob(self) -> None:
        from app_simple import _r69_sanitize_llm_error

        raw = (
            "Error: 'user': '{\"appkey\": \"secret-key-xyz\", "
            "\"session_id\": \"sess-abc-123\", \"user\": \"\", "
            "\"prompt_truncate\": \"yes\"}' more text"
        )
        sanitized = _r69_sanitize_llm_error(raw, max_len=500)
        assert "secret-key-xyz" not in sanitized
        assert "sess-abc-123" not in sanitized
        # The collapsed marker should appear so downstream readers know
        # the blob existed.
        assert "<redacted>" in sanitized

    def test_sanitizer_preserves_non_credential_text(self) -> None:
        from app_simple import _r69_sanitize_llm_error

        raw = "ERROR: llm.rate_limit_429: Requests have exceeded monthly throughput"
        sanitized = _r69_sanitize_llm_error(raw, max_len=500)
        assert "rate_limit_429" in sanitized
        assert "monthly throughput" in sanitized

    def test_sanitizer_caps_max_length(self) -> None:
        from app_simple import _r69_sanitize_llm_error

        raw = "x" * 1000
        sanitized = _r69_sanitize_llm_error(raw, max_len=200)
        assert len(sanitized) <= 200

    def test_fallback_insights_call_site_uses_sanitizer(self) -> None:
        """Round 112 / F1: ``_generate_fallback_insights`` (the path that
        emitted the leak in Compact P25) MUST route ``llm_error``
        through ``_r69_sanitize_llm_error`` rather than emitting raw
        ``str(llm_error).strip()``.

        Source-shape pin: read app_simple.py and verify the buggy line
        is gone AND the sanitizer is invoked in the fallback path.
        """
        from pathlib import Path

        src = Path(__file__).parent.parent / "app_simple.py"
        text = src.read_text()
        # The buggy direct-str pattern must be absent.
        assert "f\"[LLM error] {str(llm_error).strip()}\"" not in text, (
            "Round 112 / F1: pre-R112 raw-emit pattern detected"
        )
        # The sanitizer must be invoked in the fallback path.
        assert "[LLM error] {_r69_sanitize_llm_error(llm_error" in text, (
            "Round 112 / F1: sanitizer not wired into fallback path"
        )


# ----------------------------------------------------------------------
# F2 (P1) - Renewal Word risk-score scale 0-100 -> 0-10
# ----------------------------------------------------------------------


class TestF2RenewalScoreScale:
    """Round 112 / F2 / P1: Renewal Word risk-score box + focus-table
    cells MUST display the 0-10 primary scale per R67/B1, not the
    legacy 0-100 form.

    Pre-R112 the Build 80 Renewal docx (P21) showed
    ``"Renewal Risk Score: 12.5/100 (HEALTHY)"`` while the executive
    summary block (P16) showed
    ``"overall renewal risk score of 1.2/10 (HEALTHY; 12.5/100)"``
    -- two different scales for the SAME score within the same
    document.
    """

    def test_renewal_summary_score_box_uses_0_10_primary(self) -> None:
        from pathlib import Path

        src = Path(__file__).parent.parent / "app_simple.py"
        text = src.read_text()
        # Pre-R112 buggy pattern must be absent.
        assert "f'{risk_score:.1f}/100 ({_r70_rsbox_label})'" not in text, (
            "Round 112 / F2: pre-R112 0-100-only Renewal score box detected"
        )
        # Post-R112 form must be present.
        assert "_r112_score_10" in text, (
            "Round 112 / F2: 0-10 primary form not wired into Renewal score box"
        )

    def test_renewal_focus_table_cell_uses_0_10_primary(self) -> None:
        from pathlib import Path

        src = Path(__file__).parent.parent / "app_simple.py"
        text = src.read_text()
        # Pre-R112 buggy pattern must be absent (the ``/100`` suffix on
        # the focus-table cell).
        bad = "f\"{ana.get('renewal_risk_score', ana.get('overall_risk_score', 0)):.1f}/100\""
        assert bad not in text, (
            "Round 112 / F2: pre-R112 0-100 focus-table cell detected"
        )
        # Post-R112 marker must be present.
        assert "_r112_focus_score_10" in text, (
            "Round 112 / F2: 0-10 form not wired into focus-table cell"
        )


# ----------------------------------------------------------------------
# F3 (P1) - Citation injector wrapper-KPI suppression
# ----------------------------------------------------------------------


class TestF3CitationInjectorWrapperSuppression:
    """Round 112 / F3 / P1: when a wrapper KPI sits IMMEDIATELY before
    an embedded paren cluster (whitespace only between value-end and
    ``(``), the citation injector MUST suppress the wrapper's citation
    -- the cluster's post-``)`` citation already covers BOTH the
    wrapper and the cluster body as a single semantic unit.

    Pre-R112 the Build 80 Leader docx P117 rendered
    ``"Total Activities: 42 [Source: ...](APs: 18, ABs: 3, CPs: 1,
    TAC: 20) [Source: ...]"`` -- two adjacent citations interrupting
    the semantic unit.  Post-R112: ONE citation immediately after
    the closing paren.

    Reference contracts: R76/R76-A (whole-line paren cluster),
    R76/Build 51 (embedded paren cluster), R66/B1 (unit-deferral),
    R90 (paren-balance filter).
    """

    def test_wrapper_kpi_before_embedded_cluster_suppresses_inner_citation(
        self,
    ) -> None:
        from report_source_injector import (
            _PARAGRAPH_KPI_NUMERIC_RE,
            _rewrite_paragraph_with_inline_citations,
        )

        # The exact P117 line shape (with `| Warning:` postfix that
        # produces the trailing match).
        line = (
            "Total Activities: 42 (APs: 18, ABs: 3, CPs: 1, TAC: 20) "
            "| Warning: BEMS Escalations: 5"
        )
        ms = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))
        out = _rewrite_paragraph_with_inline_citations(
            line, ms, "[Source: Snowflake CSOne]"
        )
        # The wrapper "42" should NOT have a citation between it and `(`.
        # Specifically: "42 [Source:" must not appear right before "(".
        assert "42 [Source: Snowflake CSOne](" not in out, (
            "Round 112 / F3: wrapper-KPI emitted citation before cluster"
        )
        # The wrapper-KPI value should sit immediately before `(` with
        # only whitespace between (the original shape).
        assert re.search(r"42\s+\(APs:", out), (
            "Round 112 / F3: wrapper-KPI value not immediately before cluster"
        )
        # The cluster collapse contract from R76/Build 51 says one
        # citation after the closing `)` covers BOTH the wrapper KPI
        # and the inner cluster matches; the trailing
        # `BEMS Escalations: 5` carries its own citation. So the
        # canonical post-fix output has 2 citations total, NOT 3.
        assert out.count("[Source: Snowflake CSOne]") == 2, (
            f"Round 112 / F3: expected exactly 2 citations on the line; "
            f"got {out.count('[Source: Snowflake CSOne]')} in {out!r}"
        )

    def test_wrapper_kpi_with_text_between_keeps_citation(self) -> None:
        """Negative control: if the wrapper isn't immediately followed by
        the cluster (text between), the citation MUST still emit (we
        haven't regressed the R66/B1 unit-deferral path)."""
        from report_source_injector import (
            _PARAGRAPH_KPI_NUMERIC_RE,
            _rewrite_paragraph_with_inline_citations,
        )

        line = "Total Activities: 42 then breakdown (APs: 18, ABs: 3) follows"
        ms = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))
        out = _rewrite_paragraph_with_inline_citations(line, ms, "[Source: Test]")
        # In this case the boundary contains "then" -- the cluster
        # qualification is disqualified by the joiner-word rule, so
        # citations land per R66/B1 unit-deferral.  Just verify the
        # output is non-empty and includes the citation chrome.
        assert "[Source: Test]" in out

    def test_other_paren_cluster_paragraphs_unchanged(self) -> None:
        """Negative control: the other Leader paragraphs (P137, P157
        etc.) that ALREADY rendered cleanly (single citation after
        ``)``) MUST not regress.  Single-run input with wrapper KPI
        immediately before cluster, no trailing matches.
        """
        from report_source_injector import (
            _PARAGRAPH_KPI_NUMERIC_RE,
            _rewrite_paragraph_with_inline_citations,
        )

        # P137 shape: single line, wrapper + cluster, no trailing match.
        line = "Total Activities: 10 (APs: 8, ABs: 0, CPs: 0, TAC: 2)"
        ms = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))
        out = _rewrite_paragraph_with_inline_citations(
            line, ms, "[Source: AdoptIQ Report Data Sources]"
        )
        # ONE citation only, after `)`.
        assert out.count("[Source: AdoptIQ Report Data Sources]") == 1
        assert out.endswith("[Source: AdoptIQ Report Data Sources]") or out.endswith(
            "[Source: AdoptIQ Report Data Sources] "
        )
        # No mid-string citation between value and `(`.
        assert "10 [Source: AdoptIQ Report Data Sources](" not in out


# ----------------------------------------------------------------------
# F4 (P1) - BE Classifier honesty narrative
# ----------------------------------------------------------------------


class TestF4BEClassifierHonesty:
    """Round 112 / F4 / P1: when the LLM classifier produces zero tags
    (rate-limit, content-filter, transient error) the BE intro
    paragraph MUST emit honest copy naming the failure mode -- NOT
    claim "the top N barriers were tagged" as if classification
    succeeded.

    Pre-R112 the Build 80 Comprehensive docx (P210) rendered
    ``"the top 11 barriers were also tagged by an LLM classifier"``
    while ``classified_count == 0`` and ``llm_error`` carried the
    rate-limit body.
    """

    def test_intro_text_when_classifier_failed_is_honest(self) -> None:
        from be_priority_word_section import _intro_text
        import pandas as pd

        focus_df = pd.DataFrame(
            [
                {
                    "sub_technology": "Webex Calling",
                    "ab_category_final": "Onboarding",
                    "open_count": 5,
                    "true_blocker_count": 2,
                    "customers_affected": 3,
                    "summed_priority": 100.0,
                    "cluster_focus_score": 50.0,
                }
            ]
        )
        diag = {
            "rows_scored": 11,
            "top_n_selected": 11,
            "llm_diag": {
                "input_count": 11,
                "classified_count": 0,
                "llm_disabled": False,
                "llm_error": "ERROR: llm.rate_limit_429: ...",
            },
        }
        text = _intro_text(None, focus_df, diag)
        # Honest copy must NOT claim tagging succeeded.
        assert "were also tagged by an LLM classifier" not in text, (
            "Round 112 / F4: dishonest 'were tagged' copy in classifier-failure path"
        )
        # Honest copy SHOULD name the failure mode.
        assert "did not return any tags" in text or "LLM classification was attempted" in text

    def test_intro_text_when_classifier_succeeded_uses_tagged_copy(self) -> None:
        from be_priority_word_section import _intro_text
        import pandas as pd

        focus_df = pd.DataFrame(
            [
                {
                    "sub_technology": "Webex Calling",
                    "ab_category_final": "Onboarding",
                    "open_count": 5,
                    "true_blocker_count": 2,
                    "customers_affected": 3,
                    "summed_priority": 100.0,
                    "cluster_focus_score": 50.0,
                }
            ]
        )
        diag = {
            "rows_scored": 11,
            "top_n_selected": 11,
            "llm_diag": {
                "input_count": 11,
                "classified_count": 11,
                "llm_disabled": False,
                "llm_error": "",
            },
        }
        text = _intro_text(None, focus_df, diag)
        assert "tagged" in text.lower()

    def test_intro_text_when_classifier_disabled_says_disabled(self) -> None:
        from be_priority_word_section import _intro_text
        import pandas as pd

        focus_df = pd.DataFrame(
            [
                {
                    "sub_technology": "Webex Calling",
                    "ab_category_final": "Onboarding",
                    "open_count": 5,
                    "true_blocker_count": 2,
                    "customers_affected": 3,
                    "summed_priority": 100.0,
                    "cluster_focus_score": 50.0,
                }
            ]
        )
        diag = {
            "rows_scored": 11,
            "top_n_selected": 11,
            "llm_diag": {"llm_disabled": True, "classified_count": 0},
        }
        text = _intro_text(None, focus_df, diag)
        assert "disabled" in text.lower()


# ----------------------------------------------------------------------
# F5 (P1) - Partial Data Warning kind-aware copy
# ----------------------------------------------------------------------


class TestF5PartialDataWarningKindAware:
    """Round 112 / F5 / P1: when all partial-data warnings are scope-
    filter exclusions (``kind='tech_filter_scope_excluded'``,
    ``manager_filter_scope_excluded``, etc.) the banner preamble MUST
    say "data was filtered out by the requested scope" instead of
    "upstream data sources failed to load".

    Pre-R112 the Build 80 Renewal docx (P6) rendered the load-failure
    boilerplate when the only warning was a scope filter -- misleading
    the operator into thinking data fetch failed.
    """

    def test_compact_kind_aware_branch_present(self) -> None:
        from pathlib import Path

        src = Path(__file__).parent.parent / "app_simple.py"
        text = src.read_text()
        # The R112 source marker must be present at BOTH banner sites.
        # The first site is the compact path (R46/F-COMP-DQ-BANNER)
        # ~L7560.  Verify the kind-set + the scope-aware preamble.
        assert "_r112_scope_kinds" in text, (
            "Round 112 / F5: scope-kind set not declared at banner sites"
        )
        assert "filtered out by the requested scope" in text, (
            "Round 112 / F5: scope-aware preamble not present"
        )
        # Both occurrences should be present (compact + renewal).
        assert text.count("_r112_all_scope") >= 2, (
            "Round 112 / F5: scope-aware branch not wired into both banner sites"
        )


# ----------------------------------------------------------------------
# F6 (P2) - Smart possessive helper for "All Managers"
# ----------------------------------------------------------------------


class TestF6SmartPossessive:
    """Round 112 / F6 / P2: ``r112_smart_possessive`` returns the bare
    name for the literal sentinel ``"All Managers"`` so report titles
    render as ``"All Managers Portfolio"`` instead of the pre-R112
    ``"All Managers's Portfolio"`` (double possessive).
    """

    def test_all_managers_returns_bare_name(self) -> None:
        from report_utils import r112_smart_possessive

        assert r112_smart_possessive("All Managers") == "All Managers"

    def test_all_managers_case_insensitive(self) -> None:
        from report_utils import r112_smart_possessive

        assert r112_smart_possessive("all managers") == "all managers"
        assert r112_smart_possessive("ALL MANAGERS") == "ALL MANAGERS"

    def test_normal_name_takes_apostrophe_s(self) -> None:
        from report_utils import r112_smart_possessive

        assert r112_smart_possessive("Brian Frazier") == "Brian Frazier's"

    def test_sibilant_final_name_takes_apostrophe_only(self) -> None:
        from report_utils import r112_smart_possessive

        assert r112_smart_possessive("Charles") == "Charles'"
        assert r112_smart_possessive("Alex") == "Alex'"

    def test_empty_input_returns_empty(self) -> None:
        from report_utils import r112_smart_possessive

        assert r112_smart_possessive("") == ""
        assert r112_smart_possessive(None) == ""
        assert r112_smart_possessive("   ") == ""

    def test_app_simple_call_sites_use_helper(self) -> None:
        """Source-shape pin: every ``f"{...}'s Portfolio"`` site in
        ``app_simple.py`` must be routed through the helper."""
        from pathlib import Path

        src = Path(__file__).parent.parent / "app_simple.py"
        text = src.read_text()
        # Pre-R112 raw possessive pattern must be absent.
        assert "'s Portfolio" not in text or text.count("'s Portfolio") <= 0, (
            f"Round 112 / F6: pre-R112 raw possessive sites still present: "
            f"{text.count(chr(39) + 's Portfolio')} occurrences"
        )

    def test_adoptiq_backend_call_sites_use_helper(self) -> None:
        from pathlib import Path

        src = Path(__file__).parent.parent / "adoptiq_backend.py"
        text = src.read_text()
        # The non-comment ``'s Portfolio`` literals should be absent.
        # We allow it inside comments / docstrings (the prompt template
        # at L13619 references ``{MANAGER}'s Portfolio`` as a template
        # substitution placeholder; that's documentation, not code).
        # Count only lines that contain ``f"{`` AND ``'s Portfolio``.
        lines_with_raw_possessive = [
            line
            for line in text.splitlines()
            if "f\"" in line and "'s Portfolio" in line
        ]
        assert not lines_with_raw_possessive, (
            f"Round 112 / F6: pre-R112 raw f-string possessive sites still "
            f"present in adoptiq_backend.py: {lines_with_raw_possessive[:3]}"
        )
