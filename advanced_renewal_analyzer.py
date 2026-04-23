"""
Advanced Renewal Analysis System
Comprehensive renewal risk assessment using all available Snowflake data sources
"""

import logging
import math
import pandas as pd
from datetime import datetime, timedelta
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
# CRITICAL/HIGH/MEDIUM/LOW label everywhere in the platform.  Falling
# back to a local copy of the thresholds preserves behavior if the
# import ever fails (e.g. circular import during early startup).
try:
    from risk_scoring import RISK_BAND_THRESHOLDS as _RISK_BAND_THRESHOLDS_0_100
except Exception:  # pragma: no cover - defensive only
    _RISK_BAND_THRESHOLDS_0_100 = {
        "CRITICAL": 75,
        "HIGH": 55,
        "MEDIUM": 35,
        "LOW": 15,
    }


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
    
    def __init__(self, ctx):
        """
        Initialize the advanced renewal analyzer
        
        Args:
            ctx: Snowflake connection context
        """
        self.ctx = ctx
    
    def analyze_customer_renewal_risk(self, customer_name: str, days: int = 90) -> Dict[str, Any]:
        """
        Comprehensive renewal risk analysis for a specific customer
        
        Args:
            customer_name: Name of the customer to analyze
            days: Number of days to look back for analysis
            
        Returns:
            Dictionary containing comprehensive renewal analysis
        """
        logger.info(f"DEBUG: Starting comprehensive renewal analysis for: {customer_name}")
        
        analysis_results = {
            'customer_name': customer_name,
            'analysis_date': datetime.now().isoformat(),
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
            
            first_account = next(iter(_account_records.values()))
            account_id = first_account.get('ACCOUNT_ID_C')
            logger.info(f"Using account_id: {account_id} for customer: {customer_name}")
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
            support_metrics = self._get_support_engagement_metrics(account_id, days)
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
            
            # Query account summary table for comprehensive customer info
            # Note: Only select columns that actually exist in COLLAB_ACCOUNT_SUMMARY
            query = """
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
            WHERE UPPER(BU_ACCOUNT_NAME) LIKE UPPER(%s)
               OR UPPER(ACCOUNT_NAME) LIKE UPPER(%s)
            LIMIT 10
            """
            
            cur.execute(query, (f'%{customer_name}%', f'%{customer_name}%'))
            results = cur.fetchall()
            
            # Round 4: surface the LIMIT 10 truncation so downstream
            # consumers can disclose that only the first 10 matches were
            # inspected.  Stored under a reserved key prefixed with ``_``
            # so it cannot collide with a legitimate ``ACCOUNT_ID_C``.
            _FETCH_LIMIT = 10
            if results:
                account_info = {}
                for row in results:
                    if row[0] not in account_info:  # ACCOUNT_ID_C
                        account_info[row[0]] = {
                            'ACCOUNT_ID_C': row[0],
                            'BU_ACCOUNT_NAME': row[1],
                            'ACCOUNT_NAME': row[2],
                            'CONTRACT_STATUS': row[3],
                            'NEW_RENEWAL_EXISTING': row[4],
                            'RENEWAL_RISK_CATEGORY': row[5],
                            'ARR_VIEW': row[6],
                            'RECORD_TYPE': row[7],
                            'CONTRACT_NUMBER': row[8],
                            'SUBSCRIPTION_ID': row[9]
                        }
                account_info['_meta'] = {
                    'fetch_limit': _FETCH_LIMIT,
                    'rows_returned': len(results),
                    'was_truncated': len(results) >= _FETCH_LIMIT,
                }
                logger.info(f"✅ Found {len(account_info) - 1} account records for {customer_name}")
                return account_info
            else:
                logger.warning(f"⚠️ No account information found for {customer_name}")
                return {
                    '_meta': {
                        'fetch_limit': _FETCH_LIMIT,
                        'rows_returned': 0,
                        'was_truncated': False,
                    }
                }
                
        except Exception as e:
            _log_query_fallback(f"Account info query for {customer_name}", e)
            return {}
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
            
            # Query contract and renewal data
            query = """
            SELECT 
                SUBSCRIPTION_ID,
                CONTRACT_NUMBER,
                SKU,
                SERVICE_END_DATE,
                CONTRACT_TERM,
                C_360_SERVICE_TIER_C,
                OFFER_NAME,
                REGION,
                CAV_NAME,
                ARR_AMOUNT,
                CONTRACT_START_DATE,
                CONTRACT_END_DATE,
                RENEWAL_DATE,
                AUTO_RENEWAL_FLAG,
                CONTRACT_STATUS
            FROM CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU 
            WHERE ACCOUNT_ID_C = %s
            ORDER BY SERVICE_END_DATE DESC
            """
            
            cur.execute(query, (account_id,))
            results = cur.fetchall()
            
            contract_info = {
                'contracts': [],
                'total_arr': 0,
                'next_renewal_date': None,
                'contracts_expiring_soon': [],
                'auto_renewal_contracts': 0,
                'manual_renewal_contracts': 0
            }
            
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
                    'CONTRACT_STATUS': row[14]
                }
                
                contract_info['contracts'].append(contract)
                
                # Calculate totals
                val = row[9]
                if val is not None and not pd.isna(val):
                    contract_info['total_arr'] += float(val)
                
                # Check for auto-renewal
                if row[13] and 'AUTO' in str(row[13]).upper():
                    contract_info['auto_renewal_contracts'] += 1
                else:
                    contract_info['manual_renewal_contracts'] += 1
                
                # Check for expiring contracts (next 90 days)
                if row[3]:  # SERVICE_END_DATE
                    try:
                        end_date = pd.to_datetime(row[3])
                        if end_date <= datetime.now() + timedelta(days=90):
                            contract_info['contracts_expiring_soon'].append(contract)
                    except Exception as _dt_err:
                        logger.debug(f"Date parse error for contract: {_dt_err}")
            
            logger.info(f"✅ Found {len(contract_info['contracts'])} contracts for {customer_name}")
            return contract_info
            
        except Exception as e:
            _log_query_fallback(f"Contract info query for {customer_name}", e)
            return {}
        finally:
            if cur:
                cur.close()
    
    def _get_financial_metrics(self, account_id: str, customer_name: str) -> Dict:
        """Get financial metrics and ARR information"""
        logger.info(f"💰 Getting financial metrics for: {customer_name}")
        
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
            
            financial_metrics = {
                'total_arr': 0,
                'product_arr': 0,
                'contract_value': 0,
                'payment_terms': [],
                'billing_frequency': [],
                'currency': 'USD',
                'discount_percentage': 0,
                'pricing_tier': 'Standard'
            }
            
            for row in results:
                val = row[1]
                if val is not None and not pd.isna(val):
                    financial_metrics['total_arr'] += float(val)
                val = row[2]
                if val is not None and not pd.isna(val):
                    financial_metrics['product_arr'] += float(val)
                val = row[3]
                if val is not None and not pd.isna(val):
                    financial_metrics['contract_value'] += float(val)
                
                if row[4]:  # PAYMENT_TERMS
                    financial_metrics['payment_terms'].append(row[4])
                if row[5]:  # BILLING_FREQUENCY
                    financial_metrics['billing_frequency'].append(row[5])
                if row[6]:  # CURRENCY
                    financial_metrics['currency'] = row[6]
                val = row[7]
                if val is not None and not pd.isna(val):
                    financial_metrics['discount_percentage'] = max(financial_metrics['discount_percentage'], float(val))
                if row[8]:  # PRICING_TIER
                    financial_metrics['pricing_tier'] = row[8]
            
            logger.info(f"✅ Financial metrics calculated for {customer_name}")
            return financial_metrics
            
        except Exception as e:
            _log_query_fallback(f"Financial metrics query for {customer_name}", e)
            return {}
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
            query = """
            SELECT
                'ACTION_PLAN' as RECORD_TYPE,
                COUNT(*) as RECORD_COUNT,
                AVG(CASE WHEN STATUS_C = 'Completed' THEN 1 ELSE 0 END) as COMPLETION_RATE,
                COUNT(CASE WHEN DATE(CREATED_DATE) >= DATEADD(day, -%s, CURRENT_DATE()) THEN 1 END) as RECENT_ACTIVITY
            FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
            WHERE record_type_id = '0122T000000QHBGQA4'
              AND ACCOUNT_ID_C = %s
              AND DATE(CREATED_DATE) >= DATEADD(day, -%s, CURRENT_DATE())

            UNION ALL

            SELECT
                'ADOPTION_BARRIER' as RECORD_TYPE,
                COUNT(*) as RECORD_COUNT,
                AVG(CASE WHEN STATUS_C = 'Resolved' THEN 1 ELSE 0 END) as COMPLETION_RATE,
                COUNT(CASE WHEN DATE(CREATED_DATE) >= DATEADD(day, -%s, CURRENT_DATE()) THEN 1 END) as RECENT_ACTIVITY
            FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
            WHERE record_type_id = '0122T000000GJfTQAW'
              AND ACCOUNT_ID_C = %s
              AND DATE(CREATED_DATE) >= DATEADD(day, -%s, CURRENT_DATE())

            UNION ALL

            SELECT
                'CUSTOMER_PULSE' as RECORD_TYPE,
                COUNT(*) as RECORD_COUNT,
                AVG(CASE WHEN STATUS_C = 'Completed' THEN 1 ELSE 0 END) as COMPLETION_RATE,
                COUNT(CASE WHEN DATE(CREATEDDATE) >= DATEADD(day, -%s, CURRENT_DATE()) THEN 1 END) as RECENT_ACTIVITY
            FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C
            WHERE ACCOUNT__C = %s
              AND DATE(CREATEDDATE) >= DATEADD(day, -%s, CURRENT_DATE())
            """

            cur.execute(
                query,
                (
                    _recent_window, account_id, days,
                    _recent_window, account_id, days,
                    _recent_window, account_id, days,
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
            return {}
        finally:
            if cur:
                cur.close()
    
    def _get_support_engagement_metrics(self, account_id: str, days: int) -> Dict:
        """Get support and engagement metrics"""
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

            query = """
            SELECT 
                COUNT(*) as TOTAL_PRIORITIES,
                COUNT(CASE WHEN STATUS_C = 'Completed' THEN 1 END) as COMPLETED_PRIORITIES,
                COUNT(CASE WHEN DATE(CREATEDDATE) >= DATEADD(day, -%s, CURRENT_DATE()) THEN 1 END) as RECENT_PRIORITIES,
                AVG(CASE WHEN PRIORITY_C = 'High' THEN 1 
                         WHEN PRIORITY_C = 'Medium' THEN 0.5 
                         WHEN PRIORITY_C = 'Low' THEN 0.25 
                         ELSE 0 END) as AVG_PRIORITY_SCORE
            FROM EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C 
            WHERE RELATED_CUSTOMER__C = %s
              AND DATE(CREATEDDATE) >= DATEADD(day, -%s, CURRENT_DATE())
            """
            
            cur.execute(query, (_recent_window, account_id, days))
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
            return {}
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
              AND DATE(CREATED_DATE) >= DATEADD(day, -%s, CURRENT_DATE())
            GROUP BY AB_CATEGORY_C, SEVERITY_C, STATUS_C
            ORDER BY BARRIER_COUNT DESC
            """
            
            cur.execute(query, (account_id, days))
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
                        'resolution_rate': 0
                    }
                
                adoption_metrics['barrier_categories'][category]['count'] += count
                adoption_metrics['barrier_categories'][category]['resolution_rate'] = max(
                    adoption_metrics['barrier_categories'][category]['resolution_rate'],
                    resolution_rate
                )
            
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
            return {}
        finally:
            if cur:
                cur.close()
    
    def _calculate_renewal_risk_score(self, analysis_results: Dict) -> Dict:
        """Calculate comprehensive renewal risk score (0-100)"""
        logger.info("🎯 Calculating comprehensive renewal risk score")
        
        risk_factors = []
        success_factors = []
        risk_score = 50  # Start with neutral score
        
        # 1. Contract Risk Factors
        contract_info = analysis_results.get('contract_information', {})
        if contract_info:
            # Expiring contracts
            expiring_contracts = len(contract_info.get('contracts_expiring_soon', []))
            if expiring_contracts > 0:
                risk_factors.append(f"{expiring_contracts} contracts expiring in next 90 days")
                risk_score += (expiring_contracts * 10)  # Increase risk
            
            # Auto-renewal vs manual
            auto_renewal = contract_info.get('auto_renewal_contracts', 0)
            manual_renewal = contract_info.get('manual_renewal_contracts', 0)
            if manual_renewal > auto_renewal:
                risk_factors.append("More manual renewal contracts than auto-renewal")
                risk_score += 15
            else:
                success_factors.append("More auto-renewal contracts than manual")
                risk_score -= 10
        
        # 2. Financial Risk Factors
        financial_metrics = analysis_results.get('financial_metrics', {})
        if financial_metrics:
            total_arr = financial_metrics.get('total_arr', 0)
            if total_arr > 100000:  # High-value customer
                success_factors.append("High-value customer (ARR > $100K)")
                risk_score -= 15
            elif total_arr < 10000:  # Low-value customer
                risk_factors.append("Low-value customer (ARR < $10K)")
                risk_score += 10
            
            discount = financial_metrics.get('discount_percentage', 0)
            if discount > 50:  # High discount
                risk_factors.append(f"High discount rate ({discount}%)")
                risk_score += 20
        
        # 3. Usage and Adoption Risk Factors
        usage_metrics = analysis_results.get('usage_metrics', {})
        if usage_metrics:
            completion_rate = _safe_num(usage_metrics.get('overall_completion_rate', 0))
            if completion_rate < 0.5:
                risk_factors.append(f"Low activity completion rate ({completion_rate:.1%})")
                risk_score += 25
            elif completion_rate > 0.8:
                success_factors.append(f"High activity completion rate ({completion_rate:.1%})")
                risk_score -= 20
            
            recent_engagement = _safe_num(usage_metrics.get('recent_engagement_score', 0))
            if recent_engagement < 30:
                risk_factors.append(f"Low recent engagement ({recent_engagement:.1f}%)")
                risk_score += 20
            elif recent_engagement > 70:
                success_factors.append(f"High recent engagement ({recent_engagement:.1f}%)")
                risk_score -= 15
        
        # 4. Support and Engagement Risk Factors
        support_metrics = analysis_results.get('support_metrics', {})
        if support_metrics:
            engagement_level = support_metrics.get('engagement_level', 'LOW')
            if engagement_level == 'LOW':
                risk_factors.append("Low support engagement level")
                risk_score += 15
            elif engagement_level == 'HIGH':
                success_factors.append("High support engagement level")
                risk_score -= 10
            
            completion_rate = support_metrics.get('completion_rate', 0)
            if isinstance(completion_rate, float) and (completion_rate != completion_rate):
                completion_rate = 0
            if completion_rate < 0.6:
                risk_factors.append(f"Low support priority completion rate ({completion_rate:.1%})")
                risk_score += 15
        
        # 5. Adoption Health Risk Factors
        adoption_metrics = analysis_results.get('adoption_metrics', {})
        if adoption_metrics:
            health_score = adoption_metrics.get('adoption_health_score', 0)
            if isinstance(health_score, float) and (health_score != health_score):
                health_score = 0
            if health_score < 50:
                risk_factors.append(f"Poor adoption health score ({health_score:.1f}/100)")
                risk_score += 30
            elif health_score > 80:
                success_factors.append(f"Good adoption health score ({health_score:.1f}/100)")
                risk_score -= 20
            
            high_severity_barriers = adoption_metrics.get('high_severity_barriers', 0)
            if high_severity_barriers > 3:  # Many high-severity barriers
                risk_factors.append(f"Many high-severity adoption barriers ({high_severity_barriers})")
                risk_score += 25
        
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
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_customer = "".join(c for c in (customer_name or "Unknown") if c.isalnum() or c in (' ', '-', '_')).rstrip().replace(' ', '_')
        filename = f"AdoptIQ_Report_Renewal_{safe_customer}_{days}d_{timestamp}.docx"
        filepath = output_dir / filename
        
        doc.save(str(filepath))
        logger.info(f"Renewal report saved to: {filepath}")
        
        # Return filepath and success message (not the Document object which can't be serialized)
        return str(filepath), f"Renewal report generated successfully for {customer_name}"
    
    def _create_renewal_title_page(self, doc: Document, customer_name: str, days: int, analysis_results: Dict):
        """Create title page for renewal report"""
        # Title
        title = doc.add_heading('Customer Renewal Risk Analysis', level=1)
        if title.runs:
            title.runs[0].font.color.rgb = CISCO_BLUE
            title.runs[0].font.size = Pt(24)
        
        # Customer name
        customer_heading = doc.add_heading(customer_name, level=2)
        if customer_heading.runs:
            customer_heading.runs[0].font.color.rgb = CISCO_BLUE
            customer_heading.runs[0].font.size = Pt(18)
        
        # Analysis details
        details_para = doc.add_paragraph()
        details_para.add_run(f'Analysis Period: Last {days} days\n').font.bold = True
        details_para.add_run(f'Analysis Date: {datetime.now().strftime("%B %d, %Y")}\n')
        details_para.add_run(f'Report Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        
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
        # Heading
        heading = doc.add_heading('Executive Summary', level=1)
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
        # Heading
        heading = doc.add_heading('Detailed Risk Analysis', level=1)
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
            row_cells[0].text = factor
            row_cells[1].text = 'HIGH' if 'CRITICAL' in factor or 'HIGH' in factor else 'MEDIUM'
            row_cells[2].text = 'Snowflake Data'
            
            # Center align impact
            if row_cells[1].paragraphs:
                row_cells[1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Add page break
        doc.add_page_break()
    
    def _create_renewal_contract_analysis(self, doc: Document, analysis_results: Dict):
        """Create contract analysis section"""
        # Heading
        heading = doc.add_heading('Contract & Financial Analysis', level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Contract information
        contract_info = analysis_results.get('contract_information', {})
        if contract_info:
            contract_para = doc.add_paragraph()
            contract_para.add_run('Contract Summary:\n').font.bold = True
            contract_para.add_run(f'Total Contracts: {len(contract_info.get("contracts", []))}\n')
            contract_para.add_run(f'Total ARR: ${_safe_num(contract_info.get("total_arr", 0)):,.2f}\n')
            contract_para.add_run(f'Auto-Renewal Contracts: {_safe_num(contract_info.get("auto_renewal_contracts", 0))}\n')
            contract_para.add_run(f'Manual Renewal Contracts: {_safe_num(contract_info.get("manual_renewal_contracts", 0))}\n')
            contract_para.add_run(f'Contracts Expiring Soon: {len(contract_info.get("contracts_expiring_soon", []))}\n')
        
        # Financial metrics
        financial_metrics = analysis_results.get('financial_metrics', {})
        if financial_metrics:
            financial_para = doc.add_paragraph()
            financial_para.add_run('Financial Metrics:\n').font.bold = True
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
        heading = doc.add_heading('Usage & Adoption Analysis', level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Usage metrics
        usage_metrics = analysis_results.get('usage_metrics', {})
        if usage_metrics:
            usage_para = doc.add_paragraph()
            usage_para.add_run('Usage Metrics:\n').font.bold = True
            usage_para.add_run(f'Total Activities: {usage_metrics.get("total_activities", 0)}\n')
            usage_para.add_run(f'Overall Completion Rate: {_safe_num(usage_metrics.get("overall_completion_rate", 0)):.1%}\n')
            usage_para.add_run(f'Recent Engagement Score: {_safe_num(usage_metrics.get("recent_engagement_score", 0)):.1f}%\n')
            
            ap_metrics = usage_metrics.get('action_plans', {})
            usage_para.add_run(f'Action Plans: {ap_metrics.get("count", 0)} (Completion: {_safe_num(ap_metrics.get("completion_rate", 0)):.1%})\n')
            
            ab_metrics = usage_metrics.get('adoption_barriers', {})
            usage_para.add_run(f'Adoption Barriers: {ab_metrics.get("count", 0)} (Resolution: {_safe_num(ab_metrics.get("completion_rate", 0)):.1%})\n')
            
            cp_metrics = usage_metrics.get('customer_pulse', {})
            usage_para.add_run(f'Customer Pulse: {cp_metrics.get("count", 0)} (Completion: {_safe_num(cp_metrics.get("completion_rate", 0)):.1%})\n')
        
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
        heading = doc.add_heading('Support & Engagement Analysis', level=1)
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
        heading = doc.add_heading('Renewal Recommendations', level=1)
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
        heading = doc.add_heading('Report Data Sources', level=1)
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
        doc.add_heading('Report-Specific Snowflake Tables (this analysis)', level=2)
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


def generate_advanced_renewal_analysis(customer_name: str, days: int, ctx) -> Tuple[str, str]:
    """
    Generate advanced renewal analysis report
    
    Args:
        customer_name: Name of the customer to analyze
        days: Number of days to look back for analysis
        ctx: Snowflake connection context
        
    Returns:
        Tuple of (filepath, success_message)
    """
    try:
        logger.info(f"Starting advanced renewal analysis for {customer_name}")
        
        analyzer = AdvancedRenewalAnalyzer(ctx)
        filepath, report_msg = analyzer.generate_renewal_report(customer_name, days)
        
        success_msg = f"Advanced renewal analysis generated successfully: {filepath}"
        logger.info(success_msg)
        
        return filepath, success_msg
        
    except Exception as e:
        _log_query_fallback("Advanced renewal analysis generation", e)
        raise RuntimeError("Error generating advanced renewal analysis. See logs for details.")
