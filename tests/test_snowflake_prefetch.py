import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import snowflake_prefetch as sp


def test_run_context_normalizes_account_ids():
    run_ctx = sp.AnalysisRunContext.build(ctx=object(), account_ids=["002", "001", "001", None, "  "], days=30)
    assert run_ctx.account_ids == ("001", "002")


def test_run_context_normalizes_customer_names():
    run_ctx = sp.AnalysisRunContext.build(
        ctx=object(),
        account_ids=["001"],
        days=30,
        customer_names=[" Acme  Corp ", "Acme Corp", None, "none", "Unknown"],
    )
    assert run_ctx.customer_names == ("Acme Corp",)


def test_prefetch_datasets_uses_cache(monkeypatch):
    calls = {"count": 0}

    def _fake_fetcher(ctx, account_ids, days):
        calls["count"] += 1
        return pd.DataFrame([{"ACCOUNT_ID_C": "001"}])

    monkeypatch.setitem(sp._FETCHERS, "csconsole_action_plans", _fake_fetcher)
    run_ctx = sp.AnalysisRunContext.build(ctx=object(), account_ids=["001"], days=90)

    first = sp.prefetch_datasets(run_ctx, ["csconsole_action_plans"])["csconsole_action_plans"]
    second = sp.prefetch_datasets(run_ctx, ["csconsole_action_plans"])["csconsole_action_plans"]

    assert not first.empty
    assert second is first
    assert calls["count"] == 1
    assert run_ctx.metrics.get("csconsole_action_plans_queries") == 1
    assert run_ctx.metrics.get("csconsole_action_plans_cache_hits") == 1


def test_prefetch_comprehensive_fetches_expected_datasets(monkeypatch):
    seen = []

    def _factory(name):
        def _fetcher(ctx, account_ids, days):
            seen.append(name)
            return pd.DataFrame([{"dataset": name}])
        return _fetcher

    monkeypatch.setitem(sp._FETCHERS, "csconsole_action_plans", _factory("csconsole_action_plans"))
    monkeypatch.setitem(sp._FETCHERS, "csconsole_customer_pulse", _factory("csconsole_customer_pulse"))
    monkeypatch.setitem(sp._FETCHERS, "csconsole_success_priorities", _factory("csconsole_success_priorities"))
    monkeypatch.setitem(sp._FETCHERS, "csconsole_adoption_barriers", _factory("csconsole_adoption_barriers"))

    run_ctx = sp.AnalysisRunContext.build(ctx=object(), account_ids=["001"], days=90)
    result = sp.prefetch_comprehensive(run_ctx)

    assert set(result.keys()) == {
        "csconsole_action_plans",
        "csconsole_customer_pulse",
        "csconsole_success_priorities",
        "csconsole_adoption_barriers",
    }
    assert set(seen) == set(result.keys())


def test_prefetch_ask_ai_fetches_expected_datasets(monkeypatch):
    seen = []

    def _factory(name):
        def _fetcher(ctx, account_ids, days):
            seen.append(name)
            return pd.DataFrame([{"dataset": name}])
        return _fetcher

    monkeypatch.setitem(sp._FETCHERS, "support_cases_snowflake", _factory("support_cases_snowflake"))
    monkeypatch.setitem(sp._FETCHERS, "csconsole_customer_pulse", _factory("csconsole_customer_pulse"))
    monkeypatch.setitem(sp._FETCHERS, "csconsole_success_priorities", _factory("csconsole_success_priorities"))
    monkeypatch.setitem(sp._FETCHERS, "csconsole_action_plans", _factory("csconsole_action_plans"))

    run_ctx = sp.AnalysisRunContext.build(ctx=object(), account_ids=["001"], days=90)
    result = sp.prefetch_ask_ai(run_ctx)

    assert set(result.keys()) == {
        "support_cases_snowflake",
        "csconsole_customer_pulse",
        "csconsole_success_priorities",
        "csconsole_action_plans",
    }
    assert set(seen) == set(result.keys())


def test_success_priorities_prefetch_uses_customer_names_when_available(monkeypatch):
    captured = {"identifiers": None}

    def _sp_fetcher(ctx, identifiers, days):
        captured["identifiers"] = list(identifiers)
        return pd.DataFrame([{"ok": True}])

    monkeypatch.setitem(sp._FETCHERS, "csconsole_success_priorities", _sp_fetcher)
    run_ctx = sp.AnalysisRunContext.build(
        ctx=object(),
        account_ids=["001", "002"],
        days=90,
        customer_names=["Acme Corp", "Beta Inc"],
    )
    result = sp.prefetch_datasets(run_ctx, ["csconsole_success_priorities"])
    assert not result["csconsole_success_priorities"].empty
    assert captured["identifiers"] == ["Acme Corp", "Beta Inc"]


def test_success_priorities_prefetch_uses_empty_identifiers_without_customer_names(monkeypatch):
    captured = {"identifiers": None}

    def _sp_fetcher(ctx, identifiers, days):
        captured["identifiers"] = list(identifiers)
        return pd.DataFrame()

    monkeypatch.setitem(sp._FETCHERS, "csconsole_success_priorities", _sp_fetcher)
    run_ctx = sp.AnalysisRunContext.build(
        ctx=object(),
        account_ids=["001", "002"],
        days=90,
    )
    sp.prefetch_datasets(run_ctx, ["csconsole_success_priorities"])
    assert captured["identifiers"] == []


def test_prefetch_failure_is_cached_and_not_retried(monkeypatch):
    calls = {"count": 0}

    def _failing_fetcher(ctx, identifiers, days):
        calls["count"] += 1
        raise RuntimeError("boom")

    monkeypatch.setitem(sp._FETCHERS, "csconsole_customer_pulse", _failing_fetcher)
    run_ctx = sp.AnalysisRunContext.build(ctx=object(), account_ids=["001"], days=90)

    first = sp.prefetch_datasets(run_ctx, ["csconsole_customer_pulse"])["csconsole_customer_pulse"]
    second = sp.prefetch_datasets(run_ctx, ["csconsole_customer_pulse"])["csconsole_customer_pulse"]

    assert first.empty and second.empty
    assert calls["count"] == 1
    assert run_ctx.metrics.get("csconsole_customer_pulse_errors") == 1


def test_prefetch_skips_unknown_dataset_names():
    run_ctx = sp.AnalysisRunContext.build(ctx=object(), account_ids=["001"], days=90)
    result = sp.prefetch_datasets(run_ctx, ["unknown_dataset_name"])
    assert result == {}
