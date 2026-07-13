"""Round 4 days-window threading test.

Pin the two Round 3 misses (and Round 4's renewal-analyzer 30-day
literal) so they cannot regress:

  * ``app_simple.py`` MUST pass ``days=`` to ``run_intel_grounded_ask_ai``
    so the grounded prompt sees the same window as the user-requested
    analysis horizon.
  * ``app_simple.py`` MUST pass ``days_back=days`` to
    ``get_all_external_intel`` so external intel scoping matches the
    rest of the briefing.
  * ``advanced_renewal_analyzer._get_usage_adoption_metrics`` /
    ``_get_support_engagement_metrics`` MUST NOT contain the literal
    ``DATEADD(day, -30,`` after Round 4 — the 30-day cut should be
    parameterized.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_run_intel_grounded_ask_ai_called_with_days():
    """Round 4: the grounded Ask-AI call site must thread the user
    ``days`` value into ``run_intel_grounded_ask_ai`` rather than using
    the function default of 30.
    """
    src = _read(REPO_ROOT / "app_simple.py")
    pattern = re.compile(
        r"run_intel_grounded_ask_ai\([^)]*\bdays\s*=",
        re.DOTALL,
    )
    assert pattern.search(src), (
        "run_intel_grounded_ask_ai must be invoked with an explicit "
        "days= keyword in app_simple.py so the prompt window matches "
        "the user-requested analysis horizon."
    )


def test_get_all_external_intel_called_with_days_back():
    """Round 4: ``get_all_external_intel`` must receive the same
    ``days_back`` as the surrounding briefing so headline counts and
    rendered lists agree on horizon (default was a hardcoded 365).
    """
    src = _read(REPO_ROOT / "app_simple.py")
    pattern = re.compile(
        r"get_all_external_intel\([^)]*\bdays_back\s*=",
        re.DOTALL,
    )
    assert pattern.search(src), (
        "get_all_external_intel must be invoked with an explicit "
        "days_back= keyword in app_simple.py."
    )


def test_advanced_renewal_analyzer_no_hardcoded_30_day_window():
    """Round 4: the renewal analyzer must not contain
    ``DATEADD(day, -30,`` literals.  All recent-activity windows must
    be parameterized so a 7-day analysis cannot silently report the
    last 30 days as "recent".
    """
    src = _read(REPO_ROOT / "advanced_renewal_analyzer.py")
    assert "DATEADD(day, -30," not in src, (
        "advanced_renewal_analyzer.py must not contain hardcoded "
        "DATEADD(day, -30, ...) literals; route through the bound "
        "``days`` parameter instead."
    )


def test_grounded_prompt_announces_analysis_window():
    """Round 4: both grounded paths must inject ``Analysis window:``
    and ``Data retrieved at:`` into the user prompt so the LLM cannot
    describe "recent" trends without a horizon.
    """
    src = _read(REPO_ROOT / "ask_ai_grounded.py")
    assert "Analysis window: last" in src, (
        "Grounded Ask-AI prompts must announce the analysis window "
        "explicitly to ground LLM temporal claims."
    )
    assert "Data retrieved at:" in src, (
        "Grounded Ask-AI prompts must include the data retrieval "
        "timestamp so stale-data answers are detectable."
    )
