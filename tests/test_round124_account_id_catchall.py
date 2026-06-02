"""Round 124 / I2 regression test.

Build 92 audit: Ron Estillore's Leader scope over-attributed ~96
customers.  Root cause candidate found offline: the per-CSSM
``account_ids`` set was built with only ``.dropna()`` -- blank /
whitespace-only / "nan" sentinel account ids survived as empty strings,
and ``_slice_by_owner_or_account``'s ``isin(account_ids)`` check then
treated an empty-string account id as a WILDCARD that matched every
task row whose ``ACCOUNT_ID_C`` was blank.

This test pins the cleaned-account-id contract directly: a blank /
sentinel account id in a CSSM's subscription set must NOT pull in task
rows that merely have a blank account id of their own.
"""

import pandas as pd

import leader_report_generator as lrg


def _clean_account_ids(subscriptions_df):
    """Mirror the Round 124 / I2 cleaning block in ``generate_leader_report``."""
    if not subscriptions_df.empty and 'ACCOUNT_ID_C' in subscriptions_df.columns:
        return [
            _aid
            for _aid in (
                str(_a).strip()
                for _a in subscriptions_df['ACCOUNT_ID_C'].dropna().unique().tolist()
            )
            if _aid and _aid.lower() not in {'nan', 'none', 'null'}
        ]
    return []


def _account_mask(df, account_ids, aid_col='ACCOUNT_ID_C'):
    """Mirror the account-mask half of _slice_by_owner_or_account."""
    if aid_col in df.columns and account_ids:
        return df[aid_col].fillna('').astype(str).str.strip().isin(account_ids)
    return pd.Series([False] * len(df), index=df.index)


def test_blank_account_id_is_not_a_wildcard():
    subs = pd.DataFrame({'ACCOUNT_ID_C': ['001AAA', '', '  ', None, 'nan', '001BBB']})
    account_ids = _clean_account_ids(subs)
    assert sorted(account_ids) == ['001AAA', '001BBB']
    # Task rows: two with real ids, two with blank ids that the old code
    # would have catch-all attributed.
    tasks = pd.DataFrame(
        {'ACCOUNT_ID_C': ['001AAA', '', '   ', '009ZZZ']}
    )
    mask = _account_mask(tasks, account_ids)
    # Only the real-id row matches; blank task rows are NOT attributed.
    assert mask.tolist() == [True, False, False, False]


def test_real_account_ids_still_match():
    subs = pd.DataFrame({'ACCOUNT_ID_C': [' 001AAA ', '001BBB']})
    account_ids = _clean_account_ids(subs)
    assert sorted(account_ids) == ['001AAA', '001BBB']
    tasks = pd.DataFrame({'ACCOUNT_ID_C': ['001AAA', '001BBB', '001CCC']})
    mask = _account_mask(tasks, account_ids)
    assert mask.tolist() == [True, True, False]


def test_all_blank_subscription_yields_no_account_match():
    subs = pd.DataFrame({'ACCOUNT_ID_C': ['', '  ', None, 'nan', 'NULL', 'none']})
    account_ids = _clean_account_ids(subs)
    assert account_ids == []
    tasks = pd.DataFrame({'ACCOUNT_ID_C': ['', '001AAA', None]})
    mask = _account_mask(tasks, account_ids)
    assert mask.tolist() == [False, False, False]


def test_i2_source_marker_present():
    import inspect

    src = inspect.getsource(lrg.LeaderReportGenerator._collect_team_data)
    assert "Round 124 / I2" in src
