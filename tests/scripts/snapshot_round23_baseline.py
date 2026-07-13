"""Round 23 / R22-NEXT-001 — formatter render snapshot driver.

Drives ``create_compact_executive_report`` and
``create_executive_intelligence_report`` against the Round 19 golden
fixture WITH the multi-source ``make_extra_frames()`` extras passed
through, then dumps a stable KPI sidecar JSON alongside the rendered
``.docx`` so the closure-binding fix in ``app_simple.py`` can be
verified pre/post-fix without Snowflake mocking.

Usage
-----
    python -m tests.scripts.snapshot_round23_baseline --label pre_fix
    python -m tests.scripts.snapshot_round23_baseline --label post_fix

Outputs land in ``tests/golden/round23_baseline/``:

    {compact,ei}_<label>.docx        # rendered Word output (timestamp-noisy)
    {compact,ei}_<label>.kpis.json   # extracted, deterministic KPI dict

The KPI sidecars are the diff target. The ``.docx`` is kept for manual
inspection but should NOT be byte-diffed (timestamps + non-deterministic
docx ordering make that a false-positive trap).

Why this exists
---------------
The Round 22 priming inventory documented 22 closure-binding sites
inside ``run_compact_analysis``'s nested ``generate_report`` and
``generate_excel`` functions. Inside a nested Python function,
``'X' in locals()`` always evaluates ``False`` for free variables
captured from the enclosing scope, so every site of the form
``X if 'X' in locals() else FALLBACK`` silently emits the fallback.
The user-visible effect: csconsole-only customers are silently dropped
from the renewal universe and ``recent_window_days`` is hardcoded to
30 regardless of the analysis window.

This script does NOT exercise the buggy nested function path
(``run_compact_analysis`` requires Snowflake context that needs
invasive mocking — deferred to Round 23.1's Leader harness work).
Instead it pins the formatter-layer contract: given identical inputs,
the formatter must produce identical KPIs across the closure-binding
fix. If pre-fix and post-fix sidecars diverge, the fix accidentally
changed formatter behaviour and the diff investigation begins.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "round19"
OUTPUT_DIR = REPO_ROOT / "tests" / "golden" / "round23_baseline"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(GOLDEN_DIR) not in sys.path:
    sys.path.insert(0, str(GOLDEN_DIR))

from golden import (  # noqa: E402  -- after sys.path manipulation
    EXPECTED_KPIS,
    make_ab_df,
    make_csone_df,
    make_extra_frames,
)


# ---------------------------------------------------------------------------
# Shared docx parsing helpers (same shape as test_round21_1_formatter_render_diff)
# ---------------------------------------------------------------------------


def _docx_table_rows(table) -> List[List[str]]:
    rows: List[List[str]] = []
    for row in table.rows:
        rows.append([cell.text.strip() for cell in row.cells])
    return rows


def _find_kpi_in_tile_table(doc, header_label: str) -> Optional[str]:
    for table in doc.tables:
        rows = _docx_table_rows(table)
        if len(rows) < 2:
            continue
        header = rows[0]
        values = rows[1]
        for col_idx, label in enumerate(header):
            if label == header_label and col_idx < len(values):
                return values[col_idx]
    return None


def _find_kpi_in_metric_value_table(doc, metric_label: str) -> Optional[str]:
    for table in doc.tables:
        rows = _docx_table_rows(table)
        if not rows:
            continue
        for row in rows[1:]:
            if not row:
                continue
            if row[0] == metric_label and len(row) >= 2:
                return row[1]
    return None


# Tile labels we extract from BOTH formatters. Keep this list stable so
# the kpis.json sidecar shape is comparable across runs. Missing tiles
# emit ``null`` (json) rather than disappearing, so the diff can spot
# accidental tile removal too.
_COMMON_TILE_LABELS = (
    "Total Customers",
    "Support Cases",
    "Critical (P1)",
    "High (P2)",
    "BEMS Escalations",
    "Critical ABs",
    "Escalated Cases",
    "Software Defects",
    "Security Vulnerabilities",
)

# Metric-value table labels (Compact's verification table).
_COMPACT_VERIFICATION_LABELS = (
    "Total Adoption Barriers",
    "Total Support Cases",
)


def extract_kpis_from_doc(doc) -> Dict[str, Any]:
    """Extract a stable, JSON-serializable KPI dict from a rendered docx."""

    out: Dict[str, Any] = {
        "table_count": len(doc.tables),
        "tiles": {label: _find_kpi_in_tile_table(doc, label) for label in _COMMON_TILE_LABELS},
        "verification": {
            label: _find_kpi_in_metric_value_table(doc, label)
            for label in _COMPACT_VERIFICATION_LABELS
        },
    }
    return out


# ---------------------------------------------------------------------------
# Fixture builder: full Round 19 fixture WITH extras
# ---------------------------------------------------------------------------


def build_fixture_kwargs() -> Dict[str, Any]:
    """Build the canonical Round 19 fixture inputs WITH extras applied.

    Returns a dict of ``ab_data``, ``csone_data``, plus the four
    csconsole_* frames extracted from ``make_extra_frames()`` — this is
    the call shape a non-buggy ``run_compact_analysis`` would assemble.
    """
    extras = make_extra_frames()
    # make_extra_frames() returns a 4-tuple in this fixed order:
    #   [action_plans, customer_pulse, success_priorities, adoption_barriers]
    csconsole_action_plans = extras[0]
    csconsole_customer_pulse = extras[1]
    csconsole_success_priorities = extras[2]
    csconsole_adoption_barriers = extras[3]
    return {
        "ab_data": make_ab_df(),
        "csone_data": make_csone_df(),
        "csconsole_action_plans": csconsole_action_plans,
        "csconsole_customer_pulse": csconsole_customer_pulse,
        "csconsole_success_priorities": csconsole_success_priorities,
        "csconsole_adoption_barriers": csconsole_adoption_barriers,
    }


# ---------------------------------------------------------------------------
# Formatter drivers
# ---------------------------------------------------------------------------


def render_compact(out_path: Path) -> Dict[str, Any]:
    from compact_report_formatter import create_compact_executive_report
    from docx import Document

    fx = build_fixture_kwargs()
    create_compact_executive_report(
        analysis_id="r23-snapshot",
        manager="Manager Round23",
        technology="Webex",
        days=90,
        ab_data=fx["ab_data"],
        csone_data=fx["csone_data"],
        ai_insights={"executive_summary": "Round 23 closure-binding snapshot"},
        output_path=str(out_path),
        csconsole_action_plans=fx["csconsole_action_plans"],
        csconsole_customer_pulse=fx["csconsole_customer_pulse"],
        csconsole_success_priorities=fx["csconsole_success_priorities"],
        csconsole_adoption_barriers=fx["csconsole_adoption_barriers"],
    )
    if not out_path.exists():
        raise RuntimeError(f"Compact render did not write {out_path}")
    doc = Document(str(out_path))
    return extract_kpis_from_doc(doc)


def render_ei(out_path: Path) -> Dict[str, Any]:
    from executive_intelligence_formatter import create_executive_intelligence_report
    from docx import Document

    fx = build_fixture_kwargs()
    create_executive_intelligence_report(
        analysis_id="r23-snapshot",
        manager="Manager Round23",
        technology="Webex",
        days=90,
        ab_data=fx["ab_data"],
        csone_data=fx["csone_data"],
        ai_insights={"executive_summary": "Round 23 closure-binding snapshot"},
        ext_bugs=[],
        ext_incidents=[],
        risk_scores={},
        risk_summary={},
        output_path=str(out_path),
        csconsole_action_plans=fx["csconsole_action_plans"],
        csconsole_customer_pulse=fx["csconsole_customer_pulse"],
        csconsole_success_priorities=fx["csconsole_success_priorities"],
        csconsole_adoption_barriers=fx["csconsole_adoption_barriers"],
    )
    if not out_path.exists():
        raise RuntimeError(f"EI render did not write {out_path}")
    doc = Document(str(out_path))
    return extract_kpis_from_doc(doc)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Round 23 formatter snapshot driver")
    parser.add_argument(
        "--label",
        required=True,
        choices=("pre_fix", "post_fix"),
        help="snapshot label written into output filenames",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    label = args.label
    compact_docx = OUTPUT_DIR / f"compact_{label}.docx"
    compact_kpis = OUTPUT_DIR / f"compact_{label}.kpis.json"
    ei_docx = OUTPUT_DIR / f"ei_{label}.docx"
    ei_kpis = OUTPUT_DIR / f"ei_{label}.kpis.json"

    print(f"[snapshot] label={label}")
    print(f"[snapshot] rendering Compact -> {compact_docx.name}")
    compact_payload = render_compact(compact_docx)
    compact_kpis.write_text(json.dumps(compact_payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[snapshot]   tiles: {compact_payload['tiles']}")

    print(f"[snapshot] rendering EI -> {ei_docx.name}")
    ei_payload = render_ei(ei_docx)
    ei_kpis.write_text(json.dumps(ei_payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[snapshot]   tiles: {ei_payload['tiles']}")

    # Log the EXPECTED_KPIS multi-source customer count so the operator
    # can eyeball whether the formatter respected the extras.
    print(
        f"[snapshot] EXPECTED_KPIS['total_customers']={EXPECTED_KPIS['total_customers']} "
        f"(AB+CSOne+extras universe should yield this once the closure-binding fix is in place)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
