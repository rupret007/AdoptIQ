"""Round 52.1 / Phase 3: per-scenario numeric override resolution.

Pins the contract that ``effective_docx_thresholds`` returns a 3-tuple
``(min_text, min_numeric, min_table_numeric)`` and that the
``SCENARIO_DOCX_THRESHOLD_OVERRIDES['comprehensive']`` block carries
both a relaxed text floor (0.40) and a relaxed informational numeric
floor (0.55), while the table-only gate inherits the runner-wide
default for every scenario.

Also verifies that compact / renewal / leader inherit the runner
defaults for ALL three thresholds (no override leakage).
"""

from __future__ import annotations

from pathlib import Path

from report_iteration_loop import (
    SCENARIO_DOCX_THRESHOLD_OVERRIDES,
    RunnerConfig,
    effective_docx_thresholds,
)


def _make_config(
    *,
    min_text: float = 0.55,
    min_numeric: float = 0.80,
    min_table_numeric: float = 0.95,
) -> RunnerConfig:
    return RunnerConfig(
        base_url="",
        downloads_dir=Path("."),
        iterations=1,
        poll_interval_seconds=1.0,
        scenario_timeout_seconds=60,
        run_id="x",
        stop_on_failure=False,
        scenario_keys=[],
        baseline_mode="manifest",
        min_docx_similarity=min_text,
        min_sheet_overlap=0.85,
        min_header_similarity=0.8,
        min_docx_chars=200,
        strict=True,
        min_docx_numeric_similarity=min_numeric,
        min_docx_table_numeric_similarity=min_table_numeric,
        max_xlsx_row_delta_ratio=0.2,
        max_xlsx_row_delta_abs=25,
    )


def test_round521_effective_thresholds_returns_three_tuple_for_comprehensive():
    """Comprehensive must apply BOTH overrides (text=0.40, numeric=0.55)
    and inherit the table-only floor from the runner config."""
    cfg = _make_config()
    text, numeric, table_numeric = effective_docx_thresholds(cfg, "comprehensive")
    assert text == 0.40
    assert numeric == 0.55, (
        "Round 52.1 must override comprehensive's overall numeric to 0.55 "
        f"(informational); got {numeric!r}"
    )
    assert table_numeric == 0.95, (
        "comprehensive must inherit the runner-wide table-only floor; "
        f"got {table_numeric!r}"
    )


def test_round521_effective_thresholds_inherit_defaults_for_other_scenarios():
    cfg = _make_config()
    for scenario in ("compact", "renewal", "leader"):
        text, numeric, table_numeric = effective_docx_thresholds(cfg, scenario)
        assert text == 0.55, f"{scenario} text floor must inherit defaults"
        assert numeric == 0.80, f"{scenario} numeric floor must inherit defaults"
        assert table_numeric == 0.95, (
            f"{scenario} table-only floor must inherit defaults"
        )


def test_round521_overrides_dict_keeps_only_comprehensive_block():
    """No other scenario must accidentally pick up an override block."""
    assert "comprehensive" in SCENARIO_DOCX_THRESHOLD_OVERRIDES
    assert "compact" not in SCENARIO_DOCX_THRESHOLD_OVERRIDES
    assert "renewal" not in SCENARIO_DOCX_THRESHOLD_OVERRIDES
    assert "leader" not in SCENARIO_DOCX_THRESHOLD_OVERRIDES


def test_round521_comprehensive_override_block_carries_both_relaxations():
    """Both relaxations must live in the override block (not at runner
    config level) so the runner-wide defaults stay tight for the other
    three scenarios."""
    block = SCENARIO_DOCX_THRESHOLD_OVERRIDES["comprehensive"]
    assert block["min_docx_similarity"] == 0.40
    assert block["min_docx_numeric_similarity"] == 0.55, (
        "comprehensive must explicitly relax overall numeric to 0.55 in "
        "the per-scenario override (Round 52.1)"
    )
    # The table-only key MUST NOT live in the override block -- the
    # whole point of the table-only gate is that the same floor (0.95)
    # binds for every scenario.
    assert "min_docx_table_numeric_similarity" not in block, (
        "table-only floor must inherit from the runner config, not from "
        "the per-scenario override; otherwise comprehensive could be "
        "silently relaxed and lose its real-data-drift guard"
    )


def test_round521_runner_config_defaults_used_when_no_override(monkeypatch):
    """If the runner-wide config picks a higher table-only floor at the
    CLI, scenarios with no override must respect it."""
    cfg = _make_config(min_table_numeric=0.99)
    for scenario in ("comprehensive", "compact", "renewal", "leader"):
        _, _, table_numeric = effective_docx_thresholds(cfg, scenario)
        assert table_numeric == 0.99, (
            f"{scenario} must respect the runner-wide table-only floor of "
            f"0.99; got {table_numeric!r}"
        )
