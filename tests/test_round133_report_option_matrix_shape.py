"""Round 133 regression tests for exhaustive report option matrix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from report_iteration_loop import (
    EdgeMatrixConfig,
    MATRIX_BLOCK_ORDER,
    MATRIX_TECHNOLOGY_CHOICES,
    build_exhaustive_option_matrix,
    build_scenario_map,
    matrix_block_for_key,
    parse_matrix_blocks,
    select_matrix_scenario_keys,
)


def test_round133_matrix_block_order_is_cheap_first():
    assert MATRIX_BLOCK_ORDER[0] == "E"
    assert MATRIX_BLOCK_ORDER[-1] == "G"


def test_round133_matrix_covers_every_manager_technology_and_report_type():
    matrix = build_exhaustive_option_matrix(days=90)
    managers = set(json.loads((Path(__file__).resolve().parents[1] / "team_config.json").read_text())["managers"])
    techs = set(MATRIX_TECHNOLOGY_CHOICES)

    comp_managers = {
        s.payload.get("manager")
        for s in matrix.values()
        if s.endpoint == "/start_analysis" and s.payload.get("report_type") == "comprehensive"
    }
    comp_techs = {
        s.payload.get("technology")
        for s in matrix.values()
        if s.endpoint == "/start_analysis" and s.payload.get("report_type") == "comprehensive"
    }
    compact_techs = {
        s.payload.get("technology")
        for s in matrix.values()
        if s.endpoint == "/start_compact_analysis"
    }
    renewal_techs = {
        s.payload.get("technology")
        for s in matrix.values()
        if s.endpoint == "/start_analysis" and s.payload.get("report_type") == "renewal_portfolio"
    }
    leader_managers = {s.payload.get("manager") for s in matrix.values() if s.endpoint == "/start_leader_report"}

    assert managers <= comp_managers
    assert managers <= leader_managers
    assert techs <= comp_techs
    assert techs <= compact_techs
    assert techs <= renewal_techs

    report_types = set()
    for scenario in matrix.values():
        if scenario.endpoint == "/start_analysis":
            report_types.add(scenario.payload.get("report_type"))
        elif scenario.endpoint == "/start_compact_analysis":
            report_types.add("compact")
        elif scenario.endpoint == "/start_leader_report":
            report_types.add("leader")
        elif scenario.endpoint == "/start_subscription_analysis":
            report_types.add("subscription")
    assert {"comprehensive", "renewal_portfolio", "renewal", "compact", "leader"}.issubset(report_types)


def test_round133_matrix_cardinality_and_dedup():
    matrix = build_exhaustive_option_matrix(days=90)
    # 4 canonical + 8 B + 6 C - 1 dedup + 6 D + 8 E + 8 F + 3 base G edges
    assert 40 <= len(matrix) <= 46
    comp_scopes = [
        (s.payload.get("manager"), s.payload.get("technology"))
        for s in matrix.values()
        if s.endpoint == "/start_analysis" and s.payload.get("report_type") == "comprehensive"
    ]
    assert len(comp_scopes) == len(set(comp_scopes))


def test_round133_leader_scenarios_omit_technology():
    matrix = build_exhaustive_option_matrix(days=90)
    for key, scenario in matrix.items():
        if not key.startswith("d_leader_"):
            continue
        assert "technology" not in scenario.payload


def test_round133_subscription_edge_requires_subscription_id():
    without = build_exhaustive_option_matrix(days=90, edge=EdgeMatrixConfig(subscription_id=""))
    assert "g_subscription_analysis" not in without
    with_sub = build_exhaustive_option_matrix(days=90, edge=EdgeMatrixConfig(subscription_id="SUB123"))
    assert "g_subscription_analysis" in with_sub
    sub = with_sub["g_subscription_analysis"]
    assert sub.endpoint == "/start_subscription_analysis"
    assert sub.payload["subscription_id"] == "SUB123"


def test_round133_csone_upload_edges_optional():
    matrix = build_exhaustive_option_matrix(days=90, edge=EdgeMatrixConfig(csone_upload_path=""))
    assert "g_compact_csone_upload" not in matrix
    assert "g_leader_csone_upload" not in matrix
    with_upload = build_exhaustive_option_matrix(
        days=90,
        edge=EdgeMatrixConfig(csone_upload_path="/tmp/sample_csone.xlsx"),
    )
    assert "g_compact_csone_upload" in with_upload
    assert "g_leader_csone_upload" in with_upload


def test_round133_parse_blocks_and_resume():
    assert parse_matrix_blocks("all") == list(MATRIX_BLOCK_ORDER)
    assert parse_matrix_blocks("A,E") == ["A", "E"]
    with pytest.raises(ValueError):
        parse_matrix_blocks("Z")
    matrix = build_exhaustive_option_matrix(days=90)
    keys = select_matrix_scenario_keys(matrix, ["E", "F"], resume_from="e_compact_am_Webex_Calling")
    assert keys[0] == "e_compact_am_Webex_Calling"
    assert all(k.startswith(("e_", "f_")) for k in keys)


def test_round133_block_a_includes_canonical_quartet():
    matrix = build_exhaustive_option_matrix(days=90)
    canonical = build_scenario_map()
    for name in canonical:
        key = f"a_{name}"
        assert key in matrix
        assert matrix[key].endpoint == canonical[name].endpoint


def test_round133_matrix_block_for_key():
    assert matrix_block_for_key("e_compact_am_All") == "E"
    assert matrix_block_for_key("a_comprehensive") == "A"


def test_round133_scenario_payload_modes_match_ui():
    matrix = build_exhaustive_option_matrix(days=90)
    assert matrix["e_compact_am_All_Contact_Center"].payload_mode == "json"
    assert matrix["b_comp_am_Webex_Calling"].payload_mode == "form"
    assert matrix["d_leader_Brian_Frazier"].payload_mode == "form"
