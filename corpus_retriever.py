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
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from corpus_indexer import (
    BM25_B,
    BM25_K1,
    bm25_score,
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


@dataclass(frozen=True)
class Chunk:
    text: str
    technology: Optional[str]
    theme: Optional[str]
    customer_name: Optional[str]
    score: float
    source_filename: Optional[str] = None
    source_section: Optional[str] = None


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
        )
        for score, _row_id, row, _tokens in scored[:cap]
    ]


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
    "get_status",
    "is_configured",
    "list_customers",
    "search_playbook",
]
