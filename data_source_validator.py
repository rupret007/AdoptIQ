"""
Data Source Validator for AdoptIQ
Validates all required data sources before report generation.
Raises descriptive errors instead of using fallback data.

TAC cases and BEMS: All four report types (Comprehensive, Compact, Renewal, Leader) analyze
CSOne TAC (support) cases and BEMS escalations where CSOne data is available. BEMS detection
uses Transaction ID and bemscsc_refs; TAC case counts and lists appear in each report.
"""

import pandas as pd
import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger(__name__)


class DataSourceValidationError(Exception):
    """Custom exception for data source validation failures"""
    def __init__(self, message: str, missing_sources: List[str], details: Dict = None):
        self.message = message
        self.missing_sources = missing_sources
        self.details = details or {}
        super().__init__(self.message)


def validate_data_sources_for_report(
    report_type: str,
    snowflake_ctx,
    team_subs_df: pd.DataFrame,
    ab_data: pd.DataFrame,
    csone_data: pd.DataFrame,
    csconsole_action_plans: pd.DataFrame = None,
    csconsole_customer_pulse: pd.DataFrame = None,
    csconsole_success_priorities: pd.DataFrame = None,
    arr_data: pd.DataFrame = None,
    required_sources: List[str] = None
) -> Tuple[bool, List[str], Dict[str, str]]:
    """
    Validate all required data sources for report generation.
    
    Args:
        report_type: Type of report ('comprehensive', 'compact', 'renewal', 'leader')
        snowflake_ctx: Snowflake connection context (can be None)
        team_subs_df: Team subscriptions DataFrame
        ab_data: Adoption barriers DataFrame
        csone_data: CSOne cases DataFrame
        csconsole_action_plans: CSConsole action plans DataFrame (optional)
        csconsole_customer_pulse: CSConsole customer pulse DataFrame (optional)
        csconsole_success_priorities: CSConsole success priorities DataFrame (optional)
        arr_data: Reserved for backward compatibility (ignored)
        required_sources: List of required data sources (if None, uses defaults for report_type)
    
    Returns:
        Tuple of (is_valid, missing_sources, error_details)
    """
    missing_sources = []
    error_details = {}
    
    # Define required sources by report type
    if required_sources is None:
        if report_type == 'comprehensive':
            required_sources = ['snowflake', 'team_subscriptions', 'adoption_barriers', 'csone']
        elif report_type == 'compact':
            required_sources = ['snowflake', 'team_subscriptions', 'adoption_barriers', 'csone']
        elif report_type == 'renewal' or report_type == 'renewal_portfolio':
            # For renewal reports, only require snowflake and team_subscriptions
            # CSOne and adoption_barriers are optional (can be empty if no data exists)
            required_sources = ['snowflake', 'team_subscriptions']
        elif report_type == 'leader':
            required_sources = ['snowflake', 'team_subscriptions']
        else:
            required_sources = ['snowflake', 'team_subscriptions', 'adoption_barriers', 'csone']
    
    # Validate Snowflake connection
    if 'snowflake' in required_sources:
        if snowflake_ctx is None:
            missing_sources.append('snowflake')
            error_details['snowflake'] = (
                "Snowflake database connection is unavailable. "
                "This could be due to:\n"
                "- Network connectivity issues\n"
                "- Authentication/credential problems\n"
                "- Database server unavailability\n"
                "- Connection timeout (check if database is responding)"
            )
    
    # Validate team subscriptions
    if 'team_subscriptions' in required_sources:
        if team_subs_df is None or team_subs_df.empty:
            missing_sources.append('team_subscriptions')
            error_details['team_subscriptions'] = (
                "No team subscription data found. "
                "This could be due to:\n"
                "- Manager name not found in team roster\n"
                "- No subscriptions assigned to team members\n"
                "- Database query returned no results\n"
                "- Data filtering removed all records"
            )
        elif 'ACCOUNT_ID_C' not in team_subs_df.columns:
            missing_sources.append('team_subscriptions')
            error_details['team_subscriptions'] = (
                "Team subscription data is missing required columns (ACCOUNT_ID_C). "
                "This indicates a data schema issue."
            )
    
    # Validate adoption barriers
    if 'adoption_barriers' in required_sources:
        if ab_data is None or ab_data.empty:
            missing_sources.append('adoption_barriers')
            error_details['adoption_barriers'] = (
                "No adoption barrier data found. "
                "This could be due to:\n"
                "- No adoption barriers in the selected time period\n"
                "- Technology filter removed all records\n"
                "- Database query returned no results\n"
                "- Account IDs not matching any barriers"
            )
        elif 'customer_name' not in ab_data.columns:
            missing_sources.append('adoption_barriers')
            error_details['adoption_barriers'] = (
                "Adoption barrier data is missing required columns (customer_name). "
                "This indicates a data processing issue."
            )
    elif ab_data is not None and ab_data.empty:
        # For renewal reports, adoption barriers are optional - log warning but don't fail
        logger.warning("Adoption barrier data is empty (optional for renewal reports)")
    
    # Validate CSOne data
    if 'csone' in required_sources:
        if csone_data is None or csone_data.empty:
            missing_sources.append('csone')
            error_details['csone'] = (
                "No CSOne support case data found. "
                "Use the link to access the latest AdoptIQ Export from CSOne, download the .xlsx, "
                "then browse to select it. Also check: time period, technology filter, or customer filter."
            )
        elif 'customer_name' not in csone_data.columns:
            missing_sources.append('csone')
            error_details['csone'] = (
                "CSOne data is missing required columns (customer_name). "
                "This indicates a data processing issue."
            )
    elif csone_data is not None and csone_data.empty:
        # For renewal reports, CSOne is optional - log warning but don't fail
        logger.warning("CSOne data is empty (optional for renewal reports)")
    
    # Validate CSConsole data (optional but checked if provided)
    if csconsole_action_plans is not None:
        if csconsole_action_plans.empty:
            logger.warning("CSConsole action plans data is empty (optional)")
    
    if csconsole_customer_pulse is not None:
        if csconsole_customer_pulse.empty:
            logger.warning("CSConsole customer pulse data is empty (optional)")
    
    if csconsole_success_priorities is not None:
        if csconsole_success_priorities.empty:
            logger.warning("CSConsole success priorities data is empty (optional)")
    
    is_valid = len(missing_sources) == 0
    
    return is_valid, missing_sources, error_details


def raise_validation_error_if_invalid(
    report_type: str,
    snowflake_ctx,
    team_subs_df: pd.DataFrame,
    ab_data: pd.DataFrame,
    csone_data: pd.DataFrame,
    **kwargs
):
    """
    Validate data sources and raise descriptive error if invalid.
    
    Raises:
        DataSourceValidationError: If required data sources are missing
    """
    is_valid, missing_sources, error_details = validate_data_sources_for_report(
        report_type=report_type,
        snowflake_ctx=snowflake_ctx,
        team_subs_df=team_subs_df,
        ab_data=ab_data,
        csone_data=csone_data,
        **kwargs
    )
    
    if not is_valid:
        # Build comprehensive error message (ASCII-safe for Windows console cp1252)
        error_parts = [
            f"[ERROR] DATA SOURCE VALIDATION FAILED for {report_type.upper()} report",
            "",
            "Missing Required Data Sources:",
            "=" * 60
        ]
        
        for source in missing_sources:
            error_parts.append(f"\n[REQUIRED] {source.upper()}:")
            error_parts.append(error_details.get(source, "No details available"))
            error_parts.append("")
        
        error_parts.extend([
            "=" * 60,
            "",
            "ACTION REQUIRED:",
            "1. Check the error details above for each missing data source",
            "2. Verify database connectivity and credentials",
            "3. Ensure data exists for the selected manager/technology/time period",
            "4. Check that CSOne file was uploaded and contains valid data",
            "5. Review logs for specific query errors or timeouts",
            "",
            "The report cannot be generated without these required data sources.",
            "Please resolve the issues and try again."
        ])
        
        error_message = "\n".join(error_parts)
        
        logger.error(error_message)
        
        raise DataSourceValidationError(
            message=error_message,
            missing_sources=missing_sources,
            details=error_details
        )
    
    logger.info(f"[OK] Data source validation passed for {report_type} report")


def get_data_source_summary(
    snowflake_ctx,
    team_subs_df: pd.DataFrame,
    ab_data: pd.DataFrame,
    csone_data: pd.DataFrame,
    csconsole_action_plans: pd.DataFrame = None,
    csconsole_customer_pulse: pd.DataFrame = None,
    csconsole_success_priorities: pd.DataFrame = None,
    arr_data: pd.DataFrame = None
) -> Dict[str, Dict]:
    """
    Get a summary of all data sources and their status.
    
    Returns:
        Dictionary with status information for each data source
    """
    summary = {}
    
    # Snowflake
    summary['snowflake'] = {
        'available': snowflake_ctx is not None,
        'status': 'connected' if snowflake_ctx is not None else 'not_connected',
        'details': 'Snowflake connection active' if snowflake_ctx is not None else 'Snowflake connection unavailable'
    }
    
    # Team subscriptions
    summary['team_subscriptions'] = {
        'available': team_subs_df is not None and not team_subs_df.empty,
        'row_count': len(team_subs_df) if team_subs_df is not None and not team_subs_df.empty else 0,
        'status': 'available' if team_subs_df is not None and not team_subs_df.empty else 'empty',
        'details': f'{len(team_subs_df)} subscriptions found' if team_subs_df is not None and not team_subs_df.empty else 'No team subscriptions found'
    }
    
    # Adoption barriers
    summary['adoption_barriers'] = {
        'available': ab_data is not None and not ab_data.empty,
        'row_count': len(ab_data) if ab_data is not None and not ab_data.empty else 0,
        'status': 'available' if ab_data is not None and not ab_data.empty else 'empty',
        'details': f'{len(ab_data)} adoption barriers found' if ab_data is not None and not ab_data.empty else 'No adoption barriers found'
    }
    
    # CSOne
    summary['csone'] = {
        'available': csone_data is not None and not csone_data.empty,
        'row_count': len(csone_data) if csone_data is not None and not csone_data.empty else 0,
        'status': 'available' if csone_data is not None and not csone_data.empty else 'empty',
        'details': f'{len(csone_data)} support cases found' if csone_data is not None and not csone_data.empty else 'No CSOne cases found'
    }
    
    # CSConsole (optional)
    if csconsole_action_plans is not None:
        summary['csconsole_action_plans'] = {
            'available': not csconsole_action_plans.empty,
            'row_count': len(csconsole_action_plans),
            'status': 'available' if not csconsole_action_plans.empty else 'empty'
        }
    
    if csconsole_customer_pulse is not None:
        summary['csconsole_customer_pulse'] = {
            'available': not csconsole_customer_pulse.empty,
            'row_count': len(csconsole_customer_pulse),
            'status': 'available' if not csconsole_customer_pulse.empty else 'empty'
        }
    
    if csconsole_success_priorities is not None:
        summary['csconsole_success_priorities'] = {
            'available': not csconsole_success_priorities.empty,
            'row_count': len(csconsole_success_priorities),
            'status': 'available' if not csconsole_success_priorities.empty else 'empty'
        }
    
    return summary

