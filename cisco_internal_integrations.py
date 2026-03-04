#!/usr/bin/env python3
"""
Cisco Internal Integrations Module
Integrates with BST (Bug Search Tool) and Circuit for enhanced defect and internal data
"""

import requests
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
from dataclasses import dataclass
from enum import Enum
import time
import re
import random
from bs4 import BeautifulSoup
import urllib.parse

logger = logging.getLogger(__name__)

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
    
    def __init__(self, bst_api_key: Optional[str] = None, circuit_api_key: Optional[str] = None, 
                 psirt_api_key: Optional[str] = None, psirt_client_secret: Optional[str] = None):
        """
        Initialize Cisco internal integrations
        
        Args:
            bst_api_key: API key for BST (Bug Search Tool)
            circuit_api_key: API key for Circuit
            psirt_api_key: API key for PSIRT openVuln API
            psirt_client_secret: Client secret for PSIRT openVuln API
        """
        self.bst_api_key = bst_api_key
        self.circuit_api_key = circuit_api_key
        self.psirt_api_key = psirt_api_key
        self.psirt_client_secret = psirt_client_secret
        
        # API endpoints
        self.bst_base_url = "https://bst.cisco.com/api/v1"
        self.circuit_base_url = "https://circuit.cisco.com/api/v1"
        self.psirt_base_url = "https://apix.cisco.com/security/advisories"  # PSIRT openVuln API (apix, not api)
        
        # Rate limiting
        self.bst_last_request = 0
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
        if system == 'bst':
            if current_time - self.bst_last_request < self.request_delay:
                time.sleep(self.request_delay - (current_time - self.bst_last_request))
            self.bst_last_request = time.time()
        elif system == 'circuit':
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
            if datetime.now() < self.psirt_token_expiry:
                return self.psirt_access_token
        
        # Need to get new token
        if not self.psirt_api_key or not self.psirt_client_secret:
            return None
        
        try:
            # OAuth 2.0 token endpoint for Cisco API
            token_url = "https://id.cisco.com/oauth2/default/v1/token"
            
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
                token_data = response.json()
                self.psirt_access_token = token_data.get('access_token')
                expires_in = token_data.get('expires_in', 3600)  # Default 1 hour
                self.psirt_token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)  # Refresh 1 min early
                logger.info(f"PSIRT OAuth token obtained, expires in {expires_in} seconds")
                return self.psirt_access_token
            else:
                logger.error(f"PSIRT OAuth token request failed: {response.status_code} - {response.text}")
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
        
        if system == 'bst' and self.bst_api_key:
            headers['Authorization'] = f'Bearer {self.bst_api_key}'
        elif system == 'circuit' and self.circuit_api_key:
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
    
    def search_defects_bst_web_scraping(self, search_terms: List[str], product_filter: Optional[str] = None, 
                                       days_back: int = 90) -> List[DefectInfo]:
        """
        Search for defects in BST using web scraping (requires Cisco authentication)
        Note: This method will likely fail without proper Cisco login credentials
        Falls back to mock data for testing purposes
        
        Args:
            search_terms: List of search terms
            product_filter: Optional product filter
            days_back: Number of days to look back
            
        Returns:
            List of DefectInfo objects
        """
        defects = []
        
        try:
            self._rate_limit('bst')
            
            # Build search query
            search_query = ' '.join(search_terms)
            if product_filter:
                search_query += f' {product_filter}'
            
            # Use the actual BST URL from the interface
            bst_base_url = "https://bst.cloudapps.cisco.com/bugsearch"
            
            logger.info(f"Web scraping BST for defects: {search_query}")
            
            # Enhanced headers to mimic a real browser session
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache'
            }
            
            # First, get the BST search page to understand the form structure
            try:
                logger.info(f"Accessing BST search page: {bst_base_url}")
                response = requests.get(bst_base_url, headers=headers, timeout=30, allow_redirects=True)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    # Look for the search form
                    search_form = soup.find('form') or soup.find('div', class_=re.compile(r'search', re.I))
                    
                    if search_form:
                        # Try to submit the search form with our query
                        defects_found = self._submit_bst_search_form(search_form, search_query, product_filter, headers)
                        if defects_found:
                            defects.extend(defects_found)
                            logger.info(f"Found {len(defects_found)} defects via BST form submission")
                        else:
                            # If form submission doesn't work, try direct URL with parameters
                            defects_found = self._try_bst_direct_search(bst_base_url, search_query, product_filter, headers)
                            if defects_found:
                                defects.extend(defects_found)
                                logger.info(f"Found {len(defects_found)} defects via BST direct search")
                    else:
                        # No form found, try direct search
                        defects_found = self._try_bst_direct_search(bst_base_url, search_query, product_filter, headers)
                        if defects_found:
                            defects.extend(defects_found)
                            logger.info(f"Found {len(defects_found)} defects via BST direct search")
                
                elif response.status_code == 401:
                    logger.error("BST requires Cisco authentication - web scraping not possible without login")
                    logger.error("BST integration requires: 1) Cisco VPN connection, 2) Valid credentials, 3) Session management")
                elif response.status_code == 403:
                    logger.error("BST access forbidden - may require VPN or specific permissions")
                elif response.status_code == 404:
                    logger.error("BST URL not found - interface may have changed")
                else:
                    logger.error(f"BST returned status code: {response.status_code}")
                    
            except Exception as e:
                logger.error(f"Error accessing BST: {e}")
            
            # If no defects found via web scraping, return empty list
            if not defects:
                logger.warning("No defects found via BST web scraping - authentication required")
            
            logger.info(f"Total defects found via BST web scraping: {len(defects)}")
                
        except Exception as e:
            logger.error(f"Error web scraping BST: {e}")
            
        return defects
    
    def _parse_bst_results(self, soup: BeautifulSoup, search_query: str, product_filter: Optional[str]) -> List[DefectInfo]:
        """
        Parse BST results using multiple strategies to handle different HTML structures
        """
        defects = []
        
        # Strategy 1: Look for table rows (common in defect tracking systems)
        table_rows = soup.find_all('tr')
        for row in table_rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 3:  # At least ID, title, status
                try:
                    defect = self._extract_defect_from_row(row, search_query, product_filter)
                    if defect:
                        defects.append(defect)
                except Exception as e:
                    logger.debug(f"Error parsing table row: {e}")
        
        # Strategy 2: Look for div-based defect items
        defect_divs = soup.find_all('div', class_=re.compile(r'(defect|bug|issue|item)', re.I))
        for div in defect_divs:
            try:
                defect = self._extract_defect_from_div(div, search_query, product_filter)
                if defect:
                    defects.append(defect)
            except Exception as e:
                logger.debug(f"Error parsing defect div: {e}")
        
        # Strategy 3: Look for list items
        list_items = soup.find_all('li', class_=re.compile(r'(defect|bug|issue)', re.I))
        for li in list_items:
            try:
                defect = self._extract_defect_from_list_item(li, search_query, product_filter)
                if defect:
                    defects.append(defect)
            except Exception as e:
                logger.debug(f"Error parsing list item: {e}")
        
        return defects
    
    def _extract_defect_from_row(self, row, search_query: str, product_filter: Optional[str]) -> Optional[DefectInfo]:
        """Extract defect information from a table row"""
        cells = row.find_all(['td', 'th'])
        if len(cells) < 2:
            return None
        
        # Try to find defect ID (usually first column or contains numbers)
        defect_id = ""
        title = ""
        status = "Unknown"
        severity = "Unknown"
        product = product_filter or "Unknown"
        
        for i, cell in enumerate(cells):
            text = cell.get_text(strip=True)
            
            # Look for defect ID (usually contains numbers and letters)
            if re.match(r'^[A-Z0-9\-_]+$', text) and len(text) > 3 and not defect_id:
                defect_id = text
            
            # Look for title (usually longer text, might be in links)
            elif len(text) > 10 and not title:
                title = text
                # Check if there's a link with more detailed title
                link = cell.find('a')
                if link and link.get_text(strip=True):
                    title = link.get_text(strip=True)
            
            # Look for status
            elif text.lower() in ['open', 'closed', 'resolved', 'fixed', 'new', 'assigned']:
                status = text
            
            # Look for severity
            elif text.lower() in ['critical', 'high', 'medium', 'low', 'minor', 'major']:
                severity = text
        
        if defect_id and title:
            classification = self._classify_data('bst', title)
            return DefectInfo(
                defect_id=defect_id,
                title=title,
                status=status,
                severity=severity,
                product=product,
                component='',
                description=title,
                resolution=None,
                created_date=datetime.now().strftime('%Y-%m-%d'),
                modified_date=datetime.now().strftime('%Y-%m-%d'),
                assignee='',
                classification=classification,
                source='BST Web Scraping',
                verification_method=f'BST Web Interface - Search: {search_query}'
            )
        
        return None
    
    def _extract_defect_from_div(self, div, search_query: str, product_filter: Optional[str]) -> Optional[DefectInfo]:
        """Extract defect information from a div element"""
        # Look for common defect ID patterns
        defect_id_match = re.search(r'([A-Z0-9\-_]{4,})', div.get_text())
        if not defect_id_match:
            return None
        
        defect_id = defect_id_match.group(1)
        
        # Look for title in various elements
        title_elem = div.find(['h1', 'h2', 'h3', 'h4', 'a', 'span'], class_=re.compile(r'(title|name|subject)', re.I))
        if not title_elem:
            title_elem = div.find('a') or div.find('span')
        
        title = title_elem.get_text(strip=True) if title_elem else f"Defect {defect_id}"
        
        classification = self._classify_data('bst', title)
        return DefectInfo(
            defect_id=defect_id,
            title=title,
            status='Unknown',
            severity='Unknown',
            product=product_filter or 'Unknown',
            component='',
            description=title,
            resolution=None,
            created_date=datetime.now().strftime('%Y-%m-%d'),
            modified_date=datetime.now().strftime('%Y-%m-%d'),
            assignee='',
            classification=classification,
            source='BST Web Scraping',
            verification_method=f'BST Web Interface - Search: {search_query}'
        )
    
    def _extract_defect_from_list_item(self, li, search_query: str, product_filter: Optional[str]) -> Optional[DefectInfo]:
        """Extract defect information from a list item"""
        return self._extract_defect_from_div(li, search_query, product_filter)
    
    
    def _submit_bst_search_form(self, form, search_query: str, product_filter: Optional[str], headers: dict) -> List[DefectInfo]:
        """
        Submit the BST search form with the provided query
        Based on the actual BST interface structure
        """
        defects = []
        
        try:
            # Find the form action URL
            form_action = form.get('action', '')
            if not form_action:
                # If no action, use the current page
                form_action = "https://bst.cloudapps.cisco.com/bugsearch"
            
            # Build form data based on the BST interface structure
            form_data = {}
            
            # Find the "Search For" field (main search input)
            search_input = form.find('input', {'name': re.compile(r'search|query|term', re.I)}) or \
                          form.find('input', {'id': re.compile(r'search|query|term', re.I)}) or \
                          form.find('input', {'placeholder': re.compile(r'search|examples', re.I)})
            
            if search_input:
                search_field_name = search_input.get('name', 'search')
                form_data[search_field_name] = search_query
            
            # Find the "Product" field
            product_input = form.find('input', {'name': re.compile(r'product', re.I)}) or \
                           form.find('input', {'id': re.compile(r'product', re.I)})
            
            if product_input and product_filter:
                product_field_name = product_input.get('name', 'product')
                form_data[product_field_name] = product_filter
            
            # Find any hidden fields (CSRF tokens, etc.)
            hidden_inputs = form.find_all('input', {'type': 'hidden'})
            for hidden_input in hidden_inputs:
                name = hidden_input.get('name')
                value = hidden_input.get('value', '')
                if name:
                    form_data[name] = value
            
            # Submit the form
            logger.info(f"Submitting BST form with data: {form_data}")
            
            response = requests.post(
                form_action,
                data=form_data,
                headers=headers,
                timeout=30,
                allow_redirects=True
            )
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                defects = self._parse_bst_results(soup, search_query, product_filter)
                logger.info(f"Form submission returned {len(defects)} defects")
            else:
                logger.warning(f"BST form submission failed with status: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error submitting BST form: {e}")
        
        return defects
    
    def _try_bst_direct_search(self, base_url: str, search_query: str, product_filter: Optional[str], headers: dict) -> List[DefectInfo]:
        """
        Try direct URL-based search as fallback
        """
        defects = []
        
        try:
            # Try different parameter combinations based on common search interfaces
            search_params = [
                {'search': search_query},
                {'q': search_query},
                {'query': search_query},
                {'term': search_query}
            ]
            
            if product_filter:
                for params in search_params:
                    params['product'] = product_filter
            
            for params in search_params:
                try:
                    logger.info(f"Trying BST direct search with params: {params}")
                    
                    response = requests.get(
                        base_url,
                        params=params,
                        headers=headers,
                        timeout=30,
                        allow_redirects=True
                    )
                    
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.content, 'html.parser')
                        found_defects = self._parse_bst_results(soup, search_query, product_filter)
                        
                        if found_defects:
                            defects.extend(found_defects)
                            logger.info(f"Direct search found {len(found_defects)} defects")
                            break  # Found results, no need to try other params
                    
                except Exception as e:
                    logger.debug(f"Direct search with params {params} failed: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in direct BST search: {e}")
        
        return defects
    
    def search_defects_bst(self, search_terms: List[str], product_filter: Optional[str] = None, 
                          days_back: int = 90) -> List[DefectInfo]:
        """
        Search for defects in BST (generates direct links for manual lookup)
        
        NOTE: Cisco BST API requires special Business Critical Services (BCS) access
        that is not available through standard API Console registration. 
        This method provides direct BST links for user lookup instead.
        
        Args:
            search_terms: List of search terms
            product_filter: Optional product filter (e.g., "Webex", "Cisco Unified Communications Manager")
            days_back: Number of days to look back
            
        Returns:
            List of DefectInfo objects (may be empty - use direct links instead)
        """
        logger.info("BST integration: Using direct link generation (BST API not publicly available)")
        
        # Note: BST API requires special BCS access not available in standard API Console
        # Provide direct links instead for user lookup
        defects = []
        
        # If API credentials are somehow configured, try them
        if self.bst_api_key and hasattr(self, 'bst_client_secret'):
            logger.info("BST API credentials detected, attempting API call...")
            defects = self._search_defects_bst_official_api(search_terms, product_filter, days_back)
        
        # If no defects and user wants to try web scraping (requires VPN + auth)
        if not defects and os.environ.get('BST_ENABLE_WEB_SCRAPING') == 'true':
            logger.info("Web scraping enabled - attempting BST web scraping (requires Cisco VPN)")
            defects = self.search_defects_bst_web_scraping(search_terms, product_filter, days_back)
        
        if not defects:
            logger.info("BST: No defects retrieved - this is expected. Use direct links for manual lookup.")
            logger.info("BST direct link format: https://bst.cisco.com/bugsearch/bug/{defect_id}")
        
        return defects

    def _search_defects_bst_official_api(self, search_terms: List[str], product_filter: Optional[str] = None, 
                                        days_back: int = 90) -> List[DefectInfo]:
        """
        Search for defects using the Cisco Bug API (requires special BCS access)
        
        NOTE: Cisco BST API requires Business Critical Services (BCS) access
        which is not available through standard apiconsole.cisco.com registration.
        Contact your Cisco TAM/SE for special access if needed.
        
        Args:
            search_terms: List of search terms
            product_filter: Optional product filter
            days_back: Number of days to look back
            
        Returns:
            List of DefectInfo objects from the official API (typically empty)
        """
        defects = []
        
        try:
            # Check if we have API credentials
            if not self.bst_api_key or not hasattr(self, 'bst_client_secret'):
                logger.info("BST API: Using direct link generation (API requires special BCS access)")
                return defects
            
            # Get OAuth2 access token
            access_token = self._get_bst_oauth_token()
            if not access_token:
                logger.error("Failed to obtain OAuth2 access token for BST API")
                return defects
            
            # Build search query from terms
            search_query = " ".join(search_terms)
            
            # Determine API endpoint based on available parameters
            if product_filter:
                safe_product = urllib.parse.quote(product_filter, safe='')
                url = f"https://apix.cisco.com/bug/v2.0/bugs/product_name/{safe_product}"
                params = {
                    'keyword': search_query,
                    'modified_date': self._get_modified_date_param(days_back),
                    'page_index': 1
                }
            else:
                # Use keyword search
                url = "https://apix.cisco.com/bug/v2.0/bugs/keyword"
                params = {
                    'keyword': search_query,
                    'modified_date': self._get_modified_date_param(days_back),
                    'page_index': 1
                }
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            
            logger.info(f"Searching BST API: {url} with query: {search_query}")
            
            response = requests.get(url, params=params, headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                defects = self._parse_bst_api_response(data, search_terms, product_filter)
                logger.info(f"BST API returned {len(defects)} defects")
            elif response.status_code == 403:
                logger.error("BST API access forbidden - check API credentials and permissions")
            elif response.status_code == 401:
                logger.error("BST API authentication failed - invalid or expired token")
            else:
                logger.error(f"BST API returned status {response.status_code}: {response.text}")
                
        except Exception as e:
            logger.error(f"Error accessing BST official API: {e}")
        
        return defects
    
    def _get_bst_oauth_token(self) -> Optional[str]:
        """
        Get OAuth2 access token for BST API
        
        Returns:
            Access token string or None if failed
        """
        try:
            if not self.bst_api_key or not hasattr(self, 'bst_client_secret'):
                logger.error("BST OAuth2 credentials not configured")
                return None
            
            token_url = "https://id.cisco.com/oauth2/default/v1/token"
            
            data = {
                'grant_type': 'client_credentials',
                'client_id': self.bst_api_key,
                'client_secret': getattr(self, 'bst_client_secret', ''),
                'scope': 'bst'
            }
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            response = requests.post(token_url, data=data, headers=headers, timeout=30)
            
            if response.status_code == 200:
                token_data = response.json()
                return token_data.get('access_token')
            else:
                logger.error(f"OAuth2 token request failed: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting BST OAuth2 token: {e}")
            return None
    
    def _get_modified_date_param(self, days_back: int) -> str:
        """
        Convert days_back to BST API modified_date parameter
        
        Args:
            days_back: Number of days to look back
            
        Returns:
            BST API modified_date parameter value
        """
        if days_back <= 7:
            return "1"  # Last Week
        elif days_back <= 30:
            return "2"  # Last 30 Days
        elif days_back <= 180:
            return "3"  # Last 6 Months
        elif days_back <= 365:
            return "4"  # Last Year
        else:
            return "5"  # All
    
    def _parse_bst_api_response(self, api_data: dict, search_terms: List[str], product_filter: Optional[str]) -> List[DefectInfo]:
        """
        Parse BST API response into DefectInfo objects
        
        Args:
            api_data: JSON response from BST API
            search_terms: Original search terms
            product_filter: Original product filter
            
        Returns:
            List of DefectInfo objects
        """
        defects = []
        
        try:
            bugs = api_data.get('bugs', [])
            
            for bug in bugs:
                # Map API response to DefectInfo
                defect = DefectInfo(
                    defect_id=bug.get('bug_id', ''),
                    title=bug.get('headline', ''),
                    status=self._map_bst_status(bug.get('status', '')),
                    severity=self._map_bst_severity(bug.get('severity', '')),
                    product=bug.get('product', product_filter or 'Unknown'),
                    component=bug.get('product', ''),
                    description=bug.get('description', ''),
                    resolution=bug.get('known_fixed_releases', ''),
                    created_date=bug.get('created_date', ''),
                    modified_date=bug.get('last_modified_date', ''),
                    assignee='',  # Not available in API response
                    classification=self._classify_data('bst', bug.get('headline', '')),
                    source='Cisco Bug API (Official)',
                    verification_method=f"BST API Bug ID: {bug.get('bug_id', '')} - https://bst.cisco.com/bugsearch/bug/{bug.get('bug_id', '')}"
                )
                defects.append(defect)
                
        except Exception as e:
            logger.error(f"Error parsing BST API response: {e}")
        
        return defects
    
    def _map_bst_status(self, status: str) -> str:
        """Map BST API status codes to readable status"""
        status_map = {
            'O': 'Open',
            'F': 'Fixed',
            'T': 'Terminated'
        }
        return status_map.get(status, status)
    
    def _map_bst_severity(self, severity: str) -> str:
        """Map BST API severity codes to readable severity"""
        severity_map = {
            '1': 'Severity 1 (High)',
            '2': 'Severity 2',
            '3': 'Severity 3',
            '4': 'Severity 4',
            '5': 'Severity 5',
            '6': 'Severity 6 (Low)'
        }
        return severity_map.get(severity, f'Severity {severity}')

    def _search_defects_bst_api(self, search_terms: List[str], product_filter: Optional[str] = None, 
                               days_back: int = 90) -> List[DefectInfo]:
        """
        Legacy API method for BST search (kept for compatibility)
        """
        defects = []
        
        try:
            self._rate_limit('bst')
            
            # Calculate date range
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)
            
            # Build search query
            search_query = ' OR '.join(search_terms)
            if product_filter:
                search_query += f' AND product:"{product_filter}"'
            
            params = {
                'q': search_query,
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': end_date.strftime('%Y-%m-%d'),
                'limit': 100,
                'fields': 'id,title,status,severity,product,component,description,resolution,created_date,modified_date,assignee'
            }
            
            headers = self._get_headers('bst')
            
            logger.info(f"Searching BST API for defects: {search_query}")
            
            response = requests.get(
                f"{self.bst_base_url}/defects/search",
                params=params,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
                for defect_data in data.get('defects', []):
                    # Classify the data
                    classification = self._classify_data('bst', defect_data.get('description', ''))
                    
                    defect = DefectInfo(
                        defect_id=defect_data.get('id', ''),
                        title=defect_data.get('title', ''),
                        status=defect_data.get('status', ''),
                        severity=defect_data.get('severity', ''),
                        product=defect_data.get('product', ''),
                        component=defect_data.get('component', ''),
                        description=defect_data.get('description', ''),
                        resolution=defect_data.get('resolution'),
                        created_date=defect_data.get('created_date', ''),
                        modified_date=defect_data.get('modified_date', ''),
                        assignee=defect_data.get('assignee', ''),
                        classification=classification,
                        source='BST API',
                        verification_method=f'Search BST with ID: {defect_data.get("id", "")}'
                    )
                    
                    defects.append(defect)
                
                logger.info(f"Found {len(defects)} defects in BST API")
                
            else:
                logger.error(f"BST API error: {response.status_code} - {response.text}")
                
        except Exception as e:
            logger.error(f"Error searching BST API: {e}")
        
        return defects
    
    def get_defect_details_bst(self, defect_id: str) -> Optional[DefectInfo]:
        """
        Get detailed information for a specific defect from BST
        
        Args:
            defect_id: BST defect ID
            
        Returns:
            DefectInfo object or None
        """
        try:
            self._rate_limit('bst')
            
            headers = self._get_headers('bst')
            
            logger.info(f"Fetching defect details from BST: {defect_id}")
            
            response = requests.get(
                f"{self.bst_base_url}/defects/{defect_id}",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                defect_data = response.json()
                
                # Classify the data
                classification = self._classify_data('bst', defect_data.get('description', ''))
                
                defect = DefectInfo(
                    defect_id=defect_data.get('id', ''),
                    title=defect_data.get('title', ''),
                    status=defect_data.get('status', ''),
                    severity=defect_data.get('severity', ''),
                    product=defect_data.get('product', ''),
                    component=defect_data.get('component', ''),
                    description=defect_data.get('description', ''),
                    resolution=defect_data.get('resolution'),
                    created_date=defect_data.get('created_date', ''),
                    modified_date=defect_data.get('modified_date', ''),
                    assignee=defect_data.get('assignee', ''),
                    classification=classification,
                    source='BST (Bug Search Tool)',
                    verification_method=f'Direct BST lookup with ID: {defect_id}'
                )
                
                logger.info(f"Retrieved defect details for {defect_id}")
                return defect
                
            else:
                logger.error(f"BST API error for defect {defect_id}: {response.status_code} - {response.text}")
                
        except Exception as e:
            logger.error(f"Error fetching defect details from BST: {e}")
        
        return None
    
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
            
            # Calculate date range
            end_date = datetime.now()
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
            
            logger.info(f"Searching Circuit for data: {search_query}")
            
            response = requests.get(
                f"{self.circuit_base_url}/search",
                params=params,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
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
                logger.error(f"Circuit API error: {response.status_code} - {response.text}")
                
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
            if 'cve' in params:
                endpoint = f"{self.psirt_base_url}/v2/cve/{params['cve']}"
                response = requests.get(endpoint, headers=headers, timeout=30)
            elif 'bug_id' in params:
                endpoint = f"{self.psirt_base_url}/v2/bug_id/{params['bug_id']}"
                response = requests.get(endpoint, headers=headers, timeout=30)
            else:
                # Use /v2/all endpoint for general searches
                endpoint = f"{self.psirt_base_url}/v2/all"
                response = requests.get(endpoint, params=params, headers=headers, timeout=30)
            
            logger.info(f"PSIRT API request: {endpoint}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"PSIRT API response type: {type(data)}, keys: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}")
                vulnerabilities = self._parse_psirt_response(data, search_terms, product_filter)
                logger.info(f"PSIRT API returned {len(vulnerabilities)} vulnerabilities")
            elif response.status_code == 403:
                logger.error("PSIRT API access forbidden - check API credentials and permissions")
            elif response.status_code == 401:
                logger.error("PSIRT API authentication failed - invalid or expired token")
            else:
                logger.error(f"PSIRT API returned status {response.status_code}: {response.text}")
                
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
                data = response.json()
                vulnerability = self._parse_single_psirt_response(data, advisory_id)
                logger.info(f"Retrieved PSIRT advisory details for {advisory_id}")
                return vulnerability
            else:
                logger.error(f"PSIRT API error for advisory {advisory_id}: {response.status_code} - {response.text}")
                
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
            
            # Extract CVE IDs
            cve_ids = []
            if 'cves' in advisory:
                cve_ids = [cve.get('cve_id', '') for cve in advisory['cves']]
            
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
        Get comprehensive defect analysis from BST, Circuit, and PSIRT
        
        Args:
            defect_ids: List of specific defect IDs to look up
            search_terms: General search terms for related defects
            
        Returns:
            Comprehensive analysis dictionary
        """
        analysis = {
            'bst_defects': [],
            'circuit_data': [],
            'psirt_vulnerabilities': [],
            'classification_summary': {},
            'data_sources': [],
            'verification_methods': [],
            'timestamp': datetime.now().isoformat()
        }
        
        # Get specific defect details from BST
        for defect_id in defect_ids:
            defect = self.get_defect_details_bst(defect_id)
            if defect:
                analysis['bst_defects'].append(defect)
        
        # Search for related defects in BST
        if search_terms:
            related_defects = self.search_defects_bst(search_terms)
            analysis['bst_defects'].extend(related_defects)
        
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
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
    
    def search_and_summarize_defect(self, defect_id: str, llm_api_key: Optional[str] = None) -> Dict[str, Any]:  # noqa: C901
        """
        Search for a specific BST defect and generate comprehensive summary
        
        Args:
            defect_id: BST defect ID (e.g., CSCdr72939)
            llm_api_key: Optional API key for LLM service
            
        Returns:
            Dictionary with defect info and LLM summary
        """
        result = {
            'success': False,
            'defect_id': defect_id,
            'defect': None,
            'summary': None,
            'direct_link': f"https://bst.cisco.com/bugsearch/bug/{urllib.parse.quote(str(defect_id), safe='')}",
            'error': None
        }
        
        try:
            # Search for the defect
            logger.info(f"Searching for BST defect: {defect_id}")
            defect = self.get_defect_details_bst(defect_id)
            
            if not defect:
                # Try searching by keyword
                defects = self.search_defects_bst([defect_id])
                if defects:
                    defect = defects[0]
            
            if defect:
                result['defect'] = defect
                result['summary'] = self.generate_llm_summary_for_defect(defect, llm_api_key)
                result['success'] = True
                logger.info(f"Successfully found and summarized defect {defect_id}")
            else:
                result['error'] = f"Defect {defect_id} not found in BST"
                logger.warning(f"Defect {defect_id} not found")
                
        except Exception as e:
            result['error'] = f"Error searching for defect: {str(e)}"
            logger.error(f"Error in search_and_summarize_defect: {e}", exc_info=True)
        
        return result
    
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
            result['error'] = f"Error searching for advisory: {str(e)}"
            logger.error(f"Error in search_and_summarize_vulnerability: {e}", exc_info=True)
        
        return result

# Example usage and testing
def test_cisco_integrations():
    """Test function for Cisco internal integrations"""
    
    # Initialize with mock API keys (replace with actual keys in production)
    integrations = CiscoInternalIntegrations(
        bst_api_key="mock_bst_key",
        circuit_api_key="mock_circuit_key",
        psirt_api_key="YOUR_PSIRT_API_KEY",
        psirt_client_secret="YOUR_PSIRT_CLIENT_SECRET"
    )
    
    # Test defect search
    search_terms = ["Webex", "meeting", "audio"]
    defects = integrations.search_defects_bst(search_terms, product_filter="Webex", days_back=30)
    
    print(f"Found {len(defects)} defects")
    for defect in defects[:3]:  # Show first 3
        print(f"Defect {defect.defect_id}: {defect.title} ({defect.classification.value})")
    
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
