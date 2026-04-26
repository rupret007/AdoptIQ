"""Round 17 / Phase B.1 -- SQLite DDL SSoT for the CSOne knowledge corpus.

The Round-17 corpus pipeline persists structured customer / case /
barrier / resolution / sentiment records derived from the daily CSOne
report corpus, plus narrative chunks indexed for lexical retrieval.
This module is the *only* place that defines the database shape.

Design contract
---------------
* Pure declaration: every public symbol is either a string DDL constant
  or a small helper that opens / migrates a connection.  No I/O against
  the user's filesystem at import time.
* Schema versioning: ``SCHEMA_VERSION`` is bumped whenever a column or
  table is added so the indexer can detect a stale store and rebuild.
* Idempotent migrations: ``apply_schema(conn)`` is safe to call against
  a fresh or already-migrated database.  Tables use ``CREATE TABLE IF
  NOT EXISTS``; indexes use ``CREATE INDEX IF NOT EXISTS``.
* All identifiers are quoted with double-quotes so the DDL is portable
  across SQLite versions and survives column names that happen to
  collide with SQL keywords.
* No customer PII is logged at INFO level by helpers in this module
  (per ``codeguard-0-logging`` and ``codeguard-0-privacy-data-protection``).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

#: Bump when adding columns / tables.  Stored in ``schema_meta``; the
#: indexer compares on startup and triggers a full rebuild if the
#: persisted value is older than the code expects.
SCHEMA_VERSION: int = 1


# ---------------------------------------------------------------------------
# DDL strings
# ---------------------------------------------------------------------------

#: ``schema_meta`` carries a single row keyed on ``id=1`` that records
#: the schema version the on-disk database was migrated to and the UTC
#: timestamp of the last successful migration.
_DDL_SCHEMA_META: str = """
CREATE TABLE IF NOT EXISTS "schema_meta" (
    "id"            INTEGER PRIMARY KEY CHECK ("id" = 1),
    "version"       INTEGER NOT NULL,
    "migrated_at"   TEXT    NOT NULL
);
"""

#: ``corpus_files`` tracks every file the indexer has consumed from
#: ``Config.CSONE_ONEDRIVE_FOLDER``.  ``sha256`` lets us detect content
#: drift even when the OS lies about ``mtime`` (notably during OneDrive
#: re-sync).  ``parsed_at`` is the UTC timestamp of the last successful
#: parse; rows for files that failed parsing are kept with
#: ``parse_status='error'`` so we don't endlessly retry the same broken
#: file on every refresh.
_DDL_CORPUS_FILES: str = """
CREATE TABLE IF NOT EXISTS "corpus_files" (
    "id"             INTEGER PRIMARY KEY AUTOINCREMENT,
    "path"           TEXT    NOT NULL UNIQUE,
    "filename"       TEXT    NOT NULL,
    "size_bytes"     INTEGER NOT NULL,
    "mtime_utc"      TEXT    NOT NULL,
    "sha256"         TEXT    NOT NULL,
    "schema_kind"    TEXT,
    "schema_version" INTEGER NOT NULL,
    "parsed_at"      TEXT,
    "parse_status"   TEXT    NOT NULL DEFAULT 'pending',
    "parse_error"    TEXT
);
"""

#: ``customers`` is the canonical customer dimension.  ``name_norm`` is
#: the lowercased / whitespace-collapsed key used by retrievers so the
#: same customer joined from two different files dedupes correctly.
_DDL_CUSTOMERS: str = """
CREATE TABLE IF NOT EXISTS "customers" (
    "id"            INTEGER PRIMARY KEY AUTOINCREMENT,
    "name"          TEXT    NOT NULL,
    "name_norm"     TEXT    NOT NULL UNIQUE,
    "manager"       TEXT,
    "technology"    TEXT,
    "first_seen"    TEXT,
    "last_seen"     TEXT,
    "occurrences"   INTEGER NOT NULL DEFAULT 0
);
"""

#: ``cases`` is one row per CSOne case observed in the corpus.
#: ``case_number`` is the SR / case number, ``severity`` and
#: ``status`` use the canonical labels emitted by
#: ``data_normalization`` so corpus values are comparable to fresh-
#: report values.
_DDL_CASES: str = """
CREATE TABLE IF NOT EXISTS "cases" (
    "id"            INTEGER PRIMARY KEY AUTOINCREMENT,
    "customer_id"   INTEGER NOT NULL,
    "case_number"   TEXT,
    "severity"      TEXT,
    "status"        TEXT,
    "is_open"       INTEGER NOT NULL DEFAULT 0,
    "opened_at"     TEXT,
    "closed_at"     TEXT,
    "summary"       TEXT,
    "source_file_id" INTEGER,
    "first_seen"    TEXT,
    "last_seen"     TEXT,
    FOREIGN KEY ("customer_id")    REFERENCES "customers" ("id"),
    FOREIGN KEY ("source_file_id") REFERENCES "corpus_files" ("id")
);
"""

#: ``barriers`` aggregates adoption barriers seen for a customer in a
#: given technology and theme.  Themes are short, deduped tags ("auth
#: errors", "license sync", etc.) extracted by the indexer from the
#: barrier subject / description.
_DDL_BARRIERS: str = """
CREATE TABLE IF NOT EXISTS "barriers" (
    "id"            INTEGER PRIMARY KEY AUTOINCREMENT,
    "customer_id"   INTEGER NOT NULL,
    "technology"    TEXT,
    "theme"         TEXT NOT NULL,
    "first_seen"    TEXT,
    "last_seen"     TEXT,
    "occurrences"   INTEGER NOT NULL DEFAULT 1,
    "source_file_id" INTEGER,
    FOREIGN KEY ("customer_id")    REFERENCES "customers" ("id"),
    FOREIGN KEY ("source_file_id") REFERENCES "corpus_files" ("id")
);
"""

#: ``resolutions`` records observed methods used to resolve a
#: ``barriers`` row.  Free-text method body is bounded to 4 KB to keep
#: query plans predictable.  ``source_section`` records where in the
#: file the chunk came from (e.g. ``"Resolution Summary"``).
_DDL_RESOLUTIONS: str = """
CREATE TABLE IF NOT EXISTS "resolutions" (
    "id"            INTEGER PRIMARY KEY AUTOINCREMENT,
    "barrier_id"    INTEGER NOT NULL,
    "method_text"   TEXT    NOT NULL,
    "source_section" TEXT,
    "source_file_id" INTEGER,
    "first_seen"    TEXT,
    FOREIGN KEY ("barrier_id")     REFERENCES "barriers" ("id"),
    FOREIGN KEY ("source_file_id") REFERENCES "corpus_files" ("id")
);
"""

#: ``sentiments`` is one row per (customer, snapshot_date) point in
#: time.  ``score`` is the canonical pulse colour mapped to a numeric
#: -1.0/0.0/+1.0 scale; ``evidence_text`` is a short rationale.
_DDL_SENTIMENTS: str = """
CREATE TABLE IF NOT EXISTS "sentiments" (
    "id"            INTEGER PRIMARY KEY AUTOINCREMENT,
    "customer_id"   INTEGER NOT NULL,
    "snapshot_date" TEXT,
    "score"         REAL,
    "evidence_text" TEXT,
    "source_file_id" INTEGER,
    FOREIGN KEY ("customer_id")    REFERENCES "customers" ("id"),
    FOREIGN KEY ("source_file_id") REFERENCES "corpus_files" ("id")
);
"""

#: ``playbook_chunks`` carries the narrative spans the BM25 retriever
#: searches.  Tokens are pre-computed at index time so retrieval is a
#: pure SQL join.
_DDL_PLAYBOOK_CHUNKS: str = """
CREATE TABLE IF NOT EXISTS "playbook_chunks" (
    "id"            INTEGER PRIMARY KEY AUTOINCREMENT,
    "technology"    TEXT,
    "theme"         TEXT,
    "customer_id"   INTEGER,
    "text"          TEXT    NOT NULL,
    "tokens_json"   TEXT    NOT NULL,
    "doc_length"    INTEGER NOT NULL,
    "source_file_id" INTEGER,
    "source_section" TEXT,
    FOREIGN KEY ("customer_id")    REFERENCES "customers" ("id"),
    FOREIGN KEY ("source_file_id") REFERENCES "corpus_files" ("id")
);
"""

#: ``term_stats`` is the inverted-frequency map for the in-house BM25.
#: ``df`` is document frequency (number of chunks the term appears in)
#: which the retriever uses for the IDF half of BM25.
_DDL_TERM_STATS: str = """
CREATE TABLE IF NOT EXISTS "term_stats" (
    "term"          TEXT    PRIMARY KEY,
    "df"            INTEGER NOT NULL
);
"""

#: ``corpus_stats`` carries the small set of corpus-wide aggregates
#: BM25 needs (total chunks, average length).  One row keyed on
#: ``id=1``.
_DDL_CORPUS_STATS: str = """
CREATE TABLE IF NOT EXISTS "corpus_stats" (
    "id"            INTEGER PRIMARY KEY CHECK ("id" = 1),
    "doc_count"     INTEGER NOT NULL DEFAULT 0,
    "avg_doc_len"   REAL    NOT NULL DEFAULT 0,
    "indexed_at"    TEXT
);
"""


#: All DDL fragments in dependency order.  The schema_meta table is
#: created last so a partial migration leaves the version row absent
#: and the next start-up rebuilds.
_DDL_TABLES: tuple[str, ...] = (
    _DDL_CORPUS_FILES,
    _DDL_CUSTOMERS,
    _DDL_CASES,
    _DDL_BARRIERS,
    _DDL_RESOLUTIONS,
    _DDL_SENTIMENTS,
    _DDL_PLAYBOOK_CHUNKS,
    _DDL_TERM_STATS,
    _DDL_CORPUS_STATS,
    _DDL_SCHEMA_META,
)


#: Index DDL.  Names are explicit so the migration is idempotent and
#: rollbacks (rare) drop a known-named index.
_DDL_INDEXES: tuple[str, ...] = (
    'CREATE INDEX IF NOT EXISTS "idx_cases_customer"      ON "cases" ("customer_id");',
    'CREATE INDEX IF NOT EXISTS "idx_cases_status"        ON "cases" ("is_open", "severity");',
    'CREATE INDEX IF NOT EXISTS "idx_barriers_customer"   ON "barriers" ("customer_id");',
    'CREATE INDEX IF NOT EXISTS "idx_barriers_technology" ON "barriers" ("technology", "theme");',
    'CREATE INDEX IF NOT EXISTS "idx_resolutions_barrier" ON "resolutions" ("barrier_id");',
    'CREATE INDEX IF NOT EXISTS "idx_sentiments_customer" ON "sentiments" ("customer_id", "snapshot_date");',
    'CREATE INDEX IF NOT EXISTS "idx_chunks_technology"   ON "playbook_chunks" ("technology", "theme");',
    'CREATE INDEX IF NOT EXISTS "idx_chunks_customer"     ON "playbook_chunks" ("customer_id");',
)


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------


def apply_schema(conn: sqlite3.Connection) -> int:
    """Apply the SSoT schema to ``conn``.

    Idempotent: safe to call against a fresh database, an already-
    migrated database, or a database that was partially migrated.
    Returns the schema version that is now persisted.
    """
    if conn is None:
        raise ValueError("apply_schema requires an open sqlite3.Connection")

    # PRAGMA foreign_keys is per-connection; turn it on so the FK
    # references in our DDL actually constrain inserts.
    conn.execute('PRAGMA foreign_keys = ON;')
    # WAL journal mode keeps concurrent readers fast and is the
    # AdoptIQ default for non-volatile sqlite stores (see
    # incident_storage.py).
    try:
        conn.execute('PRAGMA journal_mode = WAL;')
    except sqlite3.DatabaseError as wal_err:  # pragma: no cover - exotic FS
        logger.debug("journal_mode=WAL failed (%s); continuing", wal_err)

    cur = conn.cursor()
    for ddl in _DDL_TABLES:
        cur.execute(ddl)
    for ddl in _DDL_INDEXES:
        cur.execute(ddl)

    cur.execute(
        'INSERT OR REPLACE INTO "schema_meta" ("id", "version", "migrated_at") '
        'VALUES (1, ?, datetime("now"));',
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return SCHEMA_VERSION


def get_persisted_schema_version(conn: sqlite3.Connection) -> int | None:
    """Return the schema version recorded on ``conn``, or ``None`` if
    the database has never been migrated.

    Defensive: returns ``None`` rather than raising on any error so the
    indexer can treat "schema_meta missing" and "schema_meta query
    failed" identically (both trigger a full rebuild).
    """
    if conn is None:
        return None
    try:
        row = conn.execute(
            'SELECT "version" FROM "schema_meta" WHERE "id" = 1;'
        ).fetchone()
    except sqlite3.DatabaseError:
        return None
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def needs_rebuild(conn: sqlite3.Connection) -> bool:
    """Return True when the on-disk schema is older than
    ``SCHEMA_VERSION``.  Used by the indexer to decide whether to drop
    and recreate the corpus tables before a refresh.
    """
    persisted = get_persisted_schema_version(conn)
    if persisted is None:
        return True
    return persisted < SCHEMA_VERSION


def all_table_names() -> Iterable[str]:
    """Yield every table name declared by this SSoT.

    Used by ``corpus_indexer`` when rebuilding from scratch (drops
    every table before re-applying the schema).  Order is not
    significant -- ``DROP TABLE IF EXISTS`` makes the operation
    idempotent.
    """
    yield "schema_meta"
    yield "corpus_files"
    yield "customers"
    yield "cases"
    yield "barriers"
    yield "resolutions"
    yield "sentiments"
    yield "playbook_chunks"
    yield "term_stats"
    yield "corpus_stats"


__all__ = [
    "SCHEMA_VERSION",
    "apply_schema",
    "get_persisted_schema_version",
    "needs_rebuild",
    "all_table_names",
]
