"""Shared normalization helpers for AdoptIQ report consistency."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd

_logger = logging.getLogger(__name__)

CUSTOMER_ALIASES_DEFAULTS_FILENAME = "customer_aliases.defaults.json"
CUSTOMER_ALIASES_USER_FILENAME = "customer_aliases.json"

# Canonical status buckets
# Round 4 / Phase 3.4: extend with the in-progress / waiting phrases
# that appear in real CSOne exports.  Without these patterns the
# corresponding rows fall through to ``Unknown`` and
# ``cm.count_open_tac`` undercounts the open backlog (open TAC and
# aging look healthier than reality).
OPEN_STATUS_PATTERNS = (
    r"\bopen\b",
    r"\bnew\b",
    r"\bin\s*progress\b",
    r"\bworking\b",
    r"\breopened?\b",
    r"\bpending\b",
    r"\bwaiting\s+on\s+customer\b",
    r"\bawaiting\s+customer\b",
    r"\bwaiting\s+on\s+(engineering|support|3rd|third)[^\b]*\b",
    r"\bawaiting\s+(engineering|support|3rd|third|response|info|information)\b",
    r"\bcustomer\s+(action|response|update|input)\b",
    r"\binvestigat(?:e|ing|ion)\b",
    r"\bmonitor(?:ing)?\b",
    r"\bidentified\b",
    r"\bassigned\b",
    r"\bactive\b",
    r"\bblocked\b",
    r"\bwaiting\b",
    r"\bnot\s+started\b",
    r"\bdraft\b",
    r"\bplanned\b",
    r"\bscheduled\b",
    r"\bto\s*do\b",
    r"\bon\s*hold\b",
    r"\bdeferred\b",
    r"\bon\s*track\b",
    r"\boff\s*(?:track|trajectory)\b",
    r"\bat\s+risk\b",
)
CLOSED_STATUS_PATTERNS = (
    r"\bclosed?\b",
    r"\bresolved?\b",
    r"\bcomplete(d)?\b",
    r"\bdone\b",
    r"\bcancelled?\b",
)

# Case type classification heuristics
PROVISIONING_PATTERNS = (
    r"\bprovision(ing|ed)?\b",
    r"\benable(ment|d)?\b",
    r"\bturn\s*on\b",
    r"\bfeature\s*request\b",
    r"\blicense\b",
    r"\bentitlement\b",
    r"\bonboard(ing)?\b",
    r"\bsetup\b",
    r"\bconfig(uration|ure)?\b",
)
BREAK_FIX_PATTERNS = (
    r"\bbreak[\s-]*fix\b",
    r"\bincident\b",
    r"\boutage\b",
    r"\berror\b",
    r"\bfail(ure|ed|ing)?\b",
    r"\bdefect\b",
    r"\bbug\b",
    r"\bcrash\b",
    r"\bdegrad(ed|ation)?\b",
    r"\bescalat(e|ion)\b",
    r"\btechnical\b",
    r"\bsev[ -]?[1-2]\b",
)

BEMS_ID_PATTERN = re.compile(r"\bBEMS[- ]?\d+\b|\bBEMS\b", re.IGNORECASE)

LIKELY_OPEN_DATE_COLS = (
    "Date/Time Opened",
    "Created",
    "Created Date",
    "OPEN_DATE",
    "OPEN_DATE_C",
    "CREATED_DATE",
    "CREATED_DATE_C",
    "CREATEDDATE",
)
LIKELY_CLOSED_DATE_COLS = (
    "Date/Time Closed",
    "Closed",
    "Closed Date",
    "CLOSED_DATE",
    "CLOSED_DATE_C",
    "RESOLVED_DATE",
    "RESOLVED_DATE_C",
)
LIKELY_STATUS_COLS = (
    "Case Status",
    "Status",
    "STATUS",
    "STATUS_C",
    "AB_STATUS_C",
    "state",
)
LIKELY_PRIORITY_COLS = (
    "Severity",
    "SEVERITY",
    "SEVERITY_C",
    "Highest Priority",
    "Priority",
    "PRIORITY",
    "PRIORITY_C",
)
LIKELY_CASE_TYPE_COLS = (
    "Case Type",
    "CASE_TYPE",
    "CASE_TYPE_C",
    "Request Type",
    "REQUEST_TYPE",
    "REQUEST_TYPE_C",
    "Category",
    "CATEGORY",
    "CATEGORY_C",
)
LIKELY_CUSTOMER_COLS = (
    "customer_name",
    "Customer Name",
    "BU_NAME",
    "CUSTOMER_NAME",
    "Customer",
    "ACCOUNT_NAME",
)
LIKELY_ACCOUNT_ID_COLS = (
    "ACCOUNT_ID_C",
    "ACCOUNT__C",
    "ACCOUNT_ID",
)

# Logical-record identifiers used by the shared customer partitioner.  Source-
# specific canonicalizers pass narrower lists; this union lets generic report
# paths quarantine an ID that appears under more than one customer before they
# split the frame and lose the ownership conflict.
LIKELY_LOGICAL_RECORD_ID_COLS = (
    "Case #",
    "SR Number",
    "SR_NUMBER",
    "Case Number",
    "CASE_NUMBER",
    "CaseNumber",
    "CASE_ID",
    "TAC_CASE_ID",
    "PULSE_ID",
    "CUSTOMER_PULSE_ID",
    "CUSTOMER_PULSE_ID_C",
    "AP_ID",
    "ACTION_PLAN_ID",
    "PLAN_ID",
    "SUBSCRIPTION_ID",
    "SUBSCRIPTION_ID_C",
    "SUBSCRIPTION_ID__C",
    "SUBSCRIPTION_REFERENCE_ID",
    "ID",
)

# Shared account-column candidates for cross-source parity/filtering.
ACCOUNT_COLUMN_CANDIDATES = (
    "ACCOUNT_ID_C",
    "ACCOUNT__C",
    "DSM_ACCOUNT_ID_C",
    "ACCOUNT_ID",
    "ACCOUNT",
    "ACCOUNTID",
    "AccountId",
    "Account ID",
    "Account Id",
)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    if text.lower() in {"", "none", "nan", "null"}:
        return ""
    return text


_MISSING_IDENTIFIER_SENTINELS = frozenset(
    {
        "",
        "-",
        "--",
        "<na>",
        "n/a",
        "n.a.",
        "na",
        "nan",
        "nat",
        "none",
        "null",
        "unknown",
        "undefined",
        "missing",
        "not available",
        "not applicable",
    }
)


def _schema_alias_key(value: Any) -> str:
    """Return a punctuation-insensitive, case-insensitive schema key.

    Snowflake, Salesforce, CSV, and curated exports spell the same logical
    field as, for example, ``SR_NUMBER``, ``SR Number``, and ``sr_number``.
    Schema matching must recognize those variants without requiring every
    possible casing to be repeated in each source-specific alias tuple.
    """

    try:
        text = unicodedata.normalize("NFKC", str(value))
    except Exception:
        return ""
    text = text.replace("#", " number ")
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def matching_schema_columns(
    columns: Iterable[Any], aliases: Sequence[str]
) -> List[Any]:
    """Find frame columns matching *aliases* in deterministic alias order."""

    by_key: Dict[str, List[Any]] = {}
    for column in columns:
        key = _schema_alias_key(column)
        if key:
            by_key.setdefault(key, []).append(column)

    matched: List[Any] = []
    seen: Set[Any] = set()
    for alias in aliases:
        for column in by_key.get(_schema_alias_key(alias), []):
            try:
                already_seen = column in seen
            except TypeError:
                already_seen = column in matched
            if already_seen:
                continue
            matched.append(column)
            try:
                seen.add(column)
            except TypeError:
                pass
    return matched


def clean_logical_record_id(value: Any) -> str:
    """Normalize a logical record ID while treating text nulls as blank."""

    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    try:
        text = unicodedata.normalize("NFKC", str(value)).strip()
    except Exception:
        return ""
    if text.casefold() in _MISSING_IDENTIFIER_SENTINELS:
        return ""
    return text


def coalesce_logical_record_ids(
    df: pd.DataFrame,
    candidates: Sequence[str],
) -> Tuple[pd.Series, List[Any]]:
    """Return row-wise logical IDs and the case-insensitive columns examined.

    The returned Series always has a unique positional RangeIndex.  This is
    intentional: source frames frequently retain duplicate labels after joins,
    and label-indexed assignment can otherwise select multiple rows or raise an
    ambiguous-truth exception.
    """

    identifiers = pd.Series([""] * len(df), dtype=str)
    matched_columns = matching_schema_columns(df.columns, candidates)
    for column in matched_columns:
        values = df[column].map(clean_logical_record_id).reset_index(drop=True)
        usable = identifiers.eq("") & values.ne("")
        if usable.any():
            identifiers.loc[usable] = values.loc[usable]
    return identifiers, matched_columns


def _clean_name_for_key(name: str) -> str:
    text = _clean_text(name)
    if not text:
        return ""
    # Round 13 / Phase 3.15: collapse compatibility variants (NFKC) and
    # case-fold (not just lowercase) before further canonicalization.
    # Without this, the join key for "ACME co." and "AcmE\u00A0co" --
    # which include a non-breaking space and a German "ß" -- mismatched
    # the corresponding "Acme Co" rows in another source, producing
    # double-counted customers in cross-source merges and BEMS lookups.
    try:
        text = unicodedata.normalize("NFKC", text)
    except Exception:
        pass
    lowered = text.casefold()
    if not lowered:
        return ""
    lowered = re.sub(r"\s+", " ", lowered).strip()
    lowered = re.sub(r"[,.\-_/]+", " ", lowered)
    lowered = re.sub(
        r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|co|company|plc)\b",
        "",
        lowered,
    )
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def normalize_customer_name(value: Any) -> str:
    """Normalize customer name for display and joins."""
    text = _clean_text(value)
    if not text:
        return "Unknown"
    # Round 13 / Phase 3.15: collapse compatibility variants (NFKC) so
    # display strings like "Acme\u00A0Co" (non-breaking space) and
    # "Acme Co" present identically in the report and in groupby /
    # nunique cardinality counts.  Note: we deliberately do NOT lower
    # or case-fold here; that's reserved for the join key
    # (``_clean_name_for_key``) so display preserves the original
    # capitalization the customer recognizes.
    try:
        text = unicodedata.normalize("NFKC", text)
    except Exception:
        pass
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_for_display(value: Any) -> str:
    """Round 39 / Phase 4.4 -- prepare a customer or account name for
    rendering in customer-facing reports.

    The Snowflake ``BU_ACCOUNT_NAME`` field sometimes carries
    multi-segment account labels separated with ``__`` (a CSOne /
    Salesforce convention used to encode parent/child or jurisdiction
    boundaries).  When those raw strings flowed into the leader and
    executive reports they printed as e.g.
    ``"TRIBUNAL...__GOBIERNO...__MX"`` -- a reader couldn't tell
    whether the underscores were significant or noise.

    This helper:

    1. Runs the value through :func:`normalize_customer_name` so it
       inherits the same NFKC + whitespace collapse policy.
    2. Replaces any run of ``_`` characters of length >= 2 with
       ``", "`` so the segments read as a list a human can parse.
    3. Collapses any leading / trailing comma noise that might appear
       if the upstream string started or ended with the separator.

    Single underscores (e.g. ``"Acme_Subsidiary"``) are preserved on
    purpose -- those occur in real customer names and the Round 39
    audit only flagged the ``__`` (double-underscore) case.
    """
    text = normalize_customer_name(value)
    if not text or text == "Unknown":
        return text
    text = re.sub(r"_{2,}", ", ", text)
    text = re.sub(r"\s*,\s*,\s*", ", ", text)
    text = text.strip().strip(",").strip()
    return text or "Unknown"


# Round 125 / B4: SSoT for collapsing merged Snowflake composite-key
# strings to a human-readable customer name.  Previously lived only in
# ``app_simple._normalize_composite_customer_key`` (Round 49); the Build 93
# audit showed the compact high-risk DOCX table (built in
# ``executive_intelligence_formatter``) still leaked the raw join key
# ``ELEVANCE_ELEVANCE HEALTH_US`` because that module had no access to the
# helper.  Promoting it here lets both consumers share one implementation.
_COMPOSITE_COUNTRY_TAIL_RE = re.compile(r"_([A-Z]{2,3})\s*$")


def normalize_composite_customer_key(name: Any) -> str:
    """Collapse a Snowflake composite key to its display name.

    Behaviour (display-only -- never feed back into joins / prompts):

      * ``X__Y__US``  -> ``X``  (split on double-underscore, keep first)
      * ``X__Y``      -> ``X``
      * ``X_Y_US``    -> ``X_Y``  (strip only a trailing 2-3 letter country
        tail; preserve internal single underscores)
      * ``ELEVANCE_ELEVANCE HEALTH_US`` -> ``ELEVANCE HEALTH`` (when the
        first segment is a case-insensitive prefix of the second, keep the
        longer human-readable second segment)
      * ``Plain Customer Name`` -> unchanged
      * idempotent on its own output; ``None`` / empty -> ``""``
    """
    if name is None:
        return ""
    try:
        s = str(name)
    except Exception:
        return ""
    s = s.strip()
    if not s:
        return s
    if "__" in s:
        first = s.split("__", 1)[0].strip()
        if first:
            return first
    if "_" in s:
        m = _COMPOSITE_COUNTRY_TAIL_RE.search(s)
        if m:
            stripped = s[: m.start()].strip()
            if stripped:
                if "_" in stripped:
                    parts = stripped.split("_")
                    if len(parts) == 2:
                        a, b = parts[0].strip(), parts[1].strip()
                        if a and b and (
                            b.lower().startswith(a.lower())
                            or a.lower().startswith(b.lower())
                        ):
                            return b if len(b) >= len(a) else a
                return stripped
    return s


# ---------------------------------------------------------------------------
# Round 126 / Build 95 (B1): shared, kind-aware partial-data-banner classifier.
#
# Pre-R126 the "scope exclusion vs load failure" decision was DUPLICATED inline
# at every banner-rendering site (the comprehensive ExecutiveReportBuilder, the
# compact + renewal Word paths in app_simple, the leader generator, the compact
# formatter, the executive-intelligence formatter).  Each copy carried its own
# ``_rXXX_scope_kinds`` set and ``all(... in kinds or startswith(...))`` test,
# so the set drifted: R124 had to "port" R112's branch into the comprehensive
# builder, and R125 then had to add ``tech_filter_empty_after_scope`` to TWO
# separate copies.  Three of the six sites were never made kind-aware at all
# (leader / compact formatter / exec-intel formatter), so a pure-scope warning
# rendered the misleading "failed to load" wording in those reports.
#
# ``PARTIAL_DATA_SCOPE_EXCLUSION_KINDS`` + ``partial_data_warnings_all_scope``
# are now the SSoT: every banner site classifies through this one function so
# the kind set can never drift again, and adding a new scope-exclusion kind is
# a single-line edit here.  ``partial_data_banner_preamble`` returns the
# canonical preamble text (scope vs load) so the non-kind-aware sites can adopt
# the correct wording without re-deriving it.
# ---------------------------------------------------------------------------
PARTIAL_DATA_SCOPE_EXCLUSION_KINDS = frozenset({
    "tech_filter_scope_excluded",
    # AB set empty AFTER the technology scope filter runs (All-Managers Webex
    # comprehensive / compact) -- a scope decision, not a load failure.  It
    # does not match the ``startswith('tech_filter_scope')`` fallback, so it
    # is named explicitly (R125 / A3 + B3).
    "tech_filter_empty_after_scope",
    "manager_filter_scope_excluded",
    "time_window_scope_excluded",
    "no_onedrive_sync",
    "autodiscovered_empty_after_scope",
})


def partial_data_warnings_all_scope(partial_data_warnings: Any) -> bool:
    """Return True when EVERY partial-data warning is a scope exclusion.

    A scope exclusion means the data loaded successfully but was filtered out
    by the requested technology / manager / time-window scope -- NOT an upstream
    load failure.  An empty / falsy input returns False (no banner is rendered
    by the callers in that case).  Mixed (scope + load) sets return False so the
    generic "failed to load" preamble wins, mirroring the pre-R126 per-site
    behaviour and the ``test_round124_comp_banner_scope`` mixed-warning pin.
    """
    if not partial_data_warnings:
        return False
    try:
        items = list(partial_data_warnings)
    except TypeError:
        return False
    if not items:
        return False
    for w in items:
        kind = str((w or {}).get("kind") or "") if isinstance(w, dict) else ""
        if kind in PARTIAL_DATA_SCOPE_EXCLUSION_KINDS:
            continue
        if kind.startswith("tech_filter_scope"):
            continue
        return False
    return True


def partial_data_banner_preamble(
    partial_data_warnings: Any,
    *,
    report_label: str = "this run",
    mention_excel: bool = False,
) -> str:
    """Return the canonical kind-aware banner preamble.

    ``report_label`` lets a caller name the report ("this comprehensive report
    run", "this run") so the wording reads naturally; ``mention_excel`` appends
    the "The Excel workbook lists the same warnings..." sentence used by the
    XLSX-bearing formats.  The scope branch contains the substring
    "filtered out by the requested scope"; the load branch contains
    "failed to load" -- both pinned across the per-format banner tests.
    """
    excel = (
        "  The Excel workbook lists the same warnings in its Report_Info / "
        "Partial_Data_Warning_Count cells."
        if mention_excel
        else ""
    )
    if partial_data_warnings_all_scope(partial_data_warnings):
        return (
            "One or more upstream data sources returned data that was "
            "filtered out by the requested scope (technology filter, manager "
            "filter, or time window). The data loaded successfully; the "
            "bulleted list below names what was excluded and why. Sections "
            "affected by the filter are reduced rather than missing." + excel
        )
    return (
        "One or more upstream data sources failed to load for "
        f"{report_label}. Sections that depend on the affected sources are "
        'marked "unavailable" rather than rendered as zero. Rerun once the '
        "source is reachable for a complete picture." + excel
    )


# ---------------------------------------------------------------------------
# Round 132 / Build 102: customer alias registry (SSoT)
# ---------------------------------------------------------------------------

@dataclass
class CustomerAliasRegistry:
    """Maps synonymous customer name strings to one canonical display name."""

    group_id_to_aliases: Dict[str, List[str]] = field(default_factory=dict)
    name_key_to_group_id: Dict[str, str] = field(default_factory=dict)

    def has_groups(self) -> bool:
        return bool(self.group_id_to_aliases)

    def group_id_for_name(self, raw: Any) -> Optional[str]:
        key = _clean_name_for_key(normalize_customer_name(raw))
        if not key:
            return None
        return self.name_key_to_group_id.get(key)

    def aliases_for_group(self, group_id: str) -> List[str]:
        return list(self.group_id_to_aliases.get(group_id) or [])

    def join_keys_for_name(self, raw: Any) -> Set[str]:
        """All ``_clean_name_for_key`` values that should match *raw*."""
        keys: Set[str] = set()
        base_key = _clean_name_for_key(normalize_customer_name(raw))
        if base_key:
            keys.add(base_key)
        group_id = self.group_id_for_name(raw)
        if group_id:
            for alias in self.aliases_for_group(group_id):
                alias_key = _clean_name_for_key(normalize_customer_name(alias))
                if alias_key:
                    keys.add(alias_key)
        return keys

    def canonical_customer_name(
        self,
        raw: Any,
        *,
        team_subs_df: Optional[pd.DataFrame] = None,
    ) -> str:
        """Resolve *raw* to one canonical customer label (DSM-auto when possible)."""
        display = normalize_customer_name(raw)
        if display == "Unknown":
            return display
        group_id = self.group_id_for_name(raw)
        if not group_id:
            return display

        dsm_matches: List[str] = []
        if team_subs_df is not None and not team_subs_df.empty and "BU_NAME" in team_subs_df.columns:
            group_keys = {
                _clean_name_for_key(normalize_customer_name(alias))
                for alias in self.aliases_for_group(group_id)
            }
            group_keys.discard("")
            for bu in team_subs_df["BU_NAME"].dropna().astype(str):
                bu_norm = normalize_customer_name(bu)
                if bu_norm == "Unknown":
                    continue
                bu_key = _clean_name_for_key(bu_norm)
                if bu_key and bu_key in group_keys:
                    dsm_matches.append(bu_norm)

        if dsm_matches:
            distinct = sorted(set(dsm_matches))
            distinct.sort(key=lambda s: (-len(s), s.casefold()))
            return distinct[0]

        aliases = sorted(
            normalize_customer_name(alias)
            for alias in self.aliases_for_group(group_id)
            if normalize_customer_name(alias) != "Unknown"
        )
        return aliases[0] if aliases else display


_REGISTRY_CACHE: Optional[CustomerAliasRegistry] = None


def _customer_aliases_bundled_path() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            frozen_path = Path(meipass) / CUSTOMER_ALIASES_DEFAULTS_FILENAME
            if frozen_path.is_file():
                return frozen_path
    return Path(__file__).resolve().parent / CUSTOMER_ALIASES_DEFAULTS_FILENAME


def _customer_aliases_user_path() -> Optional[Path]:
    try:
        from adoptiq_settings import _app_support_dir

        return _app_support_dir() / CUSTOMER_ALIASES_USER_FILENAME
    except Exception:
        return None


def _parse_alias_groups(payload: Any) -> Dict[str, List[str]]:
    if not isinstance(payload, dict):
        return {}
    groups_raw = payload.get("groups")
    if not isinstance(groups_raw, list):
        return {}
    out: Dict[str, List[str]] = {}
    for entry in groups_raw:
        if not isinstance(entry, dict):
            continue
        group_id = _clean_text(entry.get("group_id"))
        aliases_raw = entry.get("aliases")
        if not group_id or not isinstance(aliases_raw, list):
            continue
        aliases: List[str] = []
        seen_keys: Set[str] = set()
        for alias in aliases_raw:
            norm = normalize_customer_name(alias)
            if norm == "Unknown":
                continue
            key = _clean_name_for_key(norm)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            aliases.append(norm)
        if aliases:
            out[group_id] = aliases
    return out


def _merge_alias_group_maps(
    base: Dict[str, List[str]],
    override: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    merged = dict(base)
    merged.update(override)
    return merged


def load_customer_alias_registry(*, force_reload: bool = False) -> CustomerAliasRegistry:
    """Load bundled + operator customer alias groups (cached per process)."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None and not force_reload:
        return _REGISTRY_CACHE

    merged_groups: Dict[str, List[str]] = {}
    bundled_path = _customer_aliases_bundled_path()
    if bundled_path.is_file():
        try:
            merged_groups = _parse_alias_groups(json.loads(bundled_path.read_text(encoding="utf-8")))
        except Exception as exc:
            _logger.warning("Round 132: failed to load bundled customer aliases from %s: %s", bundled_path, exc)

    user_path = _customer_aliases_user_path()
    if user_path is not None and user_path.is_file():
        try:
            user_groups = _parse_alias_groups(json.loads(user_path.read_text(encoding="utf-8")))
            merged_groups = _merge_alias_group_maps(merged_groups, user_groups)
        except Exception as exc:
            _logger.warning("Round 132: failed to load operator customer aliases from %s: %s", user_path, exc)

    name_key_to_group: Dict[str, str] = {}
    for group_id, aliases in merged_groups.items():
        for alias in aliases:
            key = _clean_name_for_key(alias)
            if key:
                name_key_to_group[key] = group_id

    _REGISTRY_CACHE = CustomerAliasRegistry(
        group_id_to_aliases=merged_groups,
        name_key_to_group_id=name_key_to_group,
    )
    return _REGISTRY_CACHE


def invalidate_customer_alias_registry_cache() -> None:
    """Round 132: call after operator saves ``customer_aliases.json``."""
    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None


def alias_join_keys_for_name(raw: Any, registry: Optional[CustomerAliasRegistry] = None) -> Set[str]:
    """Expand *raw* to all join keys (self + alias siblings)."""
    reg = registry or load_customer_alias_registry()
    return reg.join_keys_for_name(raw)


def canonical_customer_name(
    raw: Any,
    *,
    team_subs_df: Optional[pd.DataFrame] = None,
    registry: Optional[CustomerAliasRegistry] = None,
) -> str:
    """Round 132: alias-aware canonical customer display/join name."""
    reg = registry or load_customer_alias_registry()
    if not reg.has_groups():
        return normalize_customer_name(raw)
    return reg.canonical_customer_name(raw, team_subs_df=team_subs_df)


def customer_names_match(
    left: Any,
    right: Any,
    *,
    team_subs_df: Optional[pd.DataFrame] = None,
    registry: Optional[CustomerAliasRegistry] = None,
) -> bool:
    """Round 132: True when *left* and *right* refer to the same customer."""
    reg = registry or load_customer_alias_registry()
    left_norm = normalize_customer_name(left)
    right_norm = normalize_customer_name(right)
    if left_norm == right_norm:
        return True
    # Case and punctuation differences are display variants, not separate
    # customers.  Keep legal suffixes in this comparison so ``Acme Inc`` and
    # ``Acme LLC`` remain distinct unless the operator explicitly aliases them.
    left_strict = _strict_customer_name_key(left_norm)
    right_strict = _strict_customer_name_key(right_norm)
    if left_strict and left_strict == right_strict:
        return True
    if not reg.has_groups():
        return False
    left_canon = reg.canonical_customer_name(left, team_subs_df=team_subs_df)
    right_canon = reg.canonical_customer_name(right, team_subs_df=team_subs_df)
    return left_canon == right_canon


def collapse_customer_name_set(
    names: Iterable[str],
    *,
    team_subs_df: Optional[pd.DataFrame] = None,
    registry: Optional[CustomerAliasRegistry] = None,
) -> Set[str]:
    """Round 132: merge alias variants in a customer-name universe."""
    reg = registry or load_customer_alias_registry()
    collapsed: Dict[str, str] = {}
    for raw in names:
        norm = normalize_customer_name(raw)
        if norm == "Unknown" or not norm:
            continue
        display = (
            reg.canonical_customer_name(raw, team_subs_df=team_subs_df)
            if reg.has_groups()
            else norm
        )
        key = customer_ownership_key(display, registry=reg)
        if not key:
            continue
        existing = collapsed.get(key)
        if existing is None or (len(display), display.casefold(), display) > (
            len(existing),
            existing.casefold(),
            existing,
        ):
            collapsed[key] = display
    return set(collapsed.values())


def apply_customer_aliases_to_frame(
    df: Optional[pd.DataFrame],
    *,
    cols: Sequence[str] = LIKELY_CUSTOMER_COLS,
    team_subs_df: Optional[pd.DataFrame] = None,
    registry: Optional[CustomerAliasRegistry] = None,
) -> Optional[pd.DataFrame]:
    """Round 132: add ``customer_name_canonical`` when alias groups are configured."""
    if df is None or df.empty:
        return df
    reg = registry or load_customer_alias_registry()
    if not reg.has_groups():
        return df
    out = df.copy()
    canonical_values: List[str] = []
    for _, row in out.iterrows():
        resolved = "Unknown"
        for col in cols:
            if col in out.columns:
                candidate = normalize_customer_name(row.get(col))
                if candidate != "Unknown":
                    resolved = reg.canonical_customer_name(candidate, team_subs_df=team_subs_df)
                    break
        canonical_values.append(resolved)
    out["customer_name_canonical"] = canonical_values
    return out


# Round 132 / Build 102 — operator customer_aliases.json API helpers
_CUSTOMER_ALIAS_GROUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_CUSTOMER_ALIAS_SCHEMA_VERSION = 1


def is_valid_customer_alias_group_id(group_id: Any) -> bool:
    """Round 132: allow-list for operator-defined alias group ids."""
    if not isinstance(group_id, str):
        return False
    candidate = group_id.strip()
    if not candidate:
        return False
    return _CUSTOMER_ALIAS_GROUP_ID_RE.match(candidate) is not None


def _groups_map_to_api_list(groups: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    return [
        {"group_id": group_id, "aliases": list(aliases)}
        for group_id, aliases in sorted(groups.items())
    ]


def _load_alias_groups_from_path(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    parsed = _parse_alias_groups(payload)
    return _groups_map_to_api_list(parsed)


def load_bundled_customer_alias_groups_list() -> List[Dict[str, Any]]:
    """Round 132: bundled defaults as API list (no PII)."""
    return _load_alias_groups_from_path(_customer_aliases_bundled_path())


def load_operator_customer_alias_groups_list() -> List[Dict[str, Any]]:
    """Round 132: operator override file as API list (empty when absent)."""
    user_path = _customer_aliases_user_path()
    if user_path is None:
        return []
    return _load_alias_groups_from_path(user_path)


def build_customer_aliases_settings_payload() -> Dict[str, Any]:
    """Round 132: GET /api/settings/customer-aliases response body."""
    bundled = load_bundled_customer_alias_groups_list()
    operator = load_operator_customer_alias_groups_list()
    user_path = _customer_aliases_user_path()
    user_file = str(user_path) if user_path is not None else ""
    has_override = bool(user_path is not None and user_path.is_file())
    reg = load_customer_alias_registry(force_reload=True)
    return {
        "ok": True,
        "schema_version": _CUSTOMER_ALIAS_SCHEMA_VERSION,
        "bundled_groups": bundled,
        "operator_groups": operator,
        "effective_group_count": len(reg.group_id_to_aliases),
        "user_file_path": user_file,
        "has_operator_override": has_override,
    }


def parse_customer_alias_groups_request(
    payload: Any,
) -> Tuple[Optional[Dict[str, List[str]]], Optional[str]]:
    """Round 132: validate POST body ``{groups: [...]}``; return (map, error_code)."""
    if not isinstance(payload, dict):
        return None, "invalid_json_payload"
    groups_raw = payload.get("groups")
    if groups_raw is None:
        return None, "groups_required"
    if not isinstance(groups_raw, list):
        return None, "groups_must_be_list"
    if len(groups_raw) > 256:
        return None, "groups_limit_exceeded"
    normalized_entries: List[Dict[str, Any]] = []
    seen_group_ids: Set[str] = set()
    for entry in groups_raw:
        if not isinstance(entry, dict):
            return None, "invalid_group_entry"
        group_id = _clean_text(entry.get("group_id"))
        if not is_valid_customer_alias_group_id(group_id):
            return None, "invalid_group_id"
        if group_id in seen_group_ids:
            return None, "duplicate_group_id"
        seen_group_ids.add(group_id)
        aliases_raw = entry.get("aliases")
        if not isinstance(aliases_raw, list):
            return None, "aliases_must_be_list"
        if len(aliases_raw) > 64:
            return None, "aliases_limit_exceeded"
        normalized_entries.append({"group_id": group_id, "aliases": aliases_raw})
    wrapper = {"groups": normalized_entries}
    parsed = _parse_alias_groups(wrapper)
    if groups_raw and not parsed:
        return None, "invalid_groups"
    return parsed, None


def _write_customer_aliases_file(path: Path, groups_map: Dict[str, List[str]]) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass
    payload = {
        "schema_version": _CUSTOMER_ALIAS_SCHEMA_VERSION,
        "groups": _groups_map_to_api_list(groups_map),
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    text += "\n"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    os.replace(tmp_path, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def save_operator_customer_aliases_file(groups_map: Dict[str, List[str]]) -> Path:
    """Round 132: persist operator override and invalidate registry cache."""
    user_path = _customer_aliases_user_path()
    if user_path is None:
        raise OSError("customer_aliases_user_path_unavailable")
    _write_customer_aliases_file(user_path, groups_map)
    invalidate_customer_alias_registry_cache()
    return user_path


def clear_operator_customer_aliases_file() -> bool:
    """Round 132: remove operator override file if present."""
    user_path = _customer_aliases_user_path()
    if user_path is None:
        return False
    removed = False
    if user_path.is_file():
        user_path.unlink()
        removed = True
    invalidate_customer_alias_registry_cache()
    return removed


def build_customer_lookup(team_subs_df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Build account-id and fuzzy customer-name lookups from subscription data.

    Round 2 / Phase 4.1: detect collisions deterministically.

    - ``account_to_customer`` was previously last-write-wins.  Two
      different ``BU_NAME`` values for the same ``ACCOUNT_ID_C`` would
      silently overwrite the prior mapping, so the same account would
      route to different customers depending on row order.
    - ``key_to_customer`` was first-write-wins (opposite policy), so
      the same dataset could yield inconsistent canonical names.

    The rebuilt logic:

    1. Tally every ``account_id -> customer`` observed (with row order
       preserved as a stable tiebreaker).
    2. For accounts with multiple distinct customer names, deterministic
       rule: pick the alphabetically first ``BU_NAME`` (stable across
       runs, independent of row order).
    3. Log every collision with the chosen winner and losers and surface
       them via the returned ``warnings`` / ``collisions`` lists so
       callers can route them to ``partial_data_warnings``.
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    account_to_customer: Dict[str, str] = {}
    key_to_customer: Dict[str, str] = {}
    ambiguous_account_ids: Dict[str, List[str]] = {}
    ambiguous_customer_keys: Dict[str, List[str]] = {}
    collisions: List[Dict[str, Any]] = []
    warnings: List[str] = []
    empty_result = {
        "account_to_customer": account_to_customer,
        "key_to_customer": key_to_customer,
        "ambiguous_account_ids": ambiguous_account_ids,
        "ambiguous_customer_keys": ambiguous_customer_keys,
        "collisions": collisions,
        "warnings": warnings,
    }
    if team_subs_df is None or team_subs_df.empty:
        return empty_result

    safe = team_subs_df.copy()
    bu_name_columns = matching_schema_columns(safe.columns, ("BU_NAME",))
    if not bu_name_columns:
        safe["BU_NAME"] = ""
    elif "BU_NAME" not in safe.columns:
        safe["BU_NAME"] = safe[bu_name_columns[0]]
    safe["BU_NAME"] = safe["BU_NAME"].apply(normalize_customer_name)
    account_id_columns = matching_schema_columns(
        safe.columns, LIKELY_ACCOUNT_ID_COLS
    )

    account_observations: Dict[str, List[str]] = {}
    key_observations: Dict[str, List[str]] = {}

    for _, row in safe.iterrows():
        customer = normalize_customer_name(row.get("BU_NAME"))
        if customer == "Unknown":
            continue
        key = _clean_name_for_key(customer)
        if key:
            key_observations.setdefault(key, []).append(customer)
        for account_col in account_id_columns:
            account_id = clean_logical_record_id(row.get(account_col))
            if account_id:
                account_observations.setdefault(account_id, []).append(customer)

    for account_id, names in account_observations.items():
        distinct = sorted(set(names))
        winner = distinct[0]
        account_to_customer[account_id] = winner
        if len(distinct) > 1:
            ambiguous_account_ids[account_id] = distinct
            collisions.append({
                "kind": "account_to_customer",
                "key": account_id,
                "chosen": winner,
                "alternatives": distinct[1:],
            })
            warning_msg = (
                f"account_to_customer collision for ACCOUNT_ID={account_id!r}: "
                f"chose {winner!r} (alphabetical) over {distinct[1:]!r}"
            )
            warnings.append(warning_msg)
            _logger.warning(warning_msg)

    for key, names in key_observations.items():
        distinct = sorted(set(names))
        winner = distinct[0]
        key_to_customer[key] = winner
        if len(distinct) > 1:
            ambiguous_customer_keys[key] = distinct
            collisions.append({
                "kind": "key_to_customer",
                "key": key,
                "chosen": winner,
                "alternatives": distinct[1:],
            })
            warning_msg = (
                f"key_to_customer collision for normalized_key={key!r}: "
                f"chose {winner!r} (alphabetical) over {distinct[1:]!r}"
            )
            warnings.append(warning_msg)
            _logger.warning(warning_msg)

    return {
        "account_to_customer": account_to_customer,
        "key_to_customer": key_to_customer,
        "ambiguous_account_ids": ambiguous_account_ids,
        "ambiguous_customer_keys": ambiguous_customer_keys,
        "collisions": collisions,
        "warnings": warnings,
    }


def _strict_customer_name_key(value: Any) -> str:
    """Case-insensitive customer label key without fuzzy name collapsing."""

    normalized = normalize_customer_name(value)
    if normalized == "Unknown":
        return ""
    try:
        normalized = unicodedata.normalize("NFKC", normalized)
    except Exception:
        pass
    normalized = re.sub(r"[,\.\-_/]+", " ", normalized.casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def resolve_customer_name(
    row: pd.Series,
    customer_lookup: Optional[Dict[str, Dict[str, str]]] = None,
    customer_columns: Sequence[str] = LIKELY_CUSTOMER_COLS,
    account_columns: Sequence[str] = LIKELY_ACCOUNT_ID_COLS,
    registry: Optional[CustomerAliasRegistry] = None,
) -> str:
    """Resolve a customer without inventing certainty for ambiguous accounts.

    Explicit row-level names are accepted when they identify one of the
    candidates for an ambiguous account.  An account-only row whose account ID
    maps to multiple customers resolves to ``"Unknown"`` instead of inheriting
    the deterministic-but-arbitrary alphabetical display winner retained in
    ``account_to_customer`` for backwards compatibility.
    """
    lookup = customer_lookup or {"account_to_customer": {}, "key_to_customer": {}}
    account_to_customer = lookup.get("account_to_customer", {})
    key_to_customer = lookup.get("key_to_customer", {})
    ambiguous_account_ids = lookup.get("ambiguous_account_ids", {}) or {}
    ambiguous_customer_keys = lookup.get("ambiguous_customer_keys", {}) or {}

    reg = registry or load_customer_alias_registry()
    explicit_names: List[str] = []
    for col in matching_schema_columns(row.index, customer_columns):
        raw = normalize_customer_name(row.get(col))
        if raw != "Unknown":
            explicit_names.append(raw)

    for col in matching_schema_columns(row.index, account_columns):
        account_id = clean_logical_record_id(row.get(col))
        if account_id and account_id in ambiguous_account_ids:
            candidates = ambiguous_account_ids.get(account_id, [])
            for raw in explicit_names:
                if any(
                    customer_names_match(raw, candidate, registry=reg)
                    or _strict_customer_name_key(raw)
                    == _strict_customer_name_key(candidate)
                    for candidate in candidates
                ):
                    return raw
            return "Unknown"
        if account_id and account_id in account_to_customer:
            return account_to_customer[account_id]

    for raw in explicit_names:
        key = _clean_name_for_key(raw)
        if key and key in ambiguous_customer_keys:
            # A fuzzy join key may intentionally remove legal suffixes.  Once
            # that key is known to represent multiple explicit entities, keep
            # the row-level name instead of routing both to the alphabetical
            # compatibility winner in ``key_to_customer``.
            return raw
        if key and key in key_to_customer:
            return key_to_customer[key]
        return raw
    return "Unknown"


def customer_identity_key(
    value: Any,
    *,
    registry: Optional[CustomerAliasRegistry] = None,
) -> str:
    """Return the shared fuzzy-safe join key for a customer label."""

    normalized = normalize_customer_name(value)
    if normalized == "Unknown":
        return ""
    reg = registry or load_customer_alias_registry()
    if reg.group_id_for_name(normalized):
        normalized = reg.canonical_customer_name(normalized)
    return _clean_name_for_key(normalized)


def customer_ownership_key(
    value: Any,
    *,
    registry: Optional[CustomerAliasRegistry] = None,
) -> str:
    """Return a conservative key for deciding logical-record ownership.

    Explicit aliases configured in the registry share a group key.  Names that
    are not configured aliases only receive exact, case-insensitive label
    normalization; legal suffixes are deliberately retained here so genuinely
    distinct entities such as ``Acme Inc`` and ``Acme LLC`` are not silently
    treated as one owner.
    """

    normalized = normalize_customer_name(value)
    if normalized == "Unknown":
        return ""
    reg = registry or load_customer_alias_registry()
    group_id = reg.group_id_for_name(normalized)
    if group_id:
        return f"alias:{group_id.casefold()}"
    strict_key = _strict_customer_name_key(normalized)
    return f"name:{strict_key}" if strict_key else ""


def _merge_cross_customer_conflict_diagnostics(
    previous: Any,
    current: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge cumulative ownership caveats across repeated normalization."""

    prior = dict(previous or {}) if isinstance(previous, dict) else {}
    columns = list(
        dict.fromkeys(
            list(prior.get("record_id_columns_used") or [])
            + list(current.get("record_id_columns_used") or [])
        )
    )
    conflict_ids = list(
        dict.fromkeys(
            list(prior.get("conflicting_ids") or [])
            + list(current.get("conflicting_ids") or [])
        )
    )
    prior_count = int(prior.get("conflict_count", len(prior.get("conflicting_ids") or [])) or 0)
    current_count = int(
        current.get("conflict_count", len(current.get("conflicting_ids") or []))
        or 0
    )
    # Counts may exceed the capped ID sample.  Preserve the larger prior/new
    # cardinality while adding newly observed sampled IDs deterministically.
    conflict_count = max(prior_count, current_count, len(conflict_ids))
    return {
        "record_id_columns_used": columns,
        "conflicting_ids": conflict_ids[:50],
        "quarantined_rows": int(prior.get("quarantined_rows", 0) or 0)
        + int(current.get("quarantined_rows", 0) or 0),
        "conflict_count": conflict_count,
    }


def quarantine_cross_customer_record_ids(
    df: Optional[pd.DataFrame],
    *,
    record_id_columns: Sequence[str] = LIKELY_LOGICAL_RECORD_ID_COLS,
    customer_lookup: Optional[Dict[str, Any]] = None,
    customer_columns: Sequence[str] = LIKELY_CUSTOMER_COLS,
    account_columns: Sequence[str] = LIKELY_ACCOUNT_ID_COLS,
    resolved_customer_keys: Optional[pd.Series] = None,
    registry: Optional[CustomerAliasRegistry] = None,
) -> pd.DataFrame:
    """Remove logical IDs whose source rows disagree on customer ownership.

    Deduplicating before customer partition used to choose one customer's row;
    partitioning first counted the same logical ID for every customer.  Neither
    result is defensible without an authoritative ownership mapping.  This
    helper fails closed: all rows for an ID observed under two or more resolved
    customer identities are quarantined and the IDs/counts are recorded in
    ``attrs['cross_customer_id_conflicts']``.

    Blank IDs remain independent.  Unresolved rows do not create a conflict by
    themselves, but they are quarantined along with a conflicted ID once two
    resolved owners disagree.
    """

    if df is None:
        return pd.DataFrame()
    safe = df.copy()
    safe.attrs.update(getattr(df, "attrs", {}) or {})
    prior_diag = safe.attrs.get("cross_customer_id_conflicts") or {}
    empty_diag = {
        "record_id_columns_used": [],
        "conflicting_ids": [],
        "quarantined_rows": 0,
        "conflict_count": 0,
    }
    if safe.empty:
        safe.attrs["cross_customer_id_conflicts"] = (
            _merge_cross_customer_conflict_diagnostics(prior_diag, empty_diag)
        )
        safe.attrs["_last_cross_customer_quarantined_positions"] = []
        return safe

    identifiers, matched_id_columns = coalesce_logical_record_ids(
        safe, record_id_columns
    )
    identifiers = identifiers.str.upper()
    used_id_columns = [str(column) for column in matched_id_columns]
    if not used_id_columns or not identifiers.ne("").any():
        empty_diag["record_id_columns_used"] = used_id_columns
        safe.attrs["cross_customer_id_conflicts"] = (
            _merge_cross_customer_conflict_diagnostics(prior_diag, empty_diag)
        )
        safe.attrs["_last_cross_customer_quarantined_positions"] = []
        return safe

    lookup = customer_lookup or {
        "account_to_customer": {},
        "key_to_customer": {},
        "ambiguous_account_ids": {},
        "ambiguous_customer_keys": {},
    }
    reg = registry or load_customer_alias_registry()
    account_schema_columns = matching_schema_columns(safe.columns, account_columns)
    resolved_values: Optional[pd.Series] = None
    if resolved_customer_keys is not None and len(resolved_customer_keys) == len(safe):
        resolved_values = pd.Series(resolved_customer_keys).reset_index(drop=True)

    row_ownership: List[Dict[str, str]] = []
    account_named_owners: Dict[str, Set[str]] = {}
    for position in range(len(safe)):
        row = safe.iloc[position]
        record_id = identifiers.iloc[position]
        if not record_id:
            continue
        if resolved_values is not None:
            owner_key = _clean_text(resolved_values.iloc[position])
            if owner_key.casefold() in {"unknown", "name:unknown"}:
                owner_key = ""
        else:
            resolved = resolve_customer_name(
                row,
                lookup,
                customer_columns=customer_columns,
                account_columns=account_columns,
                registry=reg,
            )
            owner_key = customer_ownership_key(resolved, registry=reg)

        account_id = ""
        for account_column in account_schema_columns:
            account_id = clean_logical_record_id(row.get(account_column))
            if account_id:
                account_id = account_id.casefold()
                break
        if owner_key and account_id:
            account_named_owners.setdefault(account_id, set()).add(owner_key)
        row_ownership.append(
            {
                "record_id": record_id,
                "owner_key": owner_key,
                "account_id": account_id,
            }
        )

    ownership_rows: List[Dict[str, str]] = []
    for item in row_ownership:
        owner_key = item["owner_key"]
        account_id = item["account_id"]
        if not owner_key and account_id:
            named_owners = account_named_owners.get(account_id, set())
            owner_key = (
                next(iter(named_owners))
                if len(named_owners) == 1
                else f"account:{account_id}"
            )
        if owner_key:
            ownership_rows.append(
                {"record_id": item["record_id"], "owner_key": owner_key}
            )

    conflicting_ids: List[str] = []
    if ownership_rows:
        ownership = pd.DataFrame(ownership_rows)
        owner_counts = ownership.groupby("record_id", sort=True)[
            "owner_key"
        ].nunique()
        conflicting_ids = owner_counts.index[owner_counts > 1].tolist()

    quarantine_mask = identifiers.isin(conflicting_ids)
    quarantined_rows = int(quarantine_mask.sum())
    kept_positions = [
        position
        for position, is_quarantined in enumerate(quarantine_mask.tolist())
        if not is_quarantined
    ]
    out = safe.iloc[kept_positions].copy()
    out.attrs.update(getattr(safe, "attrs", {}) or {})
    current_diag = {
        "record_id_columns_used": used_id_columns,
        "conflicting_ids": conflicting_ids[:50],
        "quarantined_rows": quarantined_rows,
        "conflict_count": len(conflicting_ids),
    }
    out.attrs["cross_customer_id_conflicts"] = (
        _merge_cross_customer_conflict_diagnostics(prior_diag, current_diag)
    )
    out.attrs["_last_cross_customer_quarantined_positions"] = [
        position
        for position, is_quarantined in enumerate(quarantine_mask.tolist())
        if is_quarantined
    ]
    if conflicting_ids:
        _logger.warning(
            "Quarantined %d row(s) for %d logical record ID(s) with "
            "conflicting customer ownership: %s",
            quarantined_rows,
            len(conflicting_ids),
            conflicting_ids[:10],
        )
    return out


def partition_customer_frame(
    df: Optional[pd.DataFrame],
    *,
    customer_lookup: Optional[Dict[str, Any]] = None,
    customer_columns: Sequence[str] = LIKELY_CUSTOMER_COLS,
    account_columns: Sequence[str] = LIKELY_ACCOUNT_ID_COLS,
    registry: Optional[CustomerAliasRegistry] = None,
) -> Dict[str, pd.DataFrame]:
    """Resolve a frame once and partition it by canonical customer key.

    Portfolio scoring must not rescan every source for every customer.  This
    helper performs the identity work once per row, preserves input row order,
    and omits ambiguous/unresolved records instead of assigning them to an
    arbitrary customer.
    """

    if df is None or df.empty:
        return {}
    lookup = customer_lookup or {
        "account_to_customer": {},
        "key_to_customer": {},
        "ambiguous_account_ids": {},
        "ambiguous_customer_keys": {},
    }
    reg = registry or load_customer_alias_registry()
    resolved_keys: List[str] = []
    ownership_keys: List[str] = []
    for position in range(len(df)):
        row = df.iloc[position]
        resolved = resolve_customer_name(
            row,
            lookup,
            customer_columns=customer_columns,
            account_columns=account_columns,
            registry=reg,
        )
        resolved_keys.append(customer_identity_key(resolved, registry=reg))
        ownership_keys.append(customer_ownership_key(resolved, registry=reg))
    safe = quarantine_cross_customer_record_ids(
        df,
        customer_lookup=lookup,
        customer_columns=customer_columns,
        account_columns=account_columns,
        resolved_customer_keys=pd.Series(ownership_keys, dtype=str),
        registry=reg,
    )
    quarantined_positions = set(
        safe.attrs.pop("_last_cross_customer_quarantined_positions", []) or []
    )
    original_positions = [
        position for position in range(len(df)) if position not in quarantined_positions
    ]
    positions_by_key: Dict[str, List[int]] = {}
    for safe_position, original_position in enumerate(original_positions):
        key = str(resolved_keys[original_position] or "").strip()
        if not key:
            continue
        positions_by_key.setdefault(key, []).append(safe_position)
    return {
        key: safe.iloc[positions].copy()
        for key, positions in positions_by_key.items()
    }


def slice_customer_frame(
    df: Optional[pd.DataFrame],
    customer_name: Any,
    *,
    customer_lookup: Optional[Dict[str, Any]] = None,
    customer_columns: Sequence[str] = LIKELY_CUSTOMER_COLS,
    account_columns: Sequence[str] = LIKELY_ACCOUNT_ID_COLS,
    registry: Optional[CustomerAliasRegistry] = None,
) -> pd.DataFrame:
    """Return only rows that resolve to ``customer_name``.

    This is the shared, identity-aware alternative to report-local string
    equality filters.  It handles legal-suffix/case variants through the
    canonical join key, supports account-only rows through ``customer_lookup``,
    and fails closed for ambiguous account-only records.
    """

    if df is None or df.empty:
        return pd.DataFrame(columns=getattr(df, "columns", None))
    reg = registry or load_customer_alias_registry()
    target = customer_identity_key(customer_name, registry=reg)
    if not target:
        return df.iloc[0:0].copy()

    lookup = customer_lookup or {
        "account_to_customer": {},
        "key_to_customer": {},
        "ambiguous_account_ids": {},
        "ambiguous_customer_keys": {},
    }
    selected_positions: List[int] = []
    for position in range(len(df)):
        row = df.iloc[position]
        resolved = resolve_customer_name(
            row,
            lookup,
            customer_columns=customer_columns,
            account_columns=account_columns,
            registry=reg,
        )
        if customer_identity_key(resolved, registry=reg) == target:
            selected_positions.append(position)
    return df.iloc[selected_positions].copy()


def normalize_status_label(value: Any) -> str:
    text = _clean_text(value).lower()
    if not text:
        return "Unknown"
    # A reopened record is active even when an export preserves its prior
    # terminal label (for example ``Closed - Reopened``).  Evaluate this
    # lifecycle transition before the generic closed patterns.
    if re.search(r"\bre-?open(?:ed)?\b", text):
        return "Open"
    # Negated completion phrases are active work, not completed work.  Keep
    # an explicit closed/resolved prefix authoritative for labels such as
    # "Closed - Will Not Complete".
    if not re.match(r"^\s*(?:closed?|resolved?)\b", text) and re.search(
        r"\b(?:incomplete|unresolved|not\s+(?:closed?|resolved?|complete(?:d)?|done))\b",
        text,
    ):
        return "Open"
    if any(re.search(pat, text) for pat in CLOSED_STATUS_PATTERNS):
        return "Closed"
    if any(re.search(pat, text) for pat in OPEN_STATUS_PATTERNS):
        return "Open"
    return "Unknown"


def normalize_priority_label(value: Any) -> str:
    text = _clean_text(value).lower()
    if not text:
        return "Unknown"

    # Common numeric-only encodings from CSOne exports.
    if text in {"1", "2", "3", "4"}:
        return {"1": "P1", "2": "P2", "3": "P3", "4": "P4"}[text]

    if re.search(r"\bp[\s\-_:]*1\b|\bsev(?:erity)?[\s\-_:]*1\b|\bpriority[\s\-_:]*1\b|\bcritical\b", text):
        return "P1"
    if re.search(r"\bp[\s\-_:]*2\b|\bsev(?:erity)?[\s\-_:]*2\b|\bpriority[\s\-_:]*2\b|\bhigh\b", text):
        return "P2"
    if re.search(r"\bp[\s\-_:]*3\b|\bsev(?:erity)?[\s\-_:]*3\b|\bpriority[\s\-_:]*3\b|\bmedium\b|\bmoderate\b", text):
        return "P3"
    if re.search(r"\bp[\s\-_:]*4\b|\bsev(?:erity)?[\s\-_:]*4\b|\bpriority[\s\-_:]*4\b|\blow\b", text):
        return "P4"
    return "Unknown"


def normalize_severity_label(value: Any) -> str:
    priority = normalize_priority_label(value)
    return {
        "P1": "Critical",
        "P2": "High",
        "P3": "Medium",
        "P4": "Low",
    }.get(priority, "Unknown")


def parse_datetime_series(series: pd.Series) -> pd.Series:
    """Round 9 / Phase 6.2: predictable, all-NaT fallback on hard parse failure.

    The previous form's nested ``except Exception: pass`` swallowed the
    failure silently and returned ``parsed`` in whatever ambiguous,
    half-converted state pandas had landed it in (sometimes
    tz-aware, sometimes tz-naive, sometimes with a mix of dtypes
    after a partial coercion).  Downstream barrier-aging and ARR
    window code then compared tz-aware to tz-naive Timestamps and
    raised an opaque ``TypeError`` *inside the report writer* --
    losing the rest of the report section.

    The new contract is:

    * On the happy path the return value is always tz-naive (UTC
      anchored) so downstream comparisons against ``pd.Timestamp.now('UTC')
      .tz_localize(None)`` (Round 8 / Phase 2.10) are well-defined.
    * On total parse failure we return an all-``NaT`` Series of the
      same length / index and stamp ``parsed.attrs['partial_data_warning']``
      so the caller (``arr_df`` / ``incidents_df`` builders, already
      wired to surface ``attrs`` via Round 7) can flag the section as
      partial in the report rather than silently degrade.
    """
    def _all_nat(reason: str) -> pd.Series:
        try:
            empty = pd.Series([pd.NaT] * len(series), index=getattr(series, 'index', None), dtype="datetime64[ns]")
        except Exception:
            empty = pd.Series([pd.NaT], dtype="datetime64[ns]")
        try:
            empty.attrs['partial_data_warning'] = reason
        except Exception:
            pass
        return empty

    try:
        parsed = pd.to_datetime(series, errors="coerce", utc=True, format="mixed")
    except TypeError:
        try:
            parsed = pd.to_datetime(series, errors="coerce", utc=True)
        except Exception as fallback_err:
            return _all_nat(f"parse_datetime_series: pd.to_datetime failed ({fallback_err.__class__.__name__})")
    except Exception as parse_err:
        return _all_nat(f"parse_datetime_series: pd.to_datetime failed ({parse_err.__class__.__name__})")
    # Round 9 / Phase 6.2: convert to tz-naive UTC for downstream
    # arithmetic.  ``tz_convert`` fails on already-naive series;
    # ``tz_localize(None)`` fails on already-tz-aware series.  Try
    # both and only fall back to ``_all_nat`` when neither path
    # leaves us with a usable datetime64 dtype.
    try:
        parsed = parsed.dt.tz_convert(None)
    except Exception:
        try:
            parsed = parsed.dt.tz_localize(None)
        except Exception:
            try:
                if not pd.api.types.is_datetime64_any_dtype(parsed):
                    return _all_nat("parse_datetime_series: tz normalisation failed and result is non-datetime")
            except Exception:
                return _all_nat("parse_datetime_series: tz normalisation failed unexpectedly")
    return parsed


def first_existing_column(columns: Iterable[str], candidates: Sequence[str]) -> Optional[str]:
    available = set(columns)
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None


def coalesce_nonempty_columns(
    df: pd.DataFrame,
    candidates: Sequence[str],
) -> Tuple[pd.Series, List[str]]:
    """Return the first non-empty candidate value on each row.

    Schema-drift frames often contain several aliases at once, with the
    preferred alias blank on only some rows.  Choosing one column for the
    entire frame silently discards valid fallback values.
    """

    values_out = pd.Series(pd.NA, index=df.index, dtype="object")
    used_columns: List[str] = []
    for candidate in candidates:
        if candidate not in df.columns:
            continue
        candidate_values = df[candidate]
        usable = candidate_values.map(lambda value: bool(_clean_text(value)))
        fill_mask = values_out.isna() & usable
        if fill_mask.any():
            values_out.loc[fill_mask] = candidate_values.loc[fill_mask]
            used_columns.append(candidate)
    return values_out, used_columns


def detect_bems_mask(df: Optional[pd.DataFrame]) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=bool)

    mask = pd.Series([False] * len(df), index=df.index)
    if "Transaction ID" in df.columns:
        mask |= df["Transaction ID"].fillna("").astype(str).str.contains(r"\bBEMS\b|BEMS[- ]?\d+", case=False, regex=True)
    if "bemscsc_refs" in df.columns:
        refs = df["bemscsc_refs"].fillna("").astype(str)
        mask |= refs.str.contains(r"\bBEMS\b|BEMS[- ]?\d+", case=False, regex=True)

    for col in ("BEMS_REF", "bems_ref", "Escalation_Ref", "Engineering_Ref", "BEMS", "bems"):
        if col in df.columns:
            mask |= df[col].fillna("").astype(str).str.contains(r"\bBEMS\b|BEMS[- ]?\d+", case=False, regex=True)

    text_cols: List[str] = []
    for col in df.columns:
        lowered = str(col).lower()
        if any(token in lowered for token in ("title", "problem", "description", "subject", "summary", "detail")):
            text_cols.append(col)
    if text_cols:
        text_blob = pd.Series([""] * len(df), index=df.index)
        for col in text_cols:
            text_blob += " " + df[col].fillna("").astype(str)
        mask |= text_blob.str.contains(
            r"\bBEMS\b|BEMS[- ]?\d+|backend\s+escalation|engineering\s+escalation",
            case=False,
            regex=True,
        )

    return mask.fillna(False)


def extract_bems_ids_from_text(text: Any) -> List[str]:
    """Extract canonical BEMS references from free text."""
    candidate = _clean_text(text)
    if not candidate:
        return []
    found = BEMS_ID_PATTERN.findall(candidate)
    normalized = []
    for token in found:
        t = _clean_text(token).upper()
        if t:
            normalized.append(t)
    if any(re.search(r"\d", token) for token in normalized):
        normalized = [token for token in normalized if re.search(r"\d", token)]
    return sorted(set(normalized))


def extract_bems_ids_from_row(
    row: pd.Series,
    columns: Sequence[str] = ("Transaction ID", "bemscsc_refs", "Title", "Problem Description", "SUBJECT", "SUBJECT_C"),
) -> List[str]:
    """Extract canonical BEMS references from selected row columns."""
    refs: List[str] = []
    for col in columns:
        if col in row.index:
            refs.extend(extract_bems_ids_from_text(row.get(col)))
    return sorted(set(refs))


def _classify_case_type_text(text: str) -> str:
    if not text:
        return "unknown"
    provisioning_hits = sum(1 for pat in PROVISIONING_PATTERNS if re.search(pat, text, flags=re.IGNORECASE))
    break_fix_hits = sum(1 for pat in BREAK_FIX_PATTERNS if re.search(pat, text, flags=re.IGNORECASE))
    if provisioning_hits > break_fix_hits and provisioning_hits > 0:
        return "provisioning_request"
    if break_fix_hits > provisioning_hits and break_fix_hits > 0:
        return "break_fix_technical"
    return "unknown"


def classify_case_type(row: pd.Series) -> str:
    for col in LIKELY_CASE_TYPE_COLS:
        if col in row.index:
            ctype = _classify_case_type_text(_clean_text(row.get(col)))
            if ctype != "unknown":
                return ctype

    text_parts: List[str] = []
    for col in ("Title", "title", "Problem Description", "DESCRIPTION_C", "SUBJECT", "SUBJECT_C", "Case Status", "Status"):
        if col in row.index:
            text_parts.append(_clean_text(row.get(col)))
    return _classify_case_type_text(" ".join(text_parts))


def add_case_lifecycle_fields(
    df: Optional[pd.DataFrame],
    customer_lookup: Optional[Dict[str, Dict[str, str]]] = None,
) -> pd.DataFrame:
    """Add normalized TAC lifecycle, severity, and case-type fields."""
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df

    # Round 6 / Phase 4.15: capture a single tz-aware UTC clock so
    # the open/closed age computations below cannot drift by the
    # host's local UTC offset and so both ages share the exact same
    # reference instant (no clock skew between the two subtractions).
    now = pd.Timestamp(datetime.now(timezone.utc))
    use = df.copy()
    lookup = customer_lookup or {"account_to_customer": {}, "key_to_customer": {}}

    use["customer_name"] = use.apply(lambda row: resolve_customer_name(row, lookup), axis=1)
    use["customer_name_norm"] = use["customer_name"].apply(normalize_customer_name)

    status_values, status_columns = coalesce_nonempty_columns(
        use, LIKELY_STATUS_COLS
    )
    priority_values, priority_columns = coalesce_nonempty_columns(
        use, LIKELY_PRIORITY_COLS
    )
    open_values, open_columns = coalesce_nonempty_columns(
        use, LIKELY_OPEN_DATE_COLS
    )
    close_values, close_columns = coalesce_nonempty_columns(
        use, LIKELY_CLOSED_DATE_COLS
    )

    use["case_status_norm"] = status_values.map(normalize_status_label)
    use["case_priority_norm"] = priority_values.map(normalize_priority_label)
    use["severity_norm"] = priority_values.map(normalize_severity_label)

    # Round 10 / Phase 9.1: ``parse_datetime_series`` stamps a
    # ``partial_data_warning`` on the returned ``Series.attrs`` when
    # parsing falls back to all-NaT.  However ``Series.attrs`` does
    # NOT propagate through ``df[col] = series`` assignment, so any
    # downstream caller that reads ``df['open_date'].attrs`` will get
    # an empty dict and silently miss the warning.  Capture the warn
    # texts off the source ``Series.attrs`` *before* assignment and
    # surface them on the returned DataFrame's ``attrs['partial_data_warnings']``
    # list so report assembly can append them to the operator-facing
    # ``partial_data_warnings`` shown on the progress page.
    _lifecycle_warnings = []
    if open_columns:
        _open_series = parse_datetime_series(open_values)
        try:
            _w = _open_series.attrs.get('partial_data_warning')
            if _w:
                _lifecycle_warnings.append({
                    'source': 'add_case_lifecycle_fields.open_date',
                    'column': " | ".join(open_columns),
                    'reason': str(_w),
                })
        except Exception:
            pass
        use["open_date"] = _open_series
    else:
        use["open_date"] = pd.NaT
    if close_columns:
        _close_series = parse_datetime_series(close_values)
        try:
            _w = _close_series.attrs.get('partial_data_warning')
            if _w:
                _lifecycle_warnings.append({
                    'source': 'add_case_lifecycle_fields.closed_date',
                    'column': " | ".join(close_columns),
                    'reason': str(_w),
                })
        except Exception:
            pass
        use["closed_date"] = _close_series
    else:
        use["closed_date"] = pd.NaT
    if _lifecycle_warnings:
        try:
            existing = list(use.attrs.get('partial_data_warnings') or [])
            existing.extend(_lifecycle_warnings)
            use.attrs['partial_data_warnings'] = existing
        except Exception:
            pass

    use["is_open"] = use["case_status_norm"].eq("Open")
    use["is_closed"] = use["case_status_norm"].eq("Closed")
    # If status is unknown but close date exists, treat as closed.
    # Round 121 / G4a: ALSO update the displayed ``case_status_norm`` to
    # "Closed" for this mask.  Pre-R121 the code flipped the is_closed /
    # is_open booleans but left ``case_status_norm == "Unknown"``, so a case
    # carrying both Opened + Closed timestamps still rendered "Status: Unknown"
    # in the Compact / Renewal support-case tables.  This is the source-of-truth
    # normalizer, so every consumer (Compact, Renewal, Leader, XLSX) inherits
    # the accurate label.  Counts are unaffected -- is_closed was already True.
    _r121_closed_unknown = use["closed_date"].notna() & use["case_status_norm"].eq("Unknown")
    use.loc[_r121_closed_unknown, "is_closed"] = True
    use.loc[_r121_closed_unknown, "is_open"] = False
    use.loc[_r121_closed_unknown, "case_status_norm"] = "Closed"

    # Round 6 / Phase 4.15: align both date columns onto the same
    # tz-aware UTC basis as ``now`` before subtraction so pandas
    # does not raise / coerce when one side is tz-naive and the
    # other tz-aware.
    def _to_utc(s: pd.Series) -> pd.Series:
        try:
            if getattr(s.dt, 'tz', None) is None:
                return s.dt.tz_localize('UTC')
            return s.dt.tz_convert('UTC')
        except Exception:
            return s

    _open_utc = _to_utc(use["open_date"])
    _closed_utc = _to_utc(use["closed_date"])
    use["open_age_days"] = (
        (now - _open_utc).dt.days.where(_open_utc.notna() & use["is_open"], other=pd.NA)
    )
    use["closed_age_days"] = (
        (now - _closed_utc).dt.days.where(_closed_utc.notna() & use["is_closed"], other=pd.NA)
    )

    use["case_type_class"] = use.apply(classify_case_type, axis=1)
    bems_mask = detect_bems_mask(use)
    use["is_bems"] = bems_mask.astype(bool)
    use["case_classification"] = use["is_bems"].map(
        lambda flagged: "bems_escalation" if bool(flagged) else "tac_case"
    )

    return use


# ---------------------------------------------------------------------------
# Round 25 / Phase F: HTML strip helper for Excel object columns
# ---------------------------------------------------------------------------
#
# Pre-Round 25 the Excel writer emitted whatever string lived in object
# columns -- including raw HTML markup leaking out of Snowflake views
# that store rich-text descriptions.  The reference Brian Frazier
# report's ``AB_Detail_All`` and ``CSConsole_Customer_Pulse`` sheets had
# cells like::
#
#     <a href="/" target="_blank"> </a>
#     <img src="/lightning/r/Account/0014..." />
#     <p>Customer asked about <strong>renewal</strong> options.</p>
#
# Excel renders ``<`` literally, so the recipient saw raw markup
# instead of the intended text content.  This helper strips well-formed
# HTML tags and unescapes named/numeric entities, while leaving plain
# text untouched.  Cells that don't contain ``<`` skip the regex pass
# entirely so this is cheap on clean exports.

import html as _html_module

_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Round 66 / Pass 1 (B4): script/style block stripper for the
# BeautifulSoup-quality fallback path. The naive ``<[^>]+>`` regex would
# leave ``<script>...</script>`` body text in place; this pre-pass
# removes the entire tag + body + closing tag for ``script`` and
# ``style`` blocks before the generic tag-stripping pass.
_HTML_DANGEROUS_BLOCK_RE = re.compile(
    r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)


def strip_html_from_string(value: Any) -> Any:
    """Strip HTML tags and unescape entities from a single cell value.

    Non-string values are returned unchanged.  Strings that contain
    NEITHER ``<`` NOR ``&`` are returned unchanged (cheap fast-path --
    no markup, no entities, nothing to do).  Strings with ``<`` are
    passed through ``re.sub`` to drop tags and ``html.unescape`` to
    convert ``&amp;`` -> ``&``, ``&nbsp;`` -> non-breaking space, etc.
    Strings with ``&`` but no ``<`` skip the regex pass and only run
    ``html.unescape`` -- this is the entity-only path.

    Round 25 / Phase F.2: applied to Excel object columns immediately
    before ``df.to_excel(...)`` so generated workbooks no longer leak
    Snowflake rich-text markup into ``AB_Detail_All`` and
    ``CSConsole_Customer_Pulse``.

    Round 66 / Pass 1 (B4) widens the strip to also handle dangerous
    block elements (``<script>`` / ``<style>``) which the naive regex
    would leave their body text behind. The block-strip pre-pass runs
    BEFORE the generic tag pass.

    Round 73 / Phase 3 (F7): widens the early-out to include strings
    with HTML entities but no tags -- pre-R73 the regex helper bailed
    on any string without ``<``, so a Snowflake URL column carrying
    ``"https://example.com/?a=1&amp;b=2"`` (or a customer name like
    ``"Acme &amp; Beta"``) leaked into Excel with literal ``&amp;``.
    The ``_strip_html_safe`` BS4-primary path already handled this
    case correctly (its fast-path probed for both ``<`` and ``&``);
    this fix brings the regex fallback into parity so the column-level
    ``strip_html_from_dataframe`` route -- which calls
    ``strip_html_from_string`` directly via ``series.map(...)`` -- no
    longer skips entity-only strings.
    """

    if not isinstance(value, str):
        return value
    has_tag = "<" in value
    has_entity = "&" in value
    if not has_tag and not has_entity:
        return value
    try:
        if has_tag:
            # R66/B4: drop entire <script>...</script> and <style>...</style>
            # blocks (tag + body + closing tag) before the generic pass so
            # the body text doesn't survive into the cleaned output.
            cleaned = _HTML_DANGEROUS_BLOCK_RE.sub("", value)
            stripped = _HTML_TAG_RE.sub("", cleaned)
            return _html_module.unescape(stripped)
        # R73 / F7: entity-only path -- skip the regex passes and just
        # decode the entities. ``html.unescape`` is a stdlib function
        # so the only failure mode is a non-string input which the
        # ``isinstance`` guard above already filters out.
        return _html_module.unescape(value)
    except Exception:
        # Defensive: a regex / unescape failure should not lose data.
        return value


# Round 66 / Pass 1 (B4): public alias for ``strip_html_from_string``
# matching the plan's naming convention. ``_strip_html_safe`` hints that
# the helper is the "safe" variant (regex-only, never raises, falls
# back to BeautifulSoup when available for edge cases). Existing
# call sites should migrate to this alias over time. The
# BeautifulSoup branch is gated on import success so we degrade
# cleanly in trimmed builds; the regex fallback handles the 99.9% case
# already (Snowflake rich-text views always emit well-formed markup).
def _strip_html_safe(value: Any) -> Any:
    """R66/B4 public alias -- BeautifulSoup primary, regex fallback.

    Behavior contract:
      * Non-string inputs returned unchanged (numeric / datetime /
        None / NaN are all passed through).
      * Strings without ``<`` returned unchanged (cheap fast-path).
      * Strings with ``<``: try BeautifulSoup with ``html.parser``
        first (handles malformed markup, dangerous blocks, entity
        decoding); on import / parse failure, fall through to
        ``strip_html_from_string`` (regex + script/style pre-strip).

    The Round 25 ``strip_html_from_string`` regex path already covers
    the production Snowflake view markup we've seen. The BS4 primary
    path adds resilience to malformed HTML (unclosed tags, nested
    quote schemes) which the regex approach mishandles.
    """

    if not isinstance(value, str):
        return value
    # Fast-path: skip strings with NEITHER a tag start ``<`` NOR an
    # HTML entity marker ``&``. Entity-only strings still need
    # ``html.unescape`` (otherwise ``"Acme &amp; Beta"`` arrives in
    # Excel as literal ``&amp;`` instead of ``&``).
    if "<" not in value and "&" not in value:
        return value
    # Try BeautifulSoup primary path -- handles malformed HTML and
    # script/style block bodies natively via ``get_text()``.
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]
        # Use the stdlib parser to avoid lxml/html5lib dependencies.
        soup = BeautifulSoup(value, "html.parser")
        # Strip dangerous block contents BEFORE get_text so the body
        # text doesn't survive into the cleaned output.
        for tag_name in ("script", "style"):
            for tag in soup.find_all(tag_name):
                tag.decompose()
        text = soup.get_text(separator="")
        return _html_module.unescape(text)
    except Exception:
        # BeautifulSoup unavailable / failed parse: fall through to
        # the regex path which already handles the production cases.
        return strip_html_from_string(value)


def strip_html_from_dataframe(df: "pd.DataFrame") -> "pd.DataFrame":
    """Return a shallow copy of ``df`` with HTML stripped from object columns.

    Only object-dtype columns whose values look like they might contain
    HTML are rewritten -- numeric / datetime columns and pure-text
    columns without any ``<`` characters are left alone.  We make a
    shallow copy so callers can pass us the DataFrame they were about
    to write to Excel without worrying about us mutating their working
    state.

    Returns ``df`` unchanged when:
    - ``df`` is None,
    - ``df`` is not a DataFrame, or
    - ``df`` is empty.

    Round 25 / Phase F.2.
    """

    if df is None:
        return df
    if not hasattr(df, "columns") or not hasattr(df, "copy"):
        return df
    try:
        if len(df) == 0:
            return df
    except Exception:
        return df

    try:
        out = df.copy()
    except Exception:
        return df

    try:
        obj_cols = list(out.select_dtypes(include=["object"]).columns)
    except Exception:
        obj_cols = []
    for col in obj_cols:
        try:
            series = out[col]
        except Exception:
            continue
        # Cheap fast-path: skip the column entirely if NO cell has
        # either a ``<`` (tag) OR ``&`` (entity) character -- avoids
        # the regex/unescape apply on big text columns.
        #
        # Round 73 / Phase 3 (F7): pre-R73 this only probed for ``<``,
        # so a column whose only HTML-ish content was ``&amp;`` /
        # ``&lt;`` / ``&gt;`` / ``&nbsp;`` (URL columns, customer-name
        # columns) was skipped entirely and the entities leaked into
        # Excel. The ``&`` half of the probe brings the column-level
        # fast-path into parity with ``strip_html_from_string``'s
        # cell-level fast-path so entity-only columns now flow
        # through ``html.unescape``.
        try:
            astr = series.astype(str)
            has_tag = astr.str.contains("<", regex=False, na=False).any()
            has_entity = astr.str.contains("&", regex=False, na=False).any()
        except Exception:
            has_tag = True
            has_entity = False
        if not has_tag and not has_entity:
            continue
        try:
            out[col] = series.map(strip_html_from_string)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Round 122 / H2: count-safe display relabel for bare sentinel tokens that
# leak into the XLSX export layer.  The Build 90 acceptance audit found two
# raw normalizer sentinels surfacing verbatim in produced workbooks:
#   * support / TAC ``case_type_class == "unknown"`` (classify_case_type
#     default), and
#   * adoption-barrier ``sub_technology == "Other/Unknown"``
#     (adoptiq_backend ``_normalize_subtech`` default).
# These are correct internal sentinels but read as raw data to the operator.
# ``relabel_display_sentinels`` rewrites ONLY exact sentinel matches in the
# named columns to a friendly display label, on a shallow copy via
# ``Series.map`` so (a) no row is dropped (count-safe) and (b) the caller's
# canonical working frame is never mutated.  It mirrors the defensiveness of
# ``strip_html_from_dataframe`` above.
# ---------------------------------------------------------------------------

_DISPLAY_SENTINEL_TOKENS = frozenset(
    {
        "unknown",
        "other/unknown",
        "other / unknown",
        "nan",
        "none",
        "n/a",
        "na",
    }
)


def relabel_display_sentinels(
    df: "pd.DataFrame",
    column_label_map: Dict[str, str],
    *,
    tokens: Optional[Iterable[str]] = None,
) -> "pd.DataFrame":
    """Round 122 / H2: return a shallow copy of ``df`` with bare sentinel
    values in the named columns relabeled to a display string.

    ``column_label_map`` maps a column name -> the replacement display label
    to use for that column, e.g.::

        {"case_type_class": "Unclassified",
         "sub_technology": "Other / Unclassified"}

    Contract:
    - Count-safe: rewrites cells via ``Series.map`` only -- never drops or
      reorders rows.
    - Canonical-safe: operates on a shallow ``df.copy()`` so the caller's
      working frame is never mutated.
    - Conservative: only an EXACT (case/whitespace-insensitive) match against
      a known sentinel token is relabeled, so a genuine label that merely
      *contains* "unknown" (e.g. ``"Unknown Protocol"``) is preserved.
    - Blank / NaN cells are left untouched.
    - Columns absent from ``df`` are skipped.

    Returns ``df`` unchanged when ``df`` is None / not a DataFrame / empty,
    or when none of the named columns are present.
    """

    if df is None:
        return df
    if not hasattr(df, "columns") or not hasattr(df, "copy"):
        return df
    if not column_label_map:
        return df
    try:
        if len(df) == 0:
            return df
    except Exception:
        return df

    try:
        present = [c for c in column_label_map if c in df.columns]
    except Exception:
        return df
    if not present:
        return df

    tokenset = (
        frozenset(str(t).strip().lower() for t in tokens)
        if tokens is not None
        else _DISPLAY_SENTINEL_TOKENS
    )

    try:
        out = df.copy()
    except Exception:
        return df

    for col in present:
        replacement = column_label_map[col]

        def _relabel(value: Any, _rep: str = replacement, _tok: "frozenset[str]" = tokenset) -> Any:
            if value is None:
                return value
            try:
                if pd.isna(value):
                    return value
            except (TypeError, ValueError):
                pass
            text = str(value).strip()
            if not text:
                return value
            if text.lower() in _tok:
                return _rep
            return value

        try:
            out[col] = out[col].map(_relabel)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Round 52 / partial-data-warning fix #3: dtype-safe customer merge for the
# CSConsole adoption-barriers re-annotation path.
# ---------------------------------------------------------------------------
#
# ``app_simple.py`` runs a left-merge between the CSConsole adoption-barriers
# frame (raw ``ACCOUNT_ID_C`` from ``C360_CS_TASK_C_VW``) and ``team_subs_df``
# (``ACCOUNT_ID_C`` + ``BU_NAME``) so the contract's ``customer`` slot can
# resolve.  Round 50 wired that merge into both compact and comprehensive
# paths but the recurring "schema_drift: missing slot(s) customer" warning
# kept appearing in production runs because the join key dtypes drift between
# Snowflake fetches: one frame would carry ``ACCOUNT_ID_C`` as ``object`` and
# the other as ``int64`` / ``Int64`` / ``float64`` (NULLs in DSM).  Pandas
# ``merge(on=...)`` returns no rows in that case (or all-NaN BU_NAME),
# leaving the ``customer`` slot unresolved and the prefetch-time
# ``fetch_error`` stamp untouched.
#
# ``merge_customer_join_keys_dtype_safe`` normalises both join sides to a
# common ``string`` form (NaN-safe, whitespace-trimmed, ``<NA>``-aware) BEFORE
# the merge so the join always succeeds when the underlying account IDs match
# regardless of upstream dtype drift.  When either input is empty / missing
# the helper short-circuits and returns the left frame unchanged.


def merge_customer_join_keys_dtype_safe(
    left: Optional[pd.DataFrame],
    right: Optional[pd.DataFrame],
    *,
    join_key: str = "ACCOUNT_ID_C",
    right_columns: Optional[Sequence[str]] = None,
    how: str = "left",
) -> Optional[pd.DataFrame]:
    """Left-merge ``left`` onto ``right`` after coercing the join key to a
    consistent string dtype on both sides.

    Returns ``left`` unchanged when either input is empty / missing the
    join key. Never raises; on any unexpected error returns ``left`` and
    leaves the caller to surface the failure via the existing
    ``fetch_error`` channel.
    """
    if left is None or not isinstance(left, pd.DataFrame) or left.empty:
        return left
    if right is None or not isinstance(right, pd.DataFrame) or right.empty:
        return left
    if join_key not in left.columns or join_key not in right.columns:
        return left

    try:
        right_subset_cols = list(right_columns) if right_columns else list(right.columns)
        if join_key not in right_subset_cols:
            right_subset_cols = [join_key] + [c for c in right_subset_cols if c != join_key]
        right_subset = right[[c for c in right_subset_cols if c in right.columns]].copy()

        left_copy = left.copy()
        left_copy[join_key] = (
            left_copy[join_key]
            .astype("string")
            .str.strip()
            .replace({"<NA>": pd.NA, "None": pd.NA, "nan": pd.NA, "": pd.NA})
        )
        right_subset[join_key] = (
            right_subset[join_key]
            .astype("string")
            .str.strip()
            .replace({"<NA>": pd.NA, "None": pd.NA, "nan": pd.NA, "": pd.NA})
        )
        right_subset = right_subset.drop_duplicates(subset=[join_key])

        return left_copy.merge(right_subset, on=join_key, how=how)
    except Exception:
        return left
