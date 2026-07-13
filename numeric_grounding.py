"""Typed numeric extraction for evidence-grounded narrative validation.

Numeric equality alone is not entailment: five cases, five percent, and five
dollars are different facts.  This module preserves sign, scale, and a compact
semantic dimension so callers can compare like with like.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, List


_MINUS_TRANSLATION = str.maketrans(
    {
        "\u2212": "-",  # mathematical minus
        "\ufe63": "-",  # small hyphen-minus
        "\uff0d": "-",  # full-width hyphen-minus
    }
)


def normalize_numeric_text(value: Any) -> str:
    """Return NFKC text with visually equivalent minus signs normalized."""

    try:
        text = str(value or "")
    except Exception:
        return ""
    return unicodedata.normalize("NFKC", text).translate(_MINUS_TRANSLATION)


@dataclass(frozen=True)
class TypedQuantity:
    value: float
    kind: str
    raw: str
    start: int
    end: int


_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_#])"
    r"(?P<open>\()?[ \t]*"
    r"(?P<prefix>"
    r"(?:[+-][ \t]*)?(?:(?:[$€£]|USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR|NZD|SEK|NOK|DKK|BRL|MXN|SGD|HKD|ZAR)[ \t]*)?"
    r"|(?:(?:[$€£]|USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR|NZD|SEK|NOK|DKK|BRL|MXN|SGD|HKD|ZAR)[ \t]*)[+-]?[ \t]*"
    r")"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:e[+-]?\d+)?)"
    r"(?P<scale>[ \t]*(?:MM|BN|K|M|B|thousand|million|billion))?"
    r"(?P<percent>[ \t]*%)?[ \t]*"
    r"(?P<close>\))?"
    r"(?![A-Za-z0-9_])",
    flags=re.IGNORECASE,
)


def _quantity_kind(
    text: str,
    match: re.Match[str],
    *,
    currency_code: str,
    has_percent: bool,
) -> str:
    before_full = text[max(0, match.start() - 96):match.start()].casefold()
    after_full = text[match.end():min(len(text), match.end() + 96)].casefold()
    before_boundary = r"[.!?;|\n]|(?<!\d),(?!\d)|\band\b"
    after_boundary = r"[.!?;|\n]|(?<!\d),(?!\d)|\b(?:and|with)\b"
    before = re.split(before_boundary, before_full)[-1].replace("_", " ")
    after = re.split(after_boundary, after_full)[0].replace("_", " ")

    if re.search(
        r"(?:case|ticket|incident|record|action\s*plan|barrier)\s*"
        r"(?:#|id|number|no\.?)\s*$",
        before,
    ):
        return "identifier"

    if re.search(r"\b(?:fy|fiscal\s+year|calendar\s+year|year)\s*(?::|=)?\s*$", before):
        return "date"

    number_text = (match.group("number") or "").replace(",", "")
    looks_like_year = False
    if (
        not match.group("scale")
        and "." not in number_text
        and "e" not in number_text.casefold()
    ):
        try:
            integer_value = int(number_text)
        except ValueError:
            integer_value = 0
        looks_like_year = 1900 <= integer_value <= 2100

    dimension_patterns = (
        ("currency", r"(?:[$€£]|\b(?:arr|revenue|tcv|currency|dollars?|usd|eur|gbp|cad|aud|jpy|contract\s+value|renewal\s+value|expiring\s+arr)\b)"),
        ("customer_count", r"\b(?:customers?|accounts?|portfolio\s+customers?)\b"),
        ("case_count", r"\b(?:support\s+cases?|tac\s+cases?|cases?|tickets?|p[1-4])\b"),
        ("barrier_count", r"\b(?:adoption\s+barriers?|barriers?)\b"),
        ("action_plan_count", r"\b(?:action\s+plans?|success\s+plans?)\b"),
        ("incident_count", r"\b(?:incidents?|outages?|maintenances?|bugs?|defects?)\b"),
        ("subscription_count", r"\b(?:subscriptions?|contracts|renewals)\b"),
        ("duration", r"\b(?:business\s+)?(?:hours?|days?|weeks?|months?|quarters?|years?)\b"),
        ("score", r"\b(?:score|rating|grade|risk\s+score)\b"),
    )
    candidates: List[tuple[int, int, str]] = []
    for kind, pattern in dimension_patterns:
        before_matches = list(re.finditer(pattern, before))
        if before_matches:
            candidates.append((len(before) - before_matches[-1].end(), 1, kind))
        after_match = re.search(pattern, after)
        if after_match:
            # A postfix unit (``7 critical barriers``) is usually more direct
            # than a neighboring label before the value.
            candidates.append((max(after_match.start() - 12, 0), 0, kind))
    semantic_candidate_kinds = {item[2] for item in candidates}
    if candidates:
        nearest_kind = min(candidates)[2]
    else:
        fallback_patterns = (
            ("rate", r"\b(?:rate|ratio)\b"),
            ("date", r"\b(?:date|dated|year|as[- ]of|generated|retrieved)\b"),
            ("record_count", r"\b(?:count|total|number\s+of|records?|items?)\b"),
        )
        fallback_candidates: List[tuple[int, int, str]] = []
        for kind, pattern in fallback_patterns:
            before_matches = list(re.finditer(pattern, before))
            if before_matches:
                fallback_candidates.append(
                    (len(before) - before_matches[-1].end(), 1, kind)
                )
            after_match = re.search(pattern, after)
            if after_match:
                fallback_candidates.append((after_match.start(), 0, kind))
        nearest_kind = (
            min(fallback_candidates)[2] if fallback_candidates else "generic"
        )
        semantic_candidate_kinds.update(item[2] for item in fallback_candidates)
    if nearest_kind == "generic":
        # Compact KPI prose commonly states an entity once and then lists
        # parenthetical states: ``24 adoption barriers (7 critical, 18 still
        # open)``.  The comma is a useful general clause boundary, but the
        # state adjective immediately after the number proves that this value
        # inherits the closest preceding entity within the sentence.
        if re.match(
            r"\s*(?:still\s+|remain(?:s|ing)?\s+)?"
            r"(?:active|blocked|closed|critical|high|inactive|low|medium|open|resolved|unresolved)\b",
            after_full,
        ):
            inherited_candidates: List[tuple[int, str]] = []
            for kind, pattern in dimension_patterns:
                if not kind.endswith("_count"):
                    continue
                matches = list(re.finditer(pattern, before_full))
                if matches:
                    inherited_candidates.append(
                        (len(before_full) - matches[-1].end(), kind)
                    )
            if inherited_candidates:
                nearest_kind = min(inherited_candidates)[1]
                semantic_candidate_kinds.add(nearest_kind)

    if nearest_kind == "generic":
        broad_before = re.split(r"[.!?\n]", before_full)[-1]
        broad_after = re.split(r"[.!?\n]", after_full)[0]
        broad_context = f"{broad_before} {broad_after}".replace("_", " ")
        broad_entity_kinds = {
            kind
            for kind, pattern in dimension_patterns
            if kind.endswith("_count") and re.search(pattern, broad_context)
        }
        if len(broad_entity_kinds) == 1:
            nearest_kind = next(iter(broad_entity_kinds))
    if has_percent:
        percent_dimensions: set[str] = set()
        for kind in semantic_candidate_kinds:
            if kind.endswith("_count"):
                percent_dimensions.add(kind[:-6])
            elif kind in {"currency", "rate", "score"}:
                percent_dimensions.add(kind)
        if currency_code:
            percent_dimensions.add("currency")
        # ``40% of at-risk total`` is a currency/exposure ratio even when the
        # nearest dollar amount or ARR label sits before a comma.  Require an
        # explicit financial denominator phrase after the percentage so a
        # nearby monetary value cannot re-type an unrelated customer rate.
        if re.match(
            r"\s*of\s+(?:the\s+)?(?:arr|revenue|tcv|contract\s+value|"
            r"renewal\s+value|expiring\s+arr|at[- ]risk(?:\s+(?:arr|total|exposure))?)\b",
            after_full,
        ):
            percent_dimensions.add("currency")
        return (
            "_".join(sorted(percent_dimensions)) + "_percent"
            if percent_dimensions
            else "percent"
        )
    if currency_code:
        return f"currency:{currency_code}"
    if looks_like_year and nearest_kind in {"generic", "date"}:
        return "date"
    if nearest_kind == "rate":
        return "percent"
    if nearest_kind == "currency":
        return "currency:UNSPECIFIED"
    if nearest_kind == "duration":
        unit_matches = list(
            re.finditer(
                r"\b(hours?|days?|weeks?|months?|quarters?|years?)\b",
                f"{before} {after}",
            )
        )
        if unit_matches:
            unit = unit_matches[-1].group(1).rstrip("s")
            return f"duration:{unit}"
    return nearest_kind


def extract_typed_quantities(value: Any) -> List[TypedQuantity]:
    """Extract normalized, typed quantities from text in source order."""

    text = normalize_numeric_text(value)
    out: List[TypedQuantity] = []
    multipliers = {
        "k": 1_000.0,
        "thousand": 1_000.0,
        "m": 1_000_000.0,
        "mm": 1_000_000.0,
        "million": 1_000_000.0,
        "b": 1_000_000_000.0,
        "bn": 1_000_000_000.0,
        "billion": 1_000_000_000.0,
    }
    for match in _NUMBER_RE.finditer(text):
        # Hyphen/slash/colon can separate a real numeric range (5-10), but it
        # also separates an identifier prefix (CASE-123).  Keep the former and
        # drop the latter based on the character before the separator.
        prefix_context = text[max(0, match.start() - 32):match.start()]
        if re.search(
            r"\b(?:CSC|BEMS|INC|SP|AP|CASE|AB)[-_:/]\s*$",
            prefix_context,
            flags=re.IGNORECASE,
        ):
            continue
        prefix = match.group("prefix") or ""
        scale = (match.group("scale") or "").strip().casefold()
        try:
            number = float(match.group("number").replace(",", ""))
        except (TypeError, ValueError):
            continue
        sign = -1.0 if "-" in prefix else 1.0
        currency_match = re.search(
            r"[$€£]|\b(?:USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR|NZD|SEK|NOK|DKK|BRL|MXN|SGD|HKD|ZAR)\b",
            prefix,
            re.I,
        )
        currency_token = currency_match.group(0).upper() if currency_match else ""
        currency_code = {"$": "USD", "€": "EUR", "£": "GBP"}.get(
            currency_token,
            currency_token,
        )
        has_percent = bool(match.group("percent"))
        # Parentheses are frequently explanatory in generated prose
        # (``ARR ($500k)``), so they are not treated as a sign here.  KPI-level
        # accounting negatives are handled explicitly by the R95 parser.
        number *= sign * multipliers.get(scale, 1.0)
        if not math.isfinite(number):
            continue
        out.append(
            TypedQuantity(
                value=number,
                kind=_quantity_kind(
                    text,
                    match,
                    currency_code=currency_code,
                    has_percent=has_percent,
                ),
                raw=match.group(0).strip(),
                start=match.start(),
                end=match.end(),
            )
        )
    return out


def typed_quantity_matches(
    claimed: TypedQuantity,
    evidence: TypedQuantity,
    *,
    absolute_tolerance: float = 1e-9,
    relative_tolerance: float = 1e-9,
) -> bool:
    """Return whether two quantities have the same meaning and value."""

    if not typed_quantity_kinds_compatible(claimed.kind, evidence.kind):
        return False
    return math.isclose(
        claimed.value,
        evidence.value,
        rel_tol=max(float(relative_tolerance), 0.0),
        abs_tol=max(float(absolute_tolerance), 0.0),
    )


def typed_quantity_kinds_compatible(claimed_kind: str, evidence_kind: str) -> bool:
    """Directional kind compatibility for a claim against evidence."""

    if claimed_kind.startswith("currency:") and evidence_kind.startswith("currency:"):
        claimed_currency = claimed_kind.split(":", 1)[1]
        evidence_currency = evidence_kind.split(":", 1)[1]
        if claimed_currency != "UNSPECIFIED" and (
            evidence_currency == "UNSPECIFIED"
            or claimed_currency != evidence_currency
        ):
            return False
        return True
    # Percentage dimensions are directional.  A generic claim (``75%``) may
    # be supported by more-specific evidence (``customer_rate_percent``), and
    # a partially-qualified claim may be supported when all of its dimensions
    # occur in the evidence.  The inverse is deliberately rejected: generic
    # evidence must not authenticate a claim about ARR, customers, or cases.
    if claimed_kind == "percent" and (
        evidence_kind == "percent" or evidence_kind.endswith("_percent")
    ):
        return True
    if claimed_kind.endswith("_percent") and evidence_kind.endswith("_percent"):
        claimed_dimensions = set(claimed_kind[:-8].split("_"))
        evidence_dimensions = set(evidence_kind[:-8].split("_"))
        return claimed_dimensions <= evidence_dimensions
    return claimed_kind == evidence_kind


def all_typed_quantities_supported(
    claimed: Iterable[TypedQuantity],
    evidence: Iterable[TypedQuantity],
    *,
    absolute_tolerance: float = 1e-9,
    relative_tolerance: float = 1e-9,
) -> bool:
    evidence_list = list(evidence)
    return all(
        any(
            typed_quantity_matches(
                quantity,
                candidate,
                absolute_tolerance=absolute_tolerance,
                relative_tolerance=relative_tolerance,
            )
            for candidate in evidence_list
        )
        for quantity in claimed
    )
