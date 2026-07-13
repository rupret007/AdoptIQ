"""Phase 5 / Phase 1.3 regression test.

When a Snowflake or CSConsole fetcher fails the resulting DataFrame
must carry ``df.attrs['fetch_error']`` so downstream report
assemblers can render an explicit "source unavailable" instead of a
confident zero.  This test pins the contract on the helper used to
classify a fetched bundle into the tristate
``empty / failed / not_configured / present`` so a regression that
silently swallows the error fails CI.
"""
from __future__ import annotations

import pandas as pd

import report_utils as ru


def test_classify_failed_when_attrs_carry_fetch_error() -> None:
    df = pd.DataFrame()
    df.attrs["fetch_error"] = "ECONNRESET — Snowflake gateway timeout"
    state = ru.classify_data_state(df)
    assert state == "failed", (
        "An empty DataFrame with df.attrs['fetch_error'] must classify "
        "as 'failed', NOT 'empty'.  Otherwise a Snowflake outage will "
        "render as 'No records found' in the report."
    )


def test_classify_failed_when_payload_dict_has_fetch_error_key() -> None:
    payload = {"rows": [], "fetch_error": "boom"}
    state = ru.classify_data_state(payload)
    assert state == "failed", (
        "Bundle dicts with a non-empty 'fetch_error' value must "
        "classify as 'failed' so the formatter renders an unavailable banner."
    )


def test_classify_present_when_rows_exist() -> None:
    df = pd.DataFrame({"id": [1, 2, 3]})
    assert ru.classify_data_state(df) == "present"


def test_classify_empty_when_truly_no_rows_no_error() -> None:
    assert ru.classify_data_state(pd.DataFrame()) == "empty"
    assert ru.classify_data_state(None) == "empty"


def test_classify_not_configured_explicit_flag() -> None:
    state = ru.classify_data_state(None, not_configured=True)
    assert state == "not_configured", (
        "Callers must be able to override to 'not_configured' for the "
        "case where a feature is intentionally not enabled."
    )


def test_render_empty_state_message_distinguishes_three_failure_modes() -> None:
    failed_msg = ru.render_empty_state_message(
        "failed",
        source_label="Snowflake CSConsole",
        error_detail="HTTP 504 timeout",
    )
    not_cfg_msg = ru.render_empty_state_message(
        "not_configured",
        source_label="External Intelligence APIs",
        suggested_action="Configure API keys in Settings.",
    )
    empty_msg = ru.render_empty_state_message(
        "empty",
        source_label="TAC cases",
    )
    assert "unavailable" in failed_msg.lower()
    assert "HTTP 504 timeout" in failed_msg
    assert "not configured" in not_cfg_msg.lower()
    assert "Configure API keys" in not_cfg_msg
    assert "no" in empty_msg.lower() or "empty" in empty_msg.lower()
    # The three messages must be distinct so the UI can never collapse
    # them back into a single misleading "no data" line.
    assert {failed_msg, not_cfg_msg, empty_msg}.__len__() == 3
