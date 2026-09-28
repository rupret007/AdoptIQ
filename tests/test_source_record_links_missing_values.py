"""Missing tabular values must never become source-record hyperlinks."""

import pandas as pd
import pytest

from source_record_links import build_source_record_url, is_allowed_source_record_url


@pytest.mark.parametrize("missing", [None, float("nan"), pd.NA, pd.NaT, "nan", "NaN", "None", "null", "<NA>", "NaT", "  null  "])
def test_missing_record_ids_do_not_generate_links(missing):
    assert build_source_record_url("action_plans", missing) == ""


@pytest.mark.parametrize("missing", [None, float("nan"), pd.NA, pd.NaT])
def test_missing_sources_and_urls_are_rejected(missing):
    assert build_source_record_url(missing, "AP-001") == ""
    assert is_allowed_source_record_url(missing) is False


@pytest.mark.parametrize("record_id", ["nan", "None", "null", "NaT"])
def test_validator_rejects_links_to_missing_record_placeholders(record_id):
    url = f"https://ciscosales.lightning.force.com/lightning/r/C360_CS_Task__c/{record_id}/view"
    assert is_allowed_source_record_url(url) is False


@pytest.mark.parametrize("record_id", ["AP-001", "a0A000000000001AAA", "NAN-001"])
def test_real_and_sanitized_record_ids_remain_supported(record_id):
    assert is_allowed_source_record_url(build_source_record_url("action_plans", record_id))
