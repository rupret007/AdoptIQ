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

import logging
import re
import unicodedata
from dataclasses import dataclass, field
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
                theme=_safe_str(b.theme, limit=80),
                occurrences=int(b.occurrences or 0),
                technology=_safe_str(b.technology, limit=80),
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

        entries.append(
            HistoricalEntry(
                customer_name=_safe_str(history.name, limit=200),
                technology=_safe_str(history.technology, limit=80) or None,
                first_seen=_safe_str(history.first_seen, limit=32) or None,
                last_seen=_safe_str(history.last_seen, limit=32) or None,
                occurrences=int(history.occurrences or 0),
                cases=tuple(cases),
                barriers=tuple(themes),
                resolutions=tuple(resolutions),
                sentiment_direction=sentiment_dir,
                source_files=tuple(sorted({s for s in source_files if s})),
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
            "'AI Projects/AdoptIQ_CSOne_Reports' (with the user's "
            "Downloads folder and ad-hoc Intelligence uploads as "
            "fallbacks)."
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
    "build_historical_context",
    "render_to_text",
    "render_to_word",
]
