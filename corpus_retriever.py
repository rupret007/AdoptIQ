"""Round 17 / Phase C -- Read-side facade over the encrypted CSOne
knowledge corpus.

Consumers (Ask AI, report pre-fill, the Customer 360 page, the
Playbook page, admin dashboard) call into this module through one of
the typed dataclass-returning helpers.  No consumer ever opens the
SQLite database or talks to the encryption layer directly.

Design contract
---------------
* Every public function either returns a typed result or raises
  :class:`CorpusUnavailable`.  Callers handle the unavailable path by
  falling back to today's behavior.
* All inputs are length-capped (``_MAX_QUERY_LEN``) and pass through
  ``_safe_identifier`` before being interpolated -- though every SQL
  call uses parameterized queries, the helper is a defense-in-depth
  guard against malformed identifiers driving downstream regex /
  display logic.
* Returns dataclasses, never raw dicts, so downstream consumers do
  not accumulate magic keys.
* No customer PII at INFO level.  Names appear in the returned
  dataclasses (callers need them) but never in module-level logs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import statistics
import threading
import unicodedata
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from corpus_indexer import (
    BM25_B,
    BM25_K1,
    bm25_score,
    detect_theme,
    tokenize as _indexer_tokenize,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CorpusUnavailable(RuntimeError):
    """Raised when the corpus database is not reachable.  Possible
    reasons: feature flag off, encryption sentinel missing (no
    SharePoint ACL), schema migration incomplete, etc.  The exception
    message names the reason so the caller can log it without
    inventing one."""


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: Maximum length of any single user-supplied query string.
_MAX_QUERY_LEN: int = 500

#: Maximum number of result rows any retriever method will return.
_MAX_RESULTS: int = 200


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseRecord:
    customer_name: str
    case_number: str
    severity: str
    status: str
    is_open: bool
    opened_at: str
    closed_at: str
    summary: str
    source_filename: Optional[str] = None


@dataclass(frozen=True)
class BarrierRecord:
    technology: str
    theme: str
    occurrences: int
    first_seen: str
    last_seen: str
    severity: str = ""  # Round 180: raw SEVERITY_C / Severity label


@dataclass(frozen=True)
class ResolutionRecord:
    method_text: str
    technology: str
    theme: str
    source_filename: Optional[str] = None
    source_section: Optional[str] = None


@dataclass(frozen=True)
class SentimentSnapshot:
    snapshot_date: str
    score: Optional[float]
    evidence_text: str


@dataclass(frozen=True)
class CustomerHistory:
    name: str
    manager: Optional[str]
    technology: Optional[str]
    first_seen: Optional[str]
    last_seen: Optional[str]
    occurrences: int
    cases: tuple[CaseRecord, ...] = field(default_factory=tuple)
    barriers: tuple[BarrierRecord, ...] = field(default_factory=tuple)
    sentiment_trend: tuple[SentimentSnapshot, ...] = field(default_factory=tuple)
    top_resolutions: tuple[ResolutionRecord, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Theme:
    technology: str
    theme: str
    customers: int
    occurrences: int


# Round 175: aggregate-only peer evidence. Never carries peer names,
# emails, or case numbers — callers publish counts and a dominant method.
@dataclass(frozen=True)
class PeerGuidanceEvidence:
    theme: str
    technology: str
    peer_customer_count: int
    resolved_peer_count: int
    dominant_method_text: str
    dominant_method_peers: int
    closed_peer_count: int
    open_peer_count: int
    close_time_median_days: Optional[float]
    pulse_recovered_count: int
    pulse_worsened_count: int
    likely_next: str
    next_step: str
    evidence_sufficient: bool
    # Round 175: method-scoped trajectory. likely-next is derived from
    # these, not from uncoupled theme-peer case/pulse totals.
    method_closed_peer_count: int = 0
    method_open_peer_count: int = 0
    method_pulse_recovered_count: int = 0
    method_pulse_worsened_count: int = 0
    # Round 175.4: aggregate-safe identity exclusion (no names).
    exclusion_count: int = 0
    exclusion_digest: str = ""
    # Round 176: barrier lifecycle among method-peers. NULL/unknown
    # status does not count. likely_next_basis is barrier|case|pulse|"".
    method_barrier_closed_peer_count: int = 0
    method_barrier_open_peer_count: int = 0
    likely_next_basis: str = ""
    # Round 179: this-account lived path for the same theme+method.
    # not_tried | already_open | already_closed. Empty on thin/unavailable.
    # Never a name — only the path token.
    target_path: str = ""  # Round 179
    # Round 180: comparable-severity path. Default comparable so constructed
    # fixtures that omit severity keep the pre-R180 view contract.
    severity_comparable: bool = True
    comparable_severity_band: str = ""
    comparable_method_peer_count: int = 0
    incomparable_severity: bool = False
    # Round 181: comparable TAC-case severity on the case-fallback path.
    # Defaults stay comparable so constructed fixtures that omit these
    # fields keep the pre-R181 view contract.
    case_severity_comparable: bool = True
    comparable_case_severity_band: str = ""
    comparable_case_method_peer_count: int = 0
    incomparable_case_severity: bool = False


@dataclass(frozen=True)
class Chunk:
    text: str
    technology: Optional[str]
    theme: Optional[str]
    customer_name: Optional[str]
    score: float
    source_filename: Optional[str] = None
    source_section: Optional[str] = None
    chunk_id: Optional[int] = None


@dataclass(frozen=True)
class CorpusSnapshot:
    """Light-weight summary returned by :func:`get_status`."""
    available: bool
    files_total: int = 0
    files_parsed: int = 0
    last_parsed_at: Optional[str] = None
    indexed_at: Optional[str] = None
    customers: int = 0
    cases: int = 0
    chunks: int = 0
    schema_version: int = 0
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Connection accessor
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
# Round 175.3: same (theme, tech, exclusion digest) is asked by ranking + every
# existing insight surface. Cache dies with configure_connection so an
# index pass cannot serve stale trajectories. Round 175.4 keys on the
# alias/suffix exclusion digest, not a single name_norm.
_PEER_EVIDENCE_CACHE: OrderedDict[tuple, "PeerGuidanceEvidence"] = OrderedDict()
_PEER_EVIDENCE_CACHE_MAX = 128


def configure_connection(conn: Optional[sqlite3.Connection]) -> None:
    """Inject the corpus connection.  ``app_simple`` calls this once
    after the indexer initial pass; tests call it with an in-memory
    connection.  Passing ``None`` clears the connection so subsequent
    calls raise :class:`CorpusUnavailable`.

    Sets ``row_factory = sqlite3.Row`` so named column access works in
    every retriever.  No-ops on ``None``.
    """
    global _CONN
    with _LOCK:
        _PEER_EVIDENCE_CACHE.clear()  # Round 175.3
        if conn is not None:
            conn.row_factory = sqlite3.Row
        _CONN = conn


def _conn() -> sqlite3.Connection:
    with _LOCK:
        if _CONN is None:
            raise CorpusUnavailable("corpus connection has not been configured")
        return _CONN


# ---------------------------------------------------------------------------
# Input sanitization
# ---------------------------------------------------------------------------


_NAME_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z0-9 .,&'\-_/()]+")


def _safe_identifier(value: object, *, label: str = "value") -> str:
    """Reject anything that looks like a control character / attempt
    to break out of an identifier into something else.  Returns the
    coerced string truncated to ``_MAX_QUERY_LEN``.  Allow-listed
    via ``codeguard-0-input-validation-injection``."""
    if value is None:
        raise CorpusUnavailable(f"{label} is required")
    body = unicodedata.normalize("NFKC", str(value)).strip()
    if not body:
        raise CorpusUnavailable(f"{label} is empty")
    if len(body) > _MAX_QUERY_LEN:
        body = body[:_MAX_QUERY_LEN]
    if not _NAME_PATTERN.fullmatch(body):
        raise CorpusUnavailable(f"{label} contains disallowed characters")
    return body


def _normalize_for_lookup(value: str) -> str:
    body = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"[^a-z0-9]+", "", body)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_status() -> CorpusSnapshot:
    """Return a light-weight snapshot of the corpus state.  Never
    raises; surfaces ``available=False`` with a ``reason`` instead so
    the admin tile / banner can render."""
    try:
        conn = _conn()
    except CorpusUnavailable as unavailable:
        return CorpusSnapshot(available=False, reason=str(unavailable))
    try:
        cur = conn.cursor()
        files = int(cur.execute('SELECT COUNT(*) FROM "corpus_files"').fetchone()[0])
        parsed = int(cur.execute(
            'SELECT COUNT(*) FROM "corpus_files" WHERE "parse_status" = \'ok\''
        ).fetchone()[0])
        last_parsed = cur.execute(
            'SELECT MAX("parsed_at") FROM "corpus_files"'
        ).fetchone()[0]
        customers = int(cur.execute('SELECT COUNT(*) FROM "customers"').fetchone()[0])
        cases = int(cur.execute('SELECT COUNT(*) FROM "cases"').fetchone()[0])
        stats_row = cur.execute(
            'SELECT "doc_count", "indexed_at" FROM "corpus_stats" WHERE "id" = 1'
        ).fetchone()
        doc_count = int(stats_row[0]) if stats_row else 0
        indexed_at = stats_row[1] if stats_row else None
        version_row = cur.execute(
            'SELECT "version" FROM "schema_meta" WHERE "id" = 1'
        ).fetchone()
        schema_version = int(version_row[0]) if version_row else 0
        return CorpusSnapshot(
            available=doc_count > 0,
            files_total=files,
            files_parsed=parsed,
            last_parsed_at=last_parsed,
            indexed_at=indexed_at,
            customers=customers,
            cases=cases,
            chunks=doc_count,
            schema_version=schema_version,
        )
    except sqlite3.DatabaseError as db_err:
        return CorpusSnapshot(available=False, reason=f"db_error:{db_err}")


# ---------------------------------------------------------------------------
# Customer history
# ---------------------------------------------------------------------------


def get_customer_history(name: object, *, limit_cases: int = 50, limit_resolutions: int = 8) -> CustomerHistory:
    """Aggregate every record we have about ``name`` into a single
    typed result.  Raises :class:`CorpusUnavailable` when the corpus
    is not configured or the customer is unknown."""
    safe_name = _safe_identifier(name, label="customer name")
    norm = _normalize_for_lookup(safe_name)
    if not norm:
        raise CorpusUnavailable("customer name normalized to empty string")
    conn = _conn()
    cur = conn.cursor()
    row = cur.execute(
        'SELECT "id", "name", "manager", "technology", "first_seen", '
        '       "last_seen", "occurrences" '
        'FROM "customers" WHERE "name_norm" = ?;',
        (norm,),
    ).fetchone()
    if row is None:
        raise CorpusUnavailable(f"customer '{safe_name}' is not in the corpus")
    customer_id = int(row["id"])
    customer_name = str(row["name"])

    cases = _load_cases(cur, customer_id, limit=max(1, min(int(limit_cases), _MAX_RESULTS)))
    barriers = _load_barriers(cur, customer_id)
    sentiment = _load_sentiment_trend(cur, customer_id)
    resolutions = _load_top_resolutions(
        cur, customer_id,
        limit=max(1, min(int(limit_resolutions), _MAX_RESULTS)),
    )

    return CustomerHistory(
        name=customer_name,
        manager=row["manager"],
        technology=row["technology"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        occurrences=int(row["occurrences"] or 0),
        cases=tuple(cases),
        barriers=tuple(barriers),
        sentiment_trend=tuple(sentiment),
        top_resolutions=tuple(resolutions),
    )


def _load_cases(cur: sqlite3.Cursor, customer_id: int, *, limit: int) -> list[CaseRecord]:
    rows = cur.execute(
        'SELECT c."case_number", c."severity", c."status", c."is_open", '
        '       c."opened_at", c."closed_at", c."summary", '
        '       cust."name" AS "customer_name", '
        '       f."filename" AS "source_filename" '
        'FROM "cases" c '
        'JOIN "customers" cust ON cust."id" = c."customer_id" '
        'LEFT JOIN "corpus_files" f ON f."id" = c."source_file_id" '
        'WHERE c."customer_id" = ? '
        'ORDER BY COALESCE(c."opened_at", c."last_seen", \'\') DESC, c."id" DESC '
        'LIMIT ?;',
        (customer_id, int(limit)),
    ).fetchall()
    return [
        CaseRecord(
            customer_name=str(r["customer_name"] or ""),
            case_number=str(r["case_number"] or ""),
            severity=str(r["severity"] or ""),
            status=str(r["status"] or ""),
            is_open=bool(r["is_open"]),
            opened_at=str(r["opened_at"] or ""),
            closed_at=str(r["closed_at"] or ""),
            summary=str(r["summary"] or ""),
            source_filename=str(r["source_filename"] or "") or None,
        )
        for r in rows
    ]


def _load_barriers(cur: sqlite3.Cursor, customer_id: int) -> list[BarrierRecord]:
    rows = cur.execute(
        'SELECT "technology", "theme", '
        '       SUM("occurrences") AS "occurrences", '
        '       MIN("first_seen") AS "first_seen", '
        '       MAX("last_seen") AS "last_seen", '
        '       CASE WHEN COUNT(DISTINCT CASE '
        '            WHEN TRIM(COALESCE("severity", \'\')) = \'\' THEN NULL '
        '            ELSE LOWER(TRIM("severity")) END) = 1 '
        '            THEN MAX(CASE WHEN TRIM(COALESCE("severity", \'\')) = \'\' '
        '                         THEN NULL ELSE "severity" END) '
        '            ELSE \'\' END AS "severity" '  # Round 180: mixed → blank
        'FROM "barriers" WHERE "customer_id" = ? '
        'GROUP BY "technology", "theme" '
        'ORDER BY "occurrences" DESC, "theme" ASC '
        'LIMIT ?;',
        (customer_id, _MAX_RESULTS),
    ).fetchall()
    return [
        BarrierRecord(
            technology=str(r["technology"] or ""),
            theme=str(r["theme"] or "general"),
            occurrences=int(r["occurrences"] or 0),
            first_seen=str(r["first_seen"] or ""),
            last_seen=str(r["last_seen"] or ""),
            severity=str(r["severity"] or ""),
        )
        for r in rows
    ]


def _load_sentiment_trend(cur: sqlite3.Cursor, customer_id: int) -> list[SentimentSnapshot]:
    rows = cur.execute(
        'SELECT "snapshot_date", "score", "evidence_text" '
        'FROM "sentiments" WHERE "customer_id" = ? '
        'ORDER BY COALESCE("snapshot_date", \'\') ASC, "id" ASC '
        'LIMIT ?;',
        (customer_id, _MAX_RESULTS),
    ).fetchall()
    return [
        SentimentSnapshot(
            snapshot_date=str(r["snapshot_date"] or ""),
            score=(None if r["score"] is None else float(r["score"])),
            evidence_text=str(r["evidence_text"] or ""),
        )
        for r in rows
    ]


def _load_top_resolutions(
    cur: sqlite3.Cursor, customer_id: int, *, limit: int
) -> list[ResolutionRecord]:
    rows = cur.execute(
        'SELECT res."method_text", res."source_section", '
        '       b."technology", b."theme", '
        '       f."filename" AS "source_filename" '
        'FROM "resolutions" res '
        'JOIN "barriers" b ON b."id" = res."barrier_id" '
        'LEFT JOIN "corpus_files" f ON f."id" = res."source_file_id" '
        'WHERE b."customer_id" = ? '
        'ORDER BY COALESCE(res."first_seen", \'\') DESC, res."id" DESC '
        'LIMIT ?;',
        (customer_id, int(limit)),
    ).fetchall()
    return [
        ResolutionRecord(
            method_text=str(r["method_text"] or ""),
            technology=str(r["technology"] or ""),
            theme=str(r["theme"] or "general"),
            source_filename=str(r["source_filename"] or "") or None,
            source_section=str(r["source_section"] or "") or None,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Recurring themes
# ---------------------------------------------------------------------------


def get_recurring_themes(technology: object, *, top_k: int = 10) -> list[Theme]:
    """Return the most-frequent ``(technology, theme)`` pairs seen
    across all customers in ``technology``.  Empty technology means
    "any technology".  Sorted by ``occurrences`` desc with theme
    name as the deterministic tiebreaker."""
    safe_tech: Optional[str]
    if technology is None or (isinstance(technology, str) and not technology.strip()):
        safe_tech = None
    else:
        safe_tech = _safe_identifier(technology, label="technology")
    conn = _conn()
    cur = conn.cursor()
    if safe_tech is None:
        rows = cur.execute(
            'SELECT "technology", "theme", '
            '       COUNT(DISTINCT "customer_id") AS "customers", '
            '       SUM("occurrences") AS "occurrences" '
            'FROM "barriers" '
            'GROUP BY "technology", "theme" '
            'ORDER BY "occurrences" DESC, "theme" ASC '
            'LIMIT ?;',
            (max(1, min(int(top_k), _MAX_RESULTS)),),
        ).fetchall()
    else:
        rows = cur.execute(
            'SELECT "technology", "theme", '
            '       COUNT(DISTINCT "customer_id") AS "customers", '
            '       SUM("occurrences") AS "occurrences" '
            'FROM "barriers" '
            'WHERE LOWER("technology") = LOWER(?) '
            'GROUP BY "technology", "theme" '
            'ORDER BY "occurrences" DESC, "theme" ASC '
            'LIMIT ?;',
            (safe_tech, max(1, min(int(top_k), _MAX_RESULTS))),
        ).fetchall()
    return [
        Theme(
            technology=str(r["technology"] or ""),
            theme=str(r["theme"] or "general"),
            customers=int(r["customers"] or 0),
            occurrences=int(r["occurrences"] or 0),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Resolutions for a barrier theme
# ---------------------------------------------------------------------------


def get_resolutions_for(theme: object, technology: object = None, *, limit: int = 10) -> list[ResolutionRecord]:
    """Return up to ``limit`` resolution records that were observed
    for the requested ``(theme, technology)`` combination.  Sorted by
    most recent ``first_seen`` first."""
    safe_theme = _safe_identifier(theme, label="theme")
    safe_tech: Optional[str]
    if technology is None or (isinstance(technology, str) and not technology.strip()):
        safe_tech = None
    else:
        safe_tech = _safe_identifier(technology, label="technology")
    conn = _conn()
    cur = conn.cursor()
    bound: tuple = (safe_theme,)
    sql = (
        'SELECT res."method_text", res."source_section", '
        '       b."technology", b."theme", '
        '       f."filename" AS "source_filename" '
        'FROM "resolutions" res '
        'JOIN "barriers" b ON b."id" = res."barrier_id" '
        'LEFT JOIN "corpus_files" f ON f."id" = res."source_file_id" '
        'WHERE LOWER(b."theme") = LOWER(?) '
    )
    if safe_tech is not None:
        sql += 'AND LOWER(b."technology") = LOWER(?) '
        bound = (safe_theme, safe_tech)
    sql += 'ORDER BY COALESCE(res."first_seen", \'\') DESC, res."id" DESC LIMIT ?;'
    bound = bound + (max(1, min(int(limit), _MAX_RESULTS)),)
    rows = cur.execute(sql, bound).fetchall()
    return [
        ResolutionRecord(
            method_text=str(r["method_text"] or ""),
            technology=str(r["technology"] or ""),
            theme=str(r["theme"] or "general"),
            source_filename=str(r["source_filename"] or "") or None,
            source_section=str(r["source_section"] or "") or None,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Peer guidance (Round 175)
# ---------------------------------------------------------------------------

_MIN_PEER_CUSTOMERS = 2
# Round 175.3: observed median is unpublished when method-closed
# durations disagree by more than this many days.
_PEER_MEDIAN_MAX_SPREAD_DAYS = 14.0
_PEER_LIKELY_NEXT = frozenset(
    {
        "closure",
        "remains_open",
        "pulse_worsening",
        "pulse_recovery",  # Round 175.2: method-scoped recovered pulse
        "insufficient",
    }
)
# Round 175.4: deterministic PII gate on method text. Fail closed — no
# LLM/redaction service. Email, filename, TAC/case id, phone, @-tokens,
# and Title-Case name pairs must not reach any published surface.
# Method phrases that start with a resolution verb ("Rotated Service
# Token") are not treated as person names — Salesforce Title-Case
# methods must remain publishable.
_PEER_PII_EMAIL_RE = re.compile(
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
    re.IGNORECASE,
)
_PEER_PII_FILE_RE = re.compile(
    r"\b[\w.-]+\.(csv|xlsx|xls|docx|doc|pdf|txt|json|xml|log)\b",
    re.IGNORECASE,
)
_PEER_PII_CASE_RE = re.compile(
    r"\b(?:TAC|SR|CASE|CSONE)[-_ ]?[A-Z0-9]{3,}\b|\b[A-Z]{2,}[-_]\d{2,}\b",
    re.IGNORECASE,
)
_PEER_PII_PHONE_RE = re.compile(
    r"\b(?:\+?\d{1,3}[-. ]?)?(?:\(?\d{3}\)?[-. ]?)\d{3}[-. ]?\d{4}\b"
)
_PEER_PII_NAME_RE = re.compile(
    r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+){0,3}\s+[A-Z](?:[a-z]+)?\b"
)
_PEER_METHOD_LEADING_VERBS = frozenset(
    {
        "applied",
        "assigned",
        "changed",
        "cleared",
        "closed",
        "configured",
        "created",
        "disabled",
        "documented",
        "enabled",
        "escalated",
        "followed",
        "granted",
        "implemented",
        "installed",
        "migrated",
        "modified",
        "opened",
        "provisioned",
        "rebuilt",
        "reissued",
        "removed",
        "replaced",
        "reset",
        "restarted",
        "restored",
        "reviewed",
        "rotated",
        "scheduled",
        "trained",
        "unlocked",
        "updated",
        "whitelisted",
    }
)


def _method_match_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _peer_case_identity(value: object) -> str:
    """Collapse case-number snapshots the same way methods are keyed."""
    return _method_match_key(value)


def _peer_text_leaks_pii(value: object) -> bool:  # Round 175.4
    """True when published peer text would carry name/email/id/filename."""
    text = str(value or "")
    if not text:
        return False
    if "@" in text:
        return True
    if _PEER_PII_EMAIL_RE.search(text):
        return True
    if _PEER_PII_FILE_RE.search(text):
        return True
    if _PEER_PII_CASE_RE.search(text):
        return True
    if _PEER_PII_PHONE_RE.search(text):
        return True
    for match in _PEER_PII_NAME_RE.finditer(text):
        first = match.group(0).split()[0].casefold().rstrip(".,;:")
        if first not in _PEER_METHOD_LEADING_VERBS:
            return True
    return False


def _peer_identity_exclusion(exclude_customer: str) -> Optional[tuple[frozenset[str], str]]:
    """Join-key identity group for *exclude_customer*, or None if unproved.

    Round 175.4: Round 132 alias keys plus ``_clean_name_for_key`` legal-suffix
    folding. Digest is over sorted keys — never names. Fail closed when the
    registry cannot be loaded or the key set is empty.
    """
    try:
        from data_normalization import alias_join_keys_for_name, load_customer_alias_registry
    except Exception:
        return None
    try:
        registry = load_customer_alias_registry()
        keys = {
            str(key).strip()
            for key in alias_join_keys_for_name(exclude_customer, registry=registry)
            if str(key or "").strip()
        }
    except Exception:
        return None
    if not keys:
        return None
    digest = hashlib.sha256("\n".join(sorted(keys)).encode("utf-8")).hexdigest()
    return frozenset(keys), digest


def _peer_row_in_exclusion(
    *,
    name: str,
    exclude_customer: str,
    exclude_keys: frozenset[str],
    registry: object,
) -> bool:
    """True when this corpus customer is the target or an alias/suffix sibling."""
    if not str(name or "").strip():
        return True
    try:
        from data_normalization import alias_join_keys_for_name, customer_names_match
    except Exception:
        return True
    try:
        peer_keys = {
            str(key).strip()
            for key in alias_join_keys_for_name(name, registry=registry)
            if str(key or "").strip()
        }
        if peer_keys & set(exclude_keys):
            return True
        return bool(
            customer_names_match(name, exclude_customer, registry=registry)
        )
    except Exception:
        return True


_PEER_TARGET_PATHS = frozenset(
    {"", "not_tried", "already_open", "already_closed"}
)


def _peer_target_path(  # Round 179
    recs: Sequence[sqlite3.Row],
    dominant_text: str,
) -> str:
    """This-account path for the dominant method. Never invents a future.

    ``already_closed`` / ``already_open`` require the same method key.
    Mixed or unknown barrier status after using the method fails closed
    to ``already_open`` so we do not recommend a fresh trial.
    """
    if not recs:
        return "not_tried"
    dom_key = _method_match_key(dominant_text)
    if not dom_key:
        return "not_tried"
    used = False
    for rec in recs:
        text = str(rec["method_text"] or "").strip()
        if text and _method_match_key(text) == dom_key:
            used = True
            break
    if not used:
        return "not_tried"
    outcome = _peer_barrier_outcome(recs)
    if outcome == "closed":
        return "already_closed"
    return "already_open"


def _peer_next_step(  # Round 178
    likely_next: str,
    method_text: str,
    target_path: str = "",
) -> str:
    """Evidence-typed next-step. Empty when thin/PII/already completed.

    Round 178: turn the observed outcome into an operator decision. Positive
    paths say how to test/hold the peer method and verify the result; negative
    paths explicitly say not to treat the method as a resolution. The method
    itself is rendered beside this instruction, so it is not copied here.
    Insight appends may omit this fragment under the 520-char cap rather than
    dropping likely-next.
    Round 179: this account's own lived path wins. A completed path gets no
    new next step. A path already tried here that remains open is not a
    fresh trial — do not repeat the method unchanged.
    """
    if not str(method_text or "").strip() or _peer_text_leaks_pii(method_text):
        return ""
    path = str(target_path or "")
    if path == "already_closed":  # Round 179
        return ""
    if path == "already_open":  # Round 179
        return (
            "This account already used the peer-observed method and the work "
            "remains open. Do not repeat it unchanged; choose another "
            "intervention."
        )
    if likely_next == "closure":
        return (
            "Test the peer-observed method on the current barrier, then verify "
            "closure before marking it resolved."
        )
    if likely_next == "remains_open":
        return (
            "Do not treat the peer-observed method as resolution; keep the barrier "
            "open and choose another intervention."
        )
    if likely_next == "pulse_worsening":
        return (
            "Pause before repeating the peer-observed method; check pulse and "
            "choose another intervention."
        )
    if likely_next == "pulse_recovery":  # Round 175.2
        return (
            "Keep the peer-observed method in place and verify pulse before "
            "adding more work."
        )
    return ""


def _peer_durations_agree(durations: Sequence[float]) -> bool:  # Round 175.3
    """True only when ≥2 dated close windows cluster. Wide spread omits median."""
    values: list[float] = []
    for item in durations:
        try:
            value = float(item)
        except (TypeError, ValueError):
            return False
        if value != value or value < 0:
            return False
        values.append(value)
    if len(values) < 2:
        return False
    return (max(values) - min(values)) <= _PEER_MEDIAN_MAX_SPREAD_DAYS


def _decide_peer_likely_next(  # Round 175.3
    *,
    dominant_n: int,
    min_n: int,
    method_closed: int,
    method_open: int,
    method_pulse_recovered: int,
    method_pulse_worsened: int,
    method_barrier_closed: int = 0,  # Round 176
    method_barrier_open: int = 0,
) -> str:
    """Case majority, then pulse. Ties and mixed close+worse-pulse fail closed.

    A 2-2 closed/open split is not closure. Closed cases plus method-scoped
    pulse worsening is mixed evidence, not a likely-next. Open work still
    outranks recovered pulse (Round 175.2).
    Round 176: when ≥min_n method-peers have a known barrier status,
    that work-item trajectory is the SSoT and a disagreement with
    theme-matched TAC cases (or closed-barrier + worse pulse) is mixed.
    """
    likely, _basis = _peer_likely_next_and_basis(
        dominant_n=dominant_n,
        min_n=min_n,
        method_closed=method_closed,
        method_open=method_open,
        method_pulse_recovered=method_pulse_recovered,
        method_pulse_worsened=method_pulse_worsened,
        method_barrier_closed=method_barrier_closed,
        method_barrier_open=method_barrier_open,
    )
    return likely


def _peer_likely_next_and_basis(  # Round 176
    *,
    dominant_n: int,
    min_n: int,
    method_closed: int,
    method_open: int,
    method_pulse_recovered: int,
    method_pulse_worsened: int,
    method_barrier_closed: int = 0,
    method_barrier_open: int = 0,
) -> tuple[str, str]:
    """Return (likely_next, basis). basis is barrier|case|pulse|''."""
    if dominant_n < min_n:
        return "insufficient", ""
    barrier_closed = (
        method_barrier_closed >= min_n and method_barrier_closed > method_barrier_open
    )
    barrier_open = (
        method_barrier_open >= min_n and method_barrier_open > method_barrier_closed
    )
    known_barrier = int(method_barrier_closed) + int(method_barrier_open)
    has_barrier_signal = known_barrier >= min_n
    case_closed = method_closed >= min_n and method_closed > method_open
    case_open = method_open >= min_n and method_open > method_closed
    pulse_worse = (
        method_pulse_worsened >= min_n
        and method_pulse_worsened > method_pulse_recovered
    )
    pulse_better = (
        method_pulse_recovered >= min_n
        and method_pulse_recovered > method_pulse_worsened
    )
    if has_barrier_signal:
        if barrier_closed and case_open:
            return "insufficient", ""
        if barrier_open and case_closed:
            return "insufficient", ""
        if barrier_closed and pulse_worse:
            return "insufficient", ""
        if barrier_closed:
            return "closure", "barrier"
        if barrier_open:
            return "remains_open", "barrier"
        # Known statuses exist but no majority (tie / split).
        return "insufficient", ""
    if case_closed and pulse_worse:
        return "insufficient", ""
    if case_closed:
        return "closure", "case"
    if case_open:
        return "remains_open", "case"
    if pulse_worse:
        return "pulse_worsening", "pulse"
    if pulse_better:
        return "pulse_recovery", "pulse"
    return "insufficient", ""


def _peer_barrier_outcome(  # Round 176
    recs: Sequence[sqlite3.Row],
) -> str:
    """Collapse one peer's theme+tech barriers to open, closed, or none.

    Mixed open/closed snapshots contribute no outcome. NULL/unknown
    ``is_open`` is skipped so a blank AB_STATUS_C does not invent closed.
    """
    states: set[str] = set()
    for rec in recs:
        try:
            flag = rec["barrier_is_open"]
        except (KeyError, IndexError, TypeError):
            continue
        if flag is None:
            continue
        try:
            states.add("open" if int(flag) else "closed")
        except (TypeError, ValueError):
            continue
    if "open" in states and "closed" in states:
        return "none"
    if "open" in states:
        return "open"
    if "closed" in states:
        return "closed"
    return "none"


_R180_SEVERITY_BANDS = frozenset({"critical", "high", "medium", "low"})
_R180_SEVERITY_ALIASES: dict[str, str] = {
    "critical": "critical",
    "p1": "critical",
    "sev1": "critical",
    "s1": "critical",
    "1": "critical",
    "high": "high",
    "p2": "high",
    "sev2": "high",
    "s2": "high",
    "2": "high",
    "medium": "medium",
    "moderate": "medium",
    "p3": "medium",
    "sev3": "medium",
    "s3": "medium",
    "3": "medium",
    "low": "low",
    "p4": "low",
    "sev4": "low",
    "s4": "low",
    "4": "low",
    "informational": "low",
    "info": "low",
}


def _peer_severity_band(value: object) -> str:
    """Exact severity band. Critical ≠ High. Blank/unknown → "". Round 180."""
    raw = str(value or "").strip().casefold()
    if not raw or raw in {"nan", "none", "null", "unknown", "n/a", "na", "--", "-"}:
        return ""
    collapsed = re.sub(r"[\s_\-]+", "", raw)
    band = _R180_SEVERITY_ALIASES.get(collapsed, "")
    if band in _R180_SEVERITY_BANDS:
        return band
    return ""


def _peer_barrier_severity_band(recs: Sequence[sqlite3.Row]) -> str:
    """One peer's theme+tech barriers collapse to a single band, unknown "", or a mixed sentinel. Round 180."""
    bands: list[str] = []
    for rec in recs:
        try:
            raw = rec["barrier_severity"]
        except (KeyError, IndexError, TypeError):
            continue
        band = _peer_severity_band(raw)
        if band and band not in bands:
            bands.append(band)
    if len(bands) == 1:
        return bands[0]
    # Round 183: mixed known severities must not become an unknown fallback.
    return "__mixed__" if bands else ""


def _lookup_target_severity_band(
    cur: sqlite3.Cursor,
    customer_name: str,
    theme: str,
    tech: str,
) -> str:
    """This-account theme+tech barrier band, mixed sentinel, or "" when missing. Round 180."""
    norm = _normalize_for_lookup(customer_name)
    if not norm:
        return ""
    try:
        row = cur.execute(
            'SELECT "id" FROM "customers" WHERE "name_norm" = ?;',
            (norm,),
        ).fetchone()
    except sqlite3.DatabaseError:
        return ""
    if row is None:
        return ""
    try:
        rows = cur.execute(
            'SELECT "severity" FROM "barriers" '
            'WHERE "customer_id" = ? '
            '  AND LOWER("theme") = LOWER(?) '
            '  AND LOWER("technology") = LOWER(?);',
            (int(row["id"]), theme, tech),
        ).fetchall()
    except sqlite3.DatabaseError:
        return ""
    bands: list[str] = []
    for rec in rows:
        band = _peer_severity_band(rec["severity"] if rec is not None else "")
        if band and band not in bands:
            bands.append(band)
    if len(bands) == 1:
        return bands[0]
    # Round 183: mixed known severities must not become an unknown fallback.
    return "__mixed__" if bands else ""


def _peer_pulse_direction(  # Round 175.3
    points: Sequence[tuple[Optional[datetime], float]],
    *,
    not_before: Optional[datetime],
) -> str:
    """first-vs-last score. Pulse last-seen before the case event is stale."""
    if len(points) < 2:
        return ""
    last_dt = points[-1][0]
    if not_before is not None and (last_dt is None or last_dt < not_before):
        return ""
    delta = points[-1][1] - points[0][1]
    if delta > 0.05:
        return "recovered"
    if delta < -0.05:
        return "worsened"
    return ""


def _peer_case_outcome(  # Round 175
    recs: Sequence[sqlite3.Row],
) -> tuple[str, Optional[float], Optional[datetime]]:
    """Collapse one peer's theme-matched cases to open, closed, or none.

    Round 175.4: same identity contract as Round 174
    ``_closed_case_precedent``. Mixed open/closed or conflicting keyed
    observations contribute no outcome, independent of input order.
    Only consistently open identities may make a peer open; only agreed
    valid closed windows may make it closed. Unkeyed rows stay independent.
    The third value is the case-event clock used to drop stale pulse.
    """
    if not recs:
        return "none", None, None

    keyed: dict[str, list[sqlite3.Row]] = defaultdict(list)
    unkeyed: list[sqlite3.Row] = []
    for rec in recs:
        case_number = _peer_case_identity(rec["case_number"])
        if case_number:
            keyed[case_number].append(rec)
        else:
            unkeyed.append(rec)

    def _closed_window(rec: sqlite3.Row) -> Optional[tuple[datetime, datetime]]:
        if bool(rec["is_open"]):
            return None
        opened = _peer_parse_timestamp(rec["opened_at"])
        closed = _peer_parse_timestamp(rec["closed_at"])
        if opened is None or closed is None or closed < opened:
            return None
        return opened, closed

    def _classify_identity(
        observations: Sequence[sqlite3.Row],
    ) -> Optional[tuple[str, Optional[float], Optional[datetime]]]:
        windows = [_closed_window(item) for item in observations]
        any_open = any(bool(item["is_open"]) for item in observations)
        any_closed_flag = any(not bool(item["is_open"]) for item in observations)
        if any_open and any_closed_flag:
            return None
        if any_open:
            open_at_local: list[datetime] = []
            for item in observations:
                opened = _peer_parse_timestamp(item["opened_at"])
                if opened is not None:
                    open_at_local.append(opened)
            return "open", None, max(open_at_local) if open_at_local else None
        if any(window is None for window in windows):
            return None
        agreed = {window for window in windows if window is not None}
        if len(agreed) != 1:
            return None
        opened, closed = next(iter(agreed))
        duration = (closed - opened).total_seconds() / 86_400.0
        return "closed", duration, closed

    peer_open = False
    peer_closed = False
    durations: list[float] = []
    close_at: list[datetime] = []
    open_at: list[datetime] = []
    identities: list[Sequence[sqlite3.Row]] = list(keyed.values())
    identities.extend([item] for item in unkeyed)
    for observations in identities:
        classified = _classify_identity(observations)
        if classified is None:
            continue
        state, duration, pivot = classified
        if state == "open":
            peer_open = True
            if pivot is not None:
                open_at.append(pivot)
        elif state == "closed":
            peer_closed = True
            if duration is not None:
                durations.append(duration)
            if pivot is not None:
                close_at.append(pivot)
    if peer_open:
        return "open", None, max(open_at) if open_at else None
    if peer_closed:
        # Round 175.2: per-peer median, not last-closed-case wins.
        median = round(float(statistics.median(durations)), 1) if durations else None
        return "closed", median, max(close_at) if close_at else None
    return "none", None, None


def _peer_parse_timestamp(value: object) -> Optional[datetime]:
    raw = str(value or "").strip()
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


def _remember_peer_evidence(
    cache_key: tuple,
    evidence: PeerGuidanceEvidence,
) -> PeerGuidanceEvidence:
    """Bound FIFO. configure_connection clears this. Round 175.3."""
    with _LOCK:
        if cache_key not in _PEER_EVIDENCE_CACHE:
            if len(_PEER_EVIDENCE_CACHE) >= _PEER_EVIDENCE_CACHE_MAX:
                _PEER_EVIDENCE_CACHE.popitem(last=False)
            _PEER_EVIDENCE_CACHE[cache_key] = evidence
    return evidence


def _empty_peer_guidance(theme: str, technology: str) -> PeerGuidanceEvidence:
    return PeerGuidanceEvidence(
        theme=theme,
        technology=technology,
        peer_customer_count=0,
        resolved_peer_count=0,
        dominant_method_text="",
        dominant_method_peers=0,
        closed_peer_count=0,
        open_peer_count=0,
        close_time_median_days=None,
        pulse_recovered_count=0,
        pulse_worsened_count=0,
        likely_next="insufficient",
        next_step="",
        evidence_sufficient=False,
    )


def _peer_case_severity_band(  # Round 181
    recs: Sequence[sqlite3.Row],
) -> str:
    """One peer's theme-matched cases collapse to a single band, unknown "", or a mixed sentinel.

    Mixed known bands on one peer contribute no comparable path — the
    same fail-closed shape as mixed open/closed on one identity.
    """
    bands: list[str] = []
    for rec in recs:
        try:
            raw = rec["severity"]
        except (KeyError, IndexError, TypeError):
            continue
        band = _peer_severity_band(raw)
        if band and band not in bands:
            bands.append(band)
    if len(bands) == 1:
        return bands[0]
    # Round 183: mixed known severities must not become an unknown fallback.
    return "__mixed__" if bands else ""


def _lookup_target_case_severity_band(  # Round 181
    cur: sqlite3.Cursor,
    customer_name: str,
    theme: str,
) -> str:
    """This-account theme-matched TAC band, mixed sentinel, or "" when missing."""
    norm = _normalize_for_lookup(customer_name)
    if not norm:
        return ""
    try:
        row = cur.execute(
            'SELECT "id" FROM "customers" WHERE "name_norm" = ?;',
            (norm,),
        ).fetchone()
    except sqlite3.DatabaseError:
        return ""
    if row is None:
        return ""
    try:
        rows = cur.execute(
            'SELECT "severity", "summary" FROM "cases" WHERE "customer_id" = ?;',
            (int(row["id"]),),
        ).fetchall()
    except sqlite3.DatabaseError:
        return ""
    theme_key = str(theme or "").casefold()
    bands: list[str] = []
    for rec in rows:
        if detect_theme(str(rec["summary"] or "")).casefold() != theme_key:
            continue
        band = _peer_severity_band(rec["severity"] if rec is not None else "")
        if band and band not in bands:
            bands.append(band)
    if len(bands) == 1:
        return bands[0]
    # Round 183: mixed known severities must not become an unknown fallback.
    return "__mixed__" if bands else ""


def get_peer_guidance_evidence(
    theme: object,
    technology: object,
    *,
    exclude_customer: object,
    min_peers: int = _MIN_PEER_CUSTOMERS,
    target_severity: object = None,
    target_case_severity: object = None,
) -> PeerGuidanceEvidence:
    """Return aggregate peer-trajectory evidence for ``theme`` + ``technology``.

    Round 175 knowledge layer: other corpus customers who already lived
    this theme/technology path.  Snapshot rows are collapsed per
    customer before counting (same identity rule as Round 174 case
    dedupe).  A peer whose own snapshots disagree on method is skipped
    for the dominant-method tally rather than guessed.  likely-next is
    derived only from peers who share the dominant method (method
    coupled to case/pulse trajectory).  Round 175.4 excludes the
    target's full alias/legal-suffix identity group; snapshot
    disagreements on one case identity contribute no trajectory.
    Round 176 joins ``barriers.is_open`` (from AB_STATUS_C / STATUS_C)
    so likely-next is the adoption-barrier lifecycle when that status
    is known for ≥min_n method-peers; theme-matched TAC cases remain
    the fallback when status is unknown, and a barrier/case disagreement
    fails closed.
    Round 180 counts method-peer outcomes only on a comparable-severity
    path (exact band; Critical ≠ High). All-unknown severity keeps the
    pre-R180 behavior. Two or more known bands, or a known target band
    that differs from the single peer band, withhold likely-next.
    Round 181: the TAC fallback counts method-peer case outcomes only on
    a comparable-severity path (``cases.severity`` already persisted;
    Critical ≠ High). All-unknown case severity keeps the pre-R181
    behavior. Two or more known bands, or a known this-account band that
    differs from the single peer band, withhold *case-basis* likely-next
    only — barrier and pulse bases stay the Round 176 SSoT.
    The payload is counts-only — no peer names, emails, or case numbers.
    """

    try:
        safe_theme = _safe_identifier(theme, label="theme")
        safe_tech = _safe_identifier(technology, label="technology")
        safe_exclude = _safe_identifier(exclude_customer, label="customer name")
        conn = _conn()
    except CorpusUnavailable:
        return _empty_peer_guidance(str(theme or ""), str(technology or ""))
    try:
        min_n = max(_MIN_PEER_CUSTOMERS, min(int(min_peers), 20))
    except (TypeError, ValueError):
        min_n = _MIN_PEER_CUSTOMERS

    identity = _peer_identity_exclusion(safe_exclude)
    if identity is None:
        empty = _empty_peer_guidance(safe_theme, safe_tech)
        return empty
    exclude_keys, exclusion_digest = identity

    cur = conn.cursor()
    if target_severity is None:
        resolved_target = _lookup_target_severity_band(
            cur, safe_exclude, safe_theme, safe_tech
        )
    else:
        resolved_target = _peer_severity_band(target_severity)
    if target_case_severity is None:
        resolved_target_case = _lookup_target_case_severity_band(
            cur, safe_exclude, safe_theme
        )
    else:
        resolved_target_case = _peer_severity_band(target_case_severity)

    cache_key = (
        id(conn),
        safe_theme.casefold(),
        safe_tech.casefold(),
        exclusion_digest,
        min_n,
        resolved_target,  # Round 180
        resolved_target_case,  # Round 181
    )
    with _LOCK:
        cached = _PEER_EVIDENCE_CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        from data_normalization import load_customer_alias_registry

        alias_registry = load_customer_alias_registry()
    except Exception:
        empty = _empty_peer_guidance(safe_theme, safe_tech)
        return _remember_peer_evidence(cache_key, empty)

    rows = cur.execute(
        'SELECT cust."id" AS "customer_id", '
        '       cust."name" AS "customer_name", '
        '       res."method_text" AS "method_text", '
        '       res."first_seen" AS "first_seen", '
        '       res."id" AS "resolution_id", '
        '       b."status" AS "barrier_status", '
        '       b."is_open" AS "barrier_is_open", '  # Round 176
        '       b."severity" AS "barrier_severity" '  # Round 180
        'FROM "barriers" b '
        'JOIN "customers" cust ON cust."id" = b."customer_id" '
        'LEFT JOIN "resolutions" res ON res."barrier_id" = b."id" '
        'WHERE LOWER(b."theme") = LOWER(?) '
        '  AND LOWER(b."technology") = LOWER(?) '
        'ORDER BY cust."id" ASC, COALESCE(res."first_seen", \'\') DESC, '
        '         COALESCE(res."id", 0) DESC;',
        (safe_theme, safe_tech),
    ).fetchall()

    by_customer: dict[int, list[sqlite3.Row]] = defaultdict(list)
    target_recs: list[sqlite3.Row] = []  # Round 179: this-account path
    for row in rows:
        peer_name = str(row["customer_name"] or "")
        if _peer_row_in_exclusion(
            name=peer_name,
            exclude_customer=safe_exclude,
            exclude_keys=exclude_keys,
            registry=alias_registry,
        ):
            target_recs.append(row)
            continue
        by_customer[int(row["customer_id"])].append(row)
    peer_ids = list(by_customer)
    if not peer_ids:
        empty = _empty_peer_guidance(safe_theme, safe_tech)
        return _remember_peer_evidence(cache_key, empty)

    method_peers: dict[str, list[tuple[int, str]]] = defaultdict(list)
    resolved_peer_count = 0
    for cid, recs in by_customer.items():
        seen_keys: list[str] = []
        first_text = ""
        for rec in recs:
            text = str(rec["method_text"] or "").strip()
            key = _method_match_key(text)
            if not text or not key:
                continue
            if key not in seen_keys:
                seen_keys.append(key)
                if not first_text:
                    first_text = text
        if len(seen_keys) == 1:
            resolved_peer_count += 1
            method_peers[seen_keys[0]].append((cid, first_text))

    dominant_text = ""
    dominant_n = 0
    dominant_ids: set[int] = set()
    if method_peers:
        ranked = sorted(
            method_peers.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
        top_key, top_pairs = ranked[0]
        top_n = len(top_pairs)
        tied = sum(1 for _key, pairs in ranked if len(pairs) == top_n)
        if tied == 1 and top_n >= min_n:
            dominant_text = top_pairs[0][1]
            dominant_n = top_n
            dominant_ids = {cid for cid, _text in top_pairs}
            if _peer_text_leaks_pii(dominant_text):  # Round 175.4
                dominant_text = ""
                dominant_n = 0
                dominant_ids = set()

    # Round 180: comparable-severity filter among method-peers.
    peer_bands: dict[int, str] = {}
    known_bands: set[str] = set()
    for cid in dominant_ids:
        band = _peer_barrier_severity_band(by_customer.get(cid) or [])
        peer_bands[cid] = band
        if band:
            known_bands.add(band)
    incomparable_severity = False
    comparable_band = ""
    comparable_ids: set[int] = set(dominant_ids)
    if "__mixed__" in known_bands or resolved_target == "__mixed__" or len(known_bands) >= 2:
        incomparable_severity = True
        comparable_ids = set()
        comparable_band = ""
    elif len(known_bands) == 1:
        only_band = next(iter(known_bands))
        if resolved_target and resolved_target != only_band:
            incomparable_severity = True
            comparable_ids = set()
            comparable_band = only_band
        else:
            comparable_band = only_band
            comparable_ids = {
                cid for cid, band in peer_bands.items() if band == only_band
            }

    placeholders = ",".join("?" * len(peer_ids))
    case_rows = cur.execute(
        f'SELECT "customer_id", "case_number", "is_open", '
        f'       "opened_at", "closed_at", "summary", "severity" '
        f'FROM "cases" WHERE "customer_id" IN ({placeholders});',  # Round 181
        tuple(peer_ids),
    ).fetchall()

    cases_by_customer: dict[int, list[sqlite3.Row]] = defaultdict(list)
    theme_key = safe_theme.casefold()
    for row in case_rows:
        if detect_theme(str(row["summary"] or "")).casefold() == theme_key:
            cases_by_customer[int(row["customer_id"])].append(row)

    # Round 181: comparable TAC-case severity among method-peers.
    # Intersect both gates: excluded barrier peers cannot support or poison
    # the case fallback. Barrier / pulse outcomes keep their own basis.
    peer_case_bands: dict[int, str] = {}
    known_case_bands: set[str] = set()
    for cid in comparable_ids:
        band = _peer_case_severity_band(cases_by_customer.get(cid) or [])
        peer_case_bands[cid] = band
        if band:
            known_case_bands.add(band)
    incomparable_case_severity = False
    comparable_case_band = ""
    comparable_case_ids: set[int] = set(comparable_ids)
    if "__mixed__" in known_case_bands or resolved_target_case == "__mixed__" or len(known_case_bands) >= 2:
        incomparable_case_severity = True
        comparable_case_ids = set()
        comparable_case_band = ""
    elif len(known_case_bands) == 1:
        only_band = next(iter(known_case_bands))
        if resolved_target_case and resolved_target_case != only_band:
            incomparable_case_severity = True
            comparable_case_ids = set()
            comparable_case_band = only_band
        else:
            comparable_case_band = only_band
            comparable_case_ids = {
                cid for cid, band in peer_case_bands.items() if band == only_band
            }

    closed_peer_count = 0
    open_peer_count = 0
    method_closed = 0
    method_open = 0
    method_barrier_closed = 0
    method_barrier_open = 0
    method_durations: list[float] = []
    case_pivot: dict[int, Optional[datetime]] = {}
    for cid in peer_ids:
        state, duration, pivot = _peer_case_outcome(cases_by_customer.get(cid) or [])
        case_pivot[cid] = pivot
        if state == "open":
            open_peer_count += 1
            if cid in comparable_ids and cid in comparable_case_ids:  # Round 180
                method_open += 1
        elif state == "closed":
            closed_peer_count += 1
            if cid in comparable_ids and cid in comparable_case_ids:  # Round 180
                method_closed += 1
                if duration is not None:
                    method_durations.append(duration)
        barrier_state = _peer_barrier_outcome(by_customer.get(cid) or [])
        if cid in comparable_ids:  # Round 180
            if barrier_state == "open":
                method_barrier_open += 1
            elif barrier_state == "closed":
                method_barrier_closed += 1

    sent_rows = cur.execute(
        f'SELECT "customer_id", "snapshot_date", "score" '
        f'FROM "sentiments" WHERE "customer_id" IN ({placeholders}) '
        f'ORDER BY "customer_id" ASC, COALESCE("snapshot_date", \'\') ASC, "id" ASC;',
        tuple(peer_ids),
    ).fetchall()
    points_by_customer: dict[int, list[tuple[Optional[datetime], float]]] = defaultdict(list)
    for row in sent_rows:
        if row["score"] is None:
            continue
        try:
            score = float(row["score"])
        except (TypeError, ValueError):
            continue
        points_by_customer[int(row["customer_id"])].append(
            (_peer_parse_timestamp(row["snapshot_date"]), score)
        )
    pulse_recovered = 0
    pulse_worsened = 0
    method_pulse_recovered = 0
    method_pulse_worsened = 0
    for cid, points in points_by_customer.items():
        # Round 175.3: method-peers drop pulse last-seen before the case event.
        pivot = case_pivot.get(cid) if cid in comparable_ids else None
        direction = _peer_pulse_direction(points, not_before=pivot)
        if direction == "recovered":
            pulse_recovered += 1
        elif direction == "worsened":
            pulse_worsened += 1
        if cid in comparable_ids:  # Round 180
            if direction == "recovered":
                method_pulse_recovered += 1
            elif direction == "worsened":
                method_pulse_worsened += 1

    # Round 175.3: strict case majority; mixed close+worse-pulse fails closed.
    # Round 176: barrier status is the work-item SSoT when known for ≥min_n peers.
    likely_next, likely_basis = _peer_likely_next_and_basis(
        dominant_n=dominant_n,
        min_n=min_n,
        method_closed=method_closed,
        method_open=method_open,
        method_pulse_recovered=method_pulse_recovered,
        method_pulse_worsened=method_pulse_worsened,
        method_barrier_closed=method_barrier_closed,
        method_barrier_open=method_barrier_open,
    )
    if likely_next not in _PEER_LIKELY_NEXT:
        likely_next = "insufficient"
        likely_basis = ""
    if incomparable_severity or (incomparable_case_severity and likely_basis == "case"):
        likely_next = "insufficient"
        likely_basis = ""
    if likely_next == "insufficient":
        likely_basis = ""

    evidence_sufficient = dominant_n >= min_n
    median_days: Optional[float] = None
    if _peer_durations_agree(method_durations):
        median_days = round(float(statistics.median(method_durations)), 1)
    target_path = _peer_target_path(target_recs, dominant_text)  # Round 179
    if target_path not in _PEER_TARGET_PATHS:
        target_path = "not_tried"
    next_step = _peer_next_step(likely_next, dominant_text, target_path=target_path)
    if incomparable_severity or (incomparable_case_severity and likely_next == "insufficient"):
        next_step = ""

    evidence = PeerGuidanceEvidence(
        theme=safe_theme,
        technology=safe_tech,
        peer_customer_count=len(peer_ids),
        resolved_peer_count=resolved_peer_count,
        dominant_method_text=dominant_text,
        dominant_method_peers=dominant_n,
        closed_peer_count=closed_peer_count,
        open_peer_count=open_peer_count,
        close_time_median_days=median_days,
        pulse_recovered_count=pulse_recovered,
        pulse_worsened_count=pulse_worsened,
        likely_next=likely_next,
        next_step=next_step,
        evidence_sufficient=bool(evidence_sufficient),
        method_closed_peer_count=method_closed,
        method_open_peer_count=method_open,
        method_pulse_recovered_count=method_pulse_recovered,
        method_pulse_worsened_count=method_pulse_worsened,
        exclusion_count=len(exclude_keys),
        exclusion_digest=exclusion_digest,
        method_barrier_closed_peer_count=method_barrier_closed,
        method_barrier_open_peer_count=method_barrier_open,
        likely_next_basis=likely_basis,  # Round 176
        target_path=target_path,  # Round 179
        severity_comparable=not incomparable_severity,
        comparable_severity_band=comparable_band,
        comparable_method_peer_count=len(comparable_ids),
        incomparable_severity=incomparable_severity,  # Round 180
        case_severity_comparable=not incomparable_case_severity,
        comparable_case_severity_band=comparable_case_band,
        comparable_case_method_peer_count=len(comparable_case_ids),
        incomparable_case_severity=incomparable_case_severity,  # Round 181
    )
    return _remember_peer_evidence(cache_key, evidence)


# ---------------------------------------------------------------------------
# Playbook search (BM25)
# ---------------------------------------------------------------------------


def search_playbook(
    query: object,
    *,
    technology: object = None,
    theme: object = None,
    top_k: int = 8,
) -> list[Chunk]:
    """BM25-rank narrative chunks against ``query``.  Returns up to
    ``top_k`` :class:`Chunk` records sorted by score desc with chunk
    id as the deterministic tiebreaker."""
    safe_query = _safe_identifier(query, label="query")
    query_tokens = _indexer_tokenize(safe_query)
    if not query_tokens:
        return []
    conn = _conn()
    cur = conn.cursor()
    stats_row = cur.execute(
        'SELECT "doc_count", "avg_doc_len" FROM "corpus_stats" WHERE "id" = 1'
    ).fetchone()
    if stats_row is None:
        return []
    doc_count = int(stats_row[0] or 0)
    avg_len = float(stats_row[1] or 0.0)
    if doc_count <= 0:
        return []

    where_parts: list[str] = []
    params: list[object] = []
    if technology is not None and isinstance(technology, str) and technology.strip():
        # Round 17 / Phase F.1 -- qualify ``technology`` and ``theme``
        # with the ``pc.`` (playbook_chunks) alias; the joined
        # ``customers`` table also exposes a ``technology`` column,
        # so an unqualified WHERE clause raises
        # ``sqlite3.OperationalError: ambiguous column name``.
        where_parts.append('LOWER(pc."technology") = LOWER(?)')
        params.append(_safe_identifier(technology, label="technology"))
    if theme is not None and isinstance(theme, str) and theme.strip():
        where_parts.append('LOWER(pc."theme") = LOWER(?)')
        params.append(_safe_identifier(theme, label="theme"))
    where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    rows = cur.execute(
        f'SELECT pc."id", pc."text", pc."tokens_json", pc."doc_length", '
        f'       pc."technology", pc."theme", '
        f'       cust."name" AS "customer_name", '
        f'       f."filename" AS "source_filename", '
        f'       pc."source_section" '
        f'FROM "playbook_chunks" pc '
        f'LEFT JOIN "customers" cust ON cust."id" = pc."customer_id" '
        f'LEFT JOIN "corpus_files" f ON f."id" = pc."source_file_id" '
        f'{where_clause}',
        tuple(params),
    ).fetchall()

    if not rows:
        return []

    df_lookup: dict[str, int] = {}
    for tok in set(query_tokens):
        r = cur.execute(
            'SELECT "df" FROM "term_stats" WHERE "term" = ?',
            (tok,),
        ).fetchone()
        df_lookup[tok] = int(r[0]) if r else 0

    scored: list[tuple[float, int, sqlite3.Row, list[str]]] = []
    for r in rows:
        try:
            tokens = json.loads(r["tokens_json"]) or []
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        score = bm25_score(
            query_tokens,
            tokens,
            chunk_length=int(r["doc_length"] or len(tokens)),
            avg_doc_length=avg_len,
            doc_count=doc_count,
            df_lookup=df_lookup,
            k1=BM25_K1,
            b=BM25_B,
        )
        if score <= 0.0:
            continue
        scored.append((score, int(r["id"]), r, tokens))

    if not scored:
        return []
    scored.sort(key=lambda t: (-t[0], t[1]))
    cap = max(1, min(int(top_k), _MAX_RESULTS))
    return [
        Chunk(
            text=str(row["text"] or ""),
            technology=(str(row["technology"]) if row["technology"] else None),
            theme=(str(row["theme"]) if row["theme"] else None),
            customer_name=(str(row["customer_name"]) if row["customer_name"] else None),
            score=float(score),
            source_filename=(str(row["source_filename"]) if row["source_filename"] else None),
            source_section=(str(row["source_section"]) if row["source_section"] else None),
            chunk_id=int(row["id"]),
        )
        for score, _row_id, row, _tokens in scored[:cap]
    ]


def _valid_dense_vector(vector: object, *, model_dim: int) -> bool:
    if vector is None or tuple(getattr(vector, "shape", ())) != (model_dim,):
        return False
    try:
        values = [float(value) for value in vector]  # type: ignore[union-attr]
        return all(math.isfinite(value) for value in values) and any(
            value != 0.0 for value in values
        )
    except (TypeError, ValueError, OverflowError):
        return False


def _load_persisted_candidate_vectors(
    candidates: Sequence[Chunk],
    *,
    model_id: str,
    model_dim: int,
) -> Optional[list[Sequence[float]]]:
    """Return a complete, validated vector set or ``None`` for live fallback."""
    if model_dim <= 0:
        return None
    chunk_ids = [candidate.chunk_id for candidate in candidates]
    if any(chunk_id is None for chunk_id in chunk_ids):
        return None
    resolved_ids = [int(chunk_id) for chunk_id in chunk_ids if chunk_id is not None]
    if len(set(resolved_ids)) != len(resolved_ids):
        return None

    placeholders = ", ".join("?" for _ in resolved_ids)
    try:
        rows = _conn().execute(
            'SELECT "chunk_id", "model_id", "model_dim", "vector" '
            f'FROM "chunk_vectors" WHERE "chunk_id" IN ({placeholders});',
            tuple(resolved_ids),
        ).fetchall()
    except sqlite3.DatabaseError:
        return None
    if len(rows) != len(resolved_ids):
        return None

    from ask_ai_embeddings import decode_vector

    by_chunk_id = {int(row["chunk_id"]): row for row in rows}
    vectors: list[Sequence[float]] = []
    for chunk_id in resolved_ids:
        row = by_chunk_id.get(chunk_id)
        if row is None or str(row["model_id"]) != model_id:
            return None
        try:
            persisted_dim = int(row["model_dim"])
        except (TypeError, ValueError):
            return None
        blob = row["vector"]
        if persisted_dim != model_dim or not isinstance(blob, (bytes, bytearray, memoryview)):
            return None
        raw = bytes(blob)
        if len(raw) != model_dim * 4:
            return None
        vector = decode_vector(raw, dim=model_dim)
        if not _valid_dense_vector(vector, model_dim=model_dim):
            return None
        vectors.append(vector)
    return vectors


def search_playbook_hybrid(
    query: object,
    *,
    technology: object = None,
    theme: object = None,
    top_k: int = 8,
) -> list[Chunk]:
    """Round 127 / Build 96 (A6): BM25 candidates + dense re-rank when hybrid is on."""
    prefetch_k = max(int(top_k) * 4, int(top_k))
    candidates = search_playbook(
        query,
        technology=technology,
        theme=theme,
        top_k=prefetch_k,
    )
    if not candidates:
        return []
    try:
        from config import Config

        if str(getattr(Config, "ASK_AI_RETRIEVAL_METHOD", "hybrid")).lower() == "lexical":
            return candidates[: int(top_k)]
    except Exception:
        return candidates[: int(top_k)]

    try:
        from ask_ai_embeddings import embed_query, embed_texts, get_embedder, rrf_fuse
        from config import Config as _Cfg

        embedder = get_embedder()
        if embedder is None:
            return candidates[: int(top_k)]
        model_id = str(
            getattr(_Cfg, "ASK_AI_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        )
        model_dim = int(getattr(_Cfg, "ASK_AI_EMBEDDING_DIM", 384) or 384)
        qvec = embed_query(str(query))
        if not _valid_dense_vector(qvec, model_dim=model_dim):
            return candidates[: int(top_k)]
        cvecs = _load_persisted_candidate_vectors(
            candidates,
            model_id=model_id,
            model_dim=model_dim,
        )
        if cvecs is None:
            texts = [c.text for c in candidates]
            live_cvecs = embed_texts(texts)
            if (
                live_cvecs is None
                or tuple(getattr(live_cvecs, "shape", ()))
                != (len(candidates), model_dim)
            ):
                return candidates[: int(top_k)]
            cvecs = list(live_cvecs)
            if any(
                not _valid_dense_vector(vector, model_dim=model_dim)
                for vector in cvecs
            ):
                return candidates[: int(top_k)]

        def _dot(a: Sequence[float], b: Sequence[float]) -> float:
            return float(sum(x * y for x, y in zip(a, b)))

        dense_order = sorted(
            range(len(candidates)),
            key=lambda i: -_dot(qvec, cvecs[i]),
        )
        bm25_order = list(range(len(candidates)))
        fused = rrf_fuse(
            [bm25_order, dense_order],
            k=int(getattr(_Cfg, "ASK_AI_RRF_K", 60) or 60),
        )
        by_index = {i: candidates[i] for i in range(len(candidates))}
        out: list[Chunk] = []
        for doc_id, _score in fused:
            if doc_id in by_index:
                out.append(by_index[doc_id])
            if len(out) >= int(top_k):
                break
        return out or candidates[: int(top_k)]
    except Exception:
        logger.debug("Round 127: search_playbook_hybrid dense fallback", exc_info=True)
        return candidates[: int(top_k)]


# ---------------------------------------------------------------------------
# Convenience: list every customer (for the Customer 360 picker)
# ---------------------------------------------------------------------------


def list_customers(prefix: object = None, *, limit: int = 50) -> list[str]:
    """Return up to ``limit`` customer names (canonical case).  When
    ``prefix`` is supplied, it is matched case-insensitively against
    ``name``.  Sorted alphabetically."""
    conn = _conn()
    cur = conn.cursor()
    if prefix is None or (isinstance(prefix, str) and not prefix.strip()):
        rows = cur.execute(
            'SELECT "name" FROM "customers" ORDER BY "name" ASC LIMIT ?;',
            (max(1, min(int(limit), _MAX_RESULTS)),),
        ).fetchall()
    else:
        safe_prefix = _safe_identifier(prefix, label="prefix")
        rows = cur.execute(
            'SELECT "name" FROM "customers" '
            'WHERE LOWER("name") LIKE LOWER(?) || \'%\' '
            'ORDER BY "name" ASC LIMIT ?;',
            (safe_prefix, max(1, min(int(limit), _MAX_RESULTS))),
        ).fetchall()
    return [str(r["name"] or "") for r in rows if r["name"]]


# ---------------------------------------------------------------------------
# Module-level utility
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    """Return True when a corpus connection has been wired in.  Does
    not actually query the database."""
    with _LOCK:
        return _CONN is not None


# ---------------------------------------------------------------------------
# Round 66 / Pass 5 - Hybrid retrieval re-exports
# ---------------------------------------------------------------------------
#
# embed_query / dense_score / hybrid_score live in ``ask_ai_embeddings``
# (single source of truth for the embedding lifecycle), but the plan
# asked for them to be addressable through corpus_retriever too because
# both the chunk-search path (search_playbook) and the evidence-record
# path (ask_ai_grounded.rank_evidence) consume them. Re-exporting keeps
# the import surface tidy without duplicating the implementation.
from ask_ai_embeddings import (  # noqa: E402 - intentional bottom-of-file import
    decode_vector,
    dense_score,
    embed_query,
    embed_texts,
    encode_vector,
    hybrid_score,
    rrf_fuse,
)


__all__ = [
    "BarrierRecord",
    "CaseRecord",
    "Chunk",
    "CorpusSnapshot",
    "CorpusUnavailable",
    "CustomerHistory",
    "ResolutionRecord",
    "SentimentSnapshot",
    "Theme",
    "PeerGuidanceEvidence",
    # Round 66 / Pass 5 hybrid retrieval (re-exported from ask_ai_embeddings)
    "decode_vector",
    "dense_score",
    "embed_query",
    "embed_texts",
    "encode_vector",
    "hybrid_score",
    "rrf_fuse",
    "configure_connection",
    "get_customer_history",
    "get_recurring_themes",
    "get_resolutions_for",
    "get_peer_guidance_evidence",
    "get_status",
    "is_configured",
    "list_customers",
    "search_playbook",
    "search_playbook_hybrid",
]
