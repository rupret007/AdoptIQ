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
        # Round 7 / Phase 2.8: the validator now resolves the
        # ``customer`` slot for the ``subscriptions`` dataset through
        # ``data_contracts.ROW_CONTRACT_ALIASES``.  ``BU_NAME`` is a
        # documented alias for the customer slot, so a frame with only
        # ``BU_NAME`` is now a *valid* subscriptions frame -- the old
        # behaviour (which insisted on ``ACCOUNT_ID_C`` literally)
        # silently disagreed with the row contract and rejected fetchers
        # that legitimately produced ``BU_NAME``.  We now assert the
        # *opposite* invariant: a frame with no aliases at all (e.g.
        # only an unrelated ``RANDOM_COL``) is the case that must fail.
        bad_subs = pd.DataFrame({'RANDOM_COL': ['Acme']})
        ok, missing, details = validate_data_sources_for_report(
            'compact', snowflake_ctx=object(),
            team_subs_df=bad_subs,
            ab_data=self._make_ab(), csone_data=self._make_csone(),
        )
        assert ok is False
        assert 'team_subscriptions' in missing
        # The error message lists the accepted aliases so operators can fix it.
        assert 'BU_NAME' in details['team_subscriptions'] or 'ACCOUNT_ID_C' in details['team_subscriptions']

    def test_legacy_account_id_column_still_accepted(self):
        # Round 7 / Phase 2.8: ``ACCOUNT_ID_C`` is the legacy literal
        # alias and must keep working.
        legacy_subs = pd.DataFrame({'ACCOUNT_ID_C': ['A1']})
        ok, _missing, _details = validate_data_sources_for_report(
            'compact', snowflake_ctx=object(),
            team_subs_df=legacy_subs,
            ab_data=self._make_ab(), csone_data=self._make_csone(),
        )
        assert ok is True

    def test_bu_name_only_subs_now_accepted(self):
        # Round 7 / Phase 2.8: a frame whose customer slot is filled
        # solely by ``BU_NAME`` must validate (mirrors live fetcher
        # output that uses ``BU_NAME`` as its customer label).
        bu_only = pd.DataFrame({'BU_NAME': ['Acme']})
        ok, _missing, _details = validate_data_sources_for_report(
            'compact', snowflake_ctx=object(),
            team_subs_df=bu_only,
            ab_data=self._make_ab(), csone_data=self._make_csone(),
        )
        assert ok is True

    def test_custom_required_sources(self):
        ok, missing, _ = validate_data_sources_for_report(
            'compact', snowflake_ctx=None,
            team_subs_df=pd.DataFrame(), ab_data=pd.DataFrame(), csone_data=pd.DataFrame(),
            required_sources=['snowflake'],
        )
        assert ok is False
        assert missing == ['snowflake']

    def test_round106_scope_empty_adoption_barriers_pass_when_required(self):
        # Round 106: an empty post-scope AB frame with explicit tech-filter
        # diagnostics means the selected technology has no scoped ABs. It is
        # partial data, not an unavailable adoption-barriers source.
        scoped_empty_ab = pd.DataFrame()
        scoped_empty_ab.attrs.update(
            {
                'tech_filter_empty_after_scope': True,
                'tech_filter_requested': 'Webex Calling',
                'tech_filter_total': 18,
                'tech_filter_matched': 0,
            }
        )

        ok, missing, details = validate_data_sources_for_report(
            'comprehensive',
            snowflake_ctx=object(),
            team_subs_df=self._make_team_subs(),
            ab_data=scoped_empty_ab,
            csone_data=self._make_csone(),
            required_sources=['snowflake', 'team_subscriptions', 'adoption_barriers'],
        )

        assert ok is True
        assert 'adoption_barriers' not in missing
        assert 'adoption_barriers' not in details

    def test_round106_scope_empty_heuristic_passes_when_required(self):
        scoped_empty_ab = pd.DataFrame()
        scoped_empty_ab.attrs.update(
            {
                'tech_filter_strict_applied': True,
                'tech_filter_total': 18,
                'tech_filter_matched': 0,
            }
        )

        ok, missing, _ = validate_data_sources_for_report(
            'comprehensive',
            snowflake_ctx=object(),
            team_subs_df=self._make_team_subs(),
            ab_data=scoped_empty_ab,
            csone_data=self._make_csone(),
            required_sources=['snowflake', 'team_subscriptions', 'adoption_barriers'],
        )

        assert ok is True
        assert 'adoption_barriers' not in missing

    def test_round106_fetch_error_still_fails_when_ab_required(self):
        failed_ab = pd.DataFrame()
        failed_ab.attrs['fetch_error'] = 'timeout while fetching AB data'

        ok, missing, details = validate_data_sources_for_report(
            'comprehensive',
            snowflake_ctx=object(),
            team_subs_df=self._make_team_subs(),
            ab_data=failed_ab,
            csone_data=self._make_csone(),
            required_sources=['snowflake', 'team_subscriptions', 'adoption_barriers'],
        )

        assert ok is False
        assert 'adoption_barriers' in missing
        assert 'fetch FAILED' in details['adoption_barriers']

    def test_round106_genuinely_empty_ab_still_fails_when_required(self):
        ok, missing, details = validate_data_sources_for_report(
            'comprehensive',
            snowflake_ctx=object(),
            team_subs_df=self._make_team_subs(),
            ab_data=pd.DataFrame(),
            csone_data=self._make_csone(),
            required_sources=['snowflake', 'team_subscriptions', 'adoption_barriers'],
        )

        assert ok is False
        assert 'adoption_barriers' in missing
        assert 'No adoption barrier data found' in details['adoption_barriers']


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
