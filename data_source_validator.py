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
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# Round 7 / Phase 2.8: derive the column-presence checks from the
# canonical ROW_CONTRACTS / ROW_CONTRACT_ALIASES table.  Previously
# this module hardcoded literal column names ("customer_name",
# "ACCOUNT_ID_C") which silently disagreed with the alias list in
# ``data_contracts.py``.  When a fetcher renamed a column to one of
# the documented aliases (e.g. ``BU_NAME`` for the customer slot), the
# validator still tripped a "missing required column" error even
# though the row contract said the data was present.  We now resolve
# the slot via the same alias map the row contract uses.
try:
    from data_contracts import ROW_CONTRACT_ALIASES as _ROW_CONTRACT_ALIASES
except Exception as _aliases_err:  # pragma: no cover - import-time guard
    logger.warning(
        "Round 7 / Phase 2.8: could not import ROW_CONTRACT_ALIASES (%s); "
        "falling back to legacy literal column checks.",
        _aliases_err,
    )
    _ROW_CONTRACT_ALIASES = {}


def _aliases_for(dataset: str, slot: str) -> Sequence[str]:
    return tuple(_ROW_CONTRACT_ALIASES.get(dataset, {}).get(slot, ()))


def _has_any_alias(df: pd.DataFrame, dataset: str, slot: str, *legacy_fallbacks: str) -> bool:
    """Return True when ``df`` has any of the contract aliases for the
    requested ``slot``.  ``legacy_fallbacks`` are the original literal
    column names this validator used; they are checked as a last resort
    so behavior remains backwards-compatible if the contract module
    is unavailable for any reason.
    """
    aliases = list(_aliases_for(dataset, slot))
    for fallback in legacy_fallbacks:
        if fallback and fallback not in aliases:
            aliases.append(fallback)
    if not aliases:
        return True
    cols = {str(c) for c in (df.columns if df is not None else [])}
    cols_ci = {str(c).lower() for c in cols}
    for alias in aliases:
        if alias in cols:
            return True
        if str(alias).lower() in cols_ci:
            return True
    return False


def _r106_ab_empty_after_technology_scope(ab_data: pd.DataFrame) -> bool:
    """Round 106: strict technology scoping can produce a valid empty AB slice.

    A zero-row scoped frame that carries the `_apply_scope_filter_ab`
    diagnostics is not the same as an unavailable adoption-barrier source.
    Keep fetch failures and genuinely empty upstream fetches fail-loud, but
    let reports proceed when the selected technology simply has no scoped ABs.
    """
    if ab_data is None or not getattr(ab_data, "empty", True):
        return False
    attrs = getattr(ab_data, "attrs", {}) or {}
    if not isinstance(attrs, dict):
        return False
    if attrs.get("fetch_error"):
        return False
    if attrs.get("tech_filter_empty_after_scope"):
        return True
    try:
        total = int(attrs.get("tech_filter_total") or 0)
        matched = int(attrs.get("tech_filter_matched") or 0)
    except (TypeError, ValueError):
        return False
    return total > 0 and matched == 0


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
    required_sources: List[str] = None,
    csone_file_provided: bool = False,
    allow_empty_required_sources: Optional[Sequence[str]] = None,
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
    # Round 162.1: a report may require a source's *quality contract* while
    # still allowing an honest zero-row result.  Keep this separate from
    # ``required_sources`` so fetch failures and malformed non-empty frames
    # remain fail-loud instead of being downgraded to optional warnings.
    try:
        empty_allowed = {
            str(source).strip()
            for source in (allow_empty_required_sources or ())
            if str(source).strip()
        }
    except TypeError:
        empty_allowed = set()

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

    # Round 2 / Phase 4.3: when the operator uploaded a CSOne file
    # for this job, treat ``csone`` as required regardless of report
    # type so that an upload silently producing zero rows fails loud
    # instead of being filed as "optional, missing" and the report
    # quietly proceeding without TAC evidence.
    if csone_file_provided and 'csone' not in required_sources:
        required_sources = list(required_sources) + ['csone']

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
        # Round 3 / Phase 3.3: explicitly distinguish a fetch failure
        # (attrs.fetch_error stamped upstream) from a genuine
        # zero-row result. Without this, a Snowflake timeout reads
        # exactly the same as "no subscriptions assigned" in the
        # error envelope, which is the wrong incident classification.
        _team_attrs = (
            getattr(team_subs_df, 'attrs', {}) or {}
            if isinstance(team_subs_df, pd.DataFrame)
            else {}
        )
        _team_fetch_error = _team_attrs.get('fetch_error') if isinstance(_team_attrs, dict) else None
        if team_subs_df is None or team_subs_df.empty:
            if _team_fetch_error:
                missing_sources.append('team_subscriptions')
                error_details['team_subscriptions'] = (
                    "Team subscription data is UNAVAILABLE because the upstream "
                    f"fetch failed: {_team_fetch_error}. "
                    "This is not the same as 'team has zero subscriptions'. "
                    "Retry once the data source recovers; do not interpret "
                    "downstream zero-counts as accurate."
                )
            elif (
                'team_subscriptions' in empty_allowed
                and isinstance(team_subs_df, pd.DataFrame)
            ):
                # A successful authorized fetch can legitimately become empty
                # after applying a named technology/customer criterion.  The
                # report may still have applicable AB, TAC, Pulse, Plan, or
                # Priority evidence.  Opt-in callers validate the source's
                # quality contract while letting the report-level integrity
                # gate decide whether the remaining scoped evidence is enough.
                logger.warning(
                    "Team subscriptions are empty after report criteria scoping; "
                    "continuing because the caller explicitly allows an honest "
                    "zero-row scoped subscription result"
                )
            else:
                missing_sources.append('team_subscriptions')
                error_details['team_subscriptions'] = (
                    "No team subscription data found. "
                    "This could be due to:\n"
                    "- Manager name not found in team roster\n"
                    "- No subscriptions assigned to team members\n"
                    "- Database query returned no results\n"
                    "- Data filtering removed all records"
                )
        elif not _has_any_alias(
            team_subs_df, "subscriptions", "customer", "ACCOUNT_ID_C"
        ):
            missing_sources.append('team_subscriptions')
            error_details['team_subscriptions'] = (
                "Team subscription data is missing the customer-identifier slot "
                f"(any of: {', '.join(_aliases_for('subscriptions', 'customer')) or 'ACCOUNT_ID_C'}). "
                "This indicates a data schema issue."
            )

    # Validate adoption barriers
    if 'adoption_barriers' in required_sources:
        # Round 5 / Phase 4.2: an empty DataFrame can mean either
        # "the query returned 0 rows" or "the fetch raised and we
        # swapped in an empty frame".  The fetch path now stamps a
        # ``fetch_error`` attribute on the returned DataFrame for the
        # latter; surface that explicitly so the validator does not
        # blame "no rows in window" when the truth is a connection
        # / parsing failure that operators need to see.
        _ab_fetch_err = None
        try:
            _ab_fetch_err = (getattr(ab_data, 'attrs', {}) or {}).get('fetch_error') if ab_data is not None else None
        except Exception:
            _ab_fetch_err = None
        if ab_data is None or ab_data.empty:
            if _ab_fetch_err:
                missing_sources.append('adoption_barriers')
                error_details['adoption_barriers'] = (
                    f"Adoption barrier fetch FAILED: {_ab_fetch_err}. "
                    "This is NOT a 'no barriers in window' result -- the upstream "
                    "fetch raised an exception and the report received an empty "
                    "DataFrame. Investigate the source system before treating "
                    "the empty result as a clean signal."
                )
            elif _r106_ab_empty_after_technology_scope(ab_data):
                logger.warning(
                    "Round 106: adoption barriers are empty after technology "
                    "scope filtering; treating this as partial data instead "
                    "of a missing required source"
                )
            elif (
                "adoption_barriers" in empty_allowed
                and isinstance(ab_data, pd.DataFrame)
            ):
                logger.warning(
                    "Round 162.1: adoption barriers are required for quality "
                    "validation but an honest empty frame is allowed; the "
                    "report-level integrity gate decides whether enough other "
                    "in-scope evidence exists to continue"
                )
            else:
                missing_sources.append('adoption_barriers')
                error_details['adoption_barriers'] = (
                    "No adoption barrier data found. "
                    "This could be due to:\n"
                    "- No adoption barriers in the selected time period\n"
                    "- Technology filter removed all records\n"
                    "- Database query returned no results\n"
                    "- Account IDs not matching any barriers"
                )
        elif not _has_any_alias(
            ab_data, "adoption_barriers", "customer", "customer_name"
        ):
            missing_sources.append('adoption_barriers')
            error_details['adoption_barriers'] = (
                "Adoption barrier data is missing the customer-identifier slot "
                f"(any of: {', '.join(_aliases_for('adoption_barriers', 'customer')) or 'customer_name'}). "
                "This indicates a data processing issue."
            )
    elif ab_data is not None and ab_data.empty:
        # For renewal reports, adoption barriers are optional - log warning but don't fail
        try:
            _opt_err = (getattr(ab_data, 'attrs', {}) or {}).get('fetch_error')
        except Exception:
            _opt_err = None
        if _opt_err:
            logger.warning("Adoption barrier data is empty due to fetch error (optional for renewal reports): %s", _opt_err)
        else:
            logger.warning("Adoption barrier data is empty (optional for renewal reports)")

    # Validate CSOne data
    if 'csone' in required_sources:
        # Round 5 / Phase 4.2: same fetch_error disambiguation as
        # adoption barriers above.
        _cs_fetch_err = None
        try:
            _cs_fetch_err = (getattr(csone_data, 'attrs', {}) or {}).get('fetch_error') if csone_data is not None else None
        except Exception:
            _cs_fetch_err = None
        if csone_data is None or csone_data.empty:
            missing_sources.append('csone')
            if _cs_fetch_err:
                error_details['csone'] = (
                    f"CSOne fetch FAILED: {_cs_fetch_err}. "
                    "This is NOT a 'no support cases' result -- the CSOne fetch "
                    "raised an exception and the report received an empty "
                    "DataFrame. Re-run after resolving the upstream issue."
                )
            else:
                error_details['csone'] = (
                    "No CSOne support case data found. "
                    "Use the link to access the latest AdoptIQ Export from CSOne, download the .xlsx, "
                    "then browse to select it. Also check: time period, technology filter, or customer filter."
                )
        elif not _has_any_alias(
            csone_data, "tac_cases", "customer", "customer_name"
        ):
            missing_sources.append('csone')
            error_details['csone'] = (
                "CSOne data is missing the customer-identifier slot "
                f"(any of: {', '.join(_aliases_for('tac_cases', 'customer')) or 'customer_name'}). "
                "This indicates a data processing issue."
            )
    elif csone_data is not None and csone_data.empty:
        try:
            _opt_err = (getattr(csone_data, 'attrs', {}) or {}).get('fetch_error')
        except Exception:
            _opt_err = None
        if _opt_err:
            logger.warning("CSOne data is empty due to fetch error (optional for renewal reports): %s", _opt_err)
        else:
            logger.warning("CSOne data is empty (optional for renewal reports)")

    # Round 6 / Phase 4.16: surface optional-CSConsole fetch_error
    # attrs into error_details so downstream summaries / UIs can show
    # *why* the optional source is empty (e.g. permissions issue,
    # transient network failure) instead of treating it as silently
    # missing.  We always store under the ``csconsole_*`` keys so the
    # caller can opt-in to display them without failing validation.
    def _opt_attrs_err(_df: Optional[pd.DataFrame]) -> Optional[str]:
        if _df is None:
            return None
        try:
            err = _df.attrs.get('fetch_error') if hasattr(_df, 'attrs') else None
        except Exception:
            err = None
        return str(err) if err else None

    if csconsole_action_plans is not None:
        if csconsole_action_plans.empty:
            _err = _opt_attrs_err(csconsole_action_plans)
            if _err:
                logger.warning(
                    "CSConsole action plans data is empty due to fetch error (optional): %s",
                    _err,
                )
                error_details['csconsole_action_plans'] = _err
            else:
                logger.warning("CSConsole action plans data is empty (optional)")

    if csconsole_customer_pulse is not None:
        if csconsole_customer_pulse.empty:
            _err = _opt_attrs_err(csconsole_customer_pulse)
            if _err:
                logger.warning(
                    "CSConsole customer pulse data is empty due to fetch error (optional): %s",
                    _err,
                )
                error_details['csconsole_customer_pulse'] = _err
            else:
                logger.warning("CSConsole customer pulse data is empty (optional)")

    if csconsole_success_priorities is not None:
        if csconsole_success_priorities.empty:
            _err = _opt_attrs_err(csconsole_success_priorities)
            if _err:
                logger.warning(
                    "CSConsole success priorities data is empty due to fetch error (optional): %s",
                    _err,
                )
                error_details['csconsole_success_priorities'] = _err
            else:
                logger.warning("CSConsole success priorities data is empty (optional)")

    is_valid = len(missing_sources) == 0

    return is_valid, missing_sources, error_details


# Round 30 / M4: a "Partial Data" surface lives next to the strict
# is_valid / missing_sources boolean.  ``error_details`` already carries
# any optional CSConsole fetch failures under ``csconsole_*`` keys;
# this helper provides a single, well-named predicate so each report
# writer can branch on "the report is generatable, but optional sources
# failed" without repeating the prefix-matching logic.
_OPTIONAL_FETCH_ERROR_KEYS: tuple = (
    'csconsole_action_plans',
    'csconsole_customer_pulse',
    'csconsole_success_priorities',
    'csconsole_adoption_barriers',
    # ARR / renewal optional source — populated by upstream renewal
    # validators when the SF arr feed fails but the report itself is
    # still generatable from the required sources.
    'arr_data',
)


def has_optional_fetch_errors(error_details: Optional[Dict[str, str]]) -> bool:
    """Round 30 / M4: True iff any optional CSConsole / ARR fetch failed.

    The strict ``is_valid`` flag returned by
    :func:`validate_data_sources_for_report` only considers REQUIRED
    sources.  When an optional CSConsole fetch fails (e.g. a transient
    network hiccup against the CSConsole API), we still emit the
    report -- but the reader should be told that one or more of the
    optional sub-feeds was unavailable so they don't treat empty
    optional sections as authoritative ("no action plans" vs "we
    couldn't fetch action plans this run").

    Returns ``False`` for ``None`` / empty dicts so callers can simply
    ``if has_optional_fetch_errors(error_details): render_banner(...)``
    without nullable-guard scaffolding at every call-site.
    """
    if not error_details or not isinstance(error_details, dict):
        return False
    try:
        for key in _OPTIONAL_FETCH_ERROR_KEYS:
            value = error_details.get(key)
            if isinstance(value, str) and value.strip():
                return True
    except Exception:  # noqa: BLE001 -- defensive; never let a bad
        # ``error_details`` shape break report generation.
        return False
    return False


def get_optional_fetch_errors(error_details: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Round 30 / M4: return only the optional-source fetch errors.

    Pairs with :func:`has_optional_fetch_errors`.  Useful for the
    renderer when it wants to list "which optional sources failed"
    in the partial-data banner without leaking required-source errors.
    Always returns a fresh ``dict``.
    """
    out: Dict[str, str] = {}
    if not error_details or not isinstance(error_details, dict):
        return out
    try:
        for key in _OPTIONAL_FETCH_ERROR_KEYS:
            value = error_details.get(key)
            if isinstance(value, str) and value.strip():
                out[key] = value.strip()
    except Exception:  # noqa: BLE001
        return {}
    return out


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
    import canonical_metrics as _r531_cm
    _ab_record_count = _r531_cm.count_total_barriers(ab_data)
    summary['adoption_barriers'] = {
        'available': ab_data is not None and not ab_data.empty,
        'row_count': len(ab_data) if ab_data is not None and not ab_data.empty else 0,
        'status': 'available' if ab_data is not None and not ab_data.empty else 'empty',
        # Round 53.1: human-facing details use distinct barrier records; keep
        # row_count above for diagnostics of the raw export shape.
        'details': f'{_ab_record_count} adoption barrier records found' if ab_data is not None and not ab_data.empty else 'No adoption barriers found'
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
