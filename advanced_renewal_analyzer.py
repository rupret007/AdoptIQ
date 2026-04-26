"""
Advanced Renewal Analysis System
Comprehensive renewal risk assessment using all available Snowflake data sources
"""

import logging
import math
import re
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Tuple, Optional
from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from snowflake_table_policy import is_table_blocked

# Round 4: route renewal-risk band labeling through the canonical
# ``RISK_BAND_THRESHOLDS`` so the same numeric score gets the same
# CRITICAL/HIGH/MEDIUM/LOW label everywhere in the platform.
#
# Round 7 / Phase 6.13: the previous implementation soft-failed to a
# literal ``{"CRITICAL": 75, "HIGH": 55, ...}`` dict if
# ``risk_scoring`` could not be imported.  That created a *second*
# source of truth for the band thresholds: any future tweak in
# ``risk_scoring.RISK_BAND_THRESHOLDS`` would silently disagree with
# the renewal Word report whenever the import path failed (the
# observed real-world trigger was a transient circular import while
# the Flask app was warming).  We now raise on import failure
# instead -- the renewal report is explicitly anchored to the
# canonical thresholds and any scenario where they cannot be loaded
# is a configuration bug we want surfaced immediately, not a silent
# divergence that ships into Word documents.
try:
    from risk_scoring import RISK_BAND_THRESHOLDS as _RISK_BAND_THRESHOLDS_0_100
except Exception as _band_imp_err:  # pragma: no cover - defensive only
    raise RuntimeError(
        "advanced_renewal_analyzer requires risk_scoring.RISK_BAND_THRESHOLDS "
        "to be importable; the legacy literal-dict fallback was removed in "
        "Round 7 / Phase 6.13 to prevent two sources of truth for renewal "
        f"risk-band labels. Underlying import error: {_band_imp_err}"
    ) from _band_imp_err


def _renewal_risk_category_from_score(score_0_100: float) -> str:
    """Round 4: Map a 0-100 renewal-risk score to a canonical band label.

    Uses the same thresholds as ``risk_scoring._risk_band`` (CRITICAL=75,
    HIGH=55, MEDIUM=35, LOW=15) so the renewal Word report and the
    cross-report summary cannot disagree on what "HIGH" means at score 60.
    The MINIMAL bucket is preserved for renewal-specific narratives where
    a "no risk signal" callout differs from the canonical HEALTHY label.
    """
    score = max(0.0, min(100.0, float(score_0_100 or 0.0)))
    if score >= _RISK_BAND_THRESHOLDS_0_100["CRITICAL"]:
        return "CRITICAL"
    if score >= _RISK_BAND_THRESHOLDS_0_100["HIGH"]:
        return "HIGH"
    if score >= _RISK_BAND_THRESHOLDS_0_100["MEDIUM"]:
        return "MEDIUM"
    if score >= _RISK_BAND_THRESHOLDS_0_100["LOW"]:
        return "LOW"
    return "MINIMAL"


logger = logging.getLogger(__name__)


def _safe_doc_text(value: Any, max_len: int = 200) -> str:
    """Round 7 / Phase 6.12: sanitize a string before
    ``doc.add_heading`` / ``add_run`` / ``cell.text`` writes in the
    renewal Word report path.

    Mirrors ``app_simple._safe_doc_text`` (Round 6 / Phase 1.4) so a
    malformed customer name, recommendation, or risk factor cannot
    crash python-docx (or produce a Word "this document needs to be
    repaired" prompt).  Drops XML-illegal control codes (the ``<0x20``
    range and unpaired surrogates that python-docx rejects), collapses
    runs of whitespace into a single space, and caps the rendered
    length so a runaway string doesn't bloat the heading.

    Kept local rather than imported from ``app_simple`` to avoid the
    circular import that triggers when ``app_simple`` is being
    loaded by Flask while ``advanced_renewal_analyzer`` is itself
    being imported transitively from the report path.
    """
    try:
        s = "" if value is None else str(value)
    except Exception:
        return ""
    cleaned_chars = []
    for ch in s:
        cp = ord(ch)
        # Strip XML 1.0 illegal C0 controls except TAB/LF/CR which
        # python-docx (and Word) accept.
        if cp < 0x20 and ch not in ("\t", "\n", "\r"):
            continue
        # Strip lone surrogate halves -- xml.etree raises ValueError on
        # these and python-docx silently corrupts the part.
        if 0xD800 <= cp <= 0xDFFF:
            continue
        cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if max_len and len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return cleaned


def _is_snowflake_access_issue(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return any(
        token in message
        for token in (
            "invalid identifier",
            "sql compilation error",
            "object does not exist",
            "does not exist or not authorized",
            "not authorized",
            "insufficient privileges",
            "permission denied",
            "access denied",
        )
    )


def _log_query_fallback(context: str, exc: Exception) -> None:
    if _is_snowflake_access_issue(exc):
        logger.warning("%s skipped due to Snowflake access/schema limitations: %s", context, str(exc).strip())
    else:
        logger.error("%s failed: %s", context, exc)


def _empty_dict_failed(exc: Exception) -> Dict[str, Any]:
    """Round 7 / Phase 6.10: build an empty dict whose ``fetch_error``
    key is populated so ``report_utils.classify_data_state`` returns
    ``"failed"`` instead of ``"empty"``.

    The renewal analyzer's per-source fetchers (``_get_account_info``,
    ``_get_contract_renewal_info``, ``_get_financial_metrics``,
    ``_get_usage_metrics``, ``_get_support_engagement_metrics``,
    ``_get_adoption_metrics``) used to swallow exceptions and return
    a bare ``{}``, which is identical to a successful "no rows"
    result.  Validators and the renewal Word's empty-state copy
    therefore could not flag a partial-data warning.  Using this
    sentinel keeps the contract (returning a dict) but lets callers
    classify the outcome without inspecting the exception.
    """
    return {
        'fetch_error': str(exc).strip() or exc.__class__.__name__ or 'fetch_failed',
    }


def _utc_window_start_iso(days: int) -> str:
    """Round 10 / Phase 5.2: compute today_utc - days as an ISO date.

    Local copy of ``adoptiq_backend._utc_window_start_iso`` to avoid the
    circular import that triggers when ``app_simple``/``adoptiq_backend``
    is being loaded by Flask while ``advanced_renewal_analyzer`` is
    itself being imported transitively from the report path. Bind the
    returned ISO date as a parameter so the lookback window is
    independent of the Snowflake session TZ.
    """
    try:
        _days = int(days)
    except (TypeError, ValueError):
        _days = 0
    if _days < 0:
        _days = 0
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=_days)
    return cutoff.isoformat()


def _safe_num(val, default=0):
    """Return val if it is a finite number, otherwise default."""
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    if not isinstance(val, (int, float)):
        return default
    if not math.isfinite(val):
        return default
    return val


# Professional color palette
CISCO_BLUE = RGBColor(0x00, 0x7B, 0xC7)
CISCO_GRAY = RGBColor(0x58, 0x59, 0x5B)
CISCO_LIGHT_GRAY = RGBColor(0xF5, 0xF5, 0xF5)

class AdvancedRenewalAnalyzer:
    """Advanced renewal analysis using comprehensive Snowflake data sources"""
    
    def __init__(self, ctx, strict_mode: bool = False):
        """
        Initialize the advanced renewal analyzer
        
        Args:
            ctx: Snowflake connection context
            strict_mode: Round 7 / Phase 6.11 — when True, partial-data
                conditions (canonical helper missing, ``fetch_error`` on
                a critical Snowflake source, currency-mismatch in ARR
                aggregation) raise ``RuntimeError`` instead of being
                silently downgraded.  Defaults to False so existing
                callers retain the soft-fail behaviour; orchestration
                code (``app_simple`` / leader report path) should
                forward the user's strict-mode preference here so the
                renewal narrative matches the executive intelligence
                strict semantics introduced in Round 7 / Phase 1.8.
        """
        self.ctx = ctx
        # Round 7 / Phase 6.11: keep on instance so any helper /
        # validator that runs during renewal analysis can read
        # ``self.strict_mode`` without changing every signature.
        self.strict_mode = bool(strict_mode)
    
    def analyze_customer_renewal_risk(self, customer_name: str, days: int = 90) -> Dict[str, Any]:
        """
        Comprehensive renewal risk analysis for a specific customer
        
        Args:
            customer_name: Name of the customer to analyze
            days: Number of days to look back for analysis
            
        Returns:
            Dictionary containing comprehensive renewal analysis
        """
        # Round 7 / Phase 6.3: customer_name is portfolio-customer
        # PII-adjacent and must not appear at INFO level.  Counts /
        # state stay at INFO; the actual customer label moves to
        # DEBUG so production INFO logs do not carry portfolio
        # identifiers.  We log a short SHA-256 digest at INFO so
        # operators can still correlate runs in audit trails without
        # the cleartext name.
        try:
            import hashlib as _hashlib_p63
            _cust_digest = _hashlib_p63.sha256(
                str(customer_name or "").encode("utf-8", errors="replace")
            ).hexdigest()[:8]
        except Exception:
            _cust_digest = "unknown"
        logger.info(f"Starting comprehensive renewal analysis (customer_digest={_cust_digest})")
        logger.debug(f"Customer name for digest {_cust_digest}: {customer_name}")
        
        analysis_results = {
            'customer_name': customer_name,
            # Round 7 / Phase 6.7: stamp ``analysis_date`` in UTC so
            # the renewal Word footer agrees with the Snowflake-side
            # date math (which always uses UTC).  Local-tz
            # ``datetime.now()`` shifted the stamp by hours depending
            # on which worker ran the job.
            'analysis_date': datetime.now(timezone.utc).isoformat(),
            'analysis_period_days': days,
            'renewal_risk_score': 0,
            'renewal_risk_category': 'UNKNOWN',
            'key_findings': [],
            'recommendations': [],
            'data_sources': {},
            'contract_information': {},
            'usage_metrics': {},
            'support_metrics': {},
            'adoption_metrics': {},
            'financial_metrics': {},
            'risk_factors': [],
            'success_factors': []
        }
        
        try:
            # 1. Get customer account information
            account_info = self._get_customer_account_info(customer_name)
            analysis_results['data_sources']['account_info'] = account_info
            
            # Round 4: ``account_info`` now carries a reserved ``_meta`` key
            # for fetch-limit disclosure; iterate only the real account
            # records when checking for emptiness or selecting the first.
            _account_records = {
                k: v for k, v in account_info.items() if not str(k).startswith('_')
            }
            if not _account_records:
                analysis_results['renewal_risk_category'] = 'HIGH'
                analysis_results['key_findings'].append('Customer not found in system - HIGH RISK')
                return analysis_results

            # Round 2 / Phase 1.3: refuse to silently pick the first
            # bucket when the LIKE-fallback resolver returned multiple
            # distinct ACCOUNT_ID_C values for the same customer name.
            _ai_meta = account_info.get('_meta', {}) or {}
            if _ai_meta.get('ambiguous'):
                analysis_results['renewal_risk_category'] = 'UNKNOWN'
                analysis_results['key_findings'].append(
                    f"Account lookup ambiguous: {_ai_meta.get('distinct_account_ids', 0)} candidates"
                    f" matched '{customer_name}' via LIKE fallback. Renewal risk cannot be"
                    " resolved without an exact account selection."
                )
                analysis_results['data_sources']['account_info_ambiguous'] = True
                return analysis_results

            first_account = next(iter(_account_records.values()))
            account_id = first_account.get('ACCOUNT_ID_C')
            # Round 7 / Phase 6.3: keep account_id at INFO (it is an
            # opaque internal Salesforce ID, not a customer name);
            # demote the cleartext customer_name to DEBUG.
            logger.info(f"Using account_id: {account_id} (customer_digest={_cust_digest})")
            logger.debug(f"Resolved account_id {account_id} for customer: {customer_name}")
            if account_id is None:
                analysis_results['renewal_risk_category'] = 'HIGH'
                analysis_results['key_findings'].append('Account ID not found in system - HIGH RISK')
                return analysis_results
            
            # 2. Get contract and renewal information
            contract_info = self._get_contract_renewal_info(account_id, customer_name)
            analysis_results['contract_information'] = contract_info
            analysis_results['data_sources']['contract_info'] = contract_info
            
            # 3. Get ARR and financial metrics
            financial_metrics = self._get_financial_metrics(account_id, customer_name)
            analysis_results['financial_metrics'] = financial_metrics
            analysis_results['data_sources']['financial_metrics'] = financial_metrics
            
            # 4. Get usage and adoption metrics
            usage_metrics = self._get_usage_adoption_metrics(account_id, days)
            analysis_results['usage_metrics'] = usage_metrics
            analysis_results['data_sources']['usage_metrics'] = usage_metrics
            
            # 5. Get support and engagement metrics
            # Round 7 / Phase 6.2: pass customer_name so the
            # SUCCESS_PRIORITY__C lookup uses the right join key.
            support_metrics = self._get_support_engagement_metrics(account_id, days, customer_name=customer_name)
            analysis_results['support_metrics'] = support_metrics
            analysis_results['data_sources']['support_metrics'] = support_metrics
            
            # 6. Get adoption barriers and success factors
            adoption_metrics = self._get_adoption_success_metrics(account_id, days)
            analysis_results['adoption_metrics'] = adoption_metrics
            analysis_results['data_sources']['adoption_metrics'] = adoption_metrics
            
            # 7. Calculate comprehensive renewal risk score
            risk_analysis = self._calculate_renewal_risk_score(analysis_results)
            analysis_results.update(risk_analysis)
            
            # 8. Generate recommendations
            recommendations = self._generate_renewal_recommendations(analysis_results)
            analysis_results['recommendations'] = recommendations
            
            logger.info(f"✅ Renewal analysis complete for {customer_name}")
            logger.info(f"   Risk Score: {analysis_results['renewal_risk_score']}/100")
            logger.info(f"   Risk Category: {analysis_results['renewal_risk_category']}")
            
            return analysis_results
            
        except Exception as e:
            _log_query_fallback(f"Renewal analysis for {customer_name}", e)
            analysis_results['error'] = 'Renewal analysis encountered an error. See logs for details.'
            analysis_results['renewal_risk_category'] = 'ERROR'
            return analysis_results
    
    def _get_customer_account_info(self, customer_name: str) -> Dict:
        """Get comprehensive customer account information"""
        logger.info(f"INFO: Getting account information for: {customer_name}")
        
        # Handle mock connections
        if isinstance(self.ctx, str) and self.ctx == 'mock_connection':
            logger.info("Using mock connection - returning empty data")
            return {}
        
        cur = None
        try:
            cur = self.ctx.cursor()

            # Round 2 / Phase 1.3: replace the legacy fuzzy LIKE %name% +
            # LIMIT 10 + first-bucket-wins lookup with a deterministic
            # multi-pass resolver:
            #   (a) exact match on BU_ACCOUNT_NAME or ACCOUNT_NAME,
            #   (b) only if (a) is empty, fall back to a bounded LIKE
            #       lookup, but fail-closed when results are ambiguous
            #       (multiple distinct ACCOUNT_ID_C values).
            # This eliminates the silent collision where two customers
            # whose names share a substring would resolve to whichever
            # row Snowflake happened to return first.
            _FETCH_LIMIT = 50  # raised from 10 so we can detect collisions

            def _columns_for_row(row):
                return {
                    'ACCOUNT_ID_C': row[0],
                    'BU_ACCOUNT_NAME': row[1],
                    'ACCOUNT_NAME': row[2],
                    'CONTRACT_STATUS': row[3],
                    'NEW_RENEWAL_EXISTING': row[4],
                    'RENEWAL_RISK_CATEGORY': row[5],
                    'ARR_VIEW': row[6],
                    'RECORD_TYPE': row[7],
                    'CONTRACT_NUMBER': row[8],
                    'SUBSCRIPTION_ID': row[9],
                }

            base_select = """
            SELECT
                ACCOUNT_ID_C,
                BU_ACCOUNT_NAME,
                ACCOUNT_NAME,
                CONTRACT_STATUS,
                NEW_RENEWAL_EXISTING,
                RENEWAL_RISK_CATEGORY,
                ARR_VIEW,
                RECORD_TYPE,
                CONTRACT_NUMBER,
                SUBSCRIPTION_ID
            FROM CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY
            """

            # Pass (a): exact name match.  Both BU_ACCOUNT_NAME and
            # ACCOUNT_NAME are candidate identifiers in this table.
            # Round 12 / Phase 7.1: previously the exact / LIKE
            # passes both ended in ``LIMIT N`` with NO ``ORDER BY``.
            # When the result set was wider than ``_FETCH_LIMIT``
            # (a customer with many subscriptions / contract
            # numbers) Snowflake was free to return *any* arbitrary
            # ``_FETCH_LIMIT`` rows -- and the multi-distinct
            # ACCOUNT_ID_C "fail-closed" guard immediately below
            # could trip or not trip non-deterministically across
            # reruns of the same report.  Sort by stable natural
            # keys (ACCOUNT_ID_C, BU_ACCOUNT_NAME, CONTRACT_NUMBER)
            # so the same input always returns the same window and
            # the resolution-mode decision is reproducible.
            exact_query = base_select + (
                " WHERE UPPER(TRIM(BU_ACCOUNT_NAME)) = UPPER(TRIM(%s))"
                "    OR UPPER(TRIM(ACCOUNT_NAME)) = UPPER(TRIM(%s))"
                " ORDER BY ACCOUNT_ID_C NULLS LAST, BU_ACCOUNT_NAME NULLS LAST,"
                "          CONTRACT_NUMBER NULLS LAST, SUBSCRIPTION_ID NULLS LAST"
                f" LIMIT {_FETCH_LIMIT}"
            )
            cur.execute(exact_query, (customer_name, customer_name))
            exact_rows = cur.fetchall() or []

            results = exact_rows
            resolution_mode = 'exact'

            if not results:
                # Pass (b): bounded LIKE fallback.  If multiple distinct
                # ACCOUNT_ID_C values come back we fail-closed instead
                # of silently picking the first bucket.
                like_query = base_select + (
                    " WHERE UPPER(BU_ACCOUNT_NAME) LIKE UPPER(%s)"
                    "    OR UPPER(ACCOUNT_NAME) LIKE UPPER(%s)"
                    " ORDER BY ACCOUNT_ID_C NULLS LAST, BU_ACCOUNT_NAME NULLS LAST,"
                    "          CONTRACT_NUMBER NULLS LAST, SUBSCRIPTION_ID NULLS LAST"
                    f" LIMIT {_FETCH_LIMIT}"
                )
                cur.execute(like_query, (f'%{customer_name}%', f'%{customer_name}%'))
                results = cur.fetchall() or []
                resolution_mode = 'like_fallback'

            if results:
                account_info: Dict[Any, Any] = {}
                for row in results:
                    if row[0] not in account_info:
                        account_info[row[0]] = _columns_for_row(row)

                distinct_account_ids = [
                    aid for aid in account_info.keys()
                    if aid is not None and not str(aid).startswith('_')
                ]
                # Round 10 / Phase 1.3: the exact-match pass can also surface
                # multiple distinct ``ACCOUNT_ID_C`` rows for the same name
                # (e.g. parent / sub-account "Acme Corp" appears twice with
                # different account IDs). Previously only the LIKE fallback
                # gated on distinct-id count, so the exact path silently
                # picked the first bucket and the renewal report attributed
                # adoption / case data to the wrong subscription. Fail-closed
                # on either pass when more than one ACCOUNT_ID_C is returned.
                ambiguous = len(distinct_account_ids) > 1
                account_info['_meta'] = {
                    'fetch_limit': _FETCH_LIMIT,
                    'rows_returned': len(results),
                    'was_truncated': len(results) >= _FETCH_LIMIT,
                    'resolution_mode': resolution_mode,
                    'distinct_account_ids': len(distinct_account_ids),
                    'ambiguous': ambiguous,
                }
                if ambiguous:
                    # Fail-closed: caller will raise renewal risk to UNKNOWN
                    # rather than blindly pick the first ACCOUNT_ID_C.
                    logger.warning(
                        "Account lookup for %s is ambiguous: %d distinct ACCOUNT_ID_C values returned via %s",
                        customer_name,
                        len(distinct_account_ids),
                        resolution_mode,
                    )
                else:
                    logger.info(
                        f"✅ Resolved {len(distinct_account_ids)} account record(s) for {customer_name} via {resolution_mode}"
                    )
                return account_info
            else:
                logger.warning(f"⚠️ No account information found for {customer_name}")
                return {
                    '_meta': {
                        'fetch_limit': _FETCH_LIMIT,
                        'rows_returned': 0,
                        'was_truncated': False,
                        'resolution_mode': resolution_mode,
                        'distinct_account_ids': 0,
                        'ambiguous': False,
                    }
                }
                
        except Exception as e:
            _log_query_fallback(f"Account info query for {customer_name}", e)
            # Round 7 / Phase 6.10: classify_data_state -> "failed"
            return _empty_dict_failed(e)
        finally:
            if cur:
                cur.close()
    
    def _get_contract_renewal_info(self, account_id: str, customer_name: str) -> Dict:
        """Get contract and renewal information"""
        logger.info(f"📄 Getting contract/renewal info for: {customer_name}")
        
        # Handle mock connections
        if isinstance(self.ctx, str) and self.ctx == 'mock_connection':
            logger.info("Using mock connection - returning empty data")
            return {'contracts': [], 'total_arr': 0, 'next_renewal_date': None, 'contracts_expiring_soon': [], 'auto_renewal_contracts': 0, 'manual_renewal_contracts': 0}
        
        cur = None
        try:
            cur = self.ctx.cursor()
            
            # Query contract and renewal data.  Round 7 / Phase 6.5:
            # we now also pull CURRENCY_CODE so the headline ``total_arr``
            # is currency-aware -- mirroring the multicurrency logic in
            # ``_get_financial_metrics``.  When the contracts span more
            # than one currency the flat ``total_arr`` is left at 0 and
            # the per-currency breakdown is surfaced via
            # ``totals_by_currency`` + ``is_multi_currency`` so renderers
            # never print a meaningless cross-currency sum.  We tolerate
            # the column being absent (older COLLAB_ARR_CON_SKU schemas)
            # by falling back to the legacy single-bucket behavior.
            select_columns = (
                "SUBSCRIPTION_ID, CONTRACT_NUMBER, SKU, SERVICE_END_DATE, "
                "CONTRACT_TERM, C_360_SERVICE_TIER_C, OFFER_NAME, REGION, "
                "CAV_NAME, ARR_AMOUNT, CONTRACT_START_DATE, CONTRACT_END_DATE, "
                "RENEWAL_DATE, AUTO_RENEWAL_FLAG, CONTRACT_STATUS, CURRENCY_CODE"
            )
            query = f"""
            SELECT {select_columns}
            FROM CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU
            WHERE ACCOUNT_ID_C = %s
            ORDER BY SERVICE_END_DATE DESC
            """

            try:
                cur.execute(query, (account_id,))
                results = cur.fetchall()
                _has_currency_col = True
            except Exception as _ccy_err:
                # Column missing in this environment; fall back to the
                # legacy 15-column projection so the rest of the
                # method still works (we just lose currency-awareness).
                logger.warning(
                    "Round 7 / Phase 6.5: CURRENCY_CODE column not available on "
                    "COLLAB_ARR_CON_SKU (%s); contract ARR totals will not be "
                    "currency-aware for this account.",
                    type(_ccy_err).__name__,
                )
                legacy_query = """
                SELECT
                    SUBSCRIPTION_ID, CONTRACT_NUMBER, SKU, SERVICE_END_DATE,
                    CONTRACT_TERM, C_360_SERVICE_TIER_C, OFFER_NAME, REGION,
                    CAV_NAME, ARR_AMOUNT, CONTRACT_START_DATE, CONTRACT_END_DATE,
                    RENEWAL_DATE, AUTO_RENEWAL_FLAG, CONTRACT_STATUS
                FROM CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU
                WHERE ACCOUNT_ID_C = %s
                ORDER BY SERVICE_END_DATE DESC
                """
                cur.execute(legacy_query, (account_id,))
                results = cur.fetchall()
                _has_currency_col = False

            contract_info = {
                'contracts': [],
                'total_arr': 0,
                'next_renewal_date': None,
                'contracts_expiring_soon': [],
                'auto_renewal_contracts': 0,
                'manual_renewal_contracts': 0,
                # Round 7 / Phase 6.5: per-currency breakdown + flag.
                'totals_by_currency': {},
                'is_multi_currency': False,
                'currency': None,
            }

            totals_by_currency: Dict[str, float] = {}

            for row in results:
                contract = {
                    'SUBSCRIPTION_ID': row[0],
                    'CONTRACT_NUMBER': row[1],
                    'SKU': row[2],
                    'SERVICE_END_DATE': row[3],
                    'CONTRACT_TERM': row[4],
                    'C_360_SERVICE_TIER_C': row[5],
                    'OFFER_NAME': row[6],
                    'REGION': row[7],
                    'CAV_NAME': row[8],
                    'ARR_AMOUNT': row[9],
                    'CONTRACT_START_DATE': row[10],
                    'CONTRACT_END_DATE': row[11],
                    'RENEWAL_DATE': row[12],
                    'AUTO_RENEWAL_FLAG': row[13],
                    'CONTRACT_STATUS': row[14],
                }
                if _has_currency_col:
                    contract['CURRENCY_CODE'] = row[15]

                contract_info['contracts'].append(contract)

                # Round 7 / Phase 6.5: bucket the ARR by currency
                # before deciding whether the flat ``total_arr`` is a
                # safe single-currency sum.  A row with no currency
                # code is bucketed as ``UNKNOWN`` so a mix of
                # ``UNKNOWN`` + ``USD`` flips into multi-currency mode
                # rather than silently coercing UNKNOWN into USD.
                val = row[9]
                if val is not None and not pd.isna(val):
                    if _has_currency_col:
                        ccy = (str(row[15]).strip().upper() if row[15] else 'UNKNOWN') or 'UNKNOWN'
                    else:
                        ccy = 'UNKNOWN'
                    totals_by_currency[ccy] = totals_by_currency.get(ccy, 0.0) + float(val)
                
                # Check for auto-renewal
                if row[13] and 'AUTO' in str(row[13]).upper():
                    contract_info['auto_renewal_contracts'] += 1
                else:
                    contract_info['manual_renewal_contracts'] += 1
                
                # Check for expiring contracts (next 90 days).
                # Round 3 / Phase 4.7: compare on the UTC calendar
                # date (not the local ``datetime.now()``) so a
                # contract whose SERVICE_END_DATE equals "today UTC"
                # is consistently counted as expiring regardless of
                # the host's local timezone, and so the upper bound
                # is "end-of-day UTC + 90 days" rather than a
                # rolling instantaneous comparison that can flip
                # mid-second.
                if row[3]:  # SERVICE_END_DATE
                    try:
                        _end_ts = pd.to_datetime(row[3], errors="coerce", utc=True)
                        if _end_ts is not None and not pd.isna(_end_ts):
                            end_date = _end_ts.tz_convert(None).normalize().date()
                            today_utc = pd.Timestamp.utcnow().tz_localize(None).date()
                            cutoff = today_utc + timedelta(days=90)
                            if end_date <= cutoff:
                                contract_info['contracts_expiring_soon'].append(contract)
                    except Exception as _dt_err:
                        logger.debug(f"Date parse error for contract: {_dt_err}")

            # Round 7 / Phase 6.5: finalize the currency-aware totals.
            # Single-currency (including the legacy fallback path that
            # buckets everything as UNKNOWN) populates the flat
            # ``total_arr``; multi-currency leaves it at 0 and exposes
            # the breakdown so downstream code prints "CURRENCY UNKNOWN"
            # rather than a misleading sum.
            contract_info['totals_by_currency'] = totals_by_currency
            distinct_real_ccys = [c for c in totals_by_currency.keys() if c and c != 'UNKNOWN']
            if len(totals_by_currency) <= 1:
                only_ccy = next(iter(totals_by_currency.keys())) if totals_by_currency else 'UNKNOWN'
                contract_info['currency'] = only_ccy
                contract_info['total_arr'] = totals_by_currency.get(only_ccy, 0.0)
                contract_info['is_multi_currency'] = False
            else:
                contract_info['currency'] = 'MIXED'
                contract_info['is_multi_currency'] = True
                contract_info['currencies_present'] = sorted(distinct_real_ccys or list(totals_by_currency.keys()))
                contract_info['total_arr'] = 0
                logger.warning(
                    "Round 7 / Phase 6.5: multi-currency contracts detected for %s: "
                    "currencies=%s; flat total_arr left at 0; see totals_by_currency.",
                    customer_name,
                    contract_info['currencies_present'],
                )

            logger.info(f"✅ Found {len(contract_info['contracts'])} contracts for {customer_name}")
            return contract_info
            
        except Exception as e:
            _log_query_fallback(f"Contract info query for {customer_name}", e)
            # Round 7 / Phase 6.10: classify_data_state -> "failed"
            return _empty_dict_failed(e)
        finally:
            if cur:
                cur.close()
    
    def _get_financial_metrics(self, account_id: str, customer_name: str) -> Dict:
        """Get financial metrics and ARR information"""
        # Round 9 / Phase 6.3: ``customer_name`` is PII-adjacent and
        # must not appear at INFO.  Mirror the digest pattern from
        # ``analyze_customer_renewal`` (Round 6 / Phase 6.7) so we
        # still get a stable correlator at INFO; verbatim name stays
        # at DEBUG for local troubleshooting.
        try:
            import hashlib as _hashlib_p63
            _cust_digest = _hashlib_p63.sha256(
                str(customer_name or "").encode("utf-8", errors="replace")
            ).hexdigest()[:8]
        except Exception:
            _cust_digest = "unknown"
        logger.info(f"💰 Getting financial metrics (customer_digest={_cust_digest})")
        logger.debug(f"💰 Getting financial metrics for: {customer_name}")
        
        cur = None
        try:
            cur = self.ctx.cursor()
            
            # Query financial data from account summary
            query = """
            SELECT 
                ARR_VIEW,
                TOTAL_ARR,
                PRODUCT_ARR,
                CONTRACT_VALUE,
                PAYMENT_TERMS,
                BILLING_FREQUENCY,
                CURRENCY,
                DISCOUNT_PERCENTAGE,
                PRICING_TIER
            FROM CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY 
            WHERE ACCOUNT_ID_C = %s
            """
            
            cur.execute(query, (account_id,))
            results = cur.fetchall()
            
            # Round 2 / Phase 1.1: ARR/TCV/PRODUCT_ARR may arrive in
            # multiple currencies on the same account.  Bucket sums by
            # currency_code so the headline is never a meaningless sum
            # across currencies.  We keep flat ``total_arr`` / etc.
            # fields for downstream callers but only populate them when
            # there is a single currency so the legacy float never lies
            # silently.  Multi-currency totals are exposed via the new
            # ``totals_by_currency`` dict and ``is_multi_currency`` flag.
            financial_metrics = {
                'total_arr': 0,
                'product_arr': 0,
                'contract_value': 0,
                'payment_terms': [],
                'billing_frequency': [],
                'currency': None,
                'discount_percentage': 0,
                'pricing_tier': 'Standard',
                'totals_by_currency': {},
                'is_multi_currency': False,
            }

            totals_by_currency: Dict[str, Dict[str, float]] = {}

            def _bucket(ccy_key: str) -> Dict[str, float]:
                bucket = totals_by_currency.setdefault(
                    ccy_key,
                    {'total_arr': 0.0, 'product_arr': 0.0, 'contract_value': 0.0},
                )
                return bucket

            for row in results:
                row_currency = (row[6] or '').strip().upper() or 'UNKNOWN'
                bucket = _bucket(row_currency)
                val = row[1]
                if val is not None and not pd.isna(val):
                    bucket['total_arr'] += float(val)
                val = row[2]
                if val is not None and not pd.isna(val):
                    bucket['product_arr'] += float(val)
                val = row[3]
                if val is not None and not pd.isna(val):
                    bucket['contract_value'] += float(val)

                if row[4]:
                    financial_metrics['payment_terms'].append(row[4])
                if row[5]:
                    financial_metrics['billing_frequency'].append(row[5])
                val = row[7]
                if val is not None and not pd.isna(val):
                    financial_metrics['discount_percentage'] = max(
                        financial_metrics['discount_percentage'], float(val)
                    )
                if row[8]:
                    financial_metrics['pricing_tier'] = row[8]

            financial_metrics['totals_by_currency'] = totals_by_currency
            distinct_ccys = [c for c in totals_by_currency.keys() if c and c != 'UNKNOWN']
            if len(totals_by_currency) <= 1:
                # Round 11 / Phase 6.4: when COLLAB_ACCOUNT_SUMMARY
                # returned no rows, the previous code defaulted the
                # currency to ``USD`` and emitted $0 totals -- which
                # silently fabricated a USD balance for accounts
                # without any financial rows.  Use ``UNKNOWN`` plus
                # an explicit ``no_financial_rows`` flag so renderers
                # can disclose missing data rather than print "$0".
                if not totals_by_currency:
                    financial_metrics['currency'] = 'UNKNOWN'
                    financial_metrics['total_arr'] = 0.0
                    financial_metrics['product_arr'] = 0.0
                    financial_metrics['contract_value'] = 0.0
                    financial_metrics['is_multi_currency'] = False
                    financial_metrics['no_financial_rows'] = True
                else:
                    only_ccy = next(iter(totals_by_currency.keys()))
                    bucket = totals_by_currency.get(only_ccy, {'total_arr': 0.0, 'product_arr': 0.0, 'contract_value': 0.0})
                    financial_metrics['currency'] = only_ccy
                    financial_metrics['total_arr'] = bucket['total_arr']
                    financial_metrics['product_arr'] = bucket['product_arr']
                    financial_metrics['contract_value'] = bucket['contract_value']
                    financial_metrics['is_multi_currency'] = False
                    financial_metrics['no_financial_rows'] = False
            else:
                # Multi-currency: keep flat totals at 0 and surface the
                # breakdown so any narrative/Excel renderer sees the
                # multi-currency flag and can render per-currency rows
                # rather than a misleading single number.
                financial_metrics['currency'] = 'MIXED'
                financial_metrics['is_multi_currency'] = True
                financial_metrics['currencies_present'] = sorted(distinct_ccys or list(totals_by_currency.keys()))
                logger.warning(
                    "Multi-currency ARR detected for %s: currencies=%s; flat total_arr left at 0; see totals_by_currency",
                    customer_name,
                    financial_metrics['currencies_present'],
                )

            logger.info(f"✅ Financial metrics calculated for {customer_name}")
            return financial_metrics
            
        except Exception as e:
            _log_query_fallback(f"Financial metrics query for {customer_name}", e)
            # Round 7 / Phase 6.10: classify_data_state -> "failed"
            return _empty_dict_failed(e)
        finally:
            if cur:
                cur.close()
    
    def _get_usage_adoption_metrics(self, account_id: str, days: int) -> Dict:
        """Get usage and adoption metrics"""
        logger.info(f"📊 Getting usage/adoption metrics for account: {account_id}")

        cur = None
        try:
            cur = self.ctx.cursor()

            # Round 4: parameterize the previously hardcoded 30-day
            # "RECENT_ACTIVITY" cuts so they honor the same analysis
            # window as the surrounding query.  We cap at the requested
            # ``days`` so a 7-day run does not silently report the
            # last 30 days as "recent".  Bound at min 1 / max ``days``.
            try:
                _recent_window = max(1, min(int(days), int(days)))
            except (TypeError, ValueError):
                _recent_window = 30

            # Query action plans and adoption barriers for usage patterns.
            # All time-window literals are parameterized so the bind
            # values are the single source of truth.
            # Round 10 / Phase 5.1: align action-plan + adoption-barrier
            # date predicate with the portfolio-side
            # ``DATE(COALESCE(OPEN_DATE_C, CREATED_DATE,
            # CREATED_DATE_C))`` form. ``DATE(CREATED_DATE)`` alone
            # silently drops rows whose ``CREATED_DATE`` is null but
            # ``OPEN_DATE_C`` is set -- common on backfilled records --
            # which made the renewal engagement signal undercount vs the
            # comprehensive portfolio surface for the same window.
            # Round 10 / Phase 5.2: use Python-computed UTC window-start
            # bound parameters instead of ``DATEADD(day, -%s,
            # CURRENT_DATE())`` so the lookback window is independent of
            # the Snowflake session time zone.
            query = """
            SELECT
                'ACTION_PLAN' as RECORD_TYPE,
                COUNT(*) as RECORD_COUNT,
                AVG(CASE WHEN STATUS_C = 'Completed' THEN 1 ELSE 0 END) as COMPLETION_RATE,
                COUNT(CASE WHEN DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                                >= %s THEN 1 END) as RECENT_ACTIVITY
            FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
            WHERE record_type_id = '0122T000000QHBGQA4'
              AND ACCOUNT_ID_C = %s
              AND DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                  >= %s

            UNION ALL

            SELECT
                'ADOPTION_BARRIER' as RECORD_TYPE,
                COUNT(*) as RECORD_COUNT,
                AVG(CASE WHEN STATUS_C = 'Resolved' THEN 1 ELSE 0 END) as COMPLETION_RATE,
                COUNT(CASE WHEN DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                                >= %s THEN 1 END) as RECENT_ACTIVITY
            FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
            WHERE record_type_id = '0122T000000GJfTQAW'
              AND ACCOUNT_ID_C = %s
              AND DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                  >= %s

            UNION ALL

            SELECT
                'CUSTOMER_PULSE' as RECORD_TYPE,
                COUNT(*) as RECORD_COUNT,
                AVG(CASE WHEN STATUS_C = 'Completed' THEN 1 ELSE 0 END) as COMPLETION_RATE,
                COUNT(CASE WHEN DATE(CREATEDDATE) >= %s THEN 1 END) as RECENT_ACTIVITY
            FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C
            WHERE ACCOUNT__C = %s
              AND DATE(CREATEDDATE) >= %s
            """

            _recent_iso = _utc_window_start_iso(_recent_window)
            _days_iso = _utc_window_start_iso(days)
            cur.execute(
                query,
                (
                    _recent_iso, account_id, _days_iso,
                    _recent_iso, account_id, _days_iso,
                    _recent_iso, account_id, _days_iso,
                ),
            )
            results = cur.fetchall()
            
            usage_metrics = {
                'action_plans': {'count': 0, 'completion_rate': 0, 'recent_activity': 0},
                'adoption_barriers': {'count': 0, 'completion_rate': 0, 'recent_activity': 0},
                'customer_pulse': {'count': 0, 'completion_rate': 0, 'recent_activity': 0},
                'total_activities': 0,
                'overall_completion_rate': 0,
                'recent_engagement_score': 0
            }
            
            for row in results:
                record_type = row[0]
                count = row[1] or 0
                completion_rate = row[2] or 0
                recent_activity = row[3] or 0
                
                if record_type == 'ACTION_PLAN':
                    usage_metrics['action_plans'] = {
                        'count': count,
                        'completion_rate': completion_rate,
                        'recent_activity': recent_activity
                    }
                elif record_type == 'ADOPTION_BARRIER':
                    usage_metrics['adoption_barriers'] = {
                        'count': count,
                        'completion_rate': completion_rate,
                        'recent_activity': recent_activity
                    }
                elif record_type == 'CUSTOMER_PULSE':
                    usage_metrics['customer_pulse'] = {
                        'count': count,
                        'completion_rate': completion_rate,
                        'recent_activity': recent_activity
                    }
                
                usage_metrics['total_activities'] += count
            
            # Calculate overall metrics
            if usage_metrics['total_activities'] > 0:
                total_completed = (
                    usage_metrics['action_plans']['count'] * usage_metrics['action_plans']['completion_rate'] +
                    usage_metrics['adoption_barriers']['count'] * usage_metrics['adoption_barriers']['completion_rate'] +
                    usage_metrics['customer_pulse']['count'] * usage_metrics['customer_pulse']['completion_rate']
                )
                usage_metrics['overall_completion_rate'] = total_completed / usage_metrics['total_activities']
            
            # Calculate recent engagement score
            total_recent = (
                usage_metrics['action_plans']['recent_activity'] +
                usage_metrics['adoption_barriers']['recent_activity'] +
                usage_metrics['customer_pulse']['recent_activity']
            )
            usage_metrics['recent_engagement_score'] = min(100, (total_recent / max(1, usage_metrics['total_activities'])) * 100)
            
            logger.info(f"✅ Usage metrics calculated for account: {account_id}")
            return usage_metrics
            
        except Exception as e:
            _log_query_fallback(f"Usage metrics query for account {account_id}", e)
            # Round 7 / Phase 6.10: classify_data_state -> "failed"
            return _empty_dict_failed(e)
        finally:
            if cur:
                cur.close()
    
    def _get_support_engagement_metrics(self, account_id: str, days: int, customer_name: Optional[str] = None) -> Dict:
        """Get support and engagement metrics.

        Round 7 / Phase 6.2: ``ESA_C360_SUCCESS_PRIORITY__C`` is keyed
        by ``RELATED_CUSTOMER__C`` -- a *customer name* string (matching
        the convention used by ``_fetch_success_priorities`` in the
        leader report path), NOT an ACCOUNT_ID_C.  The previous
        implementation bound ``account_id`` into that predicate which
        silently produced zero rows for every customer and made the
        engagement_level always ``'LOW'``.  We now require / accept the
        customer_name explicitly and short-circuit cleanly if a caller
        cannot supply it (still returning the documented "no
        engagement" default rather than a hard failure).
        """
        logger.info(f"🎧 Getting support/engagement metrics for account: {account_id}")
        support_metrics = {
            'total_priorities': 0,
            'completed_priorities': 0,
            'recent_priorities': 0,
            'completion_rate': 0,
            'avg_priority_score': 0,
            'engagement_level': 'LOW'
        }
        if is_table_blocked("EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C"):
            logger.info("Policy: Skipping ESA_C360_SUCCESS_PRIORITY__C in advanced renewal support metrics.")
            return support_metrics

        # Round 7 / Phase 6.2: refuse to run the (broken) account-id
        # query if the caller did not pass a customer_name.  The
        # historical behavior was to bind ``account_id`` and quietly
        # return an empty engagement profile; we keep the empty
        # profile but emit a warning so the missing argument does not
        # remain invisible to operators.
        if not customer_name or not str(customer_name).strip():
            logger.warning(
                "Round 7 / Phase 6.2: _get_support_engagement_metrics called "
                "without customer_name for account_id=%s; ESA_C360_SUCCESS_PRIORITY__C "
                "is keyed by RELATED_CUSTOMER__C (a name), so the lookup is skipped.",
                account_id,
            )
            return support_metrics

        cur = None
        try:
            cur = self.ctx.cursor()

            # Round 4: parameterize the previously hardcoded 30-day
            # ``RECENT_PRIORITIES`` cut so the recent-engagement count
            # honors the requested analysis window.
            try:
                _recent_window = max(1, int(days))
            except (TypeError, ValueError):
                _recent_window = 30

            # Round 10 / Phase 5.2: bind UTC window-start.
            query = """
            SELECT 
                COUNT(*) as TOTAL_PRIORITIES,
                COUNT(CASE WHEN STATUS_C = 'Completed' THEN 1 END) as COMPLETED_PRIORITIES,
                COUNT(CASE WHEN DATE(CREATEDDATE) >= %s THEN 1 END) as RECENT_PRIORITIES,
                AVG(CASE WHEN PRIORITY_C = 'High' THEN 1 
                         WHEN PRIORITY_C = 'Medium' THEN 0.5 
                         WHEN PRIORITY_C = 'Low' THEN 0.25 
                         ELSE 0 END) as AVG_PRIORITY_SCORE
            FROM EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C 
            WHERE RELATED_CUSTOMER__C = %s
              AND DATE(CREATEDDATE) >= %s
            """

            cur.execute(
                query,
                (
                    _utc_window_start_iso(_recent_window),
                    str(customer_name).strip(),
                    _utc_window_start_iso(days),
                ),
            )
            results = cur.fetchall()
            
            if results:
                row = results[0]
                support_metrics['total_priorities'] = row[0] or 0
                support_metrics['completed_priorities'] = row[1] or 0
                support_metrics['recent_priorities'] = row[2] or 0
                support_metrics['avg_priority_score'] = row[3] or 0
                
                # Calculate completion rate
                if support_metrics['total_priorities'] > 0:
                    support_metrics['completion_rate'] = support_metrics['completed_priorities'] / support_metrics['total_priorities']
                
                # Determine engagement level
                if support_metrics['recent_priorities'] >= 5:
                    support_metrics['engagement_level'] = 'HIGH'
                elif support_metrics['recent_priorities'] >= 2:
                    support_metrics['engagement_level'] = 'MEDIUM'
                else:
                    support_metrics['engagement_level'] = 'LOW'
            
            logger.info(f"✅ Support metrics calculated for account: {account_id}")
            return support_metrics
            
        except Exception as e:
            _log_query_fallback(f"Support metrics query for account {account_id}", e)
            # Round 7 / Phase 6.10: classify_data_state -> "failed"
            return _empty_dict_failed(e)
        finally:
            if cur:
                cur.close()
    
    def _get_adoption_success_metrics(self, account_id: str, days: int) -> Dict:
        """Get adoption and success metrics"""
        logger.info(f"📈 Getting adoption/success metrics for account: {account_id}")
        
        cur = None
        try:
            cur = self.ctx.cursor()
            
            # Query adoption barriers for success patterns
            query = """
            SELECT 
                AB_CATEGORY_C,
                SEVERITY_C,
                STATUS_C,
                COUNT(*) as BARRIER_COUNT,
                AVG(CASE WHEN STATUS_C = 'Resolved' THEN 1 ELSE 0 END) as RESOLUTION_RATE
            FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW 
            WHERE record_type_id = '0122T000000GJfTQAW' 
              AND ACCOUNT_ID_C = %s
              -- Round 10 / Phase 5.1: unify with portfolio adoption-barrier
              -- predicate (see comment above on the engagement query).
              -- Round 10 / Phase 5.2: bind UTC window-start.
              AND DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                  >= %s
            GROUP BY AB_CATEGORY_C, SEVERITY_C, STATUS_C
            ORDER BY BARRIER_COUNT DESC
            """
            
            cur.execute(query, (account_id, _utc_window_start_iso(days)))
            results = cur.fetchall()
            
            adoption_metrics = {
                'total_barriers': 0,
                'resolved_barriers': 0,
                'high_severity_barriers': 0,
                'barrier_categories': {},
                'resolution_rate': 0,
                'adoption_health_score': 0
            }
            
            for row in results:
                category = row[0] or 'Unknown'
                severity = row[1] or 'Unknown'
                status = row[2] or 'Unknown'
                count = row[3] or 0
                resolution_rate = row[4] or 0
                
                adoption_metrics['total_barriers'] += count
                
                # Round 4: route status through canonical
                # ``normalize_status_label`` so spelling variations
                # ("Resolved - Workaround", "RESOLVED ", lowercase) are
                # counted consistently with the rest of the platform.
                try:
                    from data_normalization import normalize_status_label as _norm_status
                    _status_canonical = _norm_status(status)
                except Exception:
                    _status_canonical = str(status or '').strip().title()
                if _status_canonical in ('Resolved', 'Closed'):
                    adoption_metrics['resolved_barriers'] += count
                
                # Round 3 hardening: route severity through canonical
                # ``normalize_severity_label`` so this counter agrees with
                # ``cm.count_critical_barriers`` and the leader/EI/compact
                # reports. The previous "HIGH in str(severity).upper()" matched
                # "HIGHEST" / "HIGHLY-REPORTED" labels and undercounted "P2".
                try:
                    from data_normalization import normalize_severity_label as _norm_sev
                    if _norm_sev(severity) in ('Critical', 'High'):
                        adoption_metrics['high_severity_barriers'] += count
                except Exception:
                    if severity and 'HIGH' in str(severity).upper():
                        adoption_metrics['high_severity_barriers'] += count
                
                if category not in adoption_metrics['barrier_categories']:
                    adoption_metrics['barrier_categories'][category] = {
                        'count': 0,
                        'resolution_rate': 0,
                        # Round 11 / Phase 6.5: track the
                        # weighted-sum / weight so we can compute
                        # a true count-weighted resolution rate
                        # across all status/severity sub-groups
                        # in this category, instead of taking the
                        # ``max`` of the sub-group rates which
                        # over-stated category health.
                        '_weighted_resolved_sum': 0.0,
                        '_weighted_count': 0,
                    }

                adoption_metrics['barrier_categories'][category]['count'] += count
                try:
                    _rr = float(resolution_rate or 0)
                except (TypeError, ValueError):
                    _rr = 0.0
                adoption_metrics['barrier_categories'][category]['_weighted_resolved_sum'] += _rr * count
                adoption_metrics['barrier_categories'][category]['_weighted_count'] += count
            
            # Round 11 / Phase 6.5: finalize per-category resolution
            # rate as the count-weighted average and drop the
            # internal accumulator keys before returning.
            for _cat, _payload in adoption_metrics['barrier_categories'].items():
                _wc = _payload.pop('_weighted_count', 0)
                _wr = _payload.pop('_weighted_resolved_sum', 0.0)
                if _wc > 0:
                    _payload['resolution_rate'] = round(_wr / _wc, 4)
                else:
                    _payload['resolution_rate'] = 0

            # Calculate overall resolution rate
            if adoption_metrics['total_barriers'] > 0:
                adoption_metrics['resolution_rate'] = adoption_metrics['resolved_barriers'] / adoption_metrics['total_barriers']
            
            # Calculate adoption health score (0-100)
            health_score = 100
            if adoption_metrics['total_barriers'] > 0:
                # Penalize for unresolved barriers
                unresolved_rate = 1 - adoption_metrics['resolution_rate']
                health_score -= (unresolved_rate * 50)
                
                # Penalize for high severity barriers
                if adoption_metrics['high_severity_barriers'] > 0:
                    high_severity_rate = adoption_metrics['high_severity_barriers'] / adoption_metrics['total_barriers']
                    health_score -= (high_severity_rate * 30)
            
            adoption_metrics['adoption_health_score'] = max(0, health_score)
            
            logger.info(f"✅ Adoption metrics calculated for account: {account_id}")
            return adoption_metrics
            
        except Exception as e:
            _log_query_fallback(f"Adoption metrics query for account {account_id}", e)
            # Round 7 / Phase 6.10: classify_data_state -> "failed"
            return _empty_dict_failed(e)
        finally:
            if cur:
                cur.close()
    
    def _calculate_renewal_risk_score(self, analysis_results: Dict) -> Dict:
        """Calculate comprehensive renewal risk score (0-100).

        Round 7 / Phase 6.6: scoring constants are now sourced from
        ``risk_scoring.RENEWAL_RISK_INCREMENTS`` and
        ``risk_scoring.RENEWAL_ARR_THRESHOLDS`` so renewal-risk tuning
        happens in one place.  Inline literal numbers (10, 15, 25, 30
        increments and 100k / 10k ARR gates) used to live here and
        silently drift from the rest of the platform's risk story.

        Round 7 / Phase 6.9: numeric formatting in the risk-factor /
        success-factor strings is now routed through
        ``format_ratio_percent`` / ``format_number`` from
        ``report_utils`` so the renewal narrative agrees with the
        global rounding policy used by the executive summary,
        compact, and dashboard layers.  ``f"{x:.1%}"`` /
        ``f"{x:.1f}"`` literals here used to drift -- e.g. a 5.04%
        completion rate would print as ``5.0%`` here but ``5%`` in
        the EI summary because the helper rounds half-to-even.
        """
        logger.info("🎯 Calculating comprehensive renewal risk score")

        # Round 7 / Phase 6.6: pull centralized constants.  Failing
        # the import here is fatal -- the renewal narrative is
        # explicitly anchored to risk_scoring's tuning, and silently
        # falling back to local literals would re-introduce the drift
        # this phase is meant to eliminate.
        from risk_scoring import RENEWAL_RISK_INCREMENTS as _RRI
        from risk_scoring import RENEWAL_ARR_THRESHOLDS as _RAT
        # Round 7 / Phase 6.9: shared formatters.  Soft-fail to inline
        # f-strings if report_utils is missing (e.g. partial bundle)
        # so risk scoring still produces a number; logged at WARNING.
        try:
            from report_utils import format_ratio_percent as _fmt_pct_p69
            from report_utils import format_number as _fmt_num_p69
        except Exception as _fmt_imp_err:
            logger.warning(
                "Round 7 / Phase 6.9: report_utils format helpers unavailable "
                "(%s); falling back to inline f-string formatting.",
                _fmt_imp_err,
            )
            def _fmt_pct_p69(v, decimals=1):
                try:
                    return f"{float(v):.{decimals}%}"
                except Exception:
                    return "N/A"
            def _fmt_num_p69(v, decimals=1, as_percent=False):
                try:
                    if as_percent:
                        return f"{float(v):.{decimals}f}%"
                    return f"{float(v):.{decimals}f}"
                except Exception:
                    return "N/A"

        risk_factors = []
        success_factors = []
        risk_score = _RAT["starting_renewal_score"]  # neutral start

        # 1. Contract Risk Factors
        contract_info = analysis_results.get('contract_information', {})
        if contract_info:
            # Expiring contracts
            expiring_contracts = len(contract_info.get('contracts_expiring_soon', []))
            if expiring_contracts > 0:
                risk_factors.append(f"{expiring_contracts} contracts expiring in next 90 days")
                risk_score += expiring_contracts * _RRI["expiring_contract_per_unit"]

            # Auto-renewal vs manual
            auto_renewal = contract_info.get('auto_renewal_contracts', 0)
            manual_renewal = contract_info.get('manual_renewal_contracts', 0)
            if manual_renewal > auto_renewal:
                risk_factors.append("More manual renewal contracts than auto-renewal")
                risk_score += _RRI["manual_renewal_majority"]
            else:
                success_factors.append("More auto-renewal contracts than manual")
                risk_score += _RRI["auto_renewal_majority_credit"]

        # 2. Financial Risk Factors
        # Round 7 / Phase 6.5+6.6: only apply ARR gates when the
        # financial frame is single-currency; otherwise the gates
        # would compare arbitrary mixed-currency sums against a
        # fixed USD-shaped threshold.
        financial_metrics = analysis_results.get('financial_metrics', {})
        if financial_metrics:
            total_arr = financial_metrics.get('total_arr', 0)
            is_multi_ccy = bool(financial_metrics.get('is_multi_currency', False))
            if not is_multi_ccy:
                if total_arr > _RAT["high_value_arr"]:
                    success_factors.append(
                        f"High-value customer (ARR > {_RAT['high_value_arr']:,})"
                    )
                    risk_score += _RRI["high_value_arr_credit"]
                elif total_arr < _RAT["low_value_arr"]:
                    risk_factors.append(
                        f"Low-value customer (ARR < {_RAT['low_value_arr']:,})"
                    )
                    risk_score += _RRI["low_value_arr_penalty"]

            # Round 11 / Phase 6.6: high-discount scoring uses
            # ``discount_percentage`` aggregated across mixed
            # currencies; a high USD discount and a small EUR
            # discount can compose to a misleading scalar.  Only
            # honor the discount penalty when the financial frame
            # is single-currency (matches Round 7 ARR gating).
            discount = financial_metrics.get('discount_percentage', 0)
            if not is_multi_ccy and discount > _RAT["high_discount_pct"]:
                risk_factors.append(f"High discount rate ({discount}%)")
                risk_score += _RRI["high_discount_penalty"]

        # 3. Usage and Adoption Risk Factors
        usage_metrics = analysis_results.get('usage_metrics', {})
        if usage_metrics:
            completion_rate = _safe_num(usage_metrics.get('overall_completion_rate', 0))
            if completion_rate < _RAT["low_completion_rate"]:
                risk_factors.append(f"Low activity completion rate ({_fmt_pct_p69(completion_rate)})")
                risk_score += _RRI["low_completion_penalty"]
            elif completion_rate > _RAT["high_completion_rate"]:
                success_factors.append(f"High activity completion rate ({_fmt_pct_p69(completion_rate)})")
                risk_score += _RRI["high_completion_credit"]

            recent_engagement = _safe_num(usage_metrics.get('recent_engagement_score', 0))
            # Round 10 / Phase 1.4: ``recent_engagement_score`` is a 0-100
            # point score (not a 0-1 ratio), so rendering it with
            # ``as_percent=True`` produced "Low recent engagement (25.0%)"
            # while the Engagement Health Indicators table in the same DOCX
            # surfaced "Recent Engagement Score (0-100): 25.0". Drop the
            # percent suffix so both sites label the same number identically.
            if recent_engagement < _RAT["low_engagement_score"]:
                risk_factors.append(f"Low recent engagement ({_fmt_num_p69(recent_engagement, decimals=1)} / 100)")
                risk_score += _RRI["low_engagement_penalty"]
            elif recent_engagement > _RAT["high_engagement_score"]:
                success_factors.append(f"High recent engagement ({_fmt_num_p69(recent_engagement, decimals=1)} / 100)")
                risk_score += _RRI["high_engagement_credit"]

        # 4. Support and Engagement Risk Factors
        support_metrics = analysis_results.get('support_metrics', {})
        if support_metrics:
            engagement_level = support_metrics.get('engagement_level', 'LOW')
            if engagement_level == 'LOW':
                risk_factors.append("Low support engagement level")
                risk_score += _RRI["low_support_engagement_penalty"]
            elif engagement_level == 'HIGH':
                success_factors.append("High support engagement level")
                risk_score += _RRI["high_support_engagement_credit"]

            completion_rate = support_metrics.get('completion_rate', 0)
            if isinstance(completion_rate, float) and (completion_rate != completion_rate):
                completion_rate = 0
            if completion_rate < _RAT["low_support_completion_rate"]:
                risk_factors.append(f"Low support priority completion rate ({_fmt_pct_p69(completion_rate)})")
                risk_score += _RRI["low_support_completion_penalty"]

        # 5. Adoption Health Risk Factors
        adoption_metrics = analysis_results.get('adoption_metrics', {})
        if adoption_metrics:
            health_score = adoption_metrics.get('adoption_health_score', 0)
            if isinstance(health_score, float) and (health_score != health_score):
                health_score = 0
            if health_score < _RAT["poor_adoption_health_score"]:
                risk_factors.append(f"Poor adoption health score ({_fmt_num_p69(health_score, decimals=1)}/100)")
                risk_score += _RRI["poor_adoption_health_penalty"]
            elif health_score > _RAT["good_adoption_health_score"]:
                success_factors.append(f"Good adoption health score ({_fmt_num_p69(health_score, decimals=1)}/100)")
                risk_score += _RRI["good_adoption_health_credit"]

            high_severity_barriers = adoption_metrics.get('high_severity_barriers', 0)
            if high_severity_barriers > _RAT["many_high_severity_barriers"]:
                risk_factors.append(f"Many high-severity adoption barriers ({high_severity_barriers})")
                risk_score += _RRI["many_high_severity_barriers_penalty"]
        
        # Normalize risk score to 0-100
        risk_score = max(0, min(100, risk_score))

        # Round 4: route through the canonical RISK_BAND_THRESHOLDS
        # (75/55/35/15) so the renewal-risk label matches every other
        # report that scores customers on a 0-100 scale.  Using the
        # legacy 80/60/40/20 cuts caused the same numeric score to
        # surface as e.g. "HIGH" in the renewal Word and "MEDIUM" in
        # the executive summary.
        risk_category = _renewal_risk_category_from_score(risk_score)
        
        return {
            'renewal_risk_score': risk_score,
            'renewal_risk_category': risk_category,
            'risk_factors': risk_factors,
            'success_factors': success_factors
        }
    
    def _generate_renewal_recommendations(self, analysis_results: Dict) -> List[str]:
        """Generate actionable renewal recommendations"""
        recommendations = []
        
        risk_category = analysis_results.get('renewal_risk_category', 'UNKNOWN')
        risk_score = analysis_results.get('renewal_risk_score', 50)
        
        # High-level recommendations based on risk category
        if risk_category in ['CRITICAL', 'HIGH']:
            recommendations.append("🚨 IMMEDIATE ACTION REQUIRED: Schedule executive-level renewal discussion")
            recommendations.append("📞 Assign dedicated Customer Success Manager for intensive engagement")
            recommendations.append("💰 Consider discount or value-add offers to improve renewal probability")
        
        # Contract-specific recommendations
        contract_info = analysis_results.get('contract_information', {})
        if contract_info:
            expiring_contracts = len(contract_info.get('contracts_expiring_soon', []))
            if expiring_contracts > 0:
                recommendations.append(f"⏰ {expiring_contracts} contracts expiring soon - start renewal process immediately")
            
            manual_renewal = contract_info.get('manual_renewal_contracts', 0)
            if manual_renewal > 0:
                recommendations.append("🔄 Consider converting manual renewal contracts to auto-renewal")
        
        # Usage and adoption recommendations
        usage_metrics = analysis_results.get('usage_metrics', {})
        if usage_metrics:
            completion_rate = _safe_num(usage_metrics.get('overall_completion_rate', 0))
            if completion_rate < 0.5:
                recommendations.append("📚 Provide additional training and onboarding support")
                recommendations.append("🎯 Focus on completing existing action plans and adoption barriers")
            
            recent_engagement = _safe_num(usage_metrics.get('recent_engagement_score', 0))
            if recent_engagement < 30:
                recommendations.append("📞 Schedule regular check-ins to increase engagement")
                recommendations.append("🎪 Organize user community events or webinars")
        
        # Support and engagement recommendations
        support_metrics = analysis_results.get('support_metrics', {})
        if support_metrics:
            engagement_level = support_metrics.get('engagement_level', 'LOW')
            if engagement_level == 'LOW':
                recommendations.append("🤝 Increase proactive support and success planning")
                recommendations.append("INFO: Create success priorities and track completion")
        
        # Adoption health recommendations
        adoption_metrics = analysis_results.get('adoption_metrics', {})
        if adoption_metrics:
            health_score = _safe_num(adoption_metrics.get('adoption_health_score', 0))
            if health_score < 50:
                recommendations.append("🔧 Address unresolved adoption barriers immediately")
                recommendations.append("📊 Implement adoption health monitoring and reporting")
            
            high_severity_barriers = _safe_num(adoption_metrics.get('high_severity_barriers', 0))
            if high_severity_barriers > 0:
                recommendations.append(f"⚠️ Resolve {int(high_severity_barriers)} high-severity adoption barriers")
        
        # Financial recommendations
        financial_metrics = analysis_results.get('financial_metrics', {})
        if financial_metrics:
            total_arr = financial_metrics.get('total_arr', 0)
            if total_arr > 100000:
                recommendations.append("👑 High-value customer - provide premium support and dedicated resources")
            elif total_arr < 10000:
                recommendations.append("💡 Identify upsell opportunities to increase contract value")
        
        return recommendations
    
    def generate_renewal_report(self, customer_name: str, days: int = 90) -> Tuple[str, str]:
        """Generate comprehensive renewal analysis report"""
        logger.info(f"📝 Generating renewal report for: {customer_name}")
        
        # Perform comprehensive analysis
        analysis_results = self.analyze_customer_renewal_risk(customer_name, days)
        
        # Create Word document
        doc = Document()
        
        # Setup document
        sections = doc.sections
        for section in sections:
            section.top_margin = Inches(1.0)
            section.bottom_margin = Inches(1.0)
            section.left_margin = Inches(1.0)
            section.right_margin = Inches(1.0)
        
        # Title page
        self._create_renewal_title_page(doc, customer_name, days, analysis_results)
        
        # Executive summary
        self._create_renewal_executive_summary(doc, analysis_results)
        
        # Risk analysis
        self._create_renewal_risk_analysis(doc, analysis_results)
        
        # Contract analysis
        self._create_renewal_contract_analysis(doc, analysis_results)
        
        # Usage and adoption analysis
        self._create_renewal_usage_analysis(doc, analysis_results)
        
        # Support and engagement analysis
        self._create_renewal_support_analysis(doc, analysis_results)
        
        # Recommendations
        self._create_renewal_recommendations(doc, analysis_results)
        
        # Data sources and verification
        self._create_renewal_data_sources(doc, analysis_results)
        
        # Save document
        output_dir = Path("outputs")
        output_dir.mkdir(exist_ok=True)
        
        # Round 7 / Phase 6.7: stamp the filename in UTC for the same
        # reason as ``leader_report_generator``: two renewal runs in
        # different timezones used to collide on ``YYYYMMDD_HHMMSS``.
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_customer = "".join(c for c in (customer_name or "Unknown") if c.isalnum() or c in (' ', '-', '_')).rstrip().replace(' ', '_')
        filename = f"AdoptIQ_Report_Renewal_{safe_customer}_{days}d_{timestamp}.docx"
        filepath = output_dir / filename
        
        doc.save(str(filepath))
        logger.info(f"Renewal report saved to: {filepath}")
        
        # Return filepath and success message (not the Document object which can't be serialized)
        return str(filepath), f"Renewal report generated successfully for {customer_name}"
    
    def _create_renewal_title_page(self, doc: Document, customer_name: str, days: int, analysis_results: Dict):
        """Create title page for renewal report"""
        # Round 7 / Phase 6.12: route every heading + customer string
        # through ``_safe_doc_text`` so a copy-pasted customer name
        # carrying NBSP padding, a stray BOM, an unpaired surrogate,
        # or a control char (CSOne exports occasionally do all four)
        # cannot crash python-docx or yield a Word "needs repair"
        # document.  The literal "Customer Renewal Risk Analysis"
        # is also routed through it for parity -- no behaviour change
        # for safe inputs, defense-in-depth otherwise.
        title = doc.add_heading(_safe_doc_text('Customer Renewal Risk Analysis'), level=1)
        if title.runs:
            title.runs[0].font.color.rgb = CISCO_BLUE
            title.runs[0].font.size = Pt(24)
        
        # Customer name
        customer_heading = doc.add_heading(_safe_doc_text(customer_name), level=2)
        if customer_heading.runs:
            customer_heading.runs[0].font.color.rgb = CISCO_BLUE
            customer_heading.runs[0].font.size = Pt(18)
        
        # Analysis details
        # Round 6 / Phase 1.19: previously "Analysis Date" and
        # "Report Generated" were both ``datetime.now()``, which made
        # them identical and obscured the fact that the data window
        # itself ends at "now - days .. now".  Surface a distinct
        # ``Data as of`` line (the upper bound of the analysis
        # window) so readers know the data freshness contract.
        _now_utc = datetime.now(timezone.utc)
        details_para = doc.add_paragraph()
        details_para.add_run(f'Analysis Period: Last {days} days\n').font.bold = True
        details_para.add_run(
            f'Data as of: {_now_utc.strftime("%Y-%m-%d %H:%M:%S")} UTC '
            f'(window upper bound)\n'
        )
        details_para.add_run(
            f'Report Generated: {_now_utc.strftime("%Y-%m-%d %H:%M:%S")} UTC '
            f'(this Word render)\n'
        )
        
        # Risk score summary
        risk_score = analysis_results.get('renewal_risk_score', 0)
        risk_category = analysis_results.get('renewal_risk_category', 'UNKNOWN')
        
        risk_para = doc.add_paragraph()
        risk_para.add_run('Renewal Risk Assessment:\n').font.bold = True
        risk_para.add_run(f'Risk Score: {risk_score}/100\n').font.bold = True
        risk_para.add_run(f'Risk Category: {risk_category}\n').font.bold = True
        
        # Add page break
        doc.add_page_break()
    
    def _create_renewal_executive_summary(self, doc: Document, analysis_results: Dict):
        """Create executive summary section"""
        # Round 7 / Phase 6.12: parity with title-page headings -- route
        # the literal heading through ``_safe_doc_text`` so the entire
        # renewal Word path uses one consistent sanitization seam.
        heading = doc.add_heading(_safe_doc_text('Executive Summary'), level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Risk assessment
        risk_score = analysis_results.get('renewal_risk_score', 0)
        risk_category = analysis_results.get('renewal_risk_category', 'UNKNOWN')
        
        risk_para = doc.add_paragraph()
        risk_para.add_run('Renewal Risk Assessment:\n').font.bold = True
        risk_para.add_run(f'Overall Risk Score: {risk_score}/100\n')
        risk_para.add_run(f'Risk Category: {risk_category}\n')
        
        # Key findings
        key_findings = analysis_results.get('key_findings', [])
        if key_findings:
            findings_para = doc.add_paragraph()
            findings_para.add_run('Key Findings:\n').font.bold = True
            for finding in key_findings:
                findings_para.add_run(f'• {finding}\n')
        
        # Risk factors
        risk_factors = analysis_results.get('risk_factors', [])
        if risk_factors:
            risk_para = doc.add_paragraph()
            risk_para.add_run('Risk Factors:\n').font.bold = True
            for factor in risk_factors:
                risk_para.add_run(f'• {factor}\n')
        
        # Success factors
        success_factors = analysis_results.get('success_factors', [])
        if success_factors:
            success_para = doc.add_paragraph()
            success_para.add_run('Success Factors:\n').font.bold = True
            for factor in success_factors:
                success_para.add_run(f'• {factor}\n')
        
        # Add page break
        doc.add_page_break()
    
    def _create_renewal_risk_analysis(self, doc: Document, analysis_results: Dict):
        """Create detailed risk analysis section"""
        # Round 7 / Phase 6.12: route heading through ``_safe_doc_text``.
        heading = doc.add_heading(_safe_doc_text('Detailed Risk Analysis'), level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Risk score breakdown
        risk_para = doc.add_paragraph()
        risk_para.add_run('Risk Score Breakdown:\n').font.bold = True
        
        # Create risk factors table
        table = doc.add_table(rows=1, cols=3)
        table.style = 'Light Grid Accent 1'
        
        # Header row
        header_cells = table.rows[0].cells
        headers = ['Risk Factor', 'Impact', 'Source']
        for i, header_text in enumerate(headers):
            cell = header_cells[i]
            cell.text = header_text
            if cell.paragraphs:
                if cell.paragraphs[0].runs:
                    cell.paragraphs[0].runs[0].font.bold = True
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Add blue background
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), '0076CE')
            cell._element.get_or_add_tcPr().append(shading_elm)
            if cell.paragraphs and cell.paragraphs[0].runs:
                cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
        
        # Add risk factors
        risk_factors = analysis_results.get('risk_factors', [])
        for factor in risk_factors:
            row_cells = table.add_row().cells
            # Round 7 / Phase 6.12: ``risk_factors`` is built from
            # Snowflake-derived strings (engagement labels, contract
            # cardinalities, currency-aware ARR descriptions).  Most
            # are safe, but we route the cell text through
            # ``_safe_doc_text`` for parity with the heading path so
            # a stray control char in a future factor source can't
            # corrupt the docx.
            row_cells[0].text = _safe_doc_text(factor)
            # Round 5 / Phase 1.18: tokenize the factor string so words
            # like "CRITICALLY" / "RECONFIGURED" / "HIGHLIGHT" do not
            # falsely classify a row as HIGH severity via naive
            # ``substring in`` checks.  Compare uppercase whole tokens.
            try:
                _factor_tokens = set(re.findall(r"[A-Z]+", str(factor).upper()))
            except Exception:
                _factor_tokens = set(str(factor).upper().split())
            row_cells[1].text = 'HIGH' if ('CRITICAL' in _factor_tokens or 'HIGH' in _factor_tokens) else 'MEDIUM'
            row_cells[2].text = 'Snowflake Data'
            
            # Center align impact
            if row_cells[1].paragraphs:
                row_cells[1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Add page break
        doc.add_page_break()
    
    def _create_renewal_contract_analysis(self, doc: Document, analysis_results: Dict):
        """Create contract analysis section"""
        # Heading
        # Round 7 / Phase 6.12: route heading through ``_safe_doc_text``.
        heading = doc.add_heading(_safe_doc_text('Contract & Financial Analysis'), level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Contract information
        contract_info = analysis_results.get('contract_information', {})
        if contract_info:
            contract_para = doc.add_paragraph()
            contract_para.add_run('Contract Summary:\n').font.bold = True
            contract_para.add_run(f'Total Contracts: {len(contract_info.get("contracts", []))}\n')
            # Round 6 / Phase 1.3: ``contract_info.total_arr`` is a
            # plain summed number; if the upstream financials block is
            # multi-currency we MUST NOT print a USD-prefixed total
            # because the same workbook's "Financial Metrics" block
            # already discloses MIXED + per-currency rows.  Reuse the
            # ``is_multi_currency`` flag so both blocks tell the same
            # story.
            _fm = analysis_results.get('financial_metrics', {}) or {}
            _is_mixed_contract = bool(_fm.get('is_multi_currency'))
            _by_ccy_contract = _fm.get('totals_by_currency') or {}
            if _is_mixed_contract:
                contract_para.add_run(
                    'Total ARR: MIXED — see Financial Metrics > totals_by_currency '
                    '(do not sum across currencies)\n'
                )
                if isinstance(_by_ccy_contract, dict) and _by_ccy_contract:
                    for _ccy, _val in _by_ccy_contract.items():
                        try:
                            contract_para.add_run(
                                f'  {_ccy}: {_safe_num(_val):,.2f}\n'
                            )
                        except Exception:
                            contract_para.add_run(f'  {_ccy}: {_val}\n')
            else:
                contract_para.add_run(
                    f'Total ARR: ${_safe_num(contract_info.get("total_arr", 0)):,.2f}\n'
                )
            contract_para.add_run(f'Auto-Renewal Contracts: {_safe_num(contract_info.get("auto_renewal_contracts", 0))}\n')
            contract_para.add_run(f'Manual Renewal Contracts: {_safe_num(contract_info.get("manual_renewal_contracts", 0))}\n')
            contract_para.add_run(f'Contracts Expiring Soon: {len(contract_info.get("contracts_expiring_soon", []))}\n')
        
        # Financial metrics
        financial_metrics = analysis_results.get('financial_metrics', {})
        if financial_metrics:
            financial_para = doc.add_paragraph()
            financial_para.add_run('Financial Metrics:\n').font.bold = True
            # Round 5 / Phase 1.3: when the upstream financials span
            # multiple currencies, do NOT prefix totals with ``$`` and
            # do NOT show a sum (which silently coerces all rows to
            # USD).  Disclose MIXED + per-currency breakdown so the
            # reader can audit each currency line.
            _is_mixed = bool(financial_metrics.get('is_multi_currency'))
            _by_ccy = financial_metrics.get('totals_by_currency') or {}
            if _is_mixed:
                financial_para.add_run(
                    'Total ARR: MIXED — see totals_by_currency (do not sum across currencies)\n'
                )
                if isinstance(_by_ccy, dict) and _by_ccy:
                    for _ccy, _val in _by_ccy.items():
                        try:
                            financial_para.add_run(
                                f'  {_ccy}: {_safe_num(_val):,.2f}\n'
                            )
                        except Exception:
                            financial_para.add_run(f'  {_ccy}: {_val}\n')
                financial_para.add_run('Product ARR: MIXED — see per-currency rows above\n')
                financial_para.add_run('Contract Value: MIXED — see per-currency rows above\n')
            else:
                financial_para.add_run(f'Total ARR: ${_safe_num(financial_metrics.get("total_arr", 0)):,.2f}\n')
                financial_para.add_run(f'Product ARR: ${_safe_num(financial_metrics.get("product_arr", 0)):,.2f}\n')
                financial_para.add_run(f'Contract Value: ${_safe_num(financial_metrics.get("contract_value", 0)):,.2f}\n')
            financial_para.add_run(f'Discount Percentage: {_safe_num(financial_metrics.get("discount_percentage", 0)):.1f}%\n')
            financial_para.add_run(f'Pricing Tier: {financial_metrics.get("pricing_tier", "Standard")}\n')
        
        # Add page break
        doc.add_page_break()
    
    def _create_renewal_usage_analysis(self, doc: Document, analysis_results: Dict):
        """Create usage and adoption analysis section"""
        # Heading
        # Round 7 / Phase 6.12: route heading through ``_safe_doc_text``.
        heading = doc.add_heading(_safe_doc_text('Usage & Adoption Analysis'), level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Usage metrics
        usage_metrics = analysis_results.get('usage_metrics', {})
        if usage_metrics:
            usage_para = doc.add_paragraph()
            usage_para.add_run('Usage Metrics:\n').font.bold = True
            usage_para.add_run(f'Total Activities: {usage_metrics.get("total_activities", 0)}\n')
            # Round 6 / Phase 1.15: previously this block mixed
            # ``:.1%`` (treats the value as a 0..1 ratio) with
            # ``{:.1f}%`` (treats the value as already-percent points).
            # Both completion-rate fields are 0..1 ratios upstream, but
            # the engagement score line was already percent-points and
            # got an extra ``%`` glyph.  Make every percent label
            # state its unit explicitly so the reader cannot misread.
            _ocr = _safe_num(usage_metrics.get("overall_completion_rate", 0))
            usage_para.add_run(
                f'Overall Completion Rate (ratio 0-1 -> %): {_ocr:.1%}\n'
            )
            _res = _safe_num(usage_metrics.get("recent_engagement_score", 0))
            usage_para.add_run(
                f'Recent Engagement Score (0-100): {_res:.1f}\n'
            )

            ap_metrics = usage_metrics.get('action_plans', {})
            usage_para.add_run(
                f'Action Plans: {ap_metrics.get("count", 0)} '
                f'(Completion ratio: {_safe_num(ap_metrics.get("completion_rate", 0)):.1%})\n'
            )

            ab_metrics = usage_metrics.get('adoption_barriers', {})
            usage_para.add_run(
                f'Adoption Barriers: {ab_metrics.get("count", 0)} '
                f'(Resolution ratio: {_safe_num(ab_metrics.get("completion_rate", 0)):.1%})\n'
            )

            cp_metrics = usage_metrics.get('customer_pulse', {})
            usage_para.add_run(
                f'Customer Pulse: {cp_metrics.get("count", 0)} '
                f'(Completion ratio: {_safe_num(cp_metrics.get("completion_rate", 0)):.1%})\n'
            )
        
        # Adoption health
        adoption_metrics = analysis_results.get('adoption_metrics', {})
        if adoption_metrics:
            adoption_para = doc.add_paragraph()
            adoption_para.add_run('Adoption Health:\n').font.bold = True
            adoption_para.add_run(f'Adoption Health Score: {_safe_num(adoption_metrics.get("adoption_health_score", 0)):.1f}/100\n')
            adoption_para.add_run(f'Total Barriers: {adoption_metrics.get("total_barriers", 0)}\n')
            adoption_para.add_run(f'Resolved Barriers: {adoption_metrics.get("resolved_barriers", 0)}\n')
            adoption_para.add_run(f'High-Severity Barriers: {adoption_metrics.get("high_severity_barriers", 0)}\n')
            adoption_para.add_run(f'Resolution Rate: {_safe_num(adoption_metrics.get("resolution_rate", 0)):.1%}\n')
        
        # Add page break
        doc.add_page_break()
    
    def _create_renewal_support_analysis(self, doc: Document, analysis_results: Dict):
        """Create support and engagement analysis section"""
        # Heading
        # Round 7 / Phase 6.12: route heading through ``_safe_doc_text``.
        heading = doc.add_heading(_safe_doc_text('Support & Engagement Analysis'), level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Support metrics
        support_metrics = analysis_results.get('support_metrics', {})
        if support_metrics:
            support_para = doc.add_paragraph()
            support_para.add_run('Support Metrics:\n').font.bold = True
            support_para.add_run(f'Total Priorities: {support_metrics.get("total_priorities", 0)}\n')
            support_para.add_run(f'Completed Priorities: {support_metrics.get("completed_priorities", 0)}\n')
            support_para.add_run(f'Recent Priorities: {support_metrics.get("recent_priorities", 0)}\n')
            support_para.add_run(f'Completion Rate: {_safe_num(support_metrics.get("completion_rate", 0)):.1%}\n')
            support_para.add_run(f'Engagement Level: {support_metrics.get("engagement_level", "UNKNOWN") or "UNKNOWN"}\n')
            support_para.add_run(f'Average Priority Score: {_safe_num(support_metrics.get("avg_priority_score", 0)):.2f}\n')
        
        # Add page break
        doc.add_page_break()
    
    def _create_renewal_recommendations(self, doc: Document, analysis_results: Dict):
        """Create recommendations section"""
        # Heading
        # Round 7 / Phase 6.12: route heading through ``_safe_doc_text``.
        heading = doc.add_heading(_safe_doc_text('Renewal Recommendations'), level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Recommendations
        recommendations = analysis_results.get('recommendations', [])
        if recommendations:
            rec_para = doc.add_paragraph()
            rec_para.add_run('Actionable Recommendations:\n').font.bold = True
            for i, rec in enumerate(recommendations, 1):
                rec_para.add_run(f'{i}. {rec}\n')
        else:
            no_rec_para = doc.add_paragraph()
            no_rec_para.add_run('No specific recommendations available at this time.').italic = True
        
        # Add page break
        doc.add_page_break()
    
    def _create_renewal_data_sources(self, doc: Document, analysis_results: Dict):
        """Create data sources and verification section – uses canonical data sources (same across all AdoptIQ reports)."""
        # Heading
        # Round 7 / Phase 6.12: route heading through ``_safe_doc_text``.
        heading = doc.add_heading(_safe_doc_text('Report Data Sources'), level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Canonical data sources (same across all AdoptIQ reports)
        try:
            from report_utils import get_data_sources_paragraph_text, get_data_sources_list
        except ImportError:
            get_data_sources_paragraph_text = lambda: (
                'Adoption Barriers: CSConsole / Snowflake C360_CS_TASK_C_VW. Support Cases: CSOne (TAC case data). '
                'BEMS: CSOne. Service Incidents: status.webex.com. Software Defects: help.webex.com. '
                'Customer Pulse, Action Plans, Success Priorities: CSConsole. ARR/Financial: Snowflake CX_DB.'
            )
            get_data_sources_list = lambda: [
                ('Adoption Barriers', 'CSConsole / Snowflake C360_CS_TASK_C_VW', 'Query by Record ID'),
                ('Support Cases (TAC)', 'CSOne (TAC case data)', 'Query by Case Number in CSOne'),
                ('BEMS Escalations', 'CSOne (Transaction ID, bemscsc_refs)', 'BEMS IDs verifiable in CSOne'),
                ('ARR / Financial', 'Snowflake CX_DB', 'Contract and ARR data from CX_DB'),
            ]
        sources_para = doc.add_paragraph()
        sources_para.add_run(get_data_sources_paragraph_text())
        doc.add_paragraph()
        
        # Canonical sources table
        table = doc.add_table(rows=1, cols=3)
        table.style = 'Light Grid Accent 1'
        header_cells = table.rows[0].cells
        for i, header_text in enumerate(['Metric', 'Source System', 'Verification Method']):
            cell = header_cells[i]
            cell.text = header_text
            if cell.paragraphs:
                if cell.paragraphs[0].runs:
                    cell.paragraphs[0].runs[0].font.bold = True
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), '0076CE')
            cell._element.get_or_add_tcPr().append(shading_elm)
            if cell.paragraphs and cell.paragraphs[0].runs:
                cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
        
        for metric, source, verification in get_data_sources_list():
            row_cells = table.add_row().cells
            row_cells[0].text = metric
            row_cells[1].text = source
            row_cells[2].text = verification
        
        doc.add_paragraph()
        # Round 7 / Phase 6.12: route heading through ``_safe_doc_text``.
        doc.add_heading(_safe_doc_text('Report-Specific Snowflake Tables (this analysis)'), level=2)
        data_sources = analysis_results.get('data_sources', {})
        source_mapping = {
            'account_info': ('Account Information', 'Snowflake', 'Account ID in COLLAB_ACCOUNT_SUMMARY'),
            'contract_info': ('Contract Data', 'Snowflake', 'Contract Number in COLLAB_ARR_CON_SKU'),
            'financial_metrics': ('Financial Metrics', 'Snowflake', 'ARR data in COLLAB_ACCOUNT_SUMMARY'),
            'usage_metrics': ('Usage Metrics', 'Snowflake', 'Record IDs in CSConsole tables'),
            'support_metrics': ('Support Metrics', 'Snowflake', 'Priority IDs in ESA_C360_SUCCESS_PRIORITY__C'),
            'adoption_metrics': ('Adoption Metrics', 'Snowflake', 'Barrier IDs in C360_CS_TASK_C_VW')
        }
        for source_key, (source_name, system, verification) in source_mapping.items():
            if source_key in data_sources:
                p = doc.add_paragraph()
                p.add_run(f'• {source_name}: ').bold = True
                p.add_run(f'{system} – {verification}')
        
        doc.add_paragraph()
        instructions_para = doc.add_paragraph()
        instructions_para.add_run('How to Verify This Analysis:\n').font.bold = True
        instructions_para.add_run('1. Account Information: Search by Account ID in COLLAB_ACCOUNT_SUMMARY table\n')
        instructions_para.add_run('2. Contract Data: Search by Contract Number in COLLAB_ARR_CON_SKU table\n')
        instructions_para.add_run('3. Usage Metrics: Search by Record ID in CSConsole tables\n')
        instructions_para.add_run('4. Support Metrics: Search by Priority ID in ESA_C360_SUCCESS_PRIORITY__C table\n')
        instructions_para.add_run('5. Adoption Metrics: Search by Barrier ID in C360_CS_TASK_C_VW table\n')
        
        doc.add_page_break()


def generate_advanced_renewal_analysis(customer_name: str, days: int, ctx,
                                       strict_mode: bool = False) -> Tuple[str, str]:
    """
    Generate advanced renewal analysis report
    
    Args:
        customer_name: Name of the customer to analyze
        days: Number of days to look back for analysis
        ctx: Snowflake connection context
        strict_mode: Round 7 / Phase 6.11 — forwarded to
            ``AdvancedRenewalAnalyzer`` so partial-data conditions raise
            instead of being silently swallowed when callers opt in.
            Defaults to False to preserve existing soft-fail behaviour
            for callers that have not yet been updated.
        
    Returns:
        Tuple of (filepath, success_message)
    """
    try:
        logger.info(f"Starting advanced renewal analysis for {customer_name}")
        
        # Round 7 / Phase 6.11: forward strict_mode through to the
        # analyzer so renewal narrative respects the same strict-mode
        # contract as the executive intelligence path.
        analyzer = AdvancedRenewalAnalyzer(ctx, strict_mode=strict_mode)
        filepath, report_msg = analyzer.generate_renewal_report(customer_name, days)
        
        success_msg = f"Advanced renewal analysis generated successfully: {filepath}"
        logger.info(success_msg)
        
        return filepath, success_msg
        
    except Exception as e:
        _log_query_fallback("Advanced renewal analysis generation", e)
        raise RuntimeError("Error generating advanced renewal analysis. See logs for details.")
