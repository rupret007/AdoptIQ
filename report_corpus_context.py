"""Round 17 / Phase D.2 -- Historical-context section for the
Executive and Leader reports.

This helper sits between the Round 17 corpus retriever and the
Word/Excel report builders.  It is intentionally tiny and side-effect
free so the much larger report generators do not have to know
anything about the encrypted SQLite store, BM25, or the corpus
schema.

Public surface
--------------
- :class:`HistoricalEntry` -- one customer's worth of historical
  context, pre-shaped for direct rendering.
- :func:`build_historical_context` -- pulls per-customer history
  from the corpus and returns a list of ``HistoricalEntry``.
- :func:`render_to_word` -- appends a Word section onto a
  ``python-docx`` ``Document`` (or any object that exposes
  ``add_heading`` / ``add_paragraph``).
- :func:`render_to_text` -- returns a deterministic plain-text
  rendering used by Excel exports and by tests.

Safety contract
---------------
* Every retrieval failure (corpus disabled, SharePoint sign-in
  required, OneDrive offline, customer missing) degrades to an
  explanatory banner -- never an exception bubbling up into the
  report build.
* All free-form corpus text is run through
  :func:`ai_narrative_validator.is_corpus_chunk_safe` before being
  rendered, so a hostile sentinel cannot inject HTML / JS payloads
  into the report.
* All inputs are length-capped and non-string types are coerced or
  dropped to keep Word's text engine well-behaved.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import statistics  # Round 173
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone  # Round 173
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)


#: Maximum customers we ever render in the Historical Context section.
#: Reports beyond this cap are summarized via the "+N additional"
#: footer to keep the section readable in a printed Word document.
_MAX_CUSTOMERS_RENDERED: int = 10

#: Hard cap on the number of cases we cite per customer.
_MAX_CASES_PER_CUSTOMER: int = 5

#: Hard cap on per-string length we'll write into the Word document.
#: Anything longer is truncated with an ellipsis.
_MAX_LINE_CHARS: int = 220

#: Banner used when the corpus is enabled but unreachable.
_BANNER_UNAVAILABLE: str = (
    "No historical context available - sign in to the SharePoint "
    "share AdoptIQ_CSOne_Reports from the admin tile (or confirm your "
    "OneDrive sync) to enable the CSOne knowledge corpus."
)

#: Banner used when the corpus is explicitly disabled.
_BANNER_DISABLED: str = (
    "Historical context section is disabled "
    "(set CORPUS_KNOWLEDGE_ENABLED=true to enable)."
)

#: Banner used when the corpus is configured but no requested
#: customer matched.  Helpful so the operator can tell the
#: difference between "no corpus" and "corpus has no rows for this
#: customer".
_BANNER_NO_CUSTOMER_MATCH: str = (
    "No historical context found in the corpus for the customers "
    "in this report."
)

# Round 172: first-class report insights may consume one content-addressed
# corpus claim.  The receipt is deliberately a plain JSON payload so the
# canonical Source Data workbook can preserve and fingerprint it without a
# second store or any raw corpus rows.
_CORPUS_INSIGHT_RECEIPT_SCHEMA: str = "adoptiq.corpus-retriever-receipt.v1"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoricalCase:
    case_number: str
    severity: str
    status: str
    summary: str
    opened_at: str
    closed_at: str
    source_filename: Optional[str] = None


@dataclass(frozen=True)
class HistoricalTheme:
    theme: str
    occurrences: int
    technology: str = ""


@dataclass(frozen=True)
class HistoricalResolution:
    method_text: str
    theme: str = ""
    technology: str = ""
    source_filename: Optional[str] = None


@dataclass(frozen=True)
class HistoricalEntry:
    customer_name: str
    technology: Optional[str]
    first_seen: Optional[str]
    last_seen: Optional[str]
    occurrences: int
    cases: tuple[HistoricalCase, ...] = field(default_factory=tuple)
    barriers: tuple[HistoricalTheme, ...] = field(default_factory=tuple)
    resolutions: tuple[HistoricalResolution, ...] = field(default_factory=tuple)
    sentiment_direction: Optional[str] = None  # "improving" | "declining" | "flat"
    source_files: tuple[str, ...] = field(default_factory=tuple)
    peer_guidance_clause: str = ""  # Round 175: aggregate-only; empty when thin


@dataclass(frozen=True)
class HistoricalContext:
    """Aggregate result returned to the report builder.

    ``available`` is False when the corpus could not be opened (and
    the builder should render the banner).  ``entries`` carries one
    record per matched customer; ``unmatched`` lists requested names
    we could not find.
    """

    available: bool
    banner: str
    entries: tuple[HistoricalEntry, ...] = field(default_factory=tuple)
    unmatched: tuple[str, ...] = field(default_factory=tuple)
    source_files: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_str(value: object, *, limit: int = _MAX_LINE_CHARS) -> str:
    """Normalize and truncate untrusted strings before they hit
    Word.  Drops control characters and trims length."""
    if value is None:
        return ""
    body = unicodedata.normalize("NFKC", str(value))
    body = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", body).strip()
    if len(body) > limit:
        body = body[: limit - 1] + "\u2026"
    return body


def _relabel_corpus_sentinel(value: str) -> str:
    """Round 122 / H1: relabel a bare ``Other/Unknown``-style sentinel in a
    Historical-Context theme/technology label to the same
    ``"Other / Unclassified"`` display string used by the BE Focus Areas
    tables (R121 / G3), so prior-build corpus rows that stored the raw
    ``"Other/Unknown"`` technology don't leak it into the customer-facing
    Historical Context section.

    Unlike ``be_priority_scorer.relabel_unclassified`` (which maps a BLANK
    value to the label), a blank value here stays blank so an absent
    technology renders no ``"(...)"`` bracket / no header bit -- only a
    non-blank sentinel is relabeled.  Delegates to the SSoT helper via a
    lazy import so the label/token set cannot drift, and falls back to the
    original value if that import is ever unavailable.
    """

    text = (value or "").strip()
    if not text:
        return value
    try:
        from be_priority_scorer import relabel_unclassified
    except Exception:  # noqa: BLE001 - relabel is a display nicety, never fatal
        return value
    return relabel_unclassified(text)


def _coerce_case_number(value: object) -> str:
    """Round 39 / Phase 3.4 -- render case numbers as integer-shaped
    strings so the corpus loader's NaN-coerced float columns
    (``1141876078.0``) don't leak the ``.0`` suffix into the
    customer-facing report.

    Pipeline: NFKC + control-char strip via ``_safe_str``, then one
    extra trailing-``.0`` chop so floats render as their integer
    representation while genuine non-numeric IDs (rare, e.g. CSOne
    might one day issue alphanumerics) pass through unchanged.
    """
    raw = _safe_str(value, limit=40)
    if not raw:
        return ""
    if re.fullmatch(r"\s*-?\d+\.0+\s*", raw):
        try:
            return str(int(float(raw)))
        except (TypeError, ValueError):
            return raw.strip()
    return raw


def _dedupe_cases(cases: "tuple[HistoricalCase, ...] | list[HistoricalCase]") -> "list[HistoricalCase]":
    """Round 39 / Phase 3.3 -- collapse duplicates by case_number.

    The corpus loader can return the same CSOne case once per source
    snapshot it appears in (a single 90-day case may show in 5+ daily
    rollup files).  Pre-Round-39 the renderer printed every duplicate,
    so a single case rendered 5x per customer.  We sort by
    ``opened_at`` descending (most-recent first) before deduping so
    the surviving record is the freshest snapshot's view of the case.
    Cases without a usable ``case_number`` fall through unchanged so
    we never silently drop them.
    """
    if not cases:
        return []
    items = list(cases)
    try:
        items.sort(key=lambda c: (c.opened_at or "", c.case_number or ""), reverse=True)
    except Exception:
        pass
    seen: set = set()
    out: list = []
    for case in items:
        cn = _coerce_case_number(case.case_number)
        if not cn:
            out.append(case)
            continue
        if cn in seen:
            continue
        seen.add(cn)
        out.append(case)
    return out


# Round 86 / Build 62 (P1/F3): internal AdoptIQ-generated filenames
# (``AdoptIQ_Report_<scope>_<ts>.docx`` / ``AdoptIQ_Data_<scope>_<ts>.xlsx``
# / ``AdoptIQ_Portfolio_*.xlsx``) leaked into customer-facing
# Historical Context narratives via the ``[src: <filename>]`` tag the
# Round 17 renderer appends after each resolution / case bullet.
# The filename itself adds no information for the reader -- the
# section heading already says "Drawn from the AdoptIQ Knowledge
# Corpus (prior daily reports)" -- and surfaces an internal artifact
# name in front of the user. ``_is_internal_adoptiq_filename`` is the
# matcher used by both render paths to suppress the tag.
_INTERNAL_ADOPTIQ_FILENAME_RE = re.compile(
    r"^\s*AdoptIQ[_\-][A-Za-z0-9_\-]+\.(?:xlsx|xls|docx|doc|pdf)\s*$",
    re.IGNORECASE,
)


def _is_internal_adoptiq_filename(value: object) -> bool:
    """Return True for AdoptIQ-generated filenames (e.g.
    ``AdoptIQ_Report_Brian_Frazier_All_Contact_Center_90d_1234.xlsx``)
    that should NOT leak into customer-facing narrative.
    """
    if not value:
        return False
    text = str(value).strip()
    if not text:
        return False
    return bool(_INTERNAL_ADOPTIQ_FILENAME_RE.match(text))


def _safe_source_label(value: object) -> Optional[str]:
    """Sanitize a corpus ``source_filename`` for rendering.

    Round 86: returns ``None`` for internal AdoptIQ filenames so the
    ``[src: ...]`` citation tag is suppressed entirely (the surrounding
    Historical Context section already attributes the data to the
    AdoptIQ corpus). Returns the raw filename for non-AdoptIQ corpus
    sources (e.g. user-supplied CSOne snapshots) where the filename is
    operator-meaningful.
    """
    if value is None:
        return None
    if _is_internal_adoptiq_filename(value):
        return None
    label = _safe_str(value, limit=120)
    return label or None


def _is_safe_chunk(text: str) -> bool:
    """Defense-in-depth -- run free-form corpus text through the
    Round 17 prompt-safety validator before letting it into a
    rendered report.

    Round 18 / Phase 5.1: this is a **security gate**, so on any
    validator failure (import error, regex engine exception,
    monkey-patched stub raising) we now fail **closed** -- i.e.
    the chunk is dropped from the rendered report rather than
    being let through unverified.

    The original Round-17 implementation returned ``True`` on
    exception with the rationale "never break a report on a
    chunk".  The report still does not break (the chunk is
    silently dropped, not raised), but the security posture is
    now correct: if we cannot vouch for a chunk, we do not put
    it in front of the user.
    """
    if not text:
        return True
    try:
        from ai_narrative_validator import is_corpus_chunk_safe
        return bool(is_corpus_chunk_safe(text))
    except Exception as _gate_err:  # noqa: BLE001 - fail closed below
        try:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Round 18 / Phase 5.1: corpus safety gate raised; "
                "dropping chunk fail-closed: %s",
                _gate_err,
            )
        except Exception:  # pragma: no cover - never block on logging
            pass
        return False


def _sentiment_direction(values: Sequence[float]) -> Optional[str]:
    """Compress a sentiment trend into a simple label.  Tolerant of
    short / sparse trends; returns ``None`` when we don't have
    enough signal."""
    cleaned = [float(v) for v in values if v is not None]
    if len(cleaned) < 2:
        return None
    delta = cleaned[-1] - cleaned[0]
    if abs(delta) < 0.05:
        return "flat"
    return "improving" if delta > 0 else "declining"


def _claim_match_key(value: object) -> str:
    """Return a conservative exact-match key for one receipt value."""

    return re.sub(r"[^a-z0-9]+", "", _safe_str(value, limit=240).casefold())


def _clean_unique_scope_values(values: Iterable[object]) -> list[str]:
    """Round 173: shared scope cleaner for the corpus claim builders.

    Hoisted, behavior-identical, from the Round 172 support-theme builder so
    both claim builders normalize their customer/technology scope the same
    way: NFKC-safe strings, exact-match dedup, blanks dropped.
    """

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        value = _safe_str(raw, limit=200)
        key = _claim_match_key(value)
        if not value or not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return cleaned


_R175_PEER_METHOD_MAX = 160  # Round 175
_R175_CORPUS_SENTENCE_MAX = 520  # Round 175: keep R172/R173 sentence cap


def _r175_load_peer_evidence(
    customer: str,
    theme: str,
    technology: str,
) -> object | None:
    """Aggregate-only peer evidence. Fail closed on any retrieval error."""
    try:
        from corpus_retriever import get_peer_guidance_evidence
    except Exception:  # noqa: BLE001 - optional corpus fails soft
        return None
    try:
        return get_peer_guidance_evidence(
            theme,
            technology,
            exclude_customer=customer,
        )
    except Exception:  # noqa: BLE001 - missing corpus fails soft
        return None


_R175_PEER_CANDIDATE_CAP = 8  # Round 175: bound per-customer theme ranking


def _r175_median_close_fragment(days: object) -> str:
    """Compact observed median close window. Empty when missing. Round 175.2."""
    try:
        value = float(days)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if value != value or value < 0:
        return ""
    shown = f"{round(value):g}" if abs(value - round(value)) < 0.05 else f"{value:g}"
    return f" (median {shown}d)"


def format_peer_guidance_clause(
    evidence: object,
    *,
    include_likely_next: bool,
    prefer_peer_resolution: bool = False,
    include_median_close: bool = True,
) -> str:
    """Compact observed-in-peers clause. Empty when evidence is thin.

    Round 175: published text is aggregate-only. No peer names, emails,
    case numbers, or filenames. Closure wording uses method-scoped
    closed-peer counts, never uncoupled theme-peer totals.
    """
    if evidence is None or not bool(getattr(evidence, "evidence_sufficient", False)):
        return ""
    method = _safe_str(getattr(evidence, "dominant_method_text", "") or "", limit=_R175_PEER_METHOD_MAX)
    if not method or not _is_safe_chunk(method):
        return ""
    peer_n = int(getattr(evidence, "peer_customer_count", 0) or 0)
    dominant_n = int(getattr(evidence, "dominant_method_peers", 0) or 0)
    if dominant_n < 2:
        return ""
    if prefer_peer_resolution:
        return (
            f"a peer-observed resolution was {method} "
            f"({dominant_n} of {peer_n} similar accounts)"
        )
    if not include_likely_next:
        return ""
    likely_next = str(getattr(evidence, "likely_next", "insufficient") or "insufficient")
    next_step = _safe_str(getattr(evidence, "next_step", "") or "", limit=160)
    if likely_next == "insufficient" or not next_step or not _is_safe_chunk(next_step):
        return ""
    method_closed_n = int(getattr(evidence, "method_closed_peer_count", 0) or 0)
    method_open_n = int(getattr(evidence, "method_open_peer_count", 0) or 0)
    method_worsened_n = int(getattr(evidence, "method_pulse_worsened_count", 0) or 0)
    method_recovered_n = int(getattr(evidence, "method_pulse_recovered_count", 0) or 0)
    if likely_next == "closure":
        if method_closed_n < 2:
            return ""
        median_frag = ""
        if include_median_close:
            median_frag = _r175_median_close_fragment(
                getattr(evidence, "close_time_median_days", None)
            )
        return (
            f"Observed-in-peers: {method_closed_n} similar accounts closed after {method}"
            f"{median_frag}; "
            f"likely-next is closure after that method (not a certainty). "
            f"Next step: {next_step}"
        )
    if likely_next == "remains_open":
        if method_open_n < 2:
            return ""
        return (
            f"Observed-in-peers: {method_open_n} similar accounts remain open after {method}; "
            f"likely-next is remaining open (not a certainty). "
            f"Next step: {next_step}"
        )
    if likely_next == "pulse_worsening":
        if method_worsened_n < 2:
            return ""
        return (
            f"Observed-in-peers: {method_worsened_n} similar accounts showed worse pulse "
            f"after {method}; likely-next is pulse remaining worse (not a certainty). "
            f"Next step: {next_step}"
        )
    if likely_next == "pulse_recovery":  # Round 175.2
        if method_recovered_n < 2:
            return ""
        return (
            f"Observed-in-peers: {method_recovered_n} similar accounts recovered pulse "
            f"after {method}; likely-next is pulse recovery (not a certainty). "
            f"Next step: {next_step}"
        )
    return ""


def _r175_published_peer_clause(evidence: object, *, include_likely_next: bool) -> str:
    """Prefer coupled likely-next; fall back to the peer method only."""
    if include_likely_next:
        clause = format_peer_guidance_clause(
            evidence, include_likely_next=True, prefer_peer_resolution=False
        )
        if clause:
            return clause
    return format_peer_guidance_clause(
        evidence, include_likely_next=False, prefer_peer_resolution=True
    )


def format_peer_guidance_ask_ai_line(evidence: object) -> str:
    """Fail-closed Ask AI line. Always names the insufficiency when thin."""
    clause = _r175_published_peer_clause(evidence, include_likely_next=True)
    if not clause:
        return (
            "CORPUS_PEER_GUIDANCE: insufficient_peer_evidence=true; "
            "likely_next=insufficient; no likely-next is asserted."
        )
    return f"CORPUS_PEER_GUIDANCE: {clause}"


def select_ranked_peer_guidance(
    *,
    customer: str,
    barriers: Sequence[object],
    fallback_technology: str = "",
) -> object | None:
    """Pick the strongest sufficient (theme, tech) from this customer's barriers.

    Round 175: do not stop at ``barriers[0]``. Rank coupled likely-next
    above method-only agreement, then method-scoped strength. Thin
    evidence stays thin — ranking never invents a method. Round 175.3:
    mixed/tied trajectories rank as ``likely_next=insufficient`` so a
    clean coupled theme wins over a first-listed mixed one.
    """
    seen: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str, int]] = []
    for barrier in barriers or ():
        theme = _safe_str(getattr(barrier, "theme", "") or "", limit=80)
        tech = _safe_str(
            getattr(barrier, "technology", "") or fallback_technology or "",
            limit=80,
        )
        if not theme or not tech:
            continue
        key = (theme.casefold(), tech.casefold())
        if key in seen:
            continue
        seen.add(key)
        try:
            occurrences = int(getattr(barrier, "occurrences", 0) or 0)
        except (TypeError, ValueError):
            occurrences = 0
        candidates.append((theme, tech, occurrences))
        if len(candidates) >= _R175_PEER_CANDIDATE_CAP:
            break
    if not candidates:
        return None

    ranked: list[tuple[tuple[object, ...], object]] = []
    for theme, tech, occurrences in candidates:
        evidence = _r175_load_peer_evidence(customer, theme, tech)
        if evidence is None:
            continue
        likely = str(getattr(evidence, "likely_next", "insufficient") or "insufficient")
        if likely == "closure":
            strength = int(getattr(evidence, "method_closed_peer_count", 0) or 0)
        elif likely == "remains_open":
            strength = int(getattr(evidence, "method_open_peer_count", 0) or 0)
        elif likely == "pulse_worsening":
            strength = int(getattr(evidence, "method_pulse_worsened_count", 0) or 0)
        elif likely == "pulse_recovery":  # Round 175.2
            strength = int(getattr(evidence, "method_pulse_recovered_count", 0) or 0)
        else:
            strength = 0
        rank = (
            0 if likely == "insufficient" else 1,
            1 if bool(getattr(evidence, "evidence_sufficient", False)) else 0,
            strength,
            int(getattr(evidence, "dominant_method_peers", 0) or 0),
            occurrences,
            theme.casefold(),
            tech.casefold(),
        )
        ranked.append((rank, evidence))
    if not ranked:
        return None
    ranked.sort(
        key=lambda item: (
            -int(item[0][0]),
            -int(item[0][1]),
            -int(item[0][2]),
            -int(item[0][3]),
            -int(item[0][4]),
            str(item[0][5]),
            str(item[0][6]),
        )
    )
    return ranked[0][1]


def load_ranked_peer_guidance(
    customer: str,
    *,
    fallback_technology: str = "",
) -> object | None:
    """History-backed ranking. Fail closed when the corpus is thin."""
    try:
        from corpus_retriever import get_customer_history, is_configured
    except Exception:  # noqa: BLE001 - optional corpus fails soft
        return None
    try:
        if not is_configured():
            return None
        history = get_customer_history(
            customer, limit_cases=8, limit_resolutions=3
        )
    except Exception:  # noqa: BLE001 - missing customer fails closed
        return None
    tech = fallback_technology or getattr(history, "technology", "") or ""
    return select_ranked_peer_guidance(
        customer=str(getattr(history, "name", customer) or customer),
        barriers=getattr(history, "barriers", ()) or (),
        fallback_technology=str(tech or ""),
    )


def format_ranked_peer_guidance_clause(
    customer: str,
    *,
    fallback_technology: str = "",
    include_likely_next: bool = True,
) -> str:
    """Existing-surface suffix. Empty when ranked evidence is thin."""
    evidence = load_ranked_peer_guidance(
        customer, fallback_technology=fallback_technology
    )
    return _r175_published_peer_clause(
        evidence, include_likely_next=include_likely_next
    )


def _r175_maybe_append_peer_clause(
    sentence: str,
    retrievals: list[dict[str, object]],
    *,
    customer: str,
    theme: str,
    technology: str,
    include_likely_next: bool,
    prefer_peer_resolution: bool,
) -> tuple[str, list[dict[str, object]]]:
    """Append a peer clause + optional 4th retrieval. Omit when thin. Round 175.

    Round 175.2: if the 520-char insight cap would drop the clause, retry
    without the observed-median fragment before giving up. Standalone
    surfaces keep the median.
    """
    evidence = _r175_load_peer_evidence(customer, theme, technology)
    base = sentence.rstrip()
    if base.endswith("."):
        base = base[:-1]
    extra: dict[str, object] = {
        "method": "corpus_retriever.get_peer_guidance_evidence",
        "arguments": {"theme": theme, "technology": technology},
        "peer_customer_count": int(getattr(evidence, "peer_customer_count", 0) or 0)
        if evidence is not None
        else 0,
        "dominant_method_peers": int(getattr(evidence, "dominant_method_peers", 0) or 0)
        if evidence is not None
        else 0,
        "likely_next": str(getattr(evidence, "likely_next", "insufficient") or "insufficient")
        if evidence is not None
        else "insufficient",
        # Round 175: method-scoped trajectory counts, not uncoupled totals.
        "method_closed_peer_count": int(
            getattr(evidence, "method_closed_peer_count", 0) or 0
        )
        if evidence is not None
        else 0,
        "method_open_peer_count": int(
            getattr(evidence, "method_open_peer_count", 0) or 0
        )
        if evidence is not None
        else 0,
        "close_time_median_days": getattr(evidence, "close_time_median_days", None)
        if evidence is not None
        else None,
    }
    for include_median in (True, False):
        clause = format_peer_guidance_clause(
            evidence,
            include_likely_next=include_likely_next,
            prefer_peer_resolution=prefer_peer_resolution,
            include_median_close=include_median,
        )
        if not clause or not _is_safe_chunk(clause):
            continue
        combined = _safe_str(f"{base}; {clause}.", limit=1024)
        if (
            combined
            and len(combined) <= _R175_CORPUS_SENTENCE_MAX
            and _is_safe_chunk(combined)
        ):
            extra["median_close_published"] = bool(include_median)
            return combined, retrievals + [extra]
    return sentence, retrievals


def build_support_theme_corpus_claim(
    customer_names: Iterable[object],
    technologies: Iterable[object],
) -> Optional[dict[str, object]]:
    """Build one scoped, safe corpus claim for the support-theme insight.

    Round 172 deliberately deepens the existing ``support_themes`` insight
    instead of adding a sixth insight that Leader reports would trim. A claim
    is emitted only when all three existing retriever surfaces agree:

    * ``get_customer_history`` matches a customer in the current report;
    * ``get_recurring_themes`` ranks a theme for a current TAC technology;
    * ``get_resolutions_for`` completes successfully (a matching resolution
      is optional and is used only when it also belongs to that customer's
      history).

    Corpus absence, a lookup miss, or a failed safety gate returns ``None``.
    No banner or synthetic pattern is substituted. The returned receipt is
    content-addressed and contains aggregate claim inputs only -- never raw
    cases, corpus rows, paths, or credentials.
    """

    # Round 173: scope cleaning hoisted to _clean_unique_scope_values so the
    # operating-health builder shares the exact Round 172 normalization.
    scoped_customers = _clean_unique_scope_values(customer_names)
    current_technologies = _clean_unique_scope_values(technologies)
    if not scoped_customers or not current_technologies:
        return None

    try:
        import corpus_retriever as cr
    except Exception as exc:  # noqa: BLE001 - optional corpus fails soft
        logger.debug("Round 172 corpus retriever import unavailable: %s", type(exc).__name__)
        return None
    try:
        if not cr.is_configured():
            return None
    except Exception as exc:  # noqa: BLE001 - optional corpus fails soft
        logger.debug("Round 172 corpus status unavailable: %s", type(exc).__name__)
        return None

    for requested_customer in scoped_customers:
        try:
            history = cr.get_customer_history(
                requested_customer,
                limit_cases=5,
                limit_resolutions=5,
            )
        except cr.CorpusUnavailable:
            continue
        except Exception as exc:  # noqa: BLE001 - missing corpus fails soft
            logger.debug("Round 172 customer-history retrieval failed: %s", type(exc).__name__)
            return None

        customer = _safe_str(history.name, limit=200)
        if not customer or not _is_safe_chunk(customer):
            continue

        for requested_technology in current_technologies:
            try:
                recurring = cr.get_recurring_themes(requested_technology, top_k=10)
            except cr.CorpusUnavailable:
                return None
            except Exception as exc:  # noqa: BLE001 - missing corpus fails soft
                logger.debug("Round 172 recurring-theme retrieval failed: %s", type(exc).__name__)
                return None

            for ranked_theme in recurring:
                technology = _safe_str(ranked_theme.technology, limit=100)
                theme = _safe_str(ranked_theme.theme, limit=100)
                if (
                    not technology
                    or not theme
                    or _claim_match_key(technology) != _claim_match_key(requested_technology)
                    or not _is_safe_chunk(technology)
                    or not _is_safe_chunk(theme)
                ):
                    continue

                scoped_barrier = next(
                    (
                        barrier
                        for barrier in history.barriers
                        if _claim_match_key(barrier.technology) == _claim_match_key(technology)
                        and _claim_match_key(barrier.theme) == _claim_match_key(theme)
                        and int(barrier.occurrences or 0) > 0
                    ),
                    None,
                )
                if scoped_barrier is None:
                    continue

                try:
                    resolution_candidates = cr.get_resolutions_for(
                        theme,
                        technology,
                        limit=5,
                    )
                except cr.CorpusUnavailable:
                    return None
                except Exception as exc:  # noqa: BLE001 - missing corpus fails soft
                    logger.debug("Round 172 resolution retrieval failed: %s", type(exc).__name__)
                    return None

                customer_resolutions = {
                    _claim_match_key(item.method_text): item
                    for item in history.top_resolutions
                    if _claim_match_key(item.technology) == _claim_match_key(technology)
                    and _claim_match_key(item.theme) == _claim_match_key(theme)
                    and _claim_match_key(item.method_text)
                }
                selected_resolution = ""
                for candidate in resolution_candidates:
                    candidate_text = _safe_str(candidate.method_text, limit=240)
                    key = _claim_match_key(candidate_text)
                    if key in customer_resolutions and candidate_text and _is_safe_chunk(candidate_text):
                        selected_resolution = candidate_text.rstrip(".")
                        break

                occurrences = int(scoped_barrier.occurrences or 0)
                occurrence_label = "1 occurrence" if occurrences == 1 else f"{occurrences} occurrences"
                sentence = (
                    f"Prior corpus pattern for {customer}: {theme} recurred "
                    f"{occurrence_label} in {technology}"
                )
                if selected_resolution:
                    sentence += f"; a previously observed resolution was {selected_resolution}"
                sentence += "."
                sentence = _safe_str(sentence, limit=1024)
                if not sentence or len(sentence) > 520 or not _is_safe_chunk(sentence):
                    continue

                retrievals: list[dict[str, object]] = [
                    {
                        "method": "corpus_retriever.get_customer_history",
                        "arguments": {
                            "customer_name": requested_customer,
                            "limit_cases": 5,
                            "limit_resolutions": 5,
                        },
                        "matched_customer": customer,
                        "matched_theme_occurrences": occurrences,
                    },
                    {
                        "method": "corpus_retriever.get_recurring_themes",
                        "arguments": {"technology": requested_technology, "top_k": 10},
                        "matched_theme": theme,
                        "matched_customers": int(ranked_theme.customers or 0),
                        "matched_occurrences": int(ranked_theme.occurrences or 0),
                    },
                    {
                        "method": "corpus_retriever.get_resolutions_for",
                        "arguments": {"theme": theme, "technology": technology, "limit": 5},
                        "result_count": len(resolution_candidates),
                        "selected_resolution": bool(selected_resolution),
                    },
                ]
                # Round 175: peer-observed fallback only when this customer
                # has no own resolution. Thin peer evidence is omitted.
                sentence, retrievals = _r175_maybe_append_peer_clause(
                    sentence,
                    retrievals,
                    customer=customer,
                    theme=theme,
                    technology=technology,
                    include_likely_next=False,
                    prefer_peer_resolution=not bool(selected_resolution),
                )

                payload: dict[str, object] = {
                    "schema": _CORPUS_INSIGHT_RECEIPT_SCHEMA,
                    "claim": {
                        "customer": customer,
                        "technology": technology,
                        "theme": theme,
                        "occurrences": occurrences,
                        "resolution": selected_resolution,
                        "sentence": sentence,
                    },
                    "retrievals": retrievals,
                }
                serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
                return {
                    "sentence": sentence,
                    "receipt_id": f"Corpus_Retriever_Receipt:{digest[:16]}",
                    "receipt_sha256": digest,
                    "receipt_payload": payload,
                }
    return None


def _parse_corpus_case_timestamp(value: object) -> Optional[datetime]:
    """Round 173: parse one corpus case timestamp conservatively.

    The corpus stores ``opened_at`` / ``closed_at`` as the raw (truncated)
    source strings.  Only unambiguous ISO-8601 values are accepted; anything
    else returns ``None`` so the closure-precedent claim stays absent instead
    of guessing a duration.  Naive timestamps are treated as UTC, matching the
    codebase-wide explicit-UTC-clock convention.
    """

    raw = _safe_str(value, limit=64)
    if not raw:
        return None
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _closed_case_precedent(cases: Sequence[object]) -> Optional[tuple[int, float]]:
    """Round 173: ``(closed_case_count, median_days_to_close)`` for corpus cases.

    Round 174 first groups repeated observations by the established normalized
    case-number identity.  The corpus can contain one row per daily source
    snapshot, so aggregating the raw rows would inflate both the visible case
    count and the median.  A keyed logical case contributes exactly once only
    when every retrieved observation agrees that it is closed and carries the
    same valid ordered opened/closed timestamps.  Conflicting open/closed state
    or timestamps fail closed for that logical case; input / database order is
    never used to pick a winner.  Unkeyed observations retain the established
    conservative behavior and are evaluated independently because there is no
    identity on which to collapse them.  Returns ``None`` when no case
    qualifies so the caller emits no corpus sentence.
    """

    def closed_window(case: object) -> Optional[tuple[datetime, datetime]]:
        if bool(getattr(case, "is_open", True)):
            return None
        opened = _parse_corpus_case_timestamp(getattr(case, "opened_at", ""))
        closed = _parse_corpus_case_timestamp(getattr(case, "closed_at", ""))
        if opened is None or closed is None or closed < opened:
            return None
        return opened, closed

    keyed: dict[str, list[object]] = {}
    unkeyed: list[object] = []
    for case in cases or ():
        case_number = _coerce_case_number(getattr(case, "case_number", ""))
        if case_number:
            keyed.setdefault(case_number, []).append(case)
        else:
            unkeyed.append(case)

    durations: list[float] = []
    for observations in keyed.values():
        windows = [closed_window(case) for case in observations]
        if any(window is None for window in windows):
            continue
        agreed_windows = {window for window in windows if window is not None}
        if len(agreed_windows) != 1:
            continue
        opened, closed = next(iter(agreed_windows))
        durations.append((closed - opened).total_seconds() / 86_400.0)

    for case in unkeyed:
        window = closed_window(case)
        if window is None:
            continue
        opened, closed = window
        durations.append((closed - opened).total_seconds() / 86_400.0)

    if not durations:
        return None
    return len(durations), round(float(statistics.median(durations)), 1)


def build_support_operating_health_corpus_claim(
    customer_names: Iterable[object],
    technologies: Iterable[object],
) -> Optional[dict[str, object]]:
    """Build one scoped, safe corpus claim for the operating-health insight.

    Round 173 deepens the existing ``support_operating_health`` insight the
    same fail-closed way Round 172 deepened ``support_themes`` -- no sixth
    insight, no second store, no LLM, no schema bump.  The claim is a closure
    precedent: how many corpus cases for a scoped customer closed with valid
    opened/closed timestamps and their median days to close, anchored to a
    current TAC technology.  A claim is emitted only when all three existing
    retriever surfaces agree:

    * ``get_customer_history`` matches a customer in the current report AND
      that customer's corpus history carries at least one closed case with
      valid, ordered timestamps;
    * ``get_recurring_themes`` ranks a theme for a current TAC technology
      that also appears in the same customer's own barrier history;
    * ``get_resolutions_for`` completes successfully (a matching resolution
      is optional and is used only when it also belongs to that customer's
      history).

    Corpus absence, a lookup miss, or a failed safety gate returns ``None``.
    No banner or synthetic pattern is substituted. The returned receipt is
    content-addressed and contains aggregate claim inputs only -- never raw
    cases, corpus rows, paths, or credentials.
    """

    scoped_customers = _clean_unique_scope_values(customer_names)
    current_technologies = _clean_unique_scope_values(technologies)
    if not scoped_customers or not current_technologies:
        return None

    try:
        import corpus_retriever as cr
    except Exception as exc:  # noqa: BLE001 - optional corpus fails soft
        logger.debug("Round 173 corpus retriever import unavailable: %s", type(exc).__name__)
        return None
    try:
        if not cr.is_configured():
            return None
    except Exception as exc:  # noqa: BLE001 - optional corpus fails soft
        logger.debug("Round 173 corpus status unavailable: %s", type(exc).__name__)
        return None

    for requested_customer in scoped_customers:
        try:
            history = cr.get_customer_history(
                requested_customer,
                limit_cases=10,
                limit_resolutions=5,
            )
        except cr.CorpusUnavailable:
            continue
        except Exception as exc:  # noqa: BLE001 - missing corpus fails soft
            logger.debug("Round 173 customer-history retrieval failed: %s", type(exc).__name__)
            return None

        customer = _safe_str(history.name, limit=200)
        if not customer or not _is_safe_chunk(customer):
            continue
        precedent = _closed_case_precedent(history.cases)
        if precedent is None:
            continue
        closed_case_count, close_time_median_days = precedent

        for requested_technology in current_technologies:
            try:
                recurring = cr.get_recurring_themes(requested_technology, top_k=10)
            except cr.CorpusUnavailable:
                return None
            except Exception as exc:  # noqa: BLE001 - missing corpus fails soft
                logger.debug("Round 173 recurring-theme retrieval failed: %s", type(exc).__name__)
                return None

            for ranked_theme in recurring:
                technology = _safe_str(ranked_theme.technology, limit=100)
                theme = _safe_str(ranked_theme.theme, limit=100)
                if (
                    not technology
                    or not theme
                    or _claim_match_key(technology) != _claim_match_key(requested_technology)
                    or not _is_safe_chunk(technology)
                    or not _is_safe_chunk(theme)
                ):
                    continue

                scoped_barrier = next(
                    (
                        barrier
                        for barrier in history.barriers
                        if _claim_match_key(barrier.technology) == _claim_match_key(technology)
                        and _claim_match_key(barrier.theme) == _claim_match_key(theme)
                        and int(barrier.occurrences or 0) > 0
                    ),
                    None,
                )
                if scoped_barrier is None:
                    continue

                try:
                    resolution_candidates = cr.get_resolutions_for(
                        theme,
                        technology,
                        limit=5,
                    )
                except cr.CorpusUnavailable:
                    return None
                except Exception as exc:  # noqa: BLE001 - missing corpus fails soft
                    logger.debug("Round 173 resolution retrieval failed: %s", type(exc).__name__)
                    return None

                customer_resolutions = {
                    _claim_match_key(item.method_text): item
                    for item in history.top_resolutions
                    if _claim_match_key(item.technology) == _claim_match_key(technology)
                    and _claim_match_key(item.theme) == _claim_match_key(theme)
                    and _claim_match_key(item.method_text)
                }
                selected_resolution = ""
                for candidate in resolution_candidates:
                    candidate_text = _safe_str(candidate.method_text, limit=240)
                    key = _claim_match_key(candidate_text)
                    if key in customer_resolutions and candidate_text and _is_safe_chunk(candidate_text):
                        selected_resolution = candidate_text.rstrip(".")
                        break

                closed_label = (
                    "1 prior corpus case"
                    if closed_case_count == 1
                    else f"{closed_case_count} prior corpus cases"
                )
                sentence = (
                    f"Corpus closure precedent for {customer}: {closed_label} closed "
                    f"with valid opened/closed timestamps, median "
                    f"{close_time_median_days:g} days to close; recurring {theme} "
                    f"history in {technology}"
                )
                if selected_resolution:
                    sentence += f"; a previously observed resolution was {selected_resolution}"
                sentence += "."
                sentence = _safe_str(sentence, limit=1024)
                if not sentence or len(sentence) > 520 or not _is_safe_chunk(sentence):
                    continue

                retrievals: list[dict[str, object]] = [
                    {
                        "method": "corpus_retriever.get_customer_history",
                        "arguments": {
                            "customer_name": requested_customer,
                            "limit_cases": 10,
                            "limit_resolutions": 5,
                        },
                        "matched_customer": customer,
                        "matched_closed_case_count": closed_case_count,
                        "matched_close_time_median_days": close_time_median_days,
                        "matched_theme_occurrences": int(scoped_barrier.occurrences or 0),
                    },
                    {
                        "method": "corpus_retriever.get_recurring_themes",
                        "arguments": {"technology": requested_technology, "top_k": 10},
                        "matched_theme": theme,
                        "matched_customers": int(ranked_theme.customers or 0),
                        "matched_occurrences": int(ranked_theme.occurrences or 0),
                    },
                    {
                        "method": "corpus_retriever.get_resolutions_for",
                        "arguments": {"theme": theme, "technology": technology, "limit": 5},
                        "result_count": len(resolution_candidates),
                        "selected_resolution": bool(selected_resolution),
                    },
                ]
                # Round 175: likely-next / next-step from peer trajectories.
                # Thin evidence is omitted so Round 173 stays at 3 retrievals.
                sentence, retrievals = _r175_maybe_append_peer_clause(
                    sentence,
                    retrievals,
                    customer=customer,
                    theme=theme,
                    technology=technology,
                    include_likely_next=True,
                    prefer_peer_resolution=False,
                )

                payload: dict[str, object] = {
                    "schema": _CORPUS_INSIGHT_RECEIPT_SCHEMA,
                    "claim": {
                        "customer": customer,
                        "technology": technology,
                        "theme": theme,
                        "closed_case_count": closed_case_count,
                        "close_time_median_days": close_time_median_days,
                        "resolution": selected_resolution,
                        "sentence": sentence,
                    },
                    "retrievals": retrievals,
                }
                serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
                return {
                    "sentence": sentence,
                    "receipt_id": f"Corpus_Retriever_Receipt:{digest[:16]}",
                    "receipt_sha256": digest,
                    "receipt_payload": payload,
                }
    return None


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_historical_context(
    customer_names: Iterable[object],
    *,
    technology: object = None,
    enabled: bool = True,
    max_customers: int = _MAX_CUSTOMERS_RENDERED,
    max_cases_per_customer: int = _MAX_CASES_PER_CUSTOMER,
) -> HistoricalContext:
    """Pull historical context for the supplied ``customer_names``.

    Returns a :class:`HistoricalContext` whose ``available`` flag is
    False whenever the corpus is unreachable, OR no requested
    customer matched.  Always returns; never raises.
    """

    if not enabled:
        return HistoricalContext(
            available=False,
            banner=_BANNER_DISABLED,
        )

    cleaned_names: list[str] = []
    seen_norm: set[str] = set()
    for raw in customer_names or []:
        text = _safe_str(raw, limit=200)
        if not text:
            continue
        norm = re.sub(r"[^a-z0-9]+", "", text.casefold())
        if not norm or norm in seen_norm:
            continue
        seen_norm.add(norm)
        cleaned_names.append(text)
        if len(cleaned_names) >= max(1, int(max_customers)):
            break

    if not cleaned_names:
        return HistoricalContext(
            available=False,
            banner=_BANNER_NO_CUSTOMER_MATCH,
        )

    try:
        import corpus_retriever as cr
    except Exception as imp_err:  # noqa: BLE001
        logger.debug("corpus retriever import failed: %s", imp_err)
        return HistoricalContext(
            available=False,
            banner=_BANNER_UNAVAILABLE,
        )

    if not cr.is_configured():
        return HistoricalContext(
            available=False,
            banner=_BANNER_UNAVAILABLE,
        )

    entries: list[HistoricalEntry] = []
    unmatched: list[str] = []
    source_files: list[str] = []

    safe_max_cases = max(1, min(int(max_cases_per_customer), 25))

    for name in cleaned_names:
        try:
            history = cr.get_customer_history(
                name,
                limit_cases=safe_max_cases,
                limit_resolutions=5,
            )
        except cr.CorpusUnavailable as miss:
            logger.debug(
                "Round 17 / corpus history miss (%s): %s",
                _safe_str(name, limit=80), miss,
            )
            unmatched.append(name)
            continue

        cases: list[HistoricalCase] = []
        for case in history.cases[:safe_max_cases]:
            summary = _safe_str(case.summary, limit=240)
            if summary and not _is_safe_chunk(summary):
                summary = ""
            # Round 86 / Build 62 (P1/F3): route through
            # ``_safe_source_label`` so AdoptIQ-internal filenames
            # (``AdoptIQ_Report_*.xlsx``, ``AdoptIQ_Data_*.xlsx``)
            # are dropped at construction time -- they then never
            # reach the [src: ...] tag rendering OR the
            # "Source files:" footer line.
            src = _safe_source_label(case.source_filename)
            if src:
                source_files.append(src)
            cases.append(
                HistoricalCase(
                    # Round 39 / Phase 3.4: coerce ``case_number`` to
                    # an integer-shaped string so downstream consumers
                    # never see ``1141876078.0`` (the float form leaks
                    # in when the corpus loader's pandas column comes
                    # back as a NaN-coerced float64 dtype).
                    case_number=_coerce_case_number(case.case_number),
                    severity=_safe_str(case.severity, limit=20),
                    status=_safe_str(case.status, limit=24),
                    summary=summary,
                    opened_at=_safe_str(case.opened_at, limit=32),
                    closed_at=_safe_str(case.closed_at, limit=32),
                    source_filename=src,
                )
            )

        themes: list[HistoricalTheme] = [
            HistoricalTheme(
                # Round 122 / H1: relabel bare Other/Unknown sentinels
                theme=_relabel_corpus_sentinel(_safe_str(b.theme, limit=80)),
                occurrences=int(b.occurrences or 0),
                technology=_relabel_corpus_sentinel(_safe_str(b.technology, limit=80)),
            )
            for b in history.barriers[:5]
        ]

        resolutions: list[HistoricalResolution] = []
        for res in history.top_resolutions[:5]:
            method = _safe_str(res.method_text, limit=240)
            if method and not _is_safe_chunk(method):
                continue
            # Round 86 / Build 62 (P1/F3): same suppression as cases
            # above -- AdoptIQ-internal filenames are dropped at
            # construction time so they never leak into Word.
            src = _safe_source_label(res.source_filename)
            if src:
                source_files.append(src)
            resolutions.append(
                HistoricalResolution(
                    method_text=method,
                    theme=_safe_str(res.theme, limit=80),
                    technology=_safe_str(res.technology, limit=80),
                    source_filename=src,
                )
            )

        sentiment_dir: Optional[str] = None
        try:
            scores = [
                snap.score for snap in history.sentiment_trend
                if snap.score is not None
            ]
            sentiment_dir = _sentiment_direction(scores)
        except Exception:  # noqa: BLE001 - sentiment is optional
            sentiment_dir = None

        peer_clause = ""
        try:
            ranked = select_ranked_peer_guidance(
                customer=_safe_str(history.name, limit=200),
                barriers=history.barriers,
                fallback_technology=_safe_str(history.technology, limit=80),
            )
            peer_clause = _r175_published_peer_clause(
                ranked, include_likely_next=True
            )
            if peer_clause and not _is_safe_chunk(peer_clause):
                peer_clause = ""
        except Exception:  # noqa: BLE001 - optional peer clause fails closed
            peer_clause = ""

        entries.append(
            HistoricalEntry(
                customer_name=_safe_str(history.name, limit=200),
                # Round 122 / H1: relabel bare Other/Unknown sentinel
                technology=_relabel_corpus_sentinel(_safe_str(history.technology, limit=80)) or None,
                first_seen=_safe_str(history.first_seen, limit=32) or None,
                last_seen=_safe_str(history.last_seen, limit=32) or None,
                occurrences=int(history.occurrences or 0),
                cases=tuple(cases),
                barriers=tuple(themes),
                resolutions=tuple(resolutions),
                sentiment_direction=sentiment_dir,
                source_files=tuple(sorted({s for s in source_files if s})),
                peer_guidance_clause=_safe_str(peer_clause, limit=520),
            )
        )

    if not entries:
        return HistoricalContext(
            available=False,
            banner=_BANNER_NO_CUSTOMER_MATCH,
            unmatched=tuple(unmatched),
        )

    # Deduplicate source-file list while preserving order.
    seen_files: set[str] = set()
    deduped_files: list[str] = []
    for f in source_files:
        if f and f not in seen_files:
            seen_files.add(f)
            deduped_files.append(f)

    return HistoricalContext(
        available=True,
        banner="",
        entries=tuple(entries),
        unmatched=tuple(unmatched),
        source_files=tuple(deduped_files),
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_to_text(context: HistoricalContext) -> str:
    """Plain-text rendering used by tests and Excel fallbacks.  Keeps
    the formatting deterministic so snapshot-style tests remain
    stable across runs."""
    lines: list[str] = ["Historical Context (CSOne Knowledge Corpus)"]
    if not context.available or not context.entries:
        lines.append(context.banner or _BANNER_NO_CUSTOMER_MATCH)
        return "\n".join(lines)
    for entry in context.entries:
        header = f"Customer: {entry.customer_name}"
        if entry.technology:
            header += f" | Technology: {entry.technology}"
        if entry.first_seen and entry.last_seen:
            header += f" | Observed: {entry.first_seen} -> {entry.last_seen}"
        header += f" | Prior occurrences: {entry.occurrences}"
        if entry.sentiment_direction:
            header += f" | Sentiment: {entry.sentiment_direction}"
        lines.append("")
        lines.append(header)
        if entry.cases:
            lines.append("  Prior cases:")
            # Round 39 / Phase 3.3: dedupe by case_number; Round 39 /
            # Phase 3.4: coerce float case-numbers to integer strings.
            for case in _dedupe_cases(entry.cases):
                summary = case.summary or "(no summary)"
                _cn = _coerce_case_number(case.case_number) or "\u2014"
                _sev = (case.severity or "").strip() or "\u2014"
                _stat = (case.status or "").strip() or "\u2014"
                lines.append(
                    f"    - {_cn} [{_sev}/{_stat}] "
                    f"opened={case.opened_at or '-'}; closed={case.closed_at or '-'}; "
                    f"summary={summary}"
                )
        if entry.barriers:
            lines.append("  Recurring themes:")
            for b in entry.barriers:
                tech = f" ({b.technology})" if b.technology else ""
                lines.append(f"    - {b.theme}{tech} -- {b.occurrences} occurrence(s)")
        if entry.resolutions:
            lines.append("  Top resolutions:")
            for r in entry.resolutions:
                # Round 86 / Build 62 (P1/F3): suppress [src: AdoptIQ_*.xlsx]
                # tags that leak internal AdoptIQ-generated filenames into
                # the customer-facing text. ``_safe_source_label`` returns
                # None for AdoptIQ-internal names; the citation is dropped
                # entirely in that case (the section header already
                # attributes the data to the AdoptIQ corpus).
                _src_label = _safe_source_label(r.source_filename)
                src = f"  [src: {_src_label}]" if _src_label else ""
                lines.append(f"    - {r.method_text}{src}")
        if entry.peer_guidance_clause:
            lines.append(f"  {entry.peer_guidance_clause}")
    if context.source_files:
        lines.append("")
        lines.append("Source files: " + "; ".join(context.source_files[:20]))
    return "\n".join(lines)


def render_to_word(doc: object, context: HistoricalContext) -> None:
    """Append a Historical Context section onto ``doc``.

    ``doc`` is duck-typed so this helper works with both the
    raw python-docx ``Document`` and the ``ExecutiveReportBuilder``
    wrapper.  We require ``add_heading`` and ``add_paragraph`` only.
    """

    add_heading = getattr(doc, "add_heading", None)
    add_paragraph = getattr(doc, "add_paragraph", None)
    if not callable(add_heading) or not callable(add_paragraph):
        logger.debug("Round 17 / render_to_word: doc lacks expected API; skipping")
        return

    try:
        add_heading("Historical Context", level=1)
        # Round 39 / Phase 3.1: the SharePoint runtime path was
        # retired in Round 36 (CLAUDE.md "Native Knowledge Corpus
        # (Round 35 -> Round 36)" section).  Drop the misleading
        # "SharePoint share..." text that survived in this docx
        # paragraph.  The OneDrive desktop client now handles SSO,
        # MFA, and admin-consent and the corpus indexer reads its
        # synced copy directly.  We name the canonical folder so the
        # reader knows which OneDrive directory underpins the
        # historical context.
        add_paragraph(
            "Drawn from the AdoptIQ Knowledge Corpus (prior daily "
            "reports). Sourced from the local OneDrive sync of "
            "'AI Projects/AdoptIQ_CSOne_Reports', generated AdoptIQ "
            "reports, and ad-hoc Intelligence uploads."
        )

        if not context.available or not context.entries:
            banner = _safe_str(context.banner or _BANNER_NO_CUSTOMER_MATCH, limit=400)
            add_paragraph(banner)
            return

        for entry in context.entries[:_MAX_CUSTOMERS_RENDERED]:
            try:
                add_heading(
                    f"Customer: {_safe_str(entry.customer_name, limit=200)}",
                    level=2,
                )
                summary_bits: list[str] = []
                if entry.technology:
                    summary_bits.append(f"Technology: {entry.technology}")
                if entry.first_seen and entry.last_seen:
                    summary_bits.append(
                        f"Observed: {entry.first_seen} -> {entry.last_seen}"
                    )
                summary_bits.append(f"Prior occurrences: {entry.occurrences}")
                if entry.sentiment_direction:
                    summary_bits.append(
                        f"Sentiment direction: {entry.sentiment_direction}"
                    )
                add_paragraph("; ".join(summary_bits))

                if entry.cases:
                    add_paragraph("Prior cases:")
                    # Round 39 / Phase 3.3: dedupe by case_number so a
                    # single CSOne case that shows up in 5 daily
                    # rollups doesn't render 5x per customer.
                    for case in _dedupe_cases(entry.cases):
                        summary = case.summary or "(no summary)"
                        # Round 39 / Phase 3.2: replace "sev?" /
                        # "status?" placeholders with em-dashes so the
                        # report doesn't leak literal question-mark
                        # placeholders into the customer-facing text.
                        # Round 39 / Phase 3.4: coerce case_number
                        # through ``_coerce_case_number`` so floats
                        # like ``1141876078.0`` render as integer
                        # strings.
                        _cn = _coerce_case_number(case.case_number) or "\u2014"
                        _sev = (case.severity or "").strip() or "\u2014"
                        _stat = (case.status or "").strip() or "\u2014"
                        add_paragraph(
                            _safe_str(
                                f"  - {_cn} "
                                f"[{_sev} / "
                                f"{_stat}] "
                                f"opened={case.opened_at or '-'}; "
                                f"closed={case.closed_at or '-'}; "
                                f"summary={summary}",
                                limit=400,
                            )
                        )

                if entry.barriers:
                    add_paragraph("Recurring themes:")
                    for theme in entry.barriers:
                        tech = (
                            f" ({theme.technology})"
                            if theme.technology else ""
                        )
                        add_paragraph(
                            _safe_str(
                                f"  - {theme.theme}{tech} -- "
                                f"{theme.occurrences} occurrence(s)",
                                limit=400,
                            )
                        )

                if entry.resolutions:
                    add_paragraph("Top resolutions:")
                    for res in entry.resolutions:
                        # Round 86 / Build 62 (P1/F3): suppress
                        # [src: AdoptIQ_*.xlsx] internal filenames
                        # leaking into customer-facing Word.
                        _src_label = _safe_source_label(res.source_filename)
                        src = f" [src: {_src_label}]" if _src_label else ""
                        add_paragraph(
                            _safe_str(
                                f"  - {res.method_text}{src}",
                                limit=400,
                            )
                        )

                if entry.peer_guidance_clause:
                    add_paragraph(
                        _safe_str(entry.peer_guidance_clause, limit=520)
                    )
            except Exception as render_err:  # noqa: BLE001
                logger.warning(
                    "Round 17 / Historical Context: per-entry render "
                    "failed (%s); skipping entry.",
                    render_err,
                )
                continue

        if context.source_files:
            add_paragraph(
                _safe_str(
                    "Source files: " + "; ".join(context.source_files[:20]),
                    limit=600,
                )
            )
    except Exception as outer_err:  # noqa: BLE001
        logger.warning(
            "Round 17 / Historical Context: render_to_word top-level "
            "failure (%s); section may be incomplete.",
            outer_err,
        )


__all__ = [
    "HistoricalCase",
    "HistoricalContext",
    "HistoricalEntry",
    "HistoricalResolution",
    "HistoricalTheme",
    "build_support_operating_health_corpus_claim",
    "build_support_theme_corpus_claim",
    "format_peer_guidance_clause",
    "format_peer_guidance_ask_ai_line",
    "format_ranked_peer_guidance_clause",
    "select_ranked_peer_guidance",
    "load_ranked_peer_guidance",
    "build_historical_context",
    "render_to_text",
    "render_to_word",
]
