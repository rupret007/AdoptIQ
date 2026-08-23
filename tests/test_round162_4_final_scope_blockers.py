"""Round 162.4 — final criteria-scope and source-depth regressions."""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd

import canonical_metrics as cm
import canonical_report_adapter as adapter
from adoptiq_backend import (
    _filter_csconsole_data_by_technology,
    _scope_action_plans_for_report,
)
from compact_report_formatter import calculate_renewal_risk_scores


_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "app_simple.py"


def _compact_worker() -> ast.FunctionDef:
    tree = ast.parse(_APP.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_compact_analysis"
    )


def _dict_assignment(function: ast.FunctionDef, target_name: str) -> ast.Dict:
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == target_name:
            assert isinstance(node.value, ast.Dict)
            return node.value
    raise AssertionError(f"{target_name} assignment not found")


def _dict_value(mapping: ast.Dict, key_name: str) -> ast.AST:
    for key, value in zip(mapping.keys, mapping.values):
        if isinstance(key, ast.Constant) and key.value == key_name:
            return value
    raise AssertionError(f"{key_name!r} key not found")


def test_named_technology_without_authoritative_fields_is_unavailable_not_zero() -> None:
    source = pd.DataFrame(
        [
            {
                "ACCOUNT_ID_C": "A-CALL",
                "BU_NAME": "Calling Customer",
                "STATUS__C": "Active",
            }
        ]
    )

    scoped = _filter_csconsole_data_by_technology(
        source,
        "Webex Calling",
        customer_names=["Calling Customer"],
        account_ids=["A-CALL"],
    )

    assert scoped.empty
    state = cm.source_data_state(scoped)
    assert state["state"] == "unavailable"
    assert "authoritative technology fields were unavailable" in state["detail"]
    assert scoped.attrs["technology_scope_requested"] == "Webex Calling"

    all_technologies = _filter_csconsole_data_by_technology(
        source,
        "All Technologies",
        customer_names=["Calling Customer"],
        account_ids=["A-CALL"],
    )
    assert len(all_technologies) == 1
    assert cm.source_data_state(all_technologies)["state"] == "available"

    missing_customer_boundary = pd.DataFrame(
        [{"ID": "CP-OUTSIDE", "TECHNOLOGY_C": "Webex Calling"}]
    )
    withheld = _filter_csconsole_data_by_technology(
        missing_customer_boundary,
        "Webex Calling",
        customer_names=["Calling Customer"],
        account_ids=["A-CALL"],
    )
    assert withheld.empty
    assert cm.source_data_state(withheld)["state"] == "unavailable"
    assert "report boundary could not be validated" in cm.source_data_state(withheld)["detail"]


def test_action_plans_without_technology_require_scoped_customer_identity() -> None:
    source = pd.DataFrame(
        [
            {"ID": "AP-CALL", "ACCOUNT_ID_C": "A-CALL", "BU_NAME": "Calling Customer"},
            {"ID": "AP-MEET", "ACCOUNT_ID_C": "A-MEET", "BU_NAME": "Meetings Customer"},
            {
                "ID": "AP-OUTSIDE-WITH-TECH",
                "ACCOUNT_ID_C": "A-OUTSIDE",
                "BU_NAME": "Outside Customer",
                "TECHNOLOGY_C": "Webex Calling",
            },
        ]
    )

    scoped = _scope_action_plans_for_report(
        source,
        "Webex Calling",
        customer_names=["Calling Customer"],
        account_ids=["A-CALL"],
    )

    assert scoped["ID"].tolist() == ["AP-CALL"]
    assert scoped.attrs["technology_scope_customer_validated_rows"] == 1
    assert scoped.attrs["technology_scope_unknown_excluded"] == 1

    unavailable = _scope_action_plans_for_report(
        source.loc[source["TECHNOLOGY_C"].isna()].copy(),
        "Webex Calling",
        customer_names=[],
        account_ids=[],
    )
    assert unavailable.empty
    assert cm.source_data_state(unavailable)["state"] == "unavailable"


def test_compact_context_and_workbook_use_scoped_subscriptions() -> None:
    worker = _compact_worker()
    context = _dict_assignment(worker, "_r23_ctx")
    context_subscriptions = _dict_value(context, "team_subs_df_unfiltered")
    assert isinstance(context_subscriptions, ast.Name)
    assert context_subscriptions.id == "team_subs_for_customer_counting"

    sheets = _dict_assignment(worker, "sheets")
    workbook_subscriptions = _dict_value(sheets, "Subscriptions")
    assert isinstance(workbook_subscriptions, ast.Name)
    assert workbook_subscriptions.id == "team_subs_for_customer_counting"

    source = _APP.read_text(encoding="utf-8")
    compact_block = source[source.index("def run_compact_analysis"):source.index("def run_customer_renewal_analysis")]
    assert "subs_df=team_subs_df_unfiltered" not in compact_block
    assert "build_customer_lookup(team_subs_df_unfiltered)" not in compact_block


def test_scoped_subscription_input_cannot_create_other_technology_risk_row() -> None:
    scoped_subscriptions = pd.DataFrame(
        [
            {
                "SUBSCRIPTION_ID": "S-CALL",
                "ACCOUNT_ID_C": "A-CALL",
                "BU_NAME": "Calling Customer",
                "TECHNOLOGY_C": "Webex Calling",
            }
        ]
    )
    risk = calculate_renewal_risk_scores(
        pd.DataFrame(),
        pd.DataFrame(),
        extra_frames=[scoped_subscriptions],
        subs_df=scoped_subscriptions,
    )
    assert set(risk) == {"Calling Customer"}


def test_compact_and_renewal_export_real_subscription_sheets() -> None:
    source = _APP.read_text(encoding="utf-8")
    compact_block = source[
        source.index("def run_compact_analysis") : source.index("def run_customer_renewal_analysis")
    ]
    renewal_block = source[
        source.index("def run_customer_renewal_analysis") : source.index("def run_comprehensive_analysis")
    ]
    assert '"Subscriptions": team_subs_for_customer_counting' in compact_block
    assert '"Subscriptions": _renewal_report_subscriptions' in renewal_block
    # Round 169 emits one canonical Source_State/Source_Detail pair for every
    # source through a shared ledger loop.  Pin the subscription frame in that
    # ledger plus the dynamic canonical key instead of requiring the obsolete
    # one-off literal row.
    assert "_compact_source_state_frames = {" in compact_block
    assert '"Item": f"Source_State:{_source_sheet}"' in compact_block
    assert (
        "for _source_sheet, _source_frame in "
        "_compact_source_state_frames.items()"
    ) in compact_block
    assert '"Source_State:Subscriptions"' in renewal_block


def test_technology_scope_warning_remains_unavailable_in_final_adapter() -> None:
    assert adapter._warning_source_state(
        {
            "dataset": "customer_pulse",
            "kind": "technology_scope_unavailable",
            "effect": "Source unavailable for selected technology, not zero.",
        }
    ) == "unavailable"
