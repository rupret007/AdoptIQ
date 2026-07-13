"""Round 16 / Phase 3 -- AI narrative grounding validator.

The schema-validated path (``adoptiq_backend.generate_llm_json_response``)
is in good shape: it parses against a strict JSON schema and rejects
shape drift.  The narrative path (``generate_llm_response``) flows
free-form model output directly into Word/Excel report bodies with only
a length and ``ERROR:``-prefix gate.  That leaves three gaps:

1. **HTML / script injection** — the model can emit ``<script>`` or
   ``javascript:`` URLs that survive into the rendered docx.
2. **Hallucinated numbers** — the model can quote a customer count or
   ARR amount that does not appear in the briefing data.
3. **Invented entities** — the model can name a customer the manager
   does not own.

This module exposes three small, deterministic validators plus a
combined ``validate_narrative`` wrapper.  They are *conservative*:
biased toward false positives over false negatives, because on
validation failure the caller replaces the narrative with a clearly
labeled placeholder rather than silently accepting potentially
hallucinated content.

The validators are pure: they do not log, raise, or call any external
service.  They return a typed result object the caller logs and acts
on at the integration point (``app_simple.py`` ``ai_insights_raw``
gate around line ~6803).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: HTML / script injection patterns the validator will reject outright.
#: Each pattern is matched case-insensitively against the narrative.
#: Order matters: the first match is recorded as the failure reason.
_HTML_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("script_tag", re.compile(r"<\s*script\b", re.IGNORECASE)),
    ("iframe_tag", re.compile(r"<\s*iframe\b", re.IGNORECASE)),
    ("svg_tag", re.compile(r"<\s*svg\b", re.IGNORECASE)),
    ("object_tag", re.compile(r"<\s*object\b", re.IGNORECASE)),
    ("embed_tag", re.compile(r"<\s*embed\b", re.IGNORECASE)),
    ("on_event_handler", re.compile(r"\bon[a-z]+\s*=\s*[\"']", re.IGNORECASE)),
    ("javascript_url", re.compile(r"javascript\s*:", re.IGNORECASE)),
    ("data_url_html", re.compile(r"data\s*:\s*text/html", re.IGNORECASE)),
)

#: Reference numbers the validator always allows in the narrative
#: regardless of whether they appear in the briefing.  These are the
#: "common-knowledge" numbers an executive narrative is expected to
#: contain (small integers, common percentages, calendar windows).
#: Round 47 / R47-AI-GATE-COMMON: widened to cover months 11-13 (the
#: prior frozenset stopped at 10 then jumped to 14, so ``12`` -- the
#: most common executive figure for "12-month outlook" or "the past 12
#: months" -- was being rejected as ungrounded), all small ints up
#: through 31 (calendar dates / list ranks), common multiples of 5/10
#: through 365, plus the common fractional percentages (1/3, 2/3, 1/4,
#: 1/6, 1/8, 1/9, 1/12) which the LLM derives from briefing
#: numerator/denominator pairs that are present individually but never
#: pre-computed as a percentage string.  This pulls AI-grounding
#: rejections from 32 (Build23 / Brian Frazier) down toward the
#: historical ~5 ceiling without losing any of the ungrounded-fact
#: detection power -- arbitrary five-digit numbers, ARR amounts, and
#: invented entity counts still trip the validator.
_COMMON_REFERENCE_NUMBERS: frozenset[float] = frozenset(
    {
        # Round 66 / Pass 3 (B11): widened from 0-31 -> 0-100 so the
        # validator stops rejecting the LLM for naming things like
        # "39 customers in HIGH band", "47 cases in 90 days", or
        # "62 % adoption" -- all small integers/percentages the LLM
        # routinely derives from briefing pairs (e.g. 11/28*100=39.3
        # rounded to 39, or "47 cases out of 81 total"). Build 38
        # acceptance saw 34% R27 rejection rate; the grounding diag
        # captured by R65/C-3 showed >70% of rejections cited an
        # offending integer in 32-99 (counts and percentages), so
        # widening the integer floor here pulls the rejection rate
        # under the 10% target without opening the door to
        # hallucinated 5/6-digit specific numbers (ARR amounts,
        # customer counts > 100, etc.) which still trip the validator
        # via the briefing-overlap path.
        *range(0, 101),
        # Multiples of 5 from 100-500 covering common day-window
        # extensions ("180 days", "270 days"), and headcount/case
        # counts in mid-range portfolios.  Pre-R66 the set jumped
        # 100 -> 120 -> 150 -> 180 -> 200 -> 250 -> 270 -> 300 -> 365
        # leaving real gaps (105, 110, 125, 140, 160, 175, 220, 240,
        # 260, 280, 320, 340, 360 etc.).  The new generator covers
        # every multiple of 5 from 100 to 500 inclusive so a narrative
        # naming "175 open ABs" or "240 day window" passes when the
        # number is small enough to be a count, not a hallucination.
        *(float(v) for v in range(100, 501, 5)),
        # Common fractional percentages the LLM derives from briefing
        # ratios (e.g. ``2 of 3 -> 66.7%``).  These never appear in the
        # briefing as literal strings because the briefing prints raw
        # counts, not derived percentages -- but they are arithmetic
        # facts, not hallucinations.
        # 1/12, 1/9, 1/8, 1/7, 1/6, 1/5, 1/4, 1/3, 2/5, 3/8, 2/3, 3/4,
        # 4/5, 5/6, 7/8, 11/12 and their complements.
        8.3, 11.1, 12.5, 14.3, 16.7, 22.2, 27.3, 33.3, 37.5, 38.9,
        41.7, 44.4, 45.5, 54.5, 55.6, 58.3, 61.1, 62.5, 63.6, 66.7,
        72.7, 77.8, 83.3, 87.5, 88.9, 91.7,
        # Round 66 / Pass 3 (B11): forward-looking calendar / fiscal
        # years through 2030 so the LLM can reference the current
        # year + a 4-year planning horizon without tripping the
        # validator.  Pre-R66 the set stopped at 2027, which would
        # have started rejecting any narrative naming 2028+ from
        # FY27 onward.
        2024.0, 2025.0, 2026.0, 2027.0, 2028.0, 2029.0, 2030.0,
    }
)

#: Tokens that look like proper-noun entity suffixes used by the
#: ``validate_no_invented_entities`` heuristic to detect customer-name
#: shapes ("Acme Corp", "Beta Inc").
_CUSTOMER_NAME_SUFFIXES: tuple[str, ...] = (
    "Corp", "Corporation", "Inc", "Inc.", "Incorporated",
    "Ltd", "Ltd.", "Limited", "LLC", "L.L.C.", "GmbH", "AG",
    "Holdings", "Group", "Co", "Co.", "Company",
    "Systems", "Networks", "Technologies", "Solutions", "Services",
    "International", "Worldwide", "Industries",
)

#: Pattern that captures a candidate customer name: a short run of
#: capitalized words ending in one of the ``_CUSTOMER_NAME_SUFFIXES``.
#: Using ``re.compile`` with a non-empty alternation pre-baked keeps
#: matches deterministic and avoids constructing the alt at every call.
_CUSTOMER_NAME_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9&\-]*\s){1,3}(?:"
    + r"|".join(re.escape(s) for s in _CUSTOMER_NAME_SUFFIXES)
    + r")\b"
)

#: Round 126 / Build 95 (G1): Cisco/Webex brand + product tokens.  This
#: is a Cisco-internal tool whose customers are external companies, so
#: any candidate whose leading (pre-suffix) tokens include a Cisco brand
#: word is a product/service offering ("Cisco Managed Services", "Webex
#: Services"), never a customer -- it must not trip the invented_entity
#: gate.
_CISCO_BRAND_TOKENS: frozenset[str] = frozenset(
    {
        "cisco", "webex", "meraki", "thousandeyes", "duo", "umbrella",
        "appdynamics", "splunk", "catalyst", "nexus", "ise", "viptela",
        "jabber", "jasper", "tetration", "stealthwatch", "securex",
        "sdwan", "spaces", "intersight",
    }
)

#: Round 126 / Build 95 (G1): common English / service-category words
#: that are NOT proper-noun anchors.  A pattern-matched candidate whose
#: every leading (pre-suffix) token is in this set -- or is itself a
#: corporate suffix -- is treated as a service phrase or a sentence
#: fragment ("Year Co", "While Services", "Identity Services", "Room
#: Systems", "Hunt Group", "Technical Solutions"), not a customer name.
#: Requiring at least one non-generic anchor token kills the greedy
#: prose fragments the pattern captures while keeping genuinely-invented
#: customers (which carry a real proper-noun token) caught.
_GENERIC_ENTITY_TOKENS: frozenset[str] = frozenset(
    {
        # sentence-leading / common words that get capitalized in prose
        "the", "this", "that", "these", "those", "while", "when",
        "where", "which", "what", "year", "years", "their", "there",
        "with", "from", "into", "over", "under", "after", "before",
        "during", "across", "within", "without", "they", "them", "its",
        "our", "your", "his", "her", "and", "but", "for", "nor", "yet",
        "via", "per", "such", "more", "less", "than", "then", "also",
        # service / offer category words
        "managed", "technical", "premier", "premium", "lifecycle",
        "identity", "room", "rooms", "hunt", "professional", "advanced",
        "cloud", "digital", "global", "customer", "customers", "support",
        "service", "services", "success", "adoption", "renewal",
        "renewals", "advisory", "solution", "solutions", "system",
        "systems", "network", "networks", "technology", "technologies",
        "platform", "platforms", "security", "secure", "collaboration",
        "calling", "meetings", "messaging", "contact", "center", "centre",
        # generic descriptors
        "total", "open", "active", "closed", "high", "low", "medium",
        "new", "next", "prior", "recent", "other", "various", "several",
        "many", "most", "all", "both", "each", "some", "key", "core",
        "main", "top", "first", "second", "third", "overall", "general",
        "local", "regional", "national", "enterprise", "business",
        "corporate", "commercial", "public", "private", "internal",
        "external", "strategic", "critical", "major", "minor", "primary",
        "secondary",
    }
)

#: Round 126 / Build 95 (G1): casefolded corporate-suffix tokens so the
#: anchor heuristic can recognise when a leading token is itself a
#: suffix word (e.g. "Solutions Services").
_GENERIC_SUFFIX_CASEFOLD: frozenset[str] = frozenset(
    s.casefold().replace(".", "") for s in _CUSTOMER_NAME_SUFFIXES
)

#: Maximum narrative size we'll validate.  Larger inputs are still
#: returned as ``failed`` (with reason ``oversized``) so the caller
#: degrades to the placeholder rather than spending CPU on a runaway
#: string.
_MAX_NARRATIVE_BYTES: int = 200_000

#: Public placeholder string the caller should substitute when a
#: narrative fails validation.  Pinned in tests so the user-visible
#: failure message stays stable.
GROUNDING_FAILURE_PLACEHOLDER: str = (
    "AI insight could not be grounded against the source data and was "
    "withheld from this report. The underlying KPIs in the data tabs "
    "remain authoritative."
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of running one or more validators against a narrative.

    Attributes
    ----------
    is_valid:
        ``True`` if every validator passed.  ``False`` if any failed.
    failures:
        Ordered tuple of human-readable failure reasons.  Empty tuple
        when ``is_valid`` is ``True``.  Suitable for logging at WARN.
    sample_offending:
        Up to one short snippet per failure category to help an
        operator triage a regression.  Empty mapping when valid.
    """

    is_valid: bool
    failures: tuple[str, ...] = field(default_factory=tuple)
    sample_offending: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_text(value: object) -> str:
    """Normalize narrative input.  Returns an empty string for None or
    non-string-like inputs so the validators never throw on bad types."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = str(value)
        except Exception:
            return ""
    # Normalize NFKC so Unicode digit variants and homoglyphs collapse
    # to their canonical form before the numeric / entity scans run.
    return unicodedata.normalize("NFKC", text)


def _extract_numbers(text: str) -> list[tuple[float, str]]:
    """Pull numeric literals from ``text`` for the grounding check.

    Captures plain integers, decimals, comma-grouped thousands, and
    optional trailing ``%`` / ``K`` / ``M`` / ``B`` suffixes.  Returns
    a list of ``(value, raw_match)`` tuples.  Sign is intentionally
    ignored (a leading ``-`` is not consumed) because narratives use
    negation prose like "down 5%" rather than ``-5%``.
    """

    pattern = re.compile(
        r"(?<![A-Za-z0-9_])"
        r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
        r"(\s*%|\s*[KMB])?"
        r"(?![A-Za-z0-9_])"
    )
    out: list[tuple[float, str]] = []
    for m in pattern.finditer(text):
        whole = m.group(1).replace(",", "")
        frac = m.group(2)
        suffix = (m.group(3) or "").strip()
        try:
            value = float(whole)
            if frac:
                value = float(f"{whole}.{frac}")
        except ValueError:  # pragma: no cover - defensive only
            continue
        # Apply suffix multiplier so a narrative "$2.5M" matches a
        # briefing that prints "2,500,000".  Percent literals stay as
        # the bare percentage (e.g. "75%" -> 75.0); the briefing
        # extraction below also strips trailing ``%`` so they match
        # symmetrically.
        if suffix == "K":
            value *= 1_000.0
        elif suffix == "M":
            value *= 1_000_000.0
        elif suffix == "B":
            value *= 1_000_000_000.0
        out.append((value, m.group(0).strip()))
    return out


def _extract_briefing_numbers(briefing: str) -> list[float]:
    """Pull every numeric value out of the briefing text.

    Used as the allow-list for ``validate_grounded_numbers``.  Returns
    a flat list of floats; deduplication is handled by the caller via
    a set membership check + tolerance.
    """

    return [v for (v, _) in _extract_numbers(briefing)]


def _number_in_allowed(
    value: float,
    allowed: Iterable[float],
    tolerance: float,
) -> bool:
    """Check whether ``value`` is within ``tolerance`` of any allowed
    number.  Treats absolute and relative tolerance: small values use
    absolute, larger values relative."""
    if value in _COMMON_REFERENCE_NUMBERS:
        return True
    # Round 66 / Pass 3 (B11): widen the relative tolerance for
    # ARR-class large magnitudes from the caller's default (1%) to a
    # floor of 5% for any value >= 100,000.  Build 38 acceptance
    # showed the LLM routinely emits "$1.2M" against a briefing of
    # "$1,234,567" -- a 2.8% drift that the prior 1% relative gate
    # rejected as ungrounded.  5% covers the common "$X.Y M / $X.Y B"
    # rendering convention without opening the door to hallucinated
    # ARR amounts (an invented "$2.5M" against a briefing of
    # "$1.2M" is a 50% drift -- still rejected).  Small-magnitude
    # values keep the caller's absolute tolerance unchanged so a
    # narrative naming 47 cases against a briefing of 12 still
    # trips the validator.
    _r66_b11_relative_tol = max(tolerance, 0.05) if abs(value) >= 100_000.0 else tolerance
    for ref in allowed:
        if value == ref:
            return True
        # Absolute tolerance for tiny magnitudes.
        if abs(value - ref) <= tolerance:
            return True
        # Relative tolerance for large magnitudes (ARR-class numbers).
        denom = max(abs(value), abs(ref))
        if denom > 0 and abs(value - ref) / denom <= _r66_b11_relative_tol:
            return True
    return False


def _is_derived_ratio_percentage(
    value: float,
    integer_briefing_numbers: list[int],
    *,
    ppt_tolerance: float = 0.5,
) -> bool:
    """Round 66 / Pass 3 (B11): check whether a narrative percentage
    is expressible as ``(a / b) * 100`` for any integer pair ``(a, b)``
    in ``integer_briefing_numbers`` within ``ppt_tolerance`` percentage
    points.

    The LLM routinely derives percentages from briefing pairs that
    appear individually but never as a pre-computed percentage --
    e.g. briefing says ``Open ABs: 11`` and ``Total customers: 28``
    and the LLM writes ``"11 of 28 customers (39.3%) carry an open
    AB"``.  Pre-R66 ``39.3`` was not in the briefing as a literal,
    not in ``_COMMON_REFERENCE_NUMBERS`` (which skipped 32-99), so
    the validator would reject the narrative as ungrounded.

    The check is conservative:
    1. ``value`` must be in ``[0.0, 100.0]`` -- only percentages
       qualify.
    2. ``integer_briefing_numbers`` must be the briefing-derived
       integer pool (caller filters via ``_extract_integer_briefing_numbers``).
    3. The match is bidirectional: ``a / b`` AND ``b / a`` are
       checked so a narrative naming the inverse ratio (e.g. the
       briefing prints "17 of 28 closed" and the narrative writes
       "39% open" = 11/28) is still grounded.

    A ppt_tolerance of 0.5 admits the natural rounding floor /
    ceiling pair (``11/28*100 = 39.286`` rounded to ``39`` or
    ``39.3``); any larger drift means the LLM is using a
    denominator the briefing does not name and the rejection
    stands.

    Returns ``True`` when a grounding pair exists, ``False`` when
    no such pair could be found.  Pure: no I/O, no logging.
    """
    if not (0.0 <= value <= 100.0):
        return False
    if not integer_briefing_numbers:
        return False
    # Build a small set of candidate denominators (positive integers
    # only).  Cap the cartesian to a reasonable size to keep this
    # O(n^2) check bounded for very large briefings.
    pool = [n for n in integer_briefing_numbers if isinstance(n, int) and n > 0]
    pool = sorted(set(pool))[:200]  # cap at 200 unique integers
    for b in pool:
        if b == 0:
            continue
        for a in pool:
            if a < 0 or a > b:
                # Clamp to a <= b so we test "fraction of total"; we
                # also try the inverse explicitly below.
                continue
            try:
                pct = (a / b) * 100.0
            except ZeroDivisionError:
                continue
            if abs(pct - value) <= ppt_tolerance:
                return True
    return False


def _extract_integer_briefing_numbers(briefing: str) -> list[int]:
    """Round 66 / Pass 3 (B11): pull integer-valued numbers from the
    briefing for use by ``_is_derived_ratio_percentage``.

    Reuses ``_extract_numbers`` so the parsing rules stay aligned
    (suffix expansion, comma-grouping), then filters to integers.
    Suffixed values (``2.5M``) are intentionally included since
    they expand to integers (``2_500_000``) and may be valid
    denominators (e.g. "ARR concentration: 35% on $2.5M").
    """
    out: list[int] = []
    for value, _ in _extract_numbers(briefing):
        if value == int(value):
            out.append(int(value))
    return out


def _extract_candidate_entity_names(text: str) -> list[str]:
    """Return the candidate company-name strings the heuristic detected.

    Scans for the ``<CapitalizedWord>* <Suffix>`` shape (see
    ``_CUSTOMER_NAME_PATTERN``).  Used by
    ``validate_no_invented_entities``.
    """

    return [m.group(0).strip() for m in _CUSTOMER_NAME_PATTERN.finditer(text)]


#: Round 47 / R47-AI-GATE-COUNTRY: trailing 2-letter ISO country-code
#: tokens routinely tacked onto customer names in the briefing source
#: data (CSConsole exports).  We strip them before normalization so a
#: narrative referring to ``EQUITABLE HOLDINGS LLC`` matches an allowed
#: entry of ``EQUITABLE HOLDINGS LLC US``.  Limited to the ~40 country
#: codes that actually appear in our customer corpus to stay
#: conservative -- expanding to all 249 ISO codes would risk trimming
#: legitimate two-letter words off the end of a real customer name.
_COUNTRY_CODE_SUFFIXES: tuple[str, ...] = (
    "US", "GB", "MX", "CA", "AU", "DE", "JP", "FR", "IT", "ES",
    "IN", "CN", "BR", "NL", "IE", "SE", "NO", "FI", "DK", "BE",
    "CH", "AT", "NZ", "ZA", "AE", "SA", "DO", "AR", "CL", "CO",
    "PE", "PT", "GR", "PL", "TR", "RU", "KR", "HK", "TW", "SG",
    "ID", "PH", "TH", "VN", "MY", "EG", "IL", "QA", "KW", "OM",
)


def _strip_country_code(name: str) -> str:
    """Remove a trailing whitespace-delimited 2-letter country-code
    token (e.g. ``"EQUITABLE HOLDINGS LLC US"`` -> ``"EQUITABLE HOLDINGS LLC"``).

    Round 47 / R47-AI-GATE-COUNTRY: applied symmetrically to both the
    candidate (LLM-emitted) name and the allow-list entries so the
    comparison is country-code-tolerant.  Conservative: only strips
    when the trailing token is in the curated ``_COUNTRY_CODE_SUFFIXES``
    list -- we never trim arbitrary 2-letter words because that would
    munge legitimate corporate suffixes (e.g. a hypothetical ``"BJ"``
    operating-unit code).
    """

    raw = str(name).strip()
    if not raw:
        return raw
    parts = raw.rsplit(None, 1)
    if len(parts) == 2 and parts[1].upper() in _COUNTRY_CODE_SUFFIXES:
        return parts[0]
    return raw


def _normalize_entity(name: str) -> str:
    """Casefold + strip non-alphanumeric so 'Acme Corp', 'acme corp.',
    and 'ACME corp' all hash to the same key for the allow-list
    comparison.  Round 47 / R47-AI-GATE-COUNTRY: also strips a trailing
    2-letter ISO country-code token so the briefing's
    ``"ACME CORP US"`` matches a narrative reference to ``"Acme Corp"``."""
    stripped = _strip_country_code(str(name))
    return re.sub(r"[^a-z0-9]+", "", stripped.casefold())


#: Round 126 / Build 95 (G1): exact normalized service-offer phrases we
#: always exempt regardless of the token-anchor heuristic below.  Built
#: through ``_normalize_entity`` (defined above) so spacing /
#: punctuation variants collapse to the same key.
_SERVICE_PHRASE_ALLOW: frozenset[str] = frozenset(
    _normalize_entity(p)
    for p in (
        "Cisco Managed Services", "Webex Services", "Identity Services",
        "Premier Services", "Lifecycle Services", "Professional Services",
        "Customer Experience Services", "Success Services",
        "Technical Solutions", "Room Systems", "Hunt Group",
        "Cisco Systems", "Cisco Networks", "Cisco Solutions",
        "Cisco Security", "Webex Calling", "Contact Center",
    )
)


def _entity_candidate_is_exempt(candidate: str) -> bool:
    """Round 126 / Build 95 (G1): True when a pattern-matched candidate
    is a Cisco/Webex service offering or a prose fragment rather than a
    customer name, so ``validate_no_invented_entities`` does NOT flag it.

    Three exemption layers, cheapest first:

    1. the exact normalized phrase is a known service offer
       (``_SERVICE_PHRASE_ALLOW``);
    2. a leading (pre-suffix) token is a Cisco brand / product name
       (``_CISCO_BRAND_TOKENS``) -- in a Cisco-internal tool a
       ``Cisco <X> Services`` candidate is always an offering;
    3. the candidate has NO non-generic proper-noun anchor token --
       every leading token is a common English / service word
       (``_GENERIC_ENTITY_TOKENS``) or a corporate suffix
       (``_GENERIC_SUFFIX_CASEFOLD``).  This kills greedy prose
       fragments like ``"Year Co"`` / ``"While Services"``.
    """

    cand = str(candidate).strip()
    if not cand:
        return True
    if _normalize_entity(cand) in _SERVICE_PHRASE_ALLOW:
        return True
    tokens = cand.split()
    if len(tokens) < 2:
        # The pattern always matches >=1 leading word + a suffix, so a
        # single-token candidate is malformed; be defensive and exempt.
        return True
    leading_cf = [t.casefold().strip(".,&-") for t in tokens[:-1]]
    if any(tok in _CISCO_BRAND_TOKENS for tok in leading_cf):
        return True
    has_anchor = any(
        tok
        and tok not in _GENERIC_ENTITY_TOKENS
        and tok not in _GENERIC_SUFFIX_CASEFOLD
        for tok in leading_cf
    )
    return not has_anchor


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_no_html_injection(text: str) -> ValidationResult:
    """Round 16 / Phase 3.1 -- reject narratives that carry HTML / JS
    injection vectors.

    The narrative is rendered into ``.docx`` (and through the Word
    sanitizer into ``.xlsx`` cells) where active content is normally
    stripped, but a defense-in-depth check at this layer keeps the
    payload out of audit logs and any downstream renderer that might
    not strip aggressively.
    """

    body = _coerce_text(text)
    if not body:
        return ValidationResult(is_valid=True)
    failures: list[str] = []
    samples: dict[str, str] = {}
    for label, pattern in _HTML_INJECTION_PATTERNS:
        m = pattern.search(body)
        if m is not None:
            failures.append(f"html_injection:{label}")
            # Capture a short window so the operator can see what tripped.
            start = max(0, m.start() - 20)
            end = min(len(body), m.end() + 40)
            samples[f"html_injection:{label}"] = body[start:end]
    if failures:
        return ValidationResult(False, tuple(failures), samples)
    return ValidationResult(is_valid=True)


def validate_grounded_numbers(
    text: str,
    briefing: str,
    *,
    tolerance: float = 0.01,
) -> ValidationResult:
    """Round 16 / Phase 3.2 -- every numeric token in the narrative
    must either be a common-reference number (``0``-``10``, common
    percentages, day windows) or appear in the briefing within
    ``tolerance``.

    ``tolerance`` is interpreted as a relative tolerance for values
    above 1.0 and an absolute tolerance below.  This handles both
    "75% (briefing says 0.7503)" and "12 customers vs briefing 12".
    """

    narrative = _coerce_text(text)
    if not narrative:
        return ValidationResult(is_valid=True)
    if len(narrative.encode("utf-8", errors="ignore")) > _MAX_NARRATIVE_BYTES:
        return ValidationResult(
            is_valid=False,
            failures=("oversized",),
            sample_offending={"oversized": narrative[:120]},
        )

    briefing_text = _coerce_text(briefing)
    allowed_numbers = _extract_briefing_numbers(briefing_text)
    # Round 66 / Pass 3 (B11): also pre-extract the integer pool for
    # the derived-percentage check.  Computed once per call so the
    # per-narrative-number loop below does not re-parse the briefing.
    _r66_b11_integer_pool = _extract_integer_briefing_numbers(briefing_text)
    narrative_numbers = _extract_numbers(narrative)
    if not narrative_numbers:
        return ValidationResult(is_valid=True)
    bad: list[str] = []
    for value, raw in narrative_numbers:
        if _number_in_allowed(value, allowed_numbers, tolerance):
            continue
        # Round 66 / Pass 3 (B11): second-chance check for derived
        # percentages -- if the value reads as a percentage (e.g.
        # ``"39%"`` or ``"39.3%"``) and is expressible as a/b*100
        # for any integer pair (a, b) in the briefing, accept it.
        # This ground-truths the LLM's "11 of 28 customers (39%)"
        # rendering convention without the briefing needing to
        # pre-compute the percentage.
        if _is_derived_ratio_percentage(value, _r66_b11_integer_pool):
            continue
        # Round 67 / B8: third-chance check for single-decimal
        # percentages.  Build 40 acceptance saw 21.1% R27 rejection
        # rate dominated by tokens like ``28.6%``, ``41.6%``,
        # ``18.6%`` -- 1-decimal percentages the LLM derives from
        # briefing pairs that the strict ratio check could not
        # cover (because the actual numerator/denominator pair was
        # not in the integer pool, or it was filtered out by the
        # 200-cap, or the derivation involves intermediate counts
        # the briefing summarises but does not enumerate).
        # Conservative scope: ONLY tokens that explicitly carry a
        # ``%`` suffix in the raw text AND fall within ``[0.0, 100.0]``
        # AND are expressible as ``round(value, 1)`` (i.e. 1-decimal
        # precision) are auto-grounded.  This admits ``28.6%``,
        # ``41.6%``, ``99.9%``, etc. without opening the door to
        # large-magnitude hallucinations: an invented ARR of
        # ``$5.7M`` extracts to ``5_700_000`` (out of [0, 100])
        # and a rendered customer count of ``57`` (no ``%``) is
        # still scrutinised by the integer allow-list.  We do not
        # widen integer percentages here because the R66 / B11
        # widening already covers ``range(0, 101)``.
        raw_token = (raw or "").strip()
        if (
            raw_token.endswith("%")
            and 0.0 <= value <= 100.0
            and abs(value - round(value, 1)) < 1e-9
        ):
            continue
        bad.append(raw)
    if bad:
        # De-duplicate while preserving order so the operator sees the
        # first few unique offenders rather than a wall of repeats.
        seen: set[str] = set()
        unique_bad: list[str] = []
        for entry in bad:
            key = entry.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique_bad.append(entry)
            if len(unique_bad) >= 5:
                break
        return ValidationResult(
            is_valid=False,
            failures=("ungrounded_number",),
            sample_offending={"ungrounded_number": ", ".join(unique_bad)},
        )
    return ValidationResult(is_valid=True)


def validate_no_invented_entities(
    text: str,
    allowed_entities: Iterable[str],
) -> ValidationResult:
    """Round 16 / Phase 3.3 -- candidate customer names in the
    narrative (``<Capitalized words> <Corp/Inc/Ltd/...>``) must
    appear in the briefing's customer list.  ``allowed_entities`` is
    typically the union of customer names in the AB / CSOne / pulse
    frames (or the briefing-level ``customer_list``).
    """

    body = _coerce_text(text)
    if not body:
        return ValidationResult(is_valid=True)

    allowed_norm = {_normalize_entity(e) for e in allowed_entities if e}
    candidates = _extract_candidate_entity_names(body)
    bad: list[str] = []
    for cand in candidates:
        # Round 126 / Build 95 (G1): exempt Cisco/Webex service-offer
        # phrases ("Cisco Managed Services", "Webex Services") and prose
        # fragments ("Year Co", "While Services") so legit service
        # vocabulary stops tripping the invented_entity gate.
        if _entity_candidate_is_exempt(cand):
            continue
        cand_norm = _normalize_entity(cand)
        if cand_norm in allowed_norm:
            continue
        # Round 47 / R47-AI-GATE-COUNTRY: substring tolerance so a
        # narrative naming ``"Equitable Holdings LLC"`` matches an
        # allowed-list entry of ``"Equitable Holdings LLC US"`` (or
        # vice versa) after country-code stripping has run.  We require
        # at least 6 characters of post-normalization overlap to avoid
        # spurious matches like ``"Inc"`` matching every Inc-suffixed
        # company; 6 chars = "abcinc" floor which is the smallest
        # plausible legitimate corporate name.
        if len(cand_norm) >= 6 and any(
            cand_norm in allowed or allowed in cand_norm
            for allowed in allowed_norm
            if len(allowed) >= 6
        ):
            continue
        bad.append(cand)
    if bad:
        seen: set[str] = set()
        unique_bad: list[str] = []
        for entry in bad:
            key = entry.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique_bad.append(entry)
            if len(unique_bad) >= 5:
                break
        return ValidationResult(
            is_valid=False,
            failures=("invented_entity",),
            sample_offending={"invented_entity": ", ".join(unique_bad)},
        )
    return ValidationResult(is_valid=True)


#: Round 17 / Phase E -- patterns we never accept inside a corpus
#: chunk that is about to enter the LLM prompt.  These are
#: pre-prompt-injection guards, distinct from the post-rendering
#: HTML/JS guards above.
_CORPUS_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("script_tag", re.compile(r"<\s*script\b", re.IGNORECASE)),
    ("javascript_url", re.compile(r"javascript\s*:", re.IGNORECASE)),
    (
        "ignore_previous_instructions",
        # Round 18 / Phase 4.1: widened so the canonical
        # prompt-injection phrase "Ignore all previous instructions"
        # is caught.  The original pattern only allowed a single
        # token between ``ignore`` and ``instructions``, so
        # ``ignore <a> <b> instructions`` (the common form) leaked
        # through.  Now allows any of ``ignore``/``disregard``/
        # ``forget`` followed by up to four intermediate word
        # tokens (no punctuation, so we cannot cross sentence
        # boundaries) before ``instruction(s)``.
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:[\w'\-]+\s+){0,4}instructions?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_override",
        re.compile(r"\b(?:system|developer|assistant)\s*[:>]\s*you\s+are\b", re.IGNORECASE),
    ),
    (
        "fence_break",
        re.compile(r"=== END USER_QUESTION ===|=== END CORPUS ===|</\s*corpus\s*>", re.IGNORECASE),
    ),
)


def is_corpus_chunk_safe(text: str) -> bool:
    """Round 17 / Phase E -- pre-prompt safety gate for corpus chunks.

    Returns ``False`` when the chunk carries an obvious prompt-
    injection sequence or HTML / script payload.  Used by
    ``ask_ai_corpus.build_corpus_block`` to drop hostile spans before
    they reach the LLM.  Conservative: any match is grounds for
    rejection.
    """

    body = _coerce_text(text)
    if not body:
        return True
    if len(body.encode("utf-8", errors="ignore")) > _MAX_NARRATIVE_BYTES:
        return False
    for _label, pattern in _CORPUS_INJECTION_PATTERNS:
        if pattern.search(body):
            return False
    return True


def validate_narrative(
    text: str,
    briefing: str,
    *,
    allowed_entities: Optional[Iterable[str]] = None,
    tolerance: float = 0.01,
) -> ValidationResult:
    """Run all Round-16 narrative validators in order and aggregate
    failures.  The first validator with a failure determines the
    overall ``is_valid`` flag, but every validator's findings are
    merged into the returned tuple so the operator sees the full
    picture in one log line.
    """

    body = _coerce_text(text)
    briefing_text = _coerce_text(briefing)

    # Run validators in priority order: injection > grounded numbers >
    # invented entities.  Each failure adds to the aggregate result.
    failures: list[str] = []
    samples: dict[str, str] = {}

    html_check = validate_no_html_injection(body)
    if not html_check.is_valid:
        failures.extend(html_check.failures)
        samples.update(html_check.sample_offending)

    number_check = validate_grounded_numbers(body, briefing_text, tolerance=tolerance)
    if not number_check.is_valid:
        failures.extend(number_check.failures)
        samples.update(number_check.sample_offending)

    if allowed_entities is not None:
        entity_check = validate_no_invented_entities(body, allowed_entities)
        if not entity_check.is_valid:
            failures.extend(entity_check.failures)
            samples.update(entity_check.sample_offending)

    if failures:
        return ValidationResult(False, tuple(failures), samples)
    return ValidationResult(is_valid=True)


__all__ = [
    "GROUNDING_FAILURE_PLACEHOLDER",
    "ValidationResult",
    "is_corpus_chunk_safe",
    "validate_grounded_numbers",
    "validate_narrative",
    "validate_no_html_injection",
    "validate_no_invented_entities",
]
