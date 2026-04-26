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
_COMMON_REFERENCE_NUMBERS: frozenset[float] = frozenset(
    {
        # Small ints that show up as ranks, list lengths, weeks, etc.
        *range(0, 11),
        # Tens and hundreds that show up in percentages and bands,
        # plus common day-window choices (7, 14, 30, 60, 90, 180, 365)
        # folded into one deduped sequence so ruff B033 stays happy.
        # ``7`` is already covered by ``range(0, 11)`` above.
        14.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0, 70.0, 75.0, 80.0,
        90.0, 100.0, 180.0, 365.0,
        # Calendar / fiscal years.
        2024.0, 2025.0, 2026.0, 2027.0,
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
    for ref in allowed:
        if value == ref:
            return True
        # Absolute tolerance for tiny magnitudes.
        if abs(value - ref) <= tolerance:
            return True
        # Relative tolerance for large magnitudes (ARR-class numbers).
        denom = max(abs(value), abs(ref))
        if denom > 0 and abs(value - ref) / denom <= tolerance:
            return True
    return False


def _extract_candidate_entity_names(text: str) -> list[str]:
    """Return the candidate company-name strings the heuristic detected.

    Scans for the ``<CapitalizedWord>* <Suffix>`` shape (see
    ``_CUSTOMER_NAME_PATTERN``).  Used by
    ``validate_no_invented_entities``.
    """

    return [m.group(0).strip() for m in _CUSTOMER_NAME_PATTERN.finditer(text)]


def _normalize_entity(name: str) -> str:
    """Casefold + strip non-alphanumeric so 'Acme Corp', 'acme corp.',
    and 'ACME corp' all hash to the same key for the allow-list
    comparison."""
    return re.sub(r"[^a-z0-9]+", "", str(name).casefold())


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

    allowed_numbers = _extract_briefing_numbers(_coerce_text(briefing))
    narrative_numbers = _extract_numbers(narrative)
    if not narrative_numbers:
        return ValidationResult(is_valid=True)
    bad: list[str] = []
    for value, raw in narrative_numbers:
        if not _number_in_allowed(value, allowed_numbers, tolerance):
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
        if _normalize_entity(cand) not in allowed_norm:
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
