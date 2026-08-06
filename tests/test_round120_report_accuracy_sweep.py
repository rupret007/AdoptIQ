"""Round 120 / Build 89 — report-accuracy sweep (F1-F7).

The Build 88 acceptance audit of the four newest reports surfaced six
model-independent accuracy/polish issues plus three deferred Leader nits.
This file pins each fix (source-shape + behavior).

F1 — Comprehensive customer-count contradiction: title page / Excel headline
     ``Customers in portfolio: 33`` vs Portfolio Overview ``Total Customers:
     13``.  Decision: relabel the narrow value (R47-COMP-CUSTCOUNT-PARITY
     contract preserved; no count logic changed).
F2 — Compact citation jammed against ``|`` in the Risk Summary tile
     (``...1.2 [Source: ...]| High Risk Customers: 3``).
F3 — Compact non-AI fallback leaked ``[LLM error] ERROR: ...`` into
     customer prose; humanize into ``Reason: ...`` (sanitizer still runs).
F4 — Renewal ``Status: Unknown, Type: unknown`` bracket flood (232x);
     build the bracket from KNOWN fields only.
F5 — Leader grammar: ``Portfolio shows 1 adoption barriers`` -> singular.
F6 — Leader TOTAL-row ``Team Avg`` mislabel in the Sentiment column.
F7 — Leader bare ``Unknown`` sentinel rows in the Customer Health table.

Round 120.  Made-with: Cursor.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source, assert_not_in_source

from pathlib import Path

import pandas as pd

_APP_SIMPLE = Path(__file__).parent.parent / "app_simple.py"
_INJECTOR = Path(__file__).parent.parent / "report_source_injector.py"
_LEADER = Path(__file__).parent.parent / "leader_report_generator.py"
_CITATION = "[Source: AdoptIQ Report Data Sources]"


# ---------------------------------------------------------------------------
# F1 — Comprehensive customer-count contradiction (relabel narrow universe)
# ---------------------------------------------------------------------------


class TestF1ComprehensiveNarrowRelabel:
    def test_portfolio_overview_narrow_label_relabeled(self) -> None:
        text = _APP_SIMPLE.read_text()
        # The new disambiguated label is present.
        assert (
            "Customers with adoption barriers, support cases, or pulse activity:"
            in text
        ), "Round 120 / F1: narrow Portfolio Overview label not relabeled"

    def test_dashboard_tile_header_relabeled(self) -> None:
        text = _APP_SIMPLE.read_text()
        assert_in_source(text, "'Customers (AB/Cases/Pulse)'", label='text')

    def test_old_ambiguous_total_customers_label_gone(self) -> None:
        """The narrow value must no longer render under the bare
        ``Total Customers`` label that collided with the 33 headline."""
        text = _APP_SIMPLE.read_text()
        assert (
            "f'• Total Customers: {total_customers_canonical_narrow}\\n'" not in text
        ), "Round 120 / F1: stale ambiguous '• Total Customers:' narrow label present"

    def test_narrow_count_variable_still_drives_both_surfaces(self) -> None:
        """Presentation-only: the SAME canonical narrow variable still
        backs both the Executive Summary line and the dashboard tile (no
        count logic changed)."""
        text = _APP_SIMPLE.read_text()
        assert_in_source(text, "{total_customers_canonical_narrow}\\n'", label='text')
        assert_in_source(text, "str(total_customers_canonical_narrow)", label='text')


# ---------------------------------------------------------------------------
# F2 — Compact citation jammed against ``|`` separator
# ---------------------------------------------------------------------------


_PIPE_TILE = (
    "Risk Summary: Overall Risk Score: 1.2 | High Risk Customers: 3 "
    "| Medium Risk Customers: 5"
)


class TestF2PipeSeparatedCitation:
    def _rewrite(self, line: str) -> str:
        from report_source_injector import (
            _PARAGRAPH_KPI_NUMERIC_RE,
            _rewrite_paragraph_with_inline_citations,
        )

        ms = list(_PARAGRAPH_KPI_NUMERIC_RE.finditer(line))
        return _rewrite_paragraph_with_inline_citations(line, ms, _CITATION)

    def test_no_citation_jammed_against_pipe(self) -> None:
        out = self._rewrite(_PIPE_TILE)
        assert "]|" not in out, (
            f"Round 120 / F2: citation jammed against '|'. Got: {out!r}"
        )

    def test_space_preserved_before_pipe(self) -> None:
        out = self._rewrite(_PIPE_TILE)
        # Every ``|`` separator keeps a leading space.
        assert " |" in out and "|H" not in out and "|M" not in out, (
            f"Round 120 / F2: missing space before pipe separator. Got: {out!r}"
        )

    def test_first_value_still_cited(self) -> None:
        out = self._rewrite(_PIPE_TILE)
        assert _CITATION in out, "Round 120 / F2: tile lost its citation"
        # The score value remains intact and a citation follows it.
        assert "1.2 " + _CITATION in out, (
            f"Round 120 / F2: first value lost adjacency. Got: {out!r}"
        )

    def test_punctuation_adjacent_value_is_no_op(self) -> None:
        """For ``value.``/``value,`` boundaries ``value_end == m.end()`` so
        the fix changes nothing -- preserves the R57 sentence-style
        contract."""
        line = "Customers: 52. Adoption Barriers: 68."
        out = self._rewrite(line)
        assert _CITATION in out
        assert "]|" not in out


# ---------------------------------------------------------------------------
# F3 — Compact non-AI fallback humanizes the LLM error (no raw marker)
# ---------------------------------------------------------------------------


class TestF3FallbackHumanizedReason:
    def test_humanizer_empty_when_no_error(self) -> None:
        from app_simple import _r120_humanize_fallback_reason

        assert _r120_humanize_fallback_reason(None) == ""
        assert _r120_humanize_fallback_reason("") == ""

    def test_rate_limit_humanized(self) -> None:
        from app_simple import _r120_humanize_fallback_reason

        out = _r120_humanize_fallback_reason("ERROR: llm.rate_limit_429: too many requests")
        assert out.startswith("Reason: ")
        assert "rate limiting" in out.lower()

    def test_no_raw_marker_or_error_token_leaks(self) -> None:
        from app_simple import _r120_humanize_fallback_reason

        for raw in (
            "ERROR: llm.rate_limit_429: blah",
            "ERROR: timeout after 30s",
            "ERROR: llm.server_error 503 bad gateway",
            "ERROR: content_filter blocked",
            "ERROR: network unreachable",
            "ERROR: invalid api key 401",
            "ERROR: empty response",
            "some totally unrecognized message",
        ):
            out = _r120_humanize_fallback_reason(raw)
            assert "[LLM error]" not in out
            assert "ERROR:" not in out
            assert out.startswith("Reason: ")

    def test_fallback_bundle_does_not_leak_raw_error(self) -> None:
        from app_simple import _generate_comprehensive_fallback_insights

        bundle = _generate_comprehensive_fallback_insights(
            ab_norm=None,
            csone_df=None,
            manager="Test Manager",
            technology="All Contact Center",
            llm_error="ERROR: llm.rate_limit_429: monthly throughput exceeded",
        )
        assert "[LLM error]" not in bundle
        assert "ERROR:" not in bundle
        assert "Reason:" in bundle

    def test_sanitizer_still_runs_first_inside_humanizer(self) -> None:
        """R112/R118 contract: credential redaction must still happen
        before classification (the humanizer calls the sanitizer first)."""
        from app_simple import _r120_humanize_fallback_reason

        out = _r120_humanize_fallback_reason(
            "ERROR: auth failed Bearer sk-secret-token-12345 invalid"
        )
        assert "sk-secret-token-12345" not in out
        assert out.startswith("Reason: ")


# ---------------------------------------------------------------------------
# F4 — Renewal TAC bracket built from KNOWN fields only
# ---------------------------------------------------------------------------


class TestF4KpiDetailBracket:
    def test_all_known_renders_full_bracket(self) -> None:
        from app_simple import _r120_kpi_detail_bracket

        out = _r120_kpi_detail_bracket(
            [("Severity", "P2"), ("Status", "Open"), ("Type", "break-fix")]
        )
        assert out == " [Severity: P2, Status: Open, Type: break-fix]"

    def test_unknown_status_and_type_omitted(self) -> None:
        from app_simple import _r120_kpi_detail_bracket

        out = _r120_kpi_detail_bracket(
            [("Severity", "P3"), ("Status", "Unknown"), ("Type", "unknown")]
        )
        assert out == " [Severity: P3]"
        assert "Unknown" not in out and "unknown" not in out

    def test_na_omitted(self) -> None:
        from app_simple import _r120_kpi_detail_bracket

        out = _r120_kpi_detail_bracket(
            [("Severity", "N/A"), ("Status", "Closed"), ("Type", "N/A")]
        )
        assert out == " [Status: Closed]"

    def test_all_unknown_returns_empty(self) -> None:
        from app_simple import _r120_kpi_detail_bracket

        out = _r120_kpi_detail_bracket(
            [("Severity", "N/A"), ("Status", "Unknown"), ("Type", "unknown")]
        )
        assert out == ""

    def test_age_str_preserved_with_known_pairs(self) -> None:
        from app_simple import _r120_kpi_detail_bracket

        out = _r120_kpi_detail_bracket(
            [("Severity", "P1"), ("Status", "Unknown"), ("Type", "unknown")],
            age_str=", opened 2026-05-01, 28 days open",
        )
        assert out == " [Severity: P1, opened 2026-05-01, 28 days open]"

    def test_age_str_only_strips_leading_separator(self) -> None:
        from app_simple import _r120_kpi_detail_bracket

        out = _r120_kpi_detail_bracket(
            [("Severity", "Unknown"), ("Status", "Unknown"), ("Type", "unknown")],
            age_str=", opened 2026-05-01, 28 days open",
        )
        assert out == " [opened 2026-05-01, 28 days open]"

    def test_source_shape_bracket_built_via_helper(self) -> None:
        text = _APP_SIMPLE.read_text()
        assert_in_source(text, "_r120_kpi_detail_bracket(", label='text')
        # The pre-R120 unconditional Type bracket must be gone.
        assert (
            "f' [Severity: {severity}, Status: {status}, Type: {case_type}{age_str}]'"
            not in text
        ), "Round 120 / F4: pre-R120 unconditional TAC bracket present"


# ---------------------------------------------------------------------------
# F5 — Leader portfolio-overview pluralization
# ---------------------------------------------------------------------------


class TestF5Pluralize:
    def test_singular(self) -> None:
        from leader_report_generator import _r120_pluralize

        assert _r120_pluralize(1, "adoption barrier") == "1 adoption barrier"
        assert _r120_pluralize(1, "TAC case") == "1 TAC case"
        assert _r120_pluralize(1, "customer account") == "1 customer account"

    def test_plural(self) -> None:
        from leader_report_generator import _r120_pluralize

        assert _r120_pluralize(0, "action plan") == "0 action plans"
        assert _r120_pluralize(3, "TAC case") == "3 TAC cases"

    def test_explicit_irregular_plural(self) -> None:
        from leader_report_generator import _r120_pluralize

        assert _r120_pluralize(2, "entry", "entries") == "2 entries"
        assert _r120_pluralize(1, "entry", "entries") == "1 entry"

    def test_non_int_falls_back_to_plural(self) -> None:
        from leader_report_generator import _r120_pluralize

        assert _r120_pluralize("n/a", "barrier") == "n/a barriers"

    def test_source_shape_uses_helper(self) -> None:
        text = _LEADER.read_text()
        assert_in_source(text, "_r120_pluralize(total_barriers, 'adoption barrier')", label='text')
        assert_in_source(text, "_r120_pluralize(total_tac_cases, 'TAC case')", label='text')
        # The pre-R120 hard-plural string is gone.
        assert (
            "Portfolio shows {total_barriers} adoption barriers, "
            "{total_action_plans} action plans" not in text
        )


# ---------------------------------------------------------------------------
# F6 — Leader TOTAL-row 'Team Avg' mislabel
# ---------------------------------------------------------------------------


class TestF6TeamAvgMislabel:
    def test_team_avg_label_gone_from_sentiment_column(self) -> None:
        text = _LEADER.read_text()
        assert_not_in_source(text, 'totals_cells[6].text = "Team Avg"', label='text')

    def test_sentiment_total_cell_is_emdash(self) -> None:
        text = _LEADER.read_text()
        assert_in_source(text, 'totals_cells[6].text = "—"', label='text')

    def test_grand_total_still_in_total_activities_column(self) -> None:
        text = _LEADER.read_text()
        # col 7 (Total Activities) still holds the grand sum.
        assert_in_source(text, "totals_cells[7].text = str(_grand_total)", label='text')


# ---------------------------------------------------------------------------
# F7 — Leader bare 'Unknown' sentinel rows in Customer Health table
# ---------------------------------------------------------------------------


class TestF7UnknownSentinelRows:
    def test_sentinel_detector(self) -> None:
        from leader_report_generator import _r120_is_customer_sentinel

        for s in ("Unknown", "unknown", "", "N/A", "  none ", "nan", "—", None):
            assert _r120_is_customer_sentinel(s) is True
        for real in ("Acme Corp", "WORLD BANK GROUP US", "Cisco Systems"):
            assert _r120_is_customer_sentinel(real) is False

    def test_compute_customer_health_skips_unknown_bucket(self) -> None:
        from leader_report_generator import LeaderReportGenerator

        gen = LeaderReportGenerator.__new__(LeaderReportGenerator)
        # Customer Pulse with one real customer and one missing (NaN -> 'Unknown')
        cp = pd.DataFrame(
            {
                "BU_NAME": ["Acme Corp", None, "Acme Corp", None],
                "SCORE__C": [8.0, 4.0, 6.0, 5.0],
            }
        )
        data = {"action_plans": None, "adoption_barriers": None, "customer_pulse": cp}
        rows = gen._compute_customer_health(data)
        names = {r["customer"] for r in rows}
        assert "Unknown" not in names, (
            f"Round 120 / F7: bare 'Unknown' row not suppressed. Got: {names}"
        )
        # The real customer survives.
        assert "Acme Corp" in names

    def test_source_shape_filter_present(self) -> None:
        text = _LEADER.read_text()
        assert_in_source(text, "_r120_is_customer_sentinel(name)", label='text')
