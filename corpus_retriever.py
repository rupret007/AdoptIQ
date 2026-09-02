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

import json
import logging
import math
import re
import sqlite3
import statistics
import threading
import unicodedata
from collections import defaultdict
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
        '       MAX("last_seen") AS "last_seen" '
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
_PEER_LIKELY_NEXT = frozenset(
    {
        "closure",
        "remains_open",
        "pulse_worsening",
        "pulse_recovery",  # Round 175.2: method-scoped recovered pulse
        "insufficient",
    }
)


def _method_match_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _peer_case_identity(value: object) -> str:
    """Collapse case-number snapshots the same way methods are keyed."""
    return _method_match_key(value)


def _peer_next_step(likely_next: str, method_text: str) -> str:  # Round 175
    if not str(method_text or "").strip():
        return ""
    if likely_next == "closure":
        return "try the same method on the current open work"
    if likely_next == "remains_open":
        return "treat the current path as still unresolved"
    if likely_next == "pulse_worsening":
        return "revisit pulse before adding new work"
    if likely_next == "pulse_recovery":  # Round 175.2
        return "keep the current method and watch pulse"
    return ""


def _peer_case_outcome(  # Round 175
    recs: Sequence[sqlite3.Row],
) -> tuple[str, Optional[float]]:
    """Collapse one peer's theme-matched cases to open, closed, or none.

    Snapshot disagreements on the same case identity fail closed for that
    identity (no guessed duration). An open observation on any identity
    classifies the peer as still open.
    """
    if not recs:
        return "none", None

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

    peer_open = False
    peer_closed = False
    durations: list[float] = []
    for observations in list(keyed.values()) + [[item] for item in unkeyed]:
        windows = [_closed_window(item) for item in observations]
        if any(bool(item["is_open"]) for item in observations):
            peer_open = True
            continue
        agreed = {window for window in windows if window is not None}
        if len(agreed) == 1 and not any(window is None for window in windows):
            opened, closed = next(iter(agreed))
            durations.append((closed - opened).total_seconds() / 86_400.0)
            peer_closed = True
    if peer_open:
        return "open", None
    if peer_closed:
        # Round 175.2: per-peer median, not last-closed-case wins.
        median = round(float(statistics.median(durations)), 1) if durations else None
        return "closed", median
    return "none", None


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


def get_peer_guidance_evidence(
    theme: object,
    technology: object,
    *,
    exclude_customer: object,
    min_peers: int = _MIN_PEER_CUSTOMERS,
) -> PeerGuidanceEvidence:
    """Return aggregate peer-trajectory evidence for ``theme`` + ``technology``.

    Round 175 knowledge layer: other corpus customers who already lived
    this theme/technology path.  Snapshot rows are collapsed per
    customer before counting (same identity rule as Round 174 case
    dedupe).  A peer whose own snapshots disagree on method is skipped
    for the dominant-method tally rather than guessed.  likely-next is
    derived only from peers who share the dominant method (method
    coupled to case/pulse trajectory).  The payload is counts-only —
    no peer names, emails, or case numbers.
    """

    try:
        safe_theme = _safe_identifier(theme, label="theme")
        safe_tech = _safe_identifier(technology, label="technology")
        safe_exclude = _safe_identifier(exclude_customer, label="customer name")
        conn = _conn()
    except CorpusUnavailable:
        return _empty_peer_guidance(str(theme or ""), str(technology or ""))
    exclude_norm = _normalize_for_lookup(safe_exclude)
    try:
        min_n = max(_MIN_PEER_CUSTOMERS, min(int(min_peers), 20))
    except (TypeError, ValueError):
        min_n = _MIN_PEER_CUSTOMERS

    cur = conn.cursor()
    rows = cur.execute(
        'SELECT cust."id" AS "customer_id", '
        '       res."method_text" AS "method_text", '
        '       res."first_seen" AS "first_seen", '
        '       res."id" AS "resolution_id" '
        'FROM "barriers" b '
        'JOIN "customers" cust ON cust."id" = b."customer_id" '
        'LEFT JOIN "resolutions" res ON res."barrier_id" = b."id" '
        'WHERE LOWER(b."theme") = LOWER(?) '
        '  AND LOWER(b."technology") = LOWER(?) '
        '  AND cust."name_norm" != ? '
        'ORDER BY cust."id" ASC, COALESCE(res."first_seen", \'\') DESC, '
        '         COALESCE(res."id", 0) DESC;',
        (safe_theme, safe_tech, exclude_norm),
    ).fetchall()

    by_customer: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_customer[int(row["customer_id"])].append(row)
    peer_ids = list(by_customer)
    if not peer_ids:
        return _empty_peer_guidance(safe_theme, safe_tech)

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

    placeholders = ",".join("?" * len(peer_ids))
    case_rows = cur.execute(
        f'SELECT "customer_id", "case_number", "is_open", '
        f'       "opened_at", "closed_at", "summary" '
        f'FROM "cases" WHERE "customer_id" IN ({placeholders});',
        tuple(peer_ids),
    ).fetchall()

    cases_by_customer: dict[int, list[sqlite3.Row]] = defaultdict(list)
    theme_key = safe_theme.casefold()
    for row in case_rows:
        if detect_theme(str(row["summary"] or "")).casefold() == theme_key:
            cases_by_customer[int(row["customer_id"])].append(row)

    closed_peer_count = 0
    open_peer_count = 0
    method_closed = 0
    method_open = 0
    method_durations: list[float] = []
    for cid in peer_ids:
        state, duration = _peer_case_outcome(cases_by_customer.get(cid) or [])
        if state == "open":
            open_peer_count += 1
            if cid in dominant_ids:
                method_open += 1
        elif state == "closed":
            closed_peer_count += 1
            if cid in dominant_ids:
                method_closed += 1
                if duration is not None:
                    method_durations.append(duration)

    sent_rows = cur.execute(
        f'SELECT "customer_id", "snapshot_date", "score" '
        f'FROM "sentiments" WHERE "customer_id" IN ({placeholders}) '
        f'ORDER BY "customer_id" ASC, COALESCE("snapshot_date", \'\') ASC, "id" ASC;',
        tuple(peer_ids),
    ).fetchall()
    scores_by_customer: dict[int, list[float]] = defaultdict(list)
    for row in sent_rows:
        if row["score"] is None:
            continue
        try:
            scores_by_customer[int(row["customer_id"])].append(float(row["score"]))
        except (TypeError, ValueError):
            continue
    pulse_recovered = 0
    pulse_worsened = 0
    method_pulse_recovered = 0
    method_pulse_worsened = 0
    for cid, scores in scores_by_customer.items():
        if len(scores) < 2:
            continue
        delta = scores[-1] - scores[0]
        direction = ""
        if delta > 0.05:
            direction = "recovered"
            pulse_recovered += 1
        elif delta < -0.05:
            direction = "worsened"
            pulse_worsened += 1
        if cid in dominant_ids:
            if direction == "recovered":
                method_pulse_recovered += 1
            elif direction == "worsened":
                method_pulse_worsened += 1

    # Round 175: likely-next is method-scoped. Uncoupled closures from
    # peers who did not share the dominant method must not become a
    # "closed after {method}" claim.
    likely_next = "insufficient"
    if dominant_n >= min_n:
        if method_closed >= min_n and method_closed >= method_open:
            likely_next = "closure"
        elif method_open >= min_n and method_open > method_closed:
            likely_next = "remains_open"
        elif (
            method_pulse_worsened >= min_n
            and method_pulse_worsened > method_pulse_recovered
        ):
            likely_next = "pulse_worsening"
        elif (
            method_pulse_recovered >= min_n
            and method_pulse_recovered > method_pulse_worsened
        ):
            # Round 175.2: recovery is a trajectory only when cases do
            # not already decide closure vs remains-open.
            likely_next = "pulse_recovery"
    if likely_next not in _PEER_LIKELY_NEXT:
        likely_next = "insufficient"

    evidence_sufficient = dominant_n >= min_n
    median_days: Optional[float] = None
    if method_durations:
        median_days = round(float(statistics.median(method_durations)), 1)
    next_step = _peer_next_step(likely_next, dominant_text)

    return PeerGuidanceEvidence(
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
    )


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
