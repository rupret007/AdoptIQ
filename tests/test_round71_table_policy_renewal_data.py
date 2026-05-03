"""Round 71 / Phase 4 (#18) -- snowflake_table_policy allow-list extension.

Pre-R71 ``RENEWAL_DATA`` was referenced by the comprehensive renewal
opportunity prefetch in ``adoptiq_backend.py`` (lines 4692, 4723) but
NOT in ``snowflake_table_policy._ALLOWED_CANONICAL``.  Any SQL routed
through ``guard_sql()`` raised ``TablePolicyViolation`` -- silently
zeroing the renewal opportunity bucket on every run.

Round 71 / Phase 4 (#18) adds the canonical name to the allow-list so
the prefetch can run.
"""

from __future__ import annotations

import pytest

import snowflake_table_policy as policy


def test_round71_renewal_data_in_allowed_canonical() -> None:
    """``CX_DB.CX_SWSSBST_BR.RENEWAL_DATA`` MUST be in
    ``_ALLOWED_CANONICAL`` so the renewal opportunity prefetch SQL
    passes ``guard_sql()``."""
    assert "CX_DB.CX_SWSSBST_BR.RENEWAL_DATA" in policy._ALLOWED_CANONICAL, (
        "Round 71 / Phase 4 (#18): RENEWAL_DATA must be in "
        "_ALLOWED_CANONICAL.  Without it, the comprehensive renewal "
        "opportunity prefetch silently 0-ed out on every run."
    )


def test_round71_renewal_data_basename_resolves_to_allowed() -> None:
    """The basename ``RENEWAL_DATA`` must also resolve as allowed
    (the policy expands canonical names + basenames into ``ALLOWED_TABLES``)."""
    assert policy.is_table_allowed("RENEWAL_DATA"), (
        "Round 71 / Phase 4 (#18): RENEWAL_DATA basename must resolve "
        "as allowed.  Without the canonical entry it would not."
    )
    assert policy.is_table_allowed("CX_DB.CX_SWSSBST_BR.RENEWAL_DATA"), (
        "Round 71 / Phase 4 (#18): RENEWAL_DATA fully-qualified name "
        "must resolve as allowed."
    )


def test_round71_guard_sql_passes_renewal_data_select() -> None:
    """A SELECT FROM RENEWAL_DATA MUST pass ``guard_sql`` without
    raising."""
    sql = "SELECT a.opportunity_id FROM CX_DB.CX_SWSSBST_BR.RENEWAL_DATA a WHERE a.account_id = %(acct)s"
    refs = policy.guard_sql(sql)
    assert "CX_DB.CX_SWSSBST_BR.RENEWAL_DATA" in refs, (
        "Round 71 / Phase 4 (#18): guard_sql must accept the renewal "
        "opportunity prefetch SQL."
    )


def test_round71_guard_sql_still_blocks_disallowed() -> None:
    """Negative control: the renewal-data extension MUST NOT loosen
    the rest of the allow-list."""
    sql = "SELECT * FROM CX_DB.CX_SWSSBST_BR.SUPPORT_CASES"
    with pytest.raises(policy.TablePolicyViolation):
        policy.guard_sql(sql)


def test_round71_renewal_data_carries_round71_marker_in_source() -> None:
    """The new allow-list entry MUST carry a Round 71 marker so the
    audit grep finds it."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "snowflake_table_policy.py").read_text(encoding="utf-8")
    assert "Round 71 / Phase 4 (#18)" in src, (
        "snowflake_table_policy._ALLOWED_CANONICAL must carry a "
        "``Round 71 / Phase 4 (#18)`` marker comment near the new "
        "RENEWAL_DATA entry."
    )
