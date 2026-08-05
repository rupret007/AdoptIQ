"""Round 142 focused tests for Leader team/member/customer scope."""

from __future__ import annotations

import inspect
import importlib.util
import sys
import types

import pandas as pd
import pytest


# This suite deliberately runs without the Snowflake connector. The production
# app imports it eagerly, so provide only the two inert attributes referenced at
# import time; every test still forbids a real connection.
if importlib.util.find_spec("snowflake") is None:
    snowflake_package = types.ModuleType("snowflake")
    snowflake_package.__path__ = []  # type: ignore[attr-defined]
    snowflake_connector = types.ModuleType("snowflake.connector")
    snowflake_connector.DictCursor = object

    def _no_snowflake_connect(*args, **kwargs):
        raise RuntimeError("Snowflake connector is unavailable in this test")

    snowflake_connector.connect = _no_snowflake_connect
    snowflake_package.connector = snowflake_connector
    sys.modules["snowflake"] = snowflake_package
    sys.modules["snowflake.connector"] = snowflake_connector


requires_py310_app_import = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="app_simple/adoptiq_backend require Python 3.10+ type syntax",
)

from leader_scope import (
    LeaderScopeValidationError,
    filter_leader_subscriptions,
    leader_customer_options,
    manager_roster_members,
    validate_leader_scope_request,
)


ROSTER = [
    ("Manager One", "Alice Able", "alice@example.com"),
    ("Manager One", "Bob Baker", "bob@example.com"),
    ("Manager Two", "Eve Else", "eve@example.com"),
]


def test_member_scope_is_canonicalized_from_manager_roster():
    selection = validate_leader_scope_request(
        "manager one", "member", "ALICE@EXAMPLE.COM", ROSTER
    )

    assert selection.manager_name == "Manager One"
    assert selection.scope_type == "member"
    assert selection.scope_value == "alice@example.com"
    assert selection.member_name == "Alice Able"
    assert selection.display_value == "Alice Able (alice@example.com)"


def test_member_outside_manager_roster_is_rejected_immediately():
    with pytest.raises(LeaderScopeValidationError, match="does not report"):
        validate_leader_scope_request(
            "Manager One", "member", "eve@example.com", ROSTER
        )


def test_customer_optional_member_must_belong_to_manager():
    with pytest.raises(LeaderScopeValidationError, match="does not report"):
        validate_leader_scope_request(
            "Manager One",
            "customer",
            "Acme Corp",
            ROSTER,
            member_email="eve@example.com",
        )


def test_customer_scope_filters_manager_subscriptions_after_fetch():
    subscriptions = pd.DataFrame(
        [
            {"CSSM_EMAIL": "alice@example.com", "BU_NAME": "Acme Corp", "ACCOUNT_ID_C": "A1"},
            {"CSSM_EMAIL": "alice@example.com", "BU_NAME": "Beta Inc", "ACCOUNT_ID_C": "B1"},
            {"CSSM_EMAIL": "bob@example.com", "BU_NAME": "Acme Corp", "ACCOUNT_ID_C": "A2"},
        ]
    )
    selection = validate_leader_scope_request(
        "Manager One",
        "customer",
        "  acme corp  ",
        ROSTER,
        member_email="alice@example.com",
    )

    scoped = filter_leader_subscriptions(subscriptions, selection)

    assert scoped["ACCOUNT_ID_C"].tolist() == ["A1"]


def test_customer_absent_from_selected_member_subscriptions_is_rejected():
    subscriptions = pd.DataFrame(
        [
            {"CSSM_EMAIL": "alice@example.com", "BU_NAME": "Acme Corp"},
            {"CSSM_EMAIL": "bob@example.com", "BU_NAME": "Beta Inc"},
        ]
    )
    selection = validate_leader_scope_request(
        "Manager One",
        "customer",
        "Beta Inc",
        ROSTER,
        member_email="alice@example.com",
    )

    with pytest.raises(LeaderScopeValidationError, match="not assigned"):
        filter_leader_subscriptions(subscriptions, selection)


def test_customer_scope_rejects_account_id_shared_with_another_customer():
    subscriptions = pd.DataFrame(
        [
            {
                "CSSM_EMAIL": "alice@example.com",
                "BU_NAME": "Acme Corp",
                "ACCOUNT_ID_C": "SHARED-A1",
            },
            {
                "CSSM_EMAIL": "bob@example.com",
                "BU_NAME": "Beta Inc",
                "ACCOUNT_ID_C": "SHARED-A1",
            },
        ]
    )
    selection = validate_leader_scope_request(
        "Manager One",
        "customer",
        "Acme Corp",
        ROSTER,
        member_email="alice@example.com",
    )

    with pytest.raises(LeaderScopeValidationError, match="shares an account"):
        filter_leader_subscriptions(subscriptions, selection)


def test_customer_options_are_deterministic_and_count_subscriptions():
    subscriptions = pd.DataFrame(
        {"BU_NAME": ["Zulu LLC", "acme corp", "Acme Corp", None, ""]}
    )

    assert leader_customer_options(subscriptions) == [
        {"value": "Acme Corp", "label": "Acme Corp", "subscription_count": 2},
        {"value": "Zulu LLC", "label": "Zulu LLC", "subscription_count": 1},
    ]


def _first_live_roster_entry(app_simple):
    manager, member_name, member_email = app_simple.TEAM_ROSTER[0]
    return manager, member_name, member_email


@requires_py310_app_import
def test_leader_form_exposes_team_member_customer_scope_controls(client):
    response = client.get("/leader_report_form")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="scope_type"' in html
    assert 'name="scope_value"' in html
    assert 'name="scope_member"' in html
    assert 'value="team"' in html
    assert 'value="member"' in html
    assert 'value="customer"' in html
    assert "/api/leader_scope_options" in html


@requires_py310_app_import
def test_scope_options_route_degrades_without_snowflake(client, monkeypatch):
    import app_simple

    manager, _member_name, _member_email = _first_live_roster_entry(app_simple)

    def unavailable():
        raise RuntimeError("Snowflake unavailable in unit test")

    monkeypatch.setattr(app_simple, "_connect_with_keeper", unavailable)
    response = client.get(
        "/api/leader_scope_options",
        query_string={"manager": manager, "include_customers": "1"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["members"] == manager_roster_members(app_simple.TEAM_ROSTER, manager)
    assert payload["customers"] == []
    assert payload["customers_available"] is False
    assert "unavailable" in payload["warning"].lower()


@requires_py310_app_import
def test_scope_options_rejects_cross_manager_member_before_snowflake(client, monkeypatch):
    import app_simple

    manager, _member_name, member_email = _first_live_roster_entry(app_simple)
    other_email = next(
        email
        for candidate_manager, _name, email in app_simple.TEAM_ROSTER
        if candidate_manager != manager
    )
    connect_called = False

    def should_not_connect():
        nonlocal connect_called
        connect_called = True
        raise AssertionError("Snowflake must not be called")

    monkeypatch.setattr(app_simple, "_connect_with_keeper", should_not_connect)
    response = client.get(
        "/api/leader_scope_options",
        query_string={
            "manager": manager,
            "member_email": other_email,
            "include_customers": "1",
        },
    )

    assert member_email != other_email
    assert response.status_code == 400
    assert response.get_json()["success"] is False
    assert connect_called is False


@requires_py310_app_import
def test_scope_options_returns_only_selected_members_customers(client, monkeypatch):
    import app_simple

    roster_by_manager = {}
    for candidate_manager, member_name, email in app_simple.TEAM_ROSTER:
        roster_by_manager.setdefault(candidate_manager, []).append((member_name, email))
    manager, members = next(
        (candidate_manager, members)
        for candidate_manager, members in roster_by_manager.items()
        if len(members) >= 2
    )
    member_email = members[0][1]
    other_member = members[1][1]

    class FakeContext:
        closed = False

        def close(self):
            self.closed = True

    fake_context = FakeContext()
    monkeypatch.setattr(app_simple, "_connect_with_keeper", lambda: fake_context)
    monkeypatch.setattr(
        app_simple,
        "get_subscriptions_for_team",
        lambda _ctx, _emails: pd.DataFrame(
            [
                {"CSSM_EMAIL": member_email, "BU_NAME": "In Scope Corp"},
                {"CSSM_EMAIL": other_member, "BU_NAME": "Outside Member Corp"},
            ]
        ),
    )

    response = client.get(
        "/api/leader_scope_options",
        query_string={
            "manager": manager,
            "member_email": member_email,
            "include_customers": "1",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["customers"] == [
        {
            "value": "In Scope Corp",
            "label": "In Scope Corp",
            "subscription_count": 1,
        }
    ]
    assert fake_context.closed is True


@requires_py310_app_import
def test_start_route_persists_validated_member_scope_without_snowflake(client, monkeypatch):
    import app_simple

    manager, _member_name, member_email = _first_live_roster_entry(app_simple)

    class NoStartThread:
        def __init__(self, *args, **kwargs):
            self.daemon = False

        def start(self):
            return None

    monkeypatch.setattr(app_simple.threading, "Thread", NoStartThread)
    monkeypatch.setattr(
        app_simple, "get_latest_csone_from_folder_diag", lambda: (None, "unknown", 0)
    )
    monkeypatch.setattr(app_simple, "save_analysis_status", lambda: None)

    response = client.post(
        "/start_leader_report",
        data={
            "manager": manager,
            "days": "90",
            "scope_type": "member",
            "scope_value": member_email,
            "scope_member": member_email,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    analysis_id = payload["analysis_id"]
    try:
        status = app_simple.analysis_status[analysis_id]
        assert status["scope_type"] == "member"
        assert status["scope_value"] == member_email.lower()
        assert status["scope_member"] == member_email.lower()
    finally:
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.pop(analysis_id, None)


@requires_py310_app_import
def test_start_route_rejects_cross_manager_member_before_file_discovery(client, monkeypatch):
    import app_simple

    manager, _member_name, _member_email = _first_live_roster_entry(app_simple)
    other_email = next(
        email
        for candidate_manager, _name, email in app_simple.TEAM_ROSTER
        if candidate_manager != manager
    )

    def should_not_discover():
        raise AssertionError("file discovery must happen after scope validation")

    monkeypatch.setattr(
        app_simple, "get_latest_csone_from_folder_diag", should_not_discover
    )
    response = client.post(
        "/start_leader_report",
        data={
            "manager": manager,
            "days": "90",
            "scope_type": "member",
            "scope_value": other_email,
        },
    )

    assert response.status_code == 400
    assert "does not report" in response.get_json()["error"]


@requires_py310_app_import
def test_same_second_leader_jobs_receive_distinct_scope_safe_ids(client, monkeypatch):
    import app_simple

    manager, _member_name, member_email = _first_live_roster_entry(app_simple)

    class NoStartThread:
        def __init__(self, *args, **kwargs):
            self.daemon = False

        def start(self):
            return None

    monkeypatch.setattr(app_simple.threading, "Thread", NoStartThread)
    monkeypatch.setattr(app_simple.time, "time", lambda: 1_800_000_000)
    monkeypatch.setattr(
        app_simple, "get_latest_csone_from_folder_diag", lambda: (None, "unknown", 0)
    )
    monkeypatch.setattr(app_simple, "save_analysis_status", lambda: None)

    responses = [
        client.post(
            "/start_leader_report",
            data={
                "manager": manager,
                "days": "90",
                "scope_type": "member",
                "scope_value": member_email,
            },
        )
        for _ in range(2)
    ]
    ids = [response.get_json()["analysis_id"] for response in responses]
    try:
        assert all(response.status_code == 200 for response in responses)
        assert ids[0] != ids[1]
        assert all("_member_" in analysis_id for analysis_id in ids)
    finally:
        with app_simple.analysis_status_lock:
            for analysis_id in ids:
                app_simple.analysis_status.pop(analysis_id, None)


@requires_py310_app_import
def test_leader_generator_signatures_accept_round142_scope_fields():
    from leader_report_generator import LeaderReportGenerator, generate_leader_report

    wrapper_params = inspect.signature(generate_leader_report).parameters
    method_params = inspect.signature(
        LeaderReportGenerator.generate_leader_report
    ).parameters
    for name in (
        "scope_type",
        "scope_value",
        "scope_member",
        "scoped_subscriptions_df",
        "technology",
    ):
        assert name in wrapper_params
        assert name in method_params


@requires_py310_app_import
def test_leader_word_fingerprint_uses_threaded_technology():
    import decision_report_delivery as delivery
    from leader_report_generator import LeaderReportGenerator

    generator = LeaderReportGenerator.__new__(LeaderReportGenerator)
    generator.data_retrieved_at = pd.Timestamp(
        "2026-08-03T21:00:00Z"
    ).to_pydatetime()
    scope = types.SimpleNamespace(
        scope_type="team",
        display_value="Manager One team",
    )
    team_data = {
        "Alice Able": {
            "subscriptions": pd.DataFrame(
                [
                    {
                        "SUBSCRIPTION_ID": "SUB-001",
                        "ACCOUNT_ID_C": "ACC-001",
                        "BU_NAME": "Acme Corp",
                    }
                ]
            ),
            "action_plans": pd.DataFrame(),
            "adoption_barriers": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame(),
            "success_priorities": pd.DataFrame(),
        }
    }

    document = generator._build_concise_decision_document(
        team_data,
        manager_name="Manager One",
        technology="All Contact Center",
        days=90,
        scope_selection=scope,
    )
    canonical_facts = delivery.build_report_facts(
        team_data,
        report_type="Leader",
        scope_type="team",
        scope_value="Manager One team",
        manager_name="Manager One",
        technology="All Contact Center",
        days=90,
        as_of=generator.data_retrieved_at,
    )
    default_technology_facts = delivery.build_report_facts(
        team_data,
        report_type="Leader",
        scope_type="team",
        scope_value="Manager One team",
        manager_name="Manager One",
        technology="All",
        days=90,
        as_of=generator.data_retrieved_at,
    )

    assert document.core_properties.identifier == (
        delivery.fact_contract_fingerprint(canonical_facts)
    )
    assert document.core_properties.identifier != (
        delivery.fact_contract_fingerprint(default_technology_facts)
    )
    assert generator._round142_facts["technology"] == "All Contact Center"
    assert document.paragraphs[1].text == (
        "Manager One • Team: Manager One team • "
        "Technology: All Contact Center • 90-day window"
    )
    assert delivery.validate_word_semantics(canonical_facts, document)["ok"] is True


@requires_py310_app_import
def test_customer_collection_uses_override_and_disables_owner_expansion(monkeypatch):
    from leader_report_generator import LeaderReportGenerator

    generator = LeaderReportGenerator.__new__(LeaderReportGenerator)
    generator.team_roster = ROSTER
    generator.safe_len = lambda value: len(value) if value is not None else 0
    monkeypatch.setattr(
        generator,
        "_get_subscriptions_for_cssm",
        lambda _emails: (_ for _ in ()).throw(
            AssertionError("authorized subscription override must be reused")
        ),
    )
    owner_arguments = []

    def empty_activity(_account_ids, _days, owner_emails=None):
        owner_arguments.append(owner_emails)
        return pd.DataFrame()

    monkeypatch.setattr(generator, "_fetch_action_plans", empty_activity)
    monkeypatch.setattr(generator, "_fetch_adoption_barriers", empty_activity)
    monkeypatch.setattr(generator, "_fetch_customer_pulse", empty_activity)
    monkeypatch.setattr(
        generator, "_fetch_success_priorities", lambda _customers, _days: pd.DataFrame()
    )
    subscriptions = pd.DataFrame(
        [
            {
                "CSSM_EMAIL": "alice@example.com",
                "BU_NAME": "Acme Corp",
                "ACCOUNT_ID_C": "A1",
                "SUBSCRIPTION_ID": "S1",
            }
        ]
    )

    team_data = generator._collect_team_data(
        [{"name": "Alice Able", "email": "alice@example.com"}],
        90,
        subscriptions_override=subscriptions,
        restrict_to_subscription_accounts=True,
    )

    assert team_data["Alice Able"]["account_ids"] == ["A1"]
    assert owner_arguments == [[], [], []]
