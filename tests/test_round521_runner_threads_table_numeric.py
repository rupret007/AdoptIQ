"""Round 52.1 / Phase 4: end-to-end runner wiring for the table gate.

Verifies (without requiring a live AdoptIQ server) that the new
``--min-docx-table-numeric-similarity`` CLI option is parsed, lands on
``RunnerConfig.min_docx_table_numeric_similarity``, is reported in
``thresholds_summary``, and is forwarded to ``compare_docx_against_baseline``
when the runner triggers a baseline comparison.
"""

from __future__ import annotations

import inspect
from argparse import Namespace

from report_iteration_loop import (
    build_arg_parser,
    build_runner_config,
    compare_docx_against_baseline,
    thresholds_summary,
)


def _full_args(**overrides) -> Namespace:
    defaults = {
        "base_url": "http://127.0.0.1:5151",
        "downloads_dir": "~/Downloads",
        "iterations": 1,
        "scenarios": "all",
        "poll_interval": 5.0,
        "timeout": 1800,
        "run_id": "test",
        "stop_on_failure": False,
        "baseline_mode": "latest",
        "baseline_manifest": "",
        "init_baseline": False,
        "init_baseline_dir": "",
        "init_baseline_label": "",
        "strict": False,
        "min_docx_similarity": 0.35,
        "min_sheet_overlap": 0.5,
        "min_header_similarity": 0.3,
        "min_docx_chars": 200,
        "min_docx_numeric_similarity": 0.8,
        "min_docx_table_numeric_similarity": 0.95,
        "max_xlsx_row_delta_ratio": 0.2,
        "max_xlsx_row_delta_abs": 25,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def test_round521_cli_argument_registered_with_default_0_95():
    """``--min-docx-table-numeric-similarity`` must default to 0.95 so
    the gate is binding without requiring callers to remember the flag."""
    parser = build_arg_parser()
    parsed = parser.parse_args([])
    assert hasattr(parsed, "min_docx_table_numeric_similarity")
    assert parsed.min_docx_table_numeric_similarity == 0.95


def test_round521_cli_argument_parses_explicit_override():
    parser = build_arg_parser()
    parsed = parser.parse_args(["--min-docx-table-numeric-similarity", "0.99"])
    assert parsed.min_docx_table_numeric_similarity == 0.99


def test_round521_runner_config_threads_table_numeric_default_through():
    cfg = build_runner_config(_full_args())
    assert cfg.min_docx_table_numeric_similarity == 0.95


def test_round521_runner_config_threads_table_numeric_override_through():
    cfg = build_runner_config(_full_args(min_docx_table_numeric_similarity=0.99))
    assert cfg.min_docx_table_numeric_similarity == 0.99


def test_round521_thresholds_summary_surfaces_table_numeric_floor():
    """``thresholds_summary`` is what ends up in the per-run summary
    JSON.  The new floor MUST be in there so the dev (and Claude in the
    next session) can see at-a-glance which gate is active."""
    cfg = build_runner_config(_full_args(min_docx_table_numeric_similarity=0.97))
    summary = thresholds_summary(cfg)
    assert summary["min_docx_table_numeric_similarity"] == 0.97


def test_round521_compare_signature_accepts_min_table_numeric_kwarg():
    """Signature contract: the gate must accept and use the new kwarg."""
    sig = inspect.signature(compare_docx_against_baseline)
    assert "min_table_numeric_similarity" in sig.parameters, (
        "compare_docx_against_baseline must accept min_table_numeric_similarity"
    )
    # Default must be 0.95 so callers that forget the kwarg still get
    # the binding gate.
    default = sig.parameters["min_table_numeric_similarity"].default
    assert default == 0.95, (
        f"min_table_numeric_similarity default must be 0.95; got {default!r}"
    )


def test_round521_runner_call_site_forwards_effective_table_floor():
    """Source-shape AST/string contract: the runner's docx baseline
    branch must call ``compare_docx_against_baseline`` with
    ``min_table_numeric_similarity=...`` from ``effective_docx_thresholds``.

    Pinning this in source rather than via a live HTTP run keeps the
    test fast and protocol-agnostic, while still catching the most
    likely regression (someone refactors the runner and drops the
    threshold kwarg).
    """
    import report_iteration_loop as mod

    src = inspect.getsource(mod)

    # The runner branch unpacks the 3-tuple from effective_docx_thresholds
    # and forwards the table-only floor to the gate.
    assert "min_text, min_numeric, min_table_numeric" in src, (
        "runner must unpack the Round 52.1 3-tuple from "
        "effective_docx_thresholds"
    )
    assert "min_table_numeric_similarity=min_table_numeric" in src, (
        "runner must forward min_table_numeric to the docx baseline gate"
    )
