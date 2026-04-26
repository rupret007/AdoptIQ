"""Round 2 / Phase 1.12 regression test.

When ``adoptiq_backend.fetch_support_cases_snowflake`` (or its
peers) hits a Snowflake table-policy block, the returned empty
DataFrame MUST carry ``attrs['fetch_error']`` and
``attrs['fetch_error_kind'] == 'table_policy_violation'`` so
downstream renderers (Excel writer, leader narrative) can show the
proper "unavailable" tristate instead of a misleading "zero cases".
"""
from __future__ import annotations

import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_table_policy_violation_kind_used_in_backend() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "'table_policy_violation'" in src or '"table_policy_violation"' in src, (
        "Round 2 Phase 1.12: adoptiq_backend must tag policy-blocked "
        "fetches with attrs['fetch_error_kind'] = "
        "'table_policy_violation' so downstream renderers can "
        "distinguish a policy block from a generic empty result."
    )


def test_policy_blocked_path_returns_frame_with_fetch_error_attrs(monkeypatch) -> None:
    """Functional check: monkeypatch ``is_table_blocked`` so the
    function takes the policy-blocked branch deterministically and
    inspect the returned frame's ``.attrs``.
    """
    import adoptiq_backend as ab

    if not hasattr(ab, "fetch_support_cases_snowflake"):
        pytest.skip("fetch_support_cases_snowflake not available in this build")

    monkeypatch.setattr(ab, "is_table_blocked", lambda _table: True)

    class _DummyCtx:
        pass

    df = ab.fetch_support_cases_snowflake(_DummyCtx(), account_ids=["A-1"], days=30)
    attrs = getattr(df, "attrs", {})
    assert attrs.get("fetch_error_kind") == "table_policy_violation", (
        "Round 2 Phase 1.12: the policy-blocked path must stamp "
        "fetch_error_kind='table_policy_violation' on the returned "
        f"frame.  Got attrs={dict(attrs)!r}."
    )
    assert attrs.get("fetch_error"), (
        "Round 2 Phase 1.12: the policy-blocked path must also stamp "
        "a non-empty fetch_error message so the Excel writer can "
        "surface the reason in the Data_Unavailable row."
    )
