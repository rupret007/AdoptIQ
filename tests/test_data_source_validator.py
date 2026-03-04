"""
Tests for data_source_validator module.
Validates data source checking logic for all report types.
"""
import os
import sys
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from data_source_validator import (
    validate_data_sources_for_report,
    raise_validation_error_if_invalid,
    get_data_source_summary,
    DataSourceValidationError,
)


class TestValidateDataSources:
    """Test validate_data_sources_for_report across report types."""

    def _make_team_subs(self):
        return pd.DataFrame({'ACCOUNT_ID_C': ['A1'], 'BU_NAME': ['Acme']})

    def _make_ab(self):
        return pd.DataFrame({'customer_name': ['Acme'], 'SEVERITY_C': ['High']})

    def _make_csone(self):
        return pd.DataFrame({'customer_name': ['Acme'], 'SR Number': ['TAC001']})

    def test_comprehensive_all_present(self):
        ok, missing, details = validate_data_sources_for_report(
            'comprehensive', snowflake_ctx=object(),
            team_subs_df=self._make_team_subs(),
            ab_data=self._make_ab(), csone_data=self._make_csone(),
        )
        assert ok is True
        assert missing == []

    def test_comprehensive_missing_snowflake(self):
        ok, missing, _ = validate_data_sources_for_report(
            'comprehensive', snowflake_ctx=None,
            team_subs_df=self._make_team_subs(),
            ab_data=self._make_ab(), csone_data=self._make_csone(),
        )
        assert ok is False
        assert 'snowflake' in missing

    def test_comprehensive_missing_csone(self):
        ok, missing, _ = validate_data_sources_for_report(
            'comprehensive', snowflake_ctx=object(),
            team_subs_df=self._make_team_subs(),
            ab_data=self._make_ab(), csone_data=pd.DataFrame(),
        )
        assert ok is False
        assert 'csone' in missing

    def test_renewal_only_needs_snowflake_and_subs(self):
        ok, missing, _ = validate_data_sources_for_report(
            'renewal', snowflake_ctx=object(),
            team_subs_df=self._make_team_subs(),
            ab_data=pd.DataFrame(), csone_data=pd.DataFrame(),
        )
        assert ok is True
        assert missing == []

    def test_missing_account_id_column(self):
        bad_subs = pd.DataFrame({'BU_NAME': ['Acme']})
        ok, missing, details = validate_data_sources_for_report(
            'compact', snowflake_ctx=object(),
            team_subs_df=bad_subs,
            ab_data=self._make_ab(), csone_data=self._make_csone(),
        )
        assert ok is False
        assert 'team_subscriptions' in missing
        assert 'ACCOUNT_ID_C' in details['team_subscriptions']

    def test_custom_required_sources(self):
        ok, missing, _ = validate_data_sources_for_report(
            'compact', snowflake_ctx=None,
            team_subs_df=pd.DataFrame(), ab_data=pd.DataFrame(), csone_data=pd.DataFrame(),
            required_sources=['snowflake'],
        )
        assert ok is False
        assert missing == ['snowflake']


class TestRaiseValidationError:
    """Test raise_validation_error_if_invalid."""

    def test_raises_on_missing(self):
        with pytest.raises(DataSourceValidationError) as exc_info:
            raise_validation_error_if_invalid(
                'comprehensive', snowflake_ctx=None,
                team_subs_df=pd.DataFrame(), ab_data=pd.DataFrame(), csone_data=pd.DataFrame(),
            )
        assert 'snowflake' in exc_info.value.missing_sources

    def test_no_raise_when_valid(self):
        subs = pd.DataFrame({'ACCOUNT_ID_C': ['A1'], 'BU_NAME': ['Acme']})
        ab = pd.DataFrame({'customer_name': ['Acme']})
        csone = pd.DataFrame({'customer_name': ['Acme']})
        raise_validation_error_if_invalid(
            'comprehensive', snowflake_ctx=object(),
            team_subs_df=subs, ab_data=ab, csone_data=csone,
        )


class TestGetDataSourceSummary:
    """Test get_data_source_summary."""

    def test_summary_all_available(self):
        subs = pd.DataFrame({'ACCOUNT_ID_C': ['A1']})
        ab = pd.DataFrame({'customer_name': ['Acme']})
        csone = pd.DataFrame({'customer_name': ['Acme']})
        summary = get_data_source_summary(
            snowflake_ctx=object(), team_subs_df=subs,
            ab_data=ab, csone_data=csone,
        )
        assert summary['snowflake']['available'] is True
        assert summary['team_subscriptions']['row_count'] == 1
        assert summary['adoption_barriers']['available'] is True
        assert summary['csone']['available'] is True

    def test_summary_empty_sources(self):
        summary = get_data_source_summary(
            snowflake_ctx=None, team_subs_df=pd.DataFrame(),
            ab_data=None, csone_data=pd.DataFrame(),
        )
        assert summary['snowflake']['available'] is False
        assert summary['team_subscriptions']['row_count'] == 0
        assert summary['adoption_barriers']['available'] is False
