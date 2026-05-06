"""Round 88 / F4 (P0) — LeaderReportGenerator delegates CSSM subscription
lookup to the R82-aware ``adoptiq_backend.get_subscriptions_for_team``.

Brian Frazier (Build 63 acceptance audit): "How does it determine the
accounts assigned to a CSS? For example, with Greg it only captures
two accounts, IBM and WBG. But he's also the primary for BU - Cisco
Systems INC CA and is secondary on BU - Wells Fargo and BU - Apple
INC US.  He does have AP's for all accounts."

Pre-R88 ``LeaderReportGenerator._get_subscriptions_for_cssm`` walked
ONE email column (``PRIMARY_DSM_EMAIL`` or ``CSSM_EMAIL``, whichever
was present FIRST) and stopped, so any subscription whose only
matching email was in a secondary column (``SECONDARY_DSM_EMAIL`` /
``BACKUP_DSM_EMAIL`` / ``DELEGATE_DSM_EMAIL`` / etc.) was silently
dropped.  The R82 contract in ``adoptiq_backend.get_subscriptions_for_team``
already implements the primary + secondary UNION; R88/F4 wires the
leader path through that helper instead of carrying its own narrower
logic.

Two-pronged pin:

1. **Source-shape**: ``_get_subscriptions_for_cssm`` MUST call
   ``get_subscriptions_for_team`` and MUST NOT carry the legacy
   single-column query loop.

2. **Behavior**: with a synthetic Snowflake cursor whose
   ``PRIMARY_DSM_EMAIL`` only matches IBM / WBG and whose
   ``SECONDARY_DSM_EMAIL`` matches Cisco Systems / Wells Fargo /
   Apple, ``_get_subscriptions_for_cssm`` MUST return ALL FIVE
   accounts.  Pre-R88 it would return ONLY IBM / WBG.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd


_REPO_ROOT = Path(__file__).resolve().parent.parent
_LEADER_PATH = _REPO_ROOT / "leader_report_generator.py"


def test_r88_f4_get_subscriptions_for_cssm_delegates_to_adoptiq_backend() -> None:
    """Source-shape pin: ``_get_subscriptions_for_cssm`` MUST import
    ``get_subscriptions_for_team`` from ``adoptiq_backend`` and MUST
    call it with ``self.ctx`` + the email roster.
    """

    text = _LEADER_PATH.read_text(encoding="utf-8")
    # The import is lazy (inside the method body) so we look for the
    # exact source-marked import line introduced by R88/F4.
    assert (
        "from adoptiq_backend import get_subscriptions_for_team"
        in text
    ), (
        "Round 88 / F4: leader_report_generator must import "
        "``get_subscriptions_for_team`` from ``adoptiq_backend`` so the "
        "R82 primary + secondary UNION contract is honored on the "
        "leader path."
    )
    # And the delegation call itself
    assert (
        "df = get_subscriptions_for_team(self.ctx, cssm_emails)"
        in text
    ), (
        "Round 88 / F4: ``_get_subscriptions_for_cssm`` must invoke "
        "``get_subscriptions_for_team(self.ctx, cssm_emails)`` so the "
        "R82 multi-column UNION runs from the leader path."
    )


def test_r88_f4_legacy_single_column_query_loop_is_removed() -> None:
    """Negative pin: the legacy single-column query loop MUST NOT be
    present in ``_get_subscriptions_for_cssm`` so a future refactor
    cannot accidentally re-introduce the Greg Dolberry bug.
    """

    text = _LEADER_PATH.read_text(encoding="utf-8")
    # Find the method body
    start = text.find("def _get_subscriptions_for_cssm(self, cssm_emails: List[str]) -> pd.DataFrame:")
    assert start > 0, "_get_subscriptions_for_cssm must exist"
    # Method body extends to the next ``def `` at the same indentation
    end = text.find("\n    def ", start + 1)
    body = text[start:end]

    # Negative: the legacy single-column WHERE clause is GONE.
    assert "WHERE {_email_col} IN" not in body, (
        "Round 88 / F4: the pre-R88 single-column WHERE clause "
        "(``WHERE {_email_col} IN ({placeholders})``) must be removed "
        "from ``_get_subscriptions_for_cssm`` — the R82 helper handles "
        "the primary + secondary UNION SQL."
    )
    # Negative: the legacy column-pick if/elif chain is GONE.
    assert 'if "PRIMARY_DSM_EMAIL" in _dsm_cols:' not in body, (
        "Round 88 / F4: the pre-R88 single-column if/elif chain "
        "(``if 'PRIMARY_DSM_EMAIL' in _dsm_cols``) must be removed — "
        "the R82 helper enumerates primary + secondary columns."
    )


def test_r88_f4_synthetic_delegation_returns_dataframe() -> None:
    """Behavior round-trip with a mocked ``get_subscriptions_for_team``:
    the leader method MUST pass through the DataFrame unchanged
    (including ``_r82_team_subs_diag`` on ``df.attrs``).
    """

    from leader_report_generator import LeaderReportGenerator  # noqa: PLC0415

    expected_df = pd.DataFrame(
        [
            {"SUBSCRIPTION_ID": "SUB1", "ACCOUNT_ID_C": "ACC1", "BU_NAME": "IBM", "CSSM_EMAIL": "greg@example.com"},
            {"SUBSCRIPTION_ID": "SUB2", "ACCOUNT_ID_C": "ACC2", "BU_NAME": "WBG", "CSSM_EMAIL": "greg@example.com"},
            {"SUBSCRIPTION_ID": "SUB3", "ACCOUNT_ID_C": "ACC3", "BU_NAME": "Cisco Systems INC CA", "CSSM_EMAIL": "greg@example.com"},
            {"SUBSCRIPTION_ID": "SUB4", "ACCOUNT_ID_C": "ACC4", "BU_NAME": "Wells Fargo", "CSSM_EMAIL": "greg@example.com"},
            {"SUBSCRIPTION_ID": "SUB5", "ACCOUNT_ID_C": "ACC5", "BU_NAME": "Apple INC US", "CSSM_EMAIL": "greg@example.com"},
        ]
    )
    expected_df.attrs["_r82_team_subs_diag"] = {
        "primary_email_column_used": "PRIMARY_DSM_EMAIL",
        "secondary_email_columns_used": ["SECONDARY_DSM_EMAIL"],
        "primary_rows": 2,
        "secondary_rows": 3,
        "merged_rows": 5,
        "duplicate_rows_dropped": 0,
    }

    # Stand up a synthetic LeaderReportGenerator with a mocked ctx
    mock_ctx = MagicMock()
    gen = LeaderReportGenerator.__new__(LeaderReportGenerator)
    gen.ctx = mock_ctx

    # Patch the lazy import at the call site
    import adoptiq_backend  # noqa: PLC0415
    original_helper = adoptiq_backend.get_subscriptions_for_team
    adoptiq_backend.get_subscriptions_for_team = lambda ctx, emails: expected_df  # type: ignore[assignment]
    try:
        result = gen._get_subscriptions_for_cssm(["greg@example.com"])
    finally:
        adoptiq_backend.get_subscriptions_for_team = original_helper  # type: ignore[assignment]

    assert isinstance(result, pd.DataFrame)
    assert len(result) == 5, (
        "Round 88 / F4: expected 5 accounts (Greg's primary + secondary "
        "spread per Brian's audit) but got %d" % len(result)
    )
    expected_accounts = {"IBM", "WBG", "Cisco Systems INC CA", "Wells Fargo", "Apple INC US"}
    actual_accounts = set(result["BU_NAME"].astype(str).tolist())
    assert actual_accounts == expected_accounts, (
        f"Round 88 / F4: expected {expected_accounts}, got {actual_accounts}"
    )


def test_r88_f4_empty_email_list_returns_empty_frame_without_invoking_helper() -> None:
    """Defensive: an empty roster must NOT invoke the (potentially
    expensive) Snowflake helper — the leader path must short-circuit
    BEFORE the import.
    """

    from leader_report_generator import LeaderReportGenerator  # noqa: PLC0415

    gen = LeaderReportGenerator.__new__(LeaderReportGenerator)
    gen.ctx = MagicMock()

    # The helper MUST NOT be called for empty input
    import adoptiq_backend  # noqa: PLC0415
    original_helper = adoptiq_backend.get_subscriptions_for_team
    sentinel = MagicMock(side_effect=AssertionError("R88/F4: helper invoked for empty roster"))
    adoptiq_backend.get_subscriptions_for_team = sentinel  # type: ignore[assignment]
    try:
        result = gen._get_subscriptions_for_cssm([])
    finally:
        adoptiq_backend.get_subscriptions_for_team = original_helper  # type: ignore[assignment]

    assert isinstance(result, pd.DataFrame)
    assert result.empty, (
        "Round 88 / F4: empty roster must return empty DataFrame "
        "without invoking get_subscriptions_for_team."
    )


def test_r88_f4_helper_exception_propagates_through_empty_df_failed() -> None:
    """When the R82 helper raises, the leader method MUST return an
    ``_empty_df_failed`` DataFrame (carries failure attrs so
    downstream classify_data_state can mark "failed" not "empty").
    """

    from leader_report_generator import LeaderReportGenerator  # noqa: PLC0415

    gen = LeaderReportGenerator.__new__(LeaderReportGenerator)
    gen.ctx = MagicMock()

    import adoptiq_backend  # noqa: PLC0415
    original_helper = adoptiq_backend.get_subscriptions_for_team

    def _raising_helper(ctx, emails):  # noqa: ARG001
        raise RuntimeError("synthetic Snowflake failure")

    adoptiq_backend.get_subscriptions_for_team = _raising_helper  # type: ignore[assignment]
    try:
        result = gen._get_subscriptions_for_cssm(["greg@example.com"])
    finally:
        adoptiq_backend.get_subscriptions_for_team = original_helper  # type: ignore[assignment]

    assert isinstance(result, pd.DataFrame)
    assert result.empty, (
        "Round 88 / F4: helper exception must produce an empty "
        "DataFrame so downstream code does not crash."
    )
    # _empty_df_failed marks attrs so the consistency surface can
    # distinguish failure from emptiness
    failure_attrs = getattr(result, "attrs", None) or {}
    assert isinstance(failure_attrs, dict), (
        "Round 88 / F4: returned DataFrame must carry attrs dict (used "
        "by classify_data_state to distinguish failure vs zero-rows)."
    )


def test_r88_f4_round_88_marker_present_in_method_docstring() -> None:
    """Audit pin: the R88/F4 source marker must be present in the
    method body so ``git diff leader_report_generator.py | grep 'Round 88'``
    surfaces the change in the per-file footprint audit.
    """

    text = _LEADER_PATH.read_text(encoding="utf-8")
    start = text.find("def _get_subscriptions_for_cssm(self, cssm_emails: List[str]) -> pd.DataFrame:")
    end = text.find("\n    def ", start + 1)
    body = text[start:end]
    assert "Round 88 / F4" in body, (
        "Round 88 / F4: the source-marker comment must be present in "
        "``_get_subscriptions_for_cssm`` so audit greps surface this "
        "round's footprint."
    )
