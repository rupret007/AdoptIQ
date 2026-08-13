"""Safe, deterministic drill-through links for supported source records."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlparse


CSCONSOLE_HOST = "ciscosales.lightning.force.com"
CSCONSOLE_BASE_URL = f"https://{CSCONSOLE_HOST}"
SOURCE_RECORD_URL_COLUMN = "Source_Record_URL"

_OBJECT_BY_SOURCE_KEY = {
    "ap": "C360_CS_Task__c",
    "ab": "C360_CS_Task__c",
    "action_plans": "C360_CS_Task__c",
    "actionplans": "C360_CS_Task__c",
    "action_plans_sheet": "C360_CS_Task__c",
    "adoption_barriers": "C360_CS_Task__c",
    "adoptionbarriers": "C360_CS_Task__c",
    "cp": "ESA_C360_Customer_Pulse__c",
    "customer_pulse": "ESA_C360_Customer_Pulse__c",
    "customerpulse": "ESA_C360_Customer_Pulse__c",
    "sp": "ESA_C360_SUCCESS_PRIORITY__C",
    "success_priorities": "ESA_C360_SUCCESS_PRIORITY__C",
    "successpriorities": "ESA_C360_SUCCESS_PRIORITY__C",
}
_SAFE_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
_SAFE_OBJECT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,80}$")


def _source_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def csconsole_object_for_source(source_key: object) -> str:
    token = _source_token(source_key)
    direct = _OBJECT_BY_SOURCE_KEY.get(token)
    if direct:
        return direct
    compact = token.replace("_", "")
    return _OBJECT_BY_SOURCE_KEY.get(compact, "")


def build_source_record_url(source_key: object, record_id: Any) -> str:
    """Return an allow-listed CSConsole URL or an empty string.

    Both real Salesforce IDs and explicit sanitized fixture IDs are supported.
    Path separators, query strings, fragments, whitespace, and control
    characters are rejected before URL construction.
    """

    object_name = csconsole_object_for_source(source_key)
    token = str(record_id or "").strip()
    if (
        not object_name
        or not _SAFE_OBJECT_RE.fullmatch(object_name)
        or not _SAFE_RECORD_ID_RE.fullmatch(token)
    ):
        return ""
    return (
        f"{CSCONSOLE_BASE_URL}/lightning/r/{quote(object_name, safe='')}/"
        f"{quote(token, safe='')}/view"
    )


def is_allowed_source_record_url(value: object) -> bool:
    """Validate a generated record URL before writing a live hyperlink."""

    try:
        parsed = urlparse(str(value or "").strip())
        parsed_port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != CSCONSOLE_HOST
        or parsed.username
        or parsed.password
        or parsed_port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.params
    ):
        return False
    pieces = [piece for piece in parsed.path.split("/") if piece]
    if len(pieces) != 5 or pieces[:2] != ["lightning", "r"] or pieces[-1] != "view":
        return False
    return bool(
        _SAFE_OBJECT_RE.fullmatch(pieces[2])
        and _SAFE_RECORD_ID_RE.fullmatch(pieces[3])
        and pieces[2] in set(_OBJECT_BY_SOURCE_KEY.values())
    )


__all__ = [
    "CSCONSOLE_BASE_URL",
    "CSCONSOLE_HOST",
    "SOURCE_RECORD_URL_COLUMN",
    "build_source_record_url",
    "csconsole_object_for_source",
    "is_allowed_source_record_url",
]
