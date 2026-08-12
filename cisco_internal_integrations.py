#!/usr/bin/env python3
"""
Cisco Internal Integrations Module
Integrates with PSIRT openVuln and Circuit for security advisories and internal data
"""

import requests
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
from dataclasses import dataclass
from enum import Enum
import time
import re
import random
import urllib.parse

logger = logging.getLogger(__name__)


def defect_portal_url(csc_id: str) -> str:
    """Static deep link to the BST web portal (no API integration)."""
    token = str(csc_id or "").strip()
    if not token:
        return ""
    return "https://bst.cisco.com/bugsearch/bug/" + urllib.parse.quote(token, safe="")


# Round 5 / Phase 4.7: cap _safe_response_json(response) body size before parse.
# A misbehaving / compromised upstream returning a giant body would
# otherwise be loaded entirely into memory.  8 MB default; override
# with ``ADOPTIQ_HTTP_BODY_CAP_BYTES``.
_HTTP_BODY_CAP_BYTES = int(os.environ.get("ADOPTIQ_HTTP_BODY_CAP_BYTES", str(8 * 1024 * 1024)))


# Round 6 / Phase 4.12: shared retry helper used by the cisco
# integrations (BST/PSIRT) for transient failures.  The previous
# implementation either retried with a fixed sleep or did not retry
# at all, so a brief 503 from BST or a per-key 429 from PSIRT would
# fail the whole report.  We use exponential backoff with full
# jitter, honouring a Retry-After header when the server provides
# one, and only retry on the documented transient classes.
_CISCO_RETRY_MAX_ATTEMPTS = max(1, int(os.environ.get("ADOPTIQ_CISCO_RETRY_MAX_ATTEMPTS", "4")))
_CISCO_RETRY_BASE_S = max(0.05, float(os.environ.get("ADOPTIQ_CISCO_RETRY_BASE_S", "0.5")))
_CISCO_RETRY_CAP_S = max(_CISCO_RETRY_BASE_S, float(os.environ.get("ADOPTIQ_CISCO_RETRY_CAP_S", "8.0")))
_CISCO_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _retry_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Any] = None,
    json_body: Optional[Any] = None,
    timeout: int = 30,
    attempts: int = _CISCO_RETRY_MAX_ATTEMPTS,
    description: str = "request",
) -> Optional[requests.Response]:
    """Issue ``requests.request`` with exponential backoff + jitter.

    Retries on connection errors, timeouts, and HTTP 429/5xx.  Returns
    the final ``Response`` (which may still be a non-2xx response), or
    ``None`` if every attempt raised an exception.
    """
    last_response: Optional[requests.Response] = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                data=data,
                json=json_body,
                timeout=timeout,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= attempts:
                logger.error(
                    "%s failed after %d attempts: %s", description, attempt, exc
                )
                return None
            sleep_s = min(_CISCO_RETRY_CAP_S, _CISCO_RETRY_BASE_S * (2 ** (attempt - 1)))
            # Round 12 / Phase 11.5: deterministic jitter in test mode
            # so golden traces of BST / PSIRT retries do not flake on
            # the wall-clock-seeded ``random.uniform``.  Production
            # behaviour is unchanged.
            if os.environ.get("ADOPTIQ_TEST_MODE", "").strip().lower() in {"1", "true", "yes"}:
                sleep_s = float(sleep_s) / 2.0
            else:
                sleep_s = random.uniform(0, sleep_s)  # full jitter
            logger.warning(
                "%s attempt %d/%d raised %s; sleeping %.2fs before retry",
                description, attempt, attempts, type(exc).__name__, sleep_s,
            )
            time.sleep(sleep_s)
            continue
        last_response = response
        if response.status_code in _CISCO_RETRY_STATUSES and attempt < attempts:
            retry_after = response.headers.get('Retry-After') if response.headers else None
            sleep_s: float
            if retry_after:
                try:
                    sleep_s = max(0.0, float(retry_after))
                except (TypeError, ValueError):
                    sleep_s = min(_CISCO_RETRY_CAP_S, _CISCO_RETRY_BASE_S * (2 ** (attempt - 1)))
            else:
                sleep_s = min(_CISCO_RETRY_CAP_S, _CISCO_RETRY_BASE_S * (2 ** (attempt - 1)))
            # Round 12 / Phase 11.5: deterministic jitter in test mode
            # (parity with the connection-error branch above).
            if os.environ.get("ADOPTIQ_TEST_MODE", "").strip().lower() in {"1", "true", "yes"}:
                sleep_s = float(sleep_s) / 2.0
            else:
                sleep_s = random.uniform(0, sleep_s)  # full jitter
            logger.warning(
                "%s attempt %d/%d returned HTTP %s; sleeping %.2fs before retry",
                description, attempt, attempts, response.status_code, sleep_s,
            )
            time.sleep(sleep_s)
            continue
        return response
    return last_response


def _query_digest(value: Any, length: int = 12) -> str:
    """Round 8 / Phase 4.4: hash a search query / param so we can log
    a stable correlator at INFO without leaking the raw text.

    The raw value remains available to operators at DEBUG.
    """
    try:
        import hashlib as _h
        return _h.sha256(str(value or '').encode('utf-8', 'replace')).hexdigest()[:length]
    except Exception:
        return '?'


def _response_body_digest(response: Any, cap: int = 200) -> Tuple[str, str]:
    """Round 9 / Phase 3.1: shared body-digest+truncate helper for error logs.

    Round 8 / Phase 4.2 hardened only the OAuth-token error path; every
    other Cisco error branch (BST API, Circuit, PSIRT) was still calling
    ``logger.error(...response.text)`` which can dump multi-megabyte
    HTML SSO error pages -- and any reflected user-id / email / scope
    fragments -- into the centralised log.  This returns a stable
    ``(short_digest, truncated_first_cap_chars)`` tuple suitable for the
    ``... body_digest=%s body_first_%d=%r`` logging form, so every call
    site can be hardened uniformly without lifting body bytes by hand.
    """
    try:
        text = getattr(response, 'text', '') or ''
    except Exception:
        text = ''
    try:
        import hashlib as _h
        digest = _h.sha256(text.encode('utf-8', 'replace')).hexdigest()[:12]
    except Exception:
        digest = '?'
    try:
        truncated = text[: max(0, int(cap))]
    except Exception:
        truncated = ''
    return digest, truncated


_CISCO_ALLOWED_NETLOC_HOSTS = (
    # Round 9 / Phase 3.3: defence-in-depth allowlist for outbound URLs
    # that pass ``allow_redirects=True``.  We do not strip the leading
    # subdomain so e.g. ``apix.cisco.com`` and ``api.cisco.com`` are
    # both anchored on a trailing ``.cisco.com`` suffix; an exact-match
    # fast-path covers ``cisco.com`` itself.  Anything else -- a typo
    # in the configured base URL, a poisoned env var pointing at a
    # lookalike domain -- is refused before the redirect chain runs.
    'cisco.com',
)


def _is_cisco_netloc(url: str) -> bool:
    """Round 9 / Phase 3.3: parse + assert a URL's netloc is on the
    Cisco allowlist before issuing a redirect-following request.

    Returns ``True`` only for ``https://`` URLs whose host equals one
    of ``_CISCO_ALLOWED_NETLOC_HOSTS`` or ends in
    ``.<allowed_host>``.  Bare hostnames, ``http://`` URLs, IP
    literals, and anything we can't parse all return ``False`` so the
    caller can fail closed rather than follow an attacker-controlled
    redirect.
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(str(url or ''))
    except Exception:
        return False
    if parsed.scheme.lower() != 'https':
        return False
    host = (parsed.hostname or '').lower()
    if not host:
        return False
    for allowed in _CISCO_ALLOWED_NETLOC_HOSTS:
        if host == allowed or host.endswith('.' + allowed):
            return True
    return False


def _safe_response_json(response: Any, cap: int = _HTTP_BODY_CAP_BYTES) -> Any:
    """Parse ``response.json()`` with a body-size cap.

    Falls back to ``response.json()`` if the response object does not
    support streaming (e.g. mocks).  Raises ``ValueError`` if the body
    exceeds ``cap`` bytes.

    Round 8 / Phase 4.3: also reject responses whose ``Content-Type``
    is not a JSON family (``application/json`` /
    ``application/*+json``).  Cisco identity/SSO endpoints have been
    observed to return an HTML SSO redirect page on auth failure,
    which previously triggered a confusing JSONDecodeError deep in
    the call stack instead of a clean "auth failed" message.
    """
    try:
        body = getattr(response, 'content', None)
    except Exception:
        body = None
    if body is None:
        return response.json()
    if len(body) > cap:
        raise ValueError(
            f"HTTP body exceeded cap of {cap} bytes (got {len(body)} bytes from "
            f"{getattr(response, 'url', '?')})"
        )
    try:
        _headers = getattr(response, 'headers', {}) or {}
        _ctype = str(_headers.get('Content-Type') or _headers.get('content-type') or '').lower()
    except Exception:
        _ctype = ''
    if _ctype:
        _primary = _ctype.split(';', 1)[0].strip()
        if _primary and not (
            _primary == 'application/json'
            or _primary.endswith('+json')
            or _primary == 'text/json'
        ):
            raise ValueError(
                f"Refusing to JSON-decode non-JSON response (Content-Type={_primary!r}, "
                f"url={getattr(response, 'url', '?')})"
            )
    return response.json()

class DataClassification(Enum):
    """Data classification levels for Cisco information"""
    CISCO_PUBLIC = "CISCO_PUBLIC"
    CISCO_RESTRICTED = "CISCO_RESTRICTED"
    CISCO_INTERNAL = "CISCO_INTERNAL"
    CISCO_CONFIDENTIAL = "CISCO_CONFIDENTIAL"

@dataclass
class DefectInfo:
    """Structured defect information from BST"""
    defect_id: str
    title: str
    status: str
    severity: str
    product: str
    component: str
    description: str
    resolution: Optional[str]
    created_date: str
    modified_date: str
    assignee: str
    classification: DataClassification
    source: str
    verification_method: str
    # Round 2 / Phase 5.1: separate scrape-time from real defect dates
    # so historical defects are not misrepresented as freshly created
    # / modified on every scrape.  ``last_indexed_at`` records when
    # this row was harvested; ``created_date`` / ``modified_date``
    # remain authoritative when parseable from the source HTML.
    last_indexed_at: Optional[str] = None

@dataclass
class CircuitData:
    """Structured Circuit internal data"""
    record_id: str
    title: str
    content: str
    author: str
    created_date: str
    modified_date: str
    classification: DataClassification
    source: str
    verification_method: str

@dataclass
class PSIRTVulnerability:
    """Structured PSIRT vulnerability information"""
    advisory_id: str
    title: str
    summary: str
    severity: str
    cve_ids: List[str]
    bug_ids: List[str]
    products: List[str]
    published_date: str
    last_updated: str
    cvrf_url: str
    csaf_url: str
    classification: DataClassification
    source: str
    verification_method: str

class CiscoInternalIntegrations:
    """Integration class for Cisco internal systems"""
    
    def __init__(
        self,
        circuit_api_key: Optional[str] = None,
        psirt_api_key: Optional[str] = None,
        psirt_client_secret: Optional[str] = None,
    ):
        """
        Initialize Cisco internal integrations.

        BST (Bug Search Tool) is web-only; use ``defect_portal_url`` for manual links.
        """
        self.circuit_api_key = circuit_api_key
        self.psirt_api_key = psirt_api_key
        self.psirt_client_secret = psirt_client_secret

        # API endpoints
        self.circuit_base_url = "https://circuit.cisco.com/api/v1"
        self.psirt_base_url = "https://apix.cisco.com/security/advisories"  # PSIRT openVuln API (apix, not api)
        
        # Rate limiting
        self.circuit_last_request = 0
        self.psirt_last_request = 0
        self.request_delay = 0.2  # 5 calls per second = 0.2 seconds between requests
        
        # PSIRT OAuth token cache
        self.psirt_access_token = None
        self.psirt_token_expiry = None
        
        # Data classification mapping
        self.classification_mapping = {
            'public': DataClassification.CISCO_PUBLIC,
            'restricted': DataClassification.CISCO_RESTRICTED,
            'internal': DataClassification.CISCO_INTERNAL,
            'confidential': DataClassification.CISCO_CONFIDENTIAL
        }
    
    def _rate_limit(self, system: str):
        """Implement rate limiting for API requests"""
        current_time = time.time()
        if system == 'circuit':
            if current_time - self.circuit_last_request < self.request_delay:
                time.sleep(self.request_delay - (current_time - self.circuit_last_request))
            self.circuit_last_request = time.time()
        elif system == 'psirt':
            if current_time - self.psirt_last_request < self.request_delay:
                time.sleep(self.request_delay - (current_time - self.psirt_last_request))
            self.psirt_last_request = time.time()
    
    def _get_psirt_access_token(self) -> Optional[str]:
        """
        Get or refresh PSIRT OAuth access token using client credentials
        According to Cisco PSIRT API docs, uses OAuth 2.0 client credentials flow
        """
        # Check if we have a valid cached token
        if self.psirt_access_token and self.psirt_token_expiry:
            # Round 6 / Phase 4.17: tz-aware UTC compare matches the
            # token_expiry value stored above.
            if datetime.now(timezone.utc) < self.psirt_token_expiry:
                return self.psirt_access_token
        
        # Need to get new token
        if not self.psirt_api_key or not self.psirt_client_secret:
            return None
        
        try:
            # OAuth 2.0 token endpoint for Cisco API
            # Round 14 / Phase 4.4: this is the public OAuth /token URL
            # used to *exchange* credentials, not a credential itself --
            # bandit/ruff S105 flags the literal because of the trailing
            # ``token`` path segment.  Pin a noqa per line.
            token_url = "https://id.cisco.com/oauth2/default/v1/token"  # noqa: S105 -- OAuth endpoint URL
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
            
            data = {
                'grant_type': 'client_credentials',
                'client_id': self.psirt_api_key,
                'client_secret': self.psirt_client_secret
            }
            
            logger.info("Requesting PSIRT OAuth access token...")
            response = requests.post(token_url, headers=headers, data=data, timeout=30)
            
            if response.status_code == 200:
                token_data = _safe_response_json(response)
                # Round 6 / Phase 4.11: validate the token JSON
                # shape before caching.  A non-dict payload (e.g. a
                # captive-portal HTML page returned with a 200) used
                # to crash subsequent .get() calls and would also
                # poison the cache with a None token, so keep the
                # cache untouched and return None when the response
                # does not match the documented OAuth shape.
                if not isinstance(token_data, dict):
                    logger.error(
                        "PSIRT OAuth token response was not a JSON object (got %s)",
                        type(token_data).__name__,
                    )
                    return None
                token = token_data.get('access_token')
                if not isinstance(token, str) or not token:
                    logger.error("PSIRT OAuth token response missing access_token")
                    return None
                expires_in = token_data.get('expires_in', 3600)
                try:
                    expires_in = int(expires_in)
                except (TypeError, ValueError):
                    expires_in = 3600
                if expires_in <= 60:
                    expires_in = 3600
                # Round 6 / Phase 4.17: tz-aware UTC for token expiry
                # so the comparison in the cache lookup remains valid
                # regardless of the host process timezone.
                self.psirt_access_token = token
                self.psirt_token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
                logger.info(f"PSIRT OAuth token obtained, expires in {expires_in} seconds")
                return self.psirt_access_token
            else:
                # Round 8 / Phase 4.2: ``response.text`` from a Cisco
                # OAuth identity endpoint can be very large (full HTML
                # error page) and may echo back our client_id/scope.
                # Truncate the body and log a digest so support can
                # still correlate without inflating logs or leaking
                # bearer-adjacent data.
                _body = (response.text or '')[:512]
                try:
                    import hashlib as _h
                    _body_digest = _h.sha256((response.text or '').encode('utf-8', 'replace')).hexdigest()[:12]
                except Exception:
                    _body_digest = '?'
                logger.error(
                    "PSIRT OAuth token request failed: status=%s body_digest=%s body_first_512=%r",
                    response.status_code, _body_digest, _body,
                )
                return None
                
        except Exception as e:
            logger.error(f"Error getting PSIRT OAuth token: {e}")
            return None
    
    def _get_headers(self, system: str) -> Dict[str, str]:
        """Get authentication headers for API requests"""
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'AdoptIQ-Executive-Analyzer/1.0'
        }
        
        if system == 'circuit' and self.circuit_api_key:
            headers['Authorization'] = f'Bearer {self.circuit_api_key}'
        elif system == 'psirt':
            # PSIRT uses OAuth 2.0 - get access token
            access_token = self._get_psirt_access_token()
            if access_token:
                headers['Authorization'] = f'Bearer {access_token}'
        
        return headers
    
    def _classify_data(self, data_source: str, content: str) -> DataClassification:
        """
        Classify data based on source and content
        
        Args:
            data_source: Source system (bst, circuit, psirt)
            content: Data content to classify
            
        Returns:
            DataClassification enum value
        """
        # BST data is typically Cisco Restricted
        if data_source == 'bst':
            return DataClassification.CISCO_RESTRICTED
        
        # PSIRT data is typically Cisco Public (security advisories are public)
        if data_source == 'psirt':
            return DataClassification.CISCO_PUBLIC
        
        # Circuit data classification based on content
        if data_source == 'circuit':
            content_lower = content.lower()
            
            # Check for confidential indicators
            confidential_keywords = ['confidential', 'proprietary', 'nda', 'non-disclosure']
            if any(keyword in content_lower for keyword in confidential_keywords):
                return DataClassification.CISCO_CONFIDENTIAL
            
            # Check for internal indicators
            internal_keywords = ['internal', 'employee', 'ciscovpn', 'ciscoworks']
            if any(keyword in content_lower for keyword in internal_keywords):
                return DataClassification.CISCO_INTERNAL
            
            # Default to restricted for Circuit
            return DataClassification.CISCO_RESTRICTED
        
        return DataClassification.CISCO_PUBLIC
    
    def search_circuit_data(self, search_terms: List[str], space_filter: Optional[str] = None,
                           days_back: int = 90) -> List[CircuitData]:
        """
        Search for internal data in Circuit
        
        Args:
            search_terms: List of search terms
            space_filter: Optional Circuit space filter
            days_back: Number of days to look back
            
        Returns:
            List of CircuitData objects
        """
        circuit_data = []
        
        try:
            self._rate_limit('circuit')

            # Round 6 / Phase 4.7: tz-aware UTC for the search window.
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=days_back)

            # Build search query
            search_query = ' OR '.join(search_terms)
            if space_filter:
                search_query += f' AND space:"{space_filter}"'
            
            params = {
                'q': search_query,
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': end_date.strftime('%Y-%m-%d'),
                'limit': 100,
                'fields': 'id,title,content,author,created_date,modified_date,space'
            }
            
            headers = self._get_headers('circuit')
            
            # Round 8 / Phase 4.4: digest the query at INFO; verbatim at DEBUG.
            logger.info("Searching Circuit for data (query_digest=%s)", _query_digest(search_query))
            logger.debug("Searching Circuit for data: %s", search_query)
            
            response = requests.get(
                f"{self.circuit_base_url}/search",
                params=params,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = _safe_response_json(response)
                
                for item_data in data.get('items', []):
                    # Classify the data
                    classification = self._classify_data('circuit', item_data.get('content', ''))
                    
                    circuit_item = CircuitData(
                        record_id=item_data.get('id', ''),
                        title=item_data.get('title', ''),
                        content=item_data.get('content', ''),
                        author=item_data.get('author', ''),
                        created_date=item_data.get('created_date', ''),
                        modified_date=item_data.get('modified_date', ''),
                        classification=classification,
                        source='Circuit (Cisco Internal)',
                        verification_method=f'Search Circuit with ID: {item_data.get("id", "")}'
                    )
                    
                    circuit_data.append(circuit_item)
                
                logger.info(f"Found {len(circuit_data)} items in Circuit")
                
            else:
                # Round 9 / Phase 3.1: digest+truncate body.
                _digest, _body = _response_body_digest(response, cap=200)
                logger.error(
                    "Circuit API error: status=%s body_digest=%s body_first_200=%r",
                    response.status_code, _digest, _body,
                )
                
        except Exception as e:
            logger.error(f"Error searching Circuit: {e}")
        
        return circuit_data
    
    def search_psirt_vulnerabilities(self, search_terms: List[str], product_filter: Optional[str] = None,
                                    days_back: int = 90) -> List[PSIRTVulnerability]:
        """
        Search for security vulnerabilities using PSIRT openVuln API
        
        Args:
            search_terms: List of search terms (CVE IDs, bug IDs, keywords)
            product_filter: Optional product filter (e.g., "Cisco IOS", "Cisco ASA")
            days_back: Number of days to look back
            
        Returns:
            List of PSIRTVulnerability objects
        """
        vulnerabilities = []
        
        try:
            self._rate_limit('psirt')
            
            if not self.psirt_api_key:
                logger.warning("PSIRT API key not configured")
                return vulnerabilities
            
            # Build search parameters
            params = {}
            
            # Check if search terms contain CVE IDs
            cve_ids = [term for term in search_terms if term.upper().startswith('CVE-')]
            if cve_ids:
                params['cve'] = ','.join(cve_ids)
            
            # Check if search terms contain bug IDs
            bug_ids = [term for term in search_terms if term.upper().startswith('CSC')]
            if bug_ids:
                params['bug_id'] = ','.join(bug_ids)
            
            # If no specific IDs, use keyword search
            if not cve_ids and not bug_ids:
                params['keyword'] = ' '.join(search_terms)
            
            # Add product filter if provided
            if product_filter:
                params['product'] = product_filter
            
            # Add date filter
            if days_back < 365:
                params['days'] = days_back
            
            headers = self._get_headers('psirt')
            
            logger.info(f"Searching PSIRT for vulnerabilities: {params}")
            
            # PSIRT API endpoint format according to official openVuln API docs
            # Reference: https://developer.cisco.com/docs/psirt/
            #
            # Round 6 / Phase 4.6: split CVE / bug ID lists into batched
            # requests.  The previous implementation joined the entire
            # ``search_terms`` CVE list into a single path segment which
            # (a) trivially exceeds typical URL length limits (~8 KiB)
            # for any non-trivial CVE batch and (b) makes the server's
            # 414 / 400 response indistinguishable from "no advisories
            # found".  We now request CVE/bug IDs in fixed-size batches
            # and merge the parsed advisories.
            _PSIRT_BATCH_SIZE = int(os.environ.get("ADOPTIQ_PSIRT_BATCH_SIZE", "10"))
            if 'cve' in params:
                _all = [c for c in cve_ids if c]
                for i in range(0, len(_all), _PSIRT_BATCH_SIZE):
                    _batch = _all[i:i + _PSIRT_BATCH_SIZE]
                    _joined = ",".join(_batch)
                    endpoint = f"{self.psirt_base_url}/v2/cve/{_joined}"
                    logger.info(
                        "PSIRT API request (CVE batch %d-%d of %d): %s",
                        i + 1, i + len(_batch), len(_all), endpoint,
                    )
                    # Round 6 / Phase 4.12: retry transient 429/5xx
                    # responses with exponential backoff + jitter.
                    response = _retry_request(
                        'GET', endpoint,
                        headers=headers,
                        timeout=30,
                        description=f"PSIRT CVE batch {i // _PSIRT_BATCH_SIZE + 1}",
                    )
                    if response is None:
                        logger.error("PSIRT CVE batch failed after retries; skipping")
                        continue
                    if response.status_code == 200:
                        data = _safe_response_json(response)
                        vulnerabilities.extend(
                            self._parse_psirt_response(data, _batch, product_filter)
                        )
                    elif response.status_code == 403:
                        logger.error("PSIRT API access forbidden - check API credentials and permissions")
                        break
                    elif response.status_code == 401:
                        # Round 8 / Phase 4.5: clear the cached
                        # PSIRT bearer so the next call refreshes
                        # against the OAuth endpoint instead of
                        # replaying a token the server already
                        # rejected.
                        logger.error("PSIRT API authentication failed - invalid or expired token (clearing cached bearer)")
                        self.psirt_access_token = None
                        self.psirt_token_expiry = None
                        break
                    else:
                        # Round 9 / Phase 3.1: digest+truncate body uniformly.
                        _digest, _body = _response_body_digest(response, cap=200)
                        logger.error(
                            "PSIRT API CVE batch returned status %s body_digest=%s body_first_200=%r",
                            response.status_code, _digest, _body,
                        )
                logger.info(f"PSIRT API returned {len(vulnerabilities)} vulnerabilities (CVE batched)")
            elif 'bug_id' in params:
                _all = [b for b in bug_ids if b]
                for i in range(0, len(_all), _PSIRT_BATCH_SIZE):
                    _batch = _all[i:i + _PSIRT_BATCH_SIZE]
                    _joined = ",".join(_batch)
                    endpoint = f"{self.psirt_base_url}/v2/bug_id/{_joined}"
                    logger.info(
                        "PSIRT API request (bug_id batch %d-%d of %d): %s",
                        i + 1, i + len(_batch), len(_all), endpoint,
                    )
                    response = _retry_request(
                        'GET', endpoint,
                        headers=headers,
                        timeout=30,
                        description=f"PSIRT bug_id batch {i // _PSIRT_BATCH_SIZE + 1}",
                    )
                    if response is None:
                        logger.error("PSIRT bug_id batch failed after retries; skipping")
                        continue
                    if response.status_code == 200:
                        data = _safe_response_json(response)
                        vulnerabilities.extend(
                            self._parse_psirt_response(data, _batch, product_filter)
                        )
                    elif response.status_code == 403:
                        logger.error("PSIRT API access forbidden - check API credentials and permissions")
                        break
                    elif response.status_code == 401:
                        # Round 8 / Phase 4.5: clear the cached PSIRT bearer.
                        logger.error("PSIRT API authentication failed - invalid or expired token (clearing cached bearer)")
                        self.psirt_access_token = None
                        self.psirt_token_expiry = None
                        break
                    else:
                        # Round 9 / Phase 3.1: digest+truncate body uniformly.
                        _digest, _body = _response_body_digest(response, cap=200)
                        logger.error(
                            "PSIRT API bug_id batch returned status %s body_digest=%s body_first_200=%r",
                            response.status_code, _digest, _body,
                        )
                logger.info(f"PSIRT API returned {len(vulnerabilities)} vulnerabilities (bug_id batched)")
            else:
                endpoint = f"{self.psirt_base_url}/v2/all"
                response = _retry_request(
                    'GET', endpoint,
                    params=params,
                    headers=headers,
                    timeout=30,
                    description="PSIRT all-advisories",
                )
                if response is None:
                    logger.error("PSIRT all-advisories failed after retries")
                    return vulnerabilities
                logger.info(f"PSIRT API request: {endpoint}")
                if response.status_code == 200:
                    data = _safe_response_json(response)
                    logger.info(
                        "PSIRT API response type: %s, keys: %s",
                        type(data), list(data.keys()) if isinstance(data, dict) else 'not a dict',
                    )
                    vulnerabilities = self._parse_psirt_response(data, search_terms, product_filter)
                    logger.info(f"PSIRT API returned {len(vulnerabilities)} vulnerabilities")
                elif response.status_code == 403:
                    logger.error("PSIRT API access forbidden - check API credentials and permissions")
                elif response.status_code == 401:
                    # Round 8 / Phase 4.5: clear the cached PSIRT bearer.
                    logger.error("PSIRT API authentication failed - invalid or expired token (clearing cached bearer)")
                    self.psirt_access_token = None
                    self.psirt_token_expiry = None
                else:
                    # Round 9 / Phase 3.1: digest+truncate body uniformly.
                    _digest, _body = _response_body_digest(response, cap=200)
                    logger.error(
                        "PSIRT API returned status %s body_digest=%s body_first_200=%r",
                        response.status_code, _digest, _body,
                    )
                
        except Exception as e:
            logger.error(f"Error accessing PSIRT API: {e}")
        
        return vulnerabilities
    
    def get_psirt_vulnerability_by_id(self, advisory_id: str) -> Optional[PSIRTVulnerability]:
        """
        Get detailed information for a specific PSIRT advisory
        
        Args:
            advisory_id: PSIRT advisory ID
            
        Returns:
            PSIRTVulnerability object or None
        """
        try:
            self._rate_limit('psirt')
            
            if not self.psirt_api_key:
                logger.warning("PSIRT API key not configured")
                return None
            
            headers = self._get_headers('psirt')
            
            logger.info(f"Fetching PSIRT advisory details: {advisory_id}")
            
            # PSIRT API endpoint for specific advisory
            response = requests.get(
                f"{self.psirt_base_url}/v2/advisory/{urllib.parse.quote(str(advisory_id), safe='')}",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = _safe_response_json(response)
                vulnerability = self._parse_single_psirt_response(data, advisory_id)
                logger.info(f"Retrieved PSIRT advisory details for {advisory_id}")
                return vulnerability
            else:
                # Round 9 / Phase 3.1: digest+truncate body.
                _digest, _body = _response_body_digest(response, cap=200)
                logger.error(
                    "PSIRT API error for advisory %s: status=%s body_digest=%s body_first_200=%r",
                    advisory_id, response.status_code, _digest, _body,
                )
                
        except Exception as e:
            logger.error(f"Error fetching PSIRT advisory details: {e}")
        
        return None
    
    def _parse_psirt_response(self, api_data: dict, search_terms: List[str], product_filter: Optional[str]) -> List[PSIRTVulnerability]:
        """
        Parse PSIRT API response into PSIRTVulnerability objects
        
        Args:
            api_data: JSON response from PSIRT API
            search_terms: Original search terms
            product_filter: Original product filter
            
        Returns:
            List of PSIRTVulnerability objects
        """
        vulnerabilities = []
        
        try:
            advisories = api_data.get('advisories', [])
            
            for advisory in advisories:
                # Extract CVE IDs (API returns list of CVE strings)
                cve_ids = []
                if 'cves' in advisory:
                    # cves can be a list of strings or list of dicts
                    for cve in advisory['cves']:
                        if isinstance(cve, str):
                            cve_ids.append(cve)
                        elif isinstance(cve, dict):
                            cve_ids.append(cve.get('cveId', cve.get('cve_id', '')))
                
                # Extract bug IDs (API uses 'bugIDs' not 'bug_ids')
                bug_ids = advisory.get('bugIDs', advisory.get('bug_ids', []))
                if not isinstance(bug_ids, list):
                    bug_ids = []
                
                # Extract products (API uses 'productNames')
                products = advisory.get('productNames', advisory.get('products', []))
                if not isinstance(products, list):
                    products = []
                
                # Map API field names to our structure
                advisory_id = advisory.get('advisoryId', advisory.get('advisory_id', ''))
                title = advisory.get('advisoryTitle', advisory.get('title', ''))
                severity = advisory.get('sir', advisory.get('severity', 'Unknown'))  # 'sir' is the severity field
                
                vulnerability = PSIRTVulnerability(
                    advisory_id=advisory_id,
                    title=title,
                    summary=advisory.get('summary', ''),
                    severity=severity,
                    cve_ids=cve_ids,
                    bug_ids=bug_ids,
                    products=products,
                    published_date=advisory.get('firstPublished', advisory.get('publication_date', '')),
                    last_updated=advisory.get('lastUpdated', advisory.get('last_updated', '')),
                    cvrf_url=advisory.get('cvrfUrl', advisory.get('cvrf_url', '')),
                    csaf_url=advisory.get('csafUrl', advisory.get('csaf_url', '')),
                    classification=self._classify_data('psirt', advisory.get('summary', '')),
                    source='PSIRT openVuln API',
                    verification_method=f"PSIRT Advisory ID: {advisory_id} - {advisory.get('publicationUrl', f'https://tools.cisco.com/security/center/content/CiscoSecurityAdvisory/{advisory_id}')}"
                )
                vulnerabilities.append(vulnerability)
                
        except Exception as e:
            logger.error(f"Error parsing PSIRT API response: {e}")
        
        return vulnerabilities
    
    def _parse_single_psirt_response(self, api_data: dict, advisory_id: str) -> Optional[PSIRTVulnerability]:
        """
        Parse single PSIRT advisory response
        
        Args:
            api_data: JSON response from PSIRT API
            advisory_id: Advisory ID
            
        Returns:
            PSIRTVulnerability object or None
        """
        try:
            advisory = api_data.get('advisory', {})
            
            # Round 2 / Phase 5.4: reuse the same CVE id normalization
            # the list parser uses; the PSIRT API returns ``cves`` as a
            # mix of plain strings and ``{cveId|cve_id}`` dicts, so the
            # previous ``cve.get('cve_id', '')`` silently dropped every
            # string entry and crashed on non-dict values.
            cve_ids: List[str] = []
            if 'cves' in advisory and isinstance(advisory['cves'], list):
                for cve in advisory['cves']:
                    if isinstance(cve, str):
                        cve_ids.append(cve)
                    elif isinstance(cve, dict):
                        cve_ids.append(cve.get('cveId', cve.get('cve_id', '')))
                cve_ids = [c for c in cve_ids if c]
            
            # Extract bug IDs
            bug_ids = []
            if 'bug_ids' in advisory:
                bug_ids = advisory['bug_ids']
            
            # Extract products
            products = []
            if 'products' in advisory:
                products = [product.get('name', '') for product in advisory['products']]
            
            vulnerability = PSIRTVulnerability(
                advisory_id=advisory.get('advisory_id', advisory_id),
                title=advisory.get('title', ''),
                summary=advisory.get('summary', ''),
                severity=advisory.get('severity', 'Unknown'),
                cve_ids=cve_ids,
                bug_ids=bug_ids,
                products=products,
                published_date=advisory.get('publication_date', ''),
                last_updated=advisory.get('last_updated', ''),
                cvrf_url=advisory.get('cvrf_url', ''),
                csaf_url=advisory.get('csaf_url', ''),
                classification=self._classify_data('psirt', advisory.get('summary', '')),
                source='PSIRT openVuln API',
                verification_method=f"PSIRT Advisory ID: {advisory_id} - https://tools.cisco.com/security/center/content/CiscoSecurityAdvisory/{advisory_id}"
            )
            
            return vulnerability
            
        except Exception as e:
            logger.error(f"Error parsing single PSIRT response: {e}")
            return None
    
    def get_comprehensive_defect_analysis(self, defect_ids: List[str], 
                                        search_terms: List[str]) -> Dict[str, Any]:
        """
        Get comprehensive defect analysis from Circuit and PSIRT
        
        Args:
            defect_ids: List of specific defect IDs to look up
            search_terms: General search terms for related defects
            
        Returns:
            Comprehensive analysis dictionary
        """
        # Round 13 / Phase 2.8: diagnostic timestamps were emitted via
        # naive ``datetime.now().isoformat()``, which yields a string
        # without any zone marker.  Downstream parsers (and humans
        # reading the JSON) had no way to know whether that referred
        # to the worker's local zone or UTC.  Standardize on UTC ISO-Z.
        analysis = {
            'bst_defects': [],
            'circuit_data': [],
            'psirt_vulnerabilities': [],
            'classification_summary': {},
            'data_sources': [],
            'verification_methods': [],
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        
        # Round 165: BST has no API; bst_defects stays empty (portal links via defect_portal_url).
        
        # Search for related internal data in Circuit
        if search_terms:
            circuit_items = self.search_circuit_data(search_terms)
            analysis['circuit_data'].extend(circuit_items)
        
        # Search for related security vulnerabilities in PSIRT
        if search_terms:
            psirt_vulnerabilities = self.search_psirt_vulnerabilities(search_terms)
            analysis['psirt_vulnerabilities'].extend(psirt_vulnerabilities)
        
        # Generate classification summary
        all_items = analysis['bst_defects'] + analysis['circuit_data'] + analysis['psirt_vulnerabilities']
        for item in all_items:
            classification = item.classification.value
            if classification not in analysis['classification_summary']:
                analysis['classification_summary'][classification] = 0
            analysis['classification_summary'][classification] += 1
        
        # Collect data sources and verification methods
        analysis['data_sources'] = list(set([item.source for item in all_items]))
        analysis['verification_methods'] = list(set([item.verification_method for item in all_items]))
        
        return analysis
    
    def generate_data_classification_report(self, analysis: Dict[str, Any]) -> str:
        """
        Generate a data classification report for the analysis
        
        Args:
            analysis: Comprehensive analysis dictionary
            
        Returns:
            Formatted classification report
        """
        report = []
        report.append("=" * 80)
        report.append("CISCO DATA CLASSIFICATION REPORT")
        report.append("=" * 80)
        # Round 13 / Phase 2.8: stamp the classification report header
        # with a real UTC timestamp so the value matches the rest of
        # the UTC-anchored reporting surface.
        report.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        report.append("")
        
        # Classification summary
        report.append("DATA CLASSIFICATION SUMMARY:")
        report.append("-" * 40)
        for classification, count in analysis['classification_summary'].items():
            report.append(f"{classification}: {count} items")
        report.append("")
        
        # Data sources
        report.append("DATA SOURCES:")
        report.append("-" * 40)
        for source in analysis['data_sources']:
            report.append(f"• {source}")
        report.append("")
        
        # Classification definitions
        report.append("CLASSIFICATION DEFINITIONS:")
        report.append("-" * 40)
        report.append("CISCO_PUBLIC: Information that can be shared publicly")
        report.append("CISCO_RESTRICTED: Information for Cisco employees and partners only")
        report.append("CISCO_INTERNAL: Information for Cisco employees only")
        report.append("CISCO_CONFIDENTIAL: Highly sensitive information with strict access controls")
        report.append("")
        
        # Handling instructions
        report.append("DATA HANDLING INSTRUCTIONS:")
        report.append("-" * 40)
        report.append("• CISCO_PUBLIC: Can be included in external reports")
        report.append("• CISCO_RESTRICTED: Include only in internal Cisco reports")
        report.append("• CISCO_INTERNAL: Include only in employee-only reports")
        report.append("• CISCO_CONFIDENTIAL: Handle according to confidentiality requirements")
        report.append("")
        
        # Verification methods
        report.append("VERIFICATION METHODS:")
        report.append("-" * 40)
        for method in analysis['verification_methods']:
            report.append(f"• {method}")
        report.append("")
        
        report.append("=" * 80)
        
        return "\n".join(report)
    
    def filter_data_by_classification(self, analysis: Dict[str, Any], 
                                    allowed_classifications: List[DataClassification]) -> Dict[str, Any]:
        """
        Filter analysis data by classification level
        
        Args:
            analysis: Comprehensive analysis dictionary
            allowed_classifications: List of allowed classification levels
            
        Returns:
            Filtered analysis dictionary
        """
        filtered_analysis = {
            'bst_defects': [],
            'circuit_data': [],
            'classification_summary': {},
            'data_sources': [],
            'verification_methods': [],
            'timestamp': analysis['timestamp']
        }
        
        # Filter BST defects
        for defect in analysis['bst_defects']:
            if defect.classification in allowed_classifications:
                filtered_analysis['bst_defects'].append(defect)
        
        # Filter Circuit data
        for item in analysis['circuit_data']:
            if item.classification in allowed_classifications:
                filtered_analysis['circuit_data'].append(item)
        
        # Recalculate classification summary
        all_items = filtered_analysis['bst_defects'] + filtered_analysis['circuit_data']
        for item in all_items:
            classification = item.classification.value
            if classification not in filtered_analysis['classification_summary']:
                filtered_analysis['classification_summary'][classification] = 0
            filtered_analysis['classification_summary'][classification] += 1
        
        # Update data sources and verification methods
        filtered_analysis['data_sources'] = list(set([item.source for item in all_items]))
        filtered_analysis['verification_methods'] = list(set([item.verification_method for item in all_items]))
        
        return filtered_analysis
    
    def generate_llm_summary_for_defect(self, defect: DefectInfo, llm_api_key: Optional[str] = None) -> str:
        """
        Generate LLM-powered summary for a BST defect
        
        Args:
            defect: DefectInfo object
            llm_api_key: Optional API key for LLM service
            
        Returns:
            LLM-generated summary of the defect
        """
        try:
            # Build the prompt for the LLM
            prompt = f"""Analyze this Cisco BST (Bug Search Tool) defect and provide a comprehensive executive summary:

**Defect ID:** {defect.defect_id}
**Title:** {defect.title}
**Status:** {defect.status}
**Severity:** {defect.severity}
**Product:** {defect.product}
**Component:** {defect.component}
**Description:** {defect.description}
**Resolution:** {defect.resolution or 'Not yet resolved'}
**Created Date:** {defect.created_date}
**Last Modified:** {defect.modified_date}
**Assignee:** {defect.assignee or 'Not assigned'}

Please provide:
1. **Executive Summary** (2-3 sentences): What is this defect about?
2. **Business Impact**: How could this affect customers?
3. **Technical Details**: Key technical points to understand
4. **Recommended Actions**: What should customer success teams do?
5. **Priority Assessment**: How urgent is this for customer-facing teams?

Format your response clearly with these sections."""

            # Try to use CircuIT AI if available
            try:
                import os
                import anthropic
                
                circuit_api_key = os.environ.get('ANTHROPIC_API_KEY') or llm_api_key
                
                if circuit_api_key:
                    client = anthropic.Anthropic(api_key=circuit_api_key)
                    
                    message = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=2000,
                        temperature=0.3,
                        messages=[{
                            "role": "user",
                            "content": prompt
                        }]
                    )
                    
                    summary = (message.content[0].text if message.content and hasattr(message.content[0], 'text') else "Summary generation failed.")
                    logger.info(f"Generated LLM summary for defect {defect.defect_id}")
                    return summary
                else:
                    logger.warning("No LLM API key configured, returning structured summary")
                    return self._generate_structured_summary_defect(defect)
                    
            except Exception as e:
                logger.warning(f"LLM summary generation failed, falling back to structured summary: {e}")
                return self._generate_structured_summary_defect(defect)
                
        except Exception as e:
            logger.error(f"Error generating defect summary: {e}")
            return f"Error generating summary for defect {defect.defect_id}"
    
    def _generate_structured_summary_defect(self, defect: DefectInfo) -> str:
        """Generate a structured summary without LLM"""
        summary = f"""**BST DEFECT SUMMARY**

**Defect ID:** {defect.defect_id}
**Direct Link:** https://bst.cisco.com/bugsearch/bug/{defect.defect_id}

**Executive Summary:**
{defect.title}

**Details:**
- **Status:** {defect.status}
- **Severity:** {defect.severity}
- **Product:** {defect.product}
- **Component:** {defect.component}
- **Created:** {defect.created_date}
- **Last Updated:** {defect.modified_date}

**Description:**
{defect.description}

**Resolution Status:**
{defect.resolution or 'Not yet resolved - monitoring ongoing'}

**Classification:** {defect.classification.value}
**Verification:** {defect.verification_method}

**Recommended Actions:**
- Monitor defect status for updates
- Check if any customer deployments are affected
- Review with engineering if customer-impacting
- Track resolution timeline for customer communication
"""
        return summary
    
    def generate_llm_summary_for_vulnerability(self, vulnerability: PSIRTVulnerability, 
                                              llm_api_key: Optional[str] = None) -> str:
        """
        Generate LLM-powered summary for a PSIRT vulnerability
        
        Args:
            vulnerability: PSIRTVulnerability object
            llm_api_key: Optional API key for LLM service
            
        Returns:
            LLM-generated summary of the vulnerability
        """
        try:
            # Build the prompt for the LLM
            prompt = f"""Analyze this Cisco PSIRT security advisory and provide a comprehensive executive summary:

**Advisory ID:** {vulnerability.advisory_id}
**Title:** {vulnerability.title}
**Severity:** {vulnerability.severity}
**CVE IDs:** {', '.join(vulnerability.cve_ids) if vulnerability.cve_ids else 'None assigned'}
**Bug IDs:** {', '.join(vulnerability.bug_ids) if vulnerability.bug_ids else 'None listed'}
**Affected Products:** {', '.join(vulnerability.products) if vulnerability.products else 'See advisory'}
**Published:** {vulnerability.published_date}
**Last Updated:** {vulnerability.last_updated}

**Summary:**
{vulnerability.summary}

Please provide:
1. **Executive Summary** (2-3 sentences): What is this security vulnerability?
2. **Risk Assessment**: What is the actual risk to customers?
3. **Affected Systems**: Which products/versions are impacted?
4. **Mitigation Steps**: What actions should customers take immediately?
5. **Customer Communication**: Key points to communicate to affected customers
6. **Priority Level**: How urgent is this for customer success teams?

Format your response clearly with these sections."""

            # Try to use CircuIT AI if available
            try:
                import os
                import anthropic
                
                circuit_api_key = os.environ.get('ANTHROPIC_API_KEY') or llm_api_key
                
                if circuit_api_key:
                    client = anthropic.Anthropic(api_key=circuit_api_key)
                    
                    message = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=2500,
                        temperature=0.3,
                        messages=[{
                            "role": "user",
                            "content": prompt
                        }]
                    )
                    
                    summary = (message.content[0].text if message.content and hasattr(message.content[0], 'text') else "Summary generation failed.")
                    logger.info(f"Generated LLM summary for PSIRT advisory {vulnerability.advisory_id}")
                    return summary
                else:
                    logger.warning("No LLM API key configured, returning structured summary")
                    return self._generate_structured_summary_vulnerability(vulnerability)
                    
            except Exception as e:
                logger.warning(f"LLM summary generation failed, falling back to structured summary: {e}")
                return self._generate_structured_summary_vulnerability(vulnerability)
                
        except Exception as e:
            logger.error(f"Error generating vulnerability summary: {e}")
            return f"Error generating summary for advisory {vulnerability.advisory_id}"
    
    def _generate_structured_summary_vulnerability(self, vulnerability: PSIRTVulnerability) -> str:
        """Generate a structured summary without LLM"""
        cve_links = []
        for cve_id in vulnerability.cve_ids[:5]:
            cve_links.append(f"https://cve.mitre.org/cgi-bin/cvename.cgi?name={urllib.parse.quote(str(cve_id), safe='')}")
        
        summary = f"""**PSIRT SECURITY ADVISORY SUMMARY**

**Advisory ID:** {vulnerability.advisory_id}
**Direct Link:** {vulnerability.verification_method.split(' - ')[-1] if ' - ' in vulnerability.verification_method else f"https://tools.cisco.com/security/center/content/CiscoSecurityAdvisory/{vulnerability.advisory_id}"}

**Executive Summary:**
{vulnerability.title}

**Severity Level:** {vulnerability.severity}

**CVE References:**
{chr(10).join(f"- {cve_id}: {link}" for cve_id, link in zip(vulnerability.cve_ids[:5], cve_links)) if vulnerability.cve_ids else "No CVEs assigned"}

**Bug References:**
{chr(10).join(f"- {bug_id}: https://bst.cisco.com/bugsearch/bug/{bug_id}" for bug_id in vulnerability.bug_ids[:5]) if vulnerability.bug_ids else "No bug IDs listed"}

**Affected Products:**
{chr(10).join(f"- {product}" for product in vulnerability.products[:10]) if vulnerability.products else "See advisory for details"}

**Summary:**
{vulnerability.summary}

**Published:** {vulnerability.published_date}
**Last Updated:** {vulnerability.last_updated}

**Resources:**
- CVRF URL: {vulnerability.cvrf_url or 'Not available'}
- CSAF URL: {vulnerability.csaf_url or 'Not available'}

**Classification:** {vulnerability.classification.value}

**Recommended Actions:**
1. Review affected products against customer deployments
2. Identify customers running vulnerable versions
3. Communicate mitigation steps to affected customers
4. Track patching progress for critical customers
5. Escalate to engineering if customer-impacting
"""
        return summary
    
    
    def search_and_summarize_vulnerability(self, advisory_id: str, llm_api_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Search for a specific PSIRT advisory and generate comprehensive summary
        
        Args:
            advisory_id: PSIRT advisory ID (e.g., cisco-sa-20240101-webex)
            llm_api_key: Optional API key for LLM service
            
        Returns:
            Dictionary with advisory info and LLM summary
        """
        result = {
            'success': False,
            'advisory_id': advisory_id,
            'vulnerability': None,
            'summary': None,
            'direct_link': f"https://tools.cisco.com/security/center/content/CiscoSecurityAdvisory/{urllib.parse.quote(str(advisory_id), safe='')}",
            'error': None
        }
        
        try:
            # Search for the advisory
            logger.info(f"Searching for PSIRT advisory: {advisory_id}")
            vulnerability = self.get_psirt_vulnerability_by_id(advisory_id)
            
            if not vulnerability:
                # Try searching by keyword
                vulnerabilities = self.search_psirt_vulnerabilities([advisory_id])
                if vulnerabilities:
                    vulnerability = vulnerabilities[0]
            
            if vulnerability:
                result['vulnerability'] = vulnerability
                result['summary'] = self.generate_llm_summary_for_vulnerability(vulnerability, llm_api_key)
                result['success'] = True
                logger.info(f"Successfully found and summarized advisory {advisory_id}")
            else:
                result['error'] = f"Advisory {advisory_id} not found in PSIRT"
                logger.warning(f"Advisory {advisory_id} not found")
                
        except Exception as e:
            result['error'] = "An error occurred while searching for the advisory. Please try again."
            logger.error(f"Error in search_and_summarize_vulnerability: {e}", exc_info=True)
        
        return result

# Example usage and testing
def test_cisco_integrations():
    """Test function for Cisco internal integrations"""
    
    # Initialize with mock API keys (replace with actual keys in production)
    # Round 14 / Phase 4.4: this is a developer smoke-test entry point
    # with deliberately fake placeholder values.  Pin per-line noqa for
    # bandit S105/S106 so the false positives are documented.
    integrations = CiscoInternalIntegrations(
        circuit_api_key="mock_circuit_key",  # noqa: S106
        psirt_api_key="test-psirt-key-not-real",  # noqa: S106
        psirt_client_secret="test-psirt-secret-not-real",  # noqa: S106
    )

    search_terms = ["Webex", "meeting", "audio"]

    # Test Circuit search
    circuit_items = integrations.search_circuit_data(search_terms, days_back=30)
    
    print(f"Found {len(circuit_items)} Circuit items")
    for item in circuit_items[:3]:  # Show first 3
        print(f"Circuit {item.record_id}: {item.title} ({item.classification.value})")
    
    # Test PSIRT vulnerability search
    psirt_vulnerabilities = integrations.search_psirt_vulnerabilities(search_terms, days_back=30)
    
    print(f"Found {len(psirt_vulnerabilities)} PSIRT vulnerabilities")
    for vuln in psirt_vulnerabilities[:3]:  # Show first 3
        print(f"PSIRT {vuln.advisory_id}: {vuln.title} ({vuln.severity})")
    
    # Test comprehensive analysis
    analysis = integrations.get_comprehensive_defect_analysis(
        defect_ids=["CSC123456", "CSC789012"],
        search_terms=search_terms
    )
    
    print(f"Comprehensive analysis: {len(analysis['bst_defects'])} BST defects, {len(analysis['circuit_data'])} Circuit items, {len(analysis['psirt_vulnerabilities'])} PSIRT vulnerabilities")
    
    # Generate classification report
    report = integrations.generate_data_classification_report(analysis)
    print(report)

if __name__ == "__main__":
    test_cisco_integrations()
