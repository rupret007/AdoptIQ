"""Report-orchestrator ownership boundaries for Comprehensive and Renewal."""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from data_normalization import (
    ACCOUNT_COLUMN_CANDIDATES,
    build_customer_lookup,
    customer_identity_key,
    normalize_customer_name,
    partition_customer_frame,
    quarantine_cross_customer_record_ids,
)
from risk_scoring import compute_customer_risk_profile


ROOT = Path(__file__).resolve().parent.parent
APP_PATH = ROOT / "app_simple.py"


def _load_partition_helpers() -> Dict[str, Any]:
    """Load the three pure helpers without importing the desktop app."""

    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    wanted = {
        "_r133_prepare_customer_partitions",
        "_r133_partition_for_customer",
        "_r133_canonical_customer_labels",
    }
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    assert {node.name for node in nodes} == wanted
    namespace: Dict[str, Any] = {
        "ACCOUNT_COLUMN_CANDIDATES": ACCOUNT_COLUMN_CANDIDATES,
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "build_customer_lookup": build_customer_lookup,
        "customer_identity_key": customer_identity_key,
        "logger": logging.getLogger(__name__),
        "normalize_customer_name": normalize_customer_name,
        "partition_customer_frame": partition_customer_frame,
        "pd": pd,
        "quarantine_cross_customer_record_ids": quarantine_cross_customer_record_ids,
    }
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, APP_PATH, "exec"), namespace)  # noqa: S102 - isolated pure helpers
    return namespace


def test_report_helper_quarantines_before_customer_slicing_and_keeps_attrs() -> None:
    helpers = _load_partition_helpers()
    prepare = helpers["_r133_prepare_customer_partitions"]
    select = helpers["_r133_partition_for_customer"]
    frame = pd.DataFrame(
        [
            {"SR_NUMBER": "SHARED-1", "BU_NAME": "Alpha", "Status": "Open"},
            {"SR_NUMBER": "SHARED-1", "BU_NAME": "Beta", "Status": "Closed"},
            {"SR_NUMBER": "ALPHA-2", "BU_NAME": "Alpha", "Status": "Open"},
        ]
    )

    empty, partitions = prepare(
        frame,
        customer_lookup=build_customer_lookup(pd.DataFrame()),
        customer_columns=("BU_NAME",),
    )
    alpha = select("Alpha", empty, partitions)
    beta = select("Beta", empty, partitions)

    assert alpha["SR_NUMBER"].tolist() == ["ALPHA-2"]
    assert beta.empty
    for scoped in (empty, alpha, beta):
        diag = scoped.attrs["cross_customer_id_conflicts"]
        assert diag["conflicting_ids"] == ["SHARED-1"]
        assert diag["quarantined_rows"] == 2


def test_conflict_only_report_input_cannot_publish_healthy() -> None:
    helpers = _load_partition_helpers()
    prepare = helpers["_r133_prepare_customer_partitions"]
    select = helpers["_r133_partition_for_customer"]
    frame = pd.DataFrame(
        [
            {"CaseNumber": "SHARED-1", "BU_NAME": "Alpha", "Status": "Open"},
            {"CaseNumber": "SHARED-1", "BU_NAME": "Beta", "Status": "Closed"},
        ]
    )
    empty, partitions = prepare(
        frame,
        customer_columns=("BU_NAME",),
    )
    support = select("Alpha", empty, partitions)

    profile = compute_customer_risk_profile(
        "Alpha",
        customer_ab=pd.DataFrame(),
        customer_csone=support,
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame(
            [{"BU_NAME": "Alpha", "STATUS_C": "Active"}]
        ),
        ext_incidents=[],
    )

    assert profile["risk_band"] == "UNKNOWN"
    assert profile["risk_score_0_100"] is None
    assert profile["evidence_quality"]["ownership_conflicts"]["support_cases"]


def test_report_customer_labels_collapse_case_and_registered_aliases() -> None:
    helpers = _load_partition_helpers()
    canonicalize = helpers["_r133_canonical_customer_labels"]
    labels = canonicalize(
        ["Acme", "ACME", "NYULH", "NYU LANGONE HEALTH"]
    )

    assert len(labels) == 2
    assert len({customer_identity_key(label) for label in labels}) == 2


def test_comprehensive_and_renewal_loops_use_full_source_partitions() -> None:
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    functions = {
        node.name: ast.get_source_segment(APP_PATH.read_text(encoding="utf-8"), node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"run_comprehensive_analysis", "run_customer_renewal_analysis"}
    }

    comprehensive = functions["run_comprehensive_analysis"] or ""
    renewal = functions["run_customer_renewal_analysis"] or ""
    assert "_r133_comprehensive_sources" in comprehensive
    assert "_r133_story_sources" in comprehensive
    assert "_r133_renewal_sources" in renewal
    assert "cust_csone = _r98_slice_customer_frame" not in renewal
