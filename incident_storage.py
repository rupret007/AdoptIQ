"""
Persistent storage for external intelligence data (incidents and bugs).
Uses SQLite so history survives between report runs and app restarts.
"""

import sqlite3
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager

logger = logging.getLogger(__name__)

def _db_path() -> str:
    """Resolve the DB path using the same Application Support directory as the main app."""
    frozen = getattr(sys, 'frozen', False)
    if frozen:
        if sys.platform == 'darwin':
            base = Path.home() / 'Library' / 'Application Support' / 'AdoptIQ'
        elif sys.platform == 'win32':
            base = Path(os.environ.get('APPDATA', str(Path.home()))) / 'AdoptIQ'
        else:
            base = Path.home() / '.adoptiq'
    else:
        base = Path(__file__).resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    return str(base / 'external_intelligence.db')


@contextmanager
def _connect():
    """Yield a DB connection with WAL mode and auto-commit."""
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist. Recreates DB if corrupted."""
    try:
        return _init_db_inner()
    except sqlite3.DatabaseError as e:
        logger.warning(f"Database appears corrupted, recreating: {e}")
        db_file = _db_path()
        try:
            os.remove(db_file)
        except OSError:
            pass
        return _init_db_inner()

def _init_db_inner():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id          TEXT PRIMARY KEY,
                title       TEXT,
                link        TEXT,
                published   TEXT,
                status      TEXT,
                impact_level TEXT,
                source      TEXT,
                description TEXT DEFAULT '',
                first_seen  TEXT NOT NULL,
                last_seen   TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bugs (
                bug_id       TEXT PRIMARY KEY,
                source_url   TEXT,
                title        TEXT,
                source       TEXT,
                discovered_at TEXT,
                first_seen   TEXT NOT NULL,
                last_seen    TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS maintenances (
                id          TEXT PRIMARY KEY,
                title       TEXT,
                link        TEXT,
                published   TEXT,
                status      TEXT,
                source      TEXT,
                description TEXT DEFAULT '',
                first_seen  TEXT NOT NULL,
                last_seen   TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_published ON incidents(published)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_last_seen ON incidents(last_seen)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bugs_last_seen ON bugs(last_seen)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maint_published ON maintenances(published)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maint_last_seen ON maintenances(last_seen)")


# Round 7 / Phase 3.12: removed the module-level ``init_db()`` side
# effect.  Importing this module previously created / migrated the
# SQLite database as a side effect, which made unit tests, dry-run
# tooling, and CI lint passes accidentally touch disk.  The
# application bootstrap (e.g. ``app_simple.py`` / ``adoptiq_backend.py``
# startup) is now responsible for explicitly calling ``init_db()``
# once at process start.  Callers that need lazy initialisation can
# wrap it in their own helper:
def ensure_initialised() -> None:
    """Idempotent helper for callers that want lazy DB initialisation.

    Round 7 / Phase 3.12: explicit replacement for the previous
    module-level ``init_db()`` call.  Application bootstrap should
    call ``init_db()`` directly; library callers that import the
    module without owning the lifecycle can call this helper.
    """
    init_db()


# Round 5 / Phase 6.11: incident / bug / maintenance rows historically used
# ``datetime.utcnow().strftime(...)`` (a *naive* UTC timestamp).  The UI
# clock and the rest of the pipeline are tz-aware UTC, so windowing
# queries that compared a naive cutoff to records written with the same
# naive function happened to work, but any code that mixed tz-aware UTC
# stamps (``datetime.now(timezone.utc)``) ended up off by the local
# offset, silently dropping fresh incidents from the ``last_seen >=
# cutoff`` filter.  Centralize on tz-aware UTC for both writes and
# windowing so the storage agrees with the UI clock everywhere.
_UTC_FMT = '%Y-%m-%d %H:%M:%S'


def _utc_now_str() -> str:
    """Return current UTC time as a ``'%Y-%m-%d %H:%M:%S'`` string.

    Wrapper around ``datetime.now(timezone.utc).strftime(...)``; using
    ``timezone.utc`` makes the resulting timestamp explicit about its
    zone and matches the format used by the UI status snapshot
    (``record_report_completion`` writes ``...Z`` for the same reason).
    The string drops the offset because every consumer of this column
    already treats it as UTC.
    """
    return datetime.now(timezone.utc).strftime(_UTC_FMT)


def _utc_cutoff_str(days_back: int) -> str:
    """Return ``now - days_back`` as a UTC ``'%Y-%m-%d %H:%M:%S'`` string."""
    return (datetime.now(timezone.utc) - timedelta(days=int(days_back))).strftime(_UTC_FMT)


# ---------------------------------------------------------------------------
# Incident storage (API expected by adoptiq_backend.fetch_status_incidents)
# ---------------------------------------------------------------------------

def store_historical_incidents(incidents: List[Dict]) -> int:
    """Upsert incidents; return count of rows inserted or updated.

    Round 9 / Phase 6.5: when a feed pushes a few hundred badly-shaped
    rows in a single refresh (typical when a Cisco RSS schema change
    rolls out), the per-row ``logger.warning`` previously emitted one
    log line per row -- flooding centralised logging and burying every
    other diagnostic that happened during the same refresh.  Aggregate
    failures into a single warning at the end with the count + the
    first error sample, so operators still see the failure mode but
    log volume stays bounded.
    """
    if not incidents:
        return 0
    now = _utc_now_str()
    stored = 0
    error_count = 0
    first_error_sample: Optional[str] = None
    with _connect() as conn:
        for inc in incidents:
            inc_id = inc.get('id') or ''
            if not inc_id:
                continue
            try:
                conn.execute("""
                    INSERT INTO incidents (id, title, link, published, status,
                                           impact_level, source, description, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title       = excluded.title,
                        link        = excluded.link,
                        published   = excluded.published,
                        status      = excluded.status,
                        impact_level = excluded.impact_level,
                        source      = excluded.source,
                        description = excluded.description,
                        last_seen   = excluded.last_seen
                """, (
                    inc_id,
                    inc.get('title') or '',
                    inc.get('link') or '',
                    inc.get('published') or '',
                    inc.get('status') or '',
                    inc.get('impact_level') or '',
                    inc.get('source') or '',
                    inc.get('description') or '',
                    now,
                    now,
                ))
                stored += 1
            except Exception as e:
                error_count += 1
                if first_error_sample is None:
                    first_error_sample = f"id={inc_id!r}: {type(e).__name__}: {e}"
                logger.debug(f"Error storing incident {inc_id}: {e}")
    if error_count:
        logger.warning(
            "Failed to store %d incident row(s); first error: %s",
            error_count, first_error_sample or '?',
        )
    logger.info(f"Stored {stored} incidents in external_intelligence.db")
    return stored


def get_historical_incidents(days_back: int = 90, limit: int = 50) -> List[Dict]:
    """Return incidents seen within the last *days_back* days, newest first.

    Round 10 / Phase 5.5: filter by the canonical ``published`` time
    rather than ``last_seen``. ``last_seen`` is the time AdoptIQ last
    *observed* the row in the upstream feed, which can lag the actual
    incident publication by hours/days when the scheduler is throttled
    -- and conversely an incident published a year ago that was
    re-fetched today would appear as "recent" to a 90-day window.
    Filtering on ``published`` gives the renewal/leader narratives a
    deterministic incident window keyed to incident occurrence, with a
    fallback to ``last_seen`` for legacy rows that were stored before
    ``published`` was reliably populated.
    """
    cutoff = _utc_cutoff_str(days_back)
    with _connect() as conn:
        rows = conn.execute("""
            SELECT * FROM incidents
            WHERE COALESCE(NULLIF(published, ''), last_seen) >= ?
            ORDER BY published DESC, last_seen DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()
    return [dict(r) for r in rows]


def get_incident_statistics(days_back: Optional[int] = None) -> Dict:
    """Aggregate statistics for the stored incidents.

    When ``days_back`` is provided, every count is restricted to incidents
    whose ``last_seen`` is within the same window as the rows surfaced by
    ``get_historical_incidents`` so the dashboard headline matches the
    table beneath it (no more "Total: 421" above a list that only shows
    the last 90 days).
    """
    # Round 10 / Phase 5.5: align the stats window with
    # ``get_historical_incidents`` which now filters on the canonical
    # ``published`` time (with ``last_seen`` fallback). Otherwise
    # ``Total: 421`` (counted by ``last_seen``) drifted away from the
    # listed 90-day rows (counted by ``published``).
    where_clause = ""
    params: tuple = ()
    if days_back is not None and int(days_back) > 0:
        cutoff = _utc_cutoff_str(int(days_back))
        where_clause = "WHERE COALESCE(NULLIF(published, ''), last_seen) >= ?"
        params = (cutoff,)
    with _connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM incidents {where_clause}", params
        ).fetchone()[0]
        active = conn.execute(
            f"SELECT COUNT(*) FROM incidents {where_clause}{' AND' if where_clause else ' WHERE'} status = 'active'",
            params,
        ).fetchone()[0]
        resolved = conn.execute(
            f"SELECT COUNT(*) FROM incidents {where_clause}{' AND' if where_clause else ' WHERE'} status = 'resolved'",
            params,
        ).fetchone()[0]
        sources_rows = conn.execute(
            f"SELECT DISTINCT source FROM incidents {where_clause}", params
        ).fetchall()
        sources = [r[0] for r in sources_rows if r[0]]
        oldest = conn.execute(
            f"SELECT MIN(first_seen) FROM incidents {where_clause}", params
        ).fetchone()[0]
        # Round 12 / Phase 2.3: list windows (e.g. ``get_recent_incidents``)
        # already use ``COALESCE(NULLIF(published,''), last_seen)`` to
        # decide an incident's effective "as-of" time, but the
        # statistics headline used the raw ``last_seen`` column.
        # That lets a "resolved 2 days ago, originally published 30
        # days ago" item show as "newest=2 days ago" in the headline
        # while the list view honours its publish date.  Match the
        # list-window semantics so the header KPI and the rows agree.
        newest = conn.execute(
            f"SELECT MAX(COALESCE(NULLIF(published, ''), last_seen)) FROM incidents {where_clause}",
            params,
        ).fetchone()[0]
    return {
        'total': total,
        'active': active,
        'resolved': resolved,
        'sources': sources,
        'oldest': oldest or '',
        'newest': newest or '',
        'days_back': int(days_back) if days_back is not None and int(days_back) > 0 else None,
    }


# ---------------------------------------------------------------------------
# Bug storage
# ---------------------------------------------------------------------------

def store_historical_bugs(bugs: List[Dict]) -> int:
    """Upsert bugs; return count of rows inserted or updated.

    Round 9 / Phase 6.5: aggregate per-row failures (count + first
    error sample) instead of one warning per row.
    """
    if not bugs:
        return 0
    now = _utc_now_str()
    stored = 0
    error_count = 0
    first_error_sample: Optional[str] = None
    with _connect() as conn:
        for bug in bugs:
            bug_id = bug.get('bug_id') or ''
            if not bug_id:
                continue
            try:
                conn.execute("""
                    INSERT INTO bugs (bug_id, source_url, title, source, discovered_at,
                                      first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(bug_id) DO UPDATE SET
                        source_url    = excluded.source_url,
                        title         = excluded.title,
                        source        = excluded.source,
                        discovered_at = excluded.discovered_at,
                        last_seen     = excluded.last_seen
                """, (
                    bug_id,
                    bug.get('source_url') or '',
                    bug.get('title') or '',
                    bug.get('source') or '',
                    bug.get('discovered_at') or '',
                    now,
                    now,
                ))
                stored += 1
            except Exception as e:
                error_count += 1
                if first_error_sample is None:
                    first_error_sample = f"bug_id={bug_id!r}: {type(e).__name__}: {e}"
                logger.debug(f"Error storing bug {bug_id}: {e}")
    if error_count:
        logger.warning(
            "Failed to store %d bug row(s); first error: %s",
            error_count, first_error_sample or '?',
        )
    logger.info(f"Stored {stored} bugs in external_intelligence.db")
    return stored


def get_historical_bugs(days_back: int = 90, limit: int = 50) -> List[Dict]:
    """Return bugs seen within the last *days_back* days, newest first.

    Round 10 / Phase 5.5: filter by the canonical ``discovered_at`` time
    rather than ``last_seen`` for the same reason as
    ``get_historical_incidents`` (see that function for rationale).
    """
    cutoff = _utc_cutoff_str(days_back)
    with _connect() as conn:
        rows = conn.execute("""
            SELECT * FROM bugs
            WHERE COALESCE(NULLIF(discovered_at, ''), last_seen) >= ?
            ORDER BY discovered_at DESC, last_seen DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()
    return [dict(r) for r in rows]


def get_bug_statistics(days_back: Optional[int] = None) -> Dict:
    """Aggregate statistics for the stored bugs.

    See :func:`get_incident_statistics` for the rationale behind the
    optional ``days_back`` window.
    """
    # Round 10 / Phase 5.5: align with get_historical_bugs (canonical
    # discovered_at, fallback to last_seen).
    where_clause = ""
    params: tuple = ()
    if days_back is not None and int(days_back) > 0:
        cutoff = _utc_cutoff_str(int(days_back))
        where_clause = "WHERE COALESCE(NULLIF(discovered_at, ''), last_seen) >= ?"
        params = (cutoff,)
    with _connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM bugs {where_clause}", params
        ).fetchone()[0]
        sources_rows = conn.execute(
            f"SELECT DISTINCT source FROM bugs {where_clause}", params
        ).fetchall()
        sources = [r[0] for r in sources_rows if r[0]]
        oldest = conn.execute(
            f"SELECT MIN(first_seen) FROM bugs {where_clause}", params
        ).fetchone()[0]
        newest = conn.execute(
            f"SELECT MAX(last_seen) FROM bugs {where_clause}", params
        ).fetchone()[0]
    return {
        'total': total,
        'sources': sources,
        'oldest': oldest or '',
        'newest': newest or '',
        'days_back': int(days_back) if days_back is not None and int(days_back) > 0 else None,
    }


# ---------------------------------------------------------------------------
# Maintenance storage
# ---------------------------------------------------------------------------

def store_historical_maintenances(maintenances: List[Dict]) -> int:
    """Upsert maintenances; return count of rows inserted or updated.

    Round 9 / Phase 6.5: aggregate per-row failures (count + first
    error sample) instead of one warning per row.
    """
    if not maintenances:
        return 0
    now = _utc_now_str()
    stored = 0
    error_count = 0
    first_error_sample: Optional[str] = None
    with _connect() as conn:
        for maint in maintenances:
            maint_id = maint.get('id') or ''
            if not maint_id:
                continue
            try:
                conn.execute("""
                    INSERT INTO maintenances (id, title, link, published, status,
                                              source, description, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title       = excluded.title,
                        link        = excluded.link,
                        published   = excluded.published,
                        status      = excluded.status,
                        source      = excluded.source,
                        description = excluded.description,
                        last_seen   = excluded.last_seen
                """, (
                    maint_id,
                    maint.get('title') or '',
                    maint.get('link') or '',
                    maint.get('published') or '',
                    maint.get('status') or '',
                    maint.get('source') or '',
                    maint.get('description') or '',
                    now,
                    now,
                ))
                stored += 1
            except Exception as e:
                error_count += 1
                if first_error_sample is None:
                    first_error_sample = f"id={maint_id!r}: {type(e).__name__}: {e}"
                logger.debug(f"Error storing maintenance {maint_id}: {e}")
    if error_count:
        logger.warning(
            "Failed to store %d maintenance row(s); first error: %s",
            error_count, first_error_sample or '?',
        )
    logger.info(f"Stored {stored} maintenances in external_intelligence.db")
    return stored


def get_historical_maintenances(days_back: int = 90, limit: int = 200) -> List[Dict]:
    """Return maintenances seen within the last *days_back* days, newest first.

    Round 10 / Phase 5.5: filter by canonical ``published`` time
    (fallback to ``last_seen``) for parity with
    ``get_historical_incidents``.
    """
    cutoff = _utc_cutoff_str(days_back)
    with _connect() as conn:
        rows = conn.execute("""
            SELECT * FROM maintenances
            WHERE COALESCE(NULLIF(published, ''), last_seen) >= ?
            ORDER BY published DESC, last_seen DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()
    return [dict(r) for r in rows]


def get_maintenance_statistics(days_back: Optional[int] = None) -> Dict:
    """Aggregate statistics for the stored maintenances.

    See :func:`get_incident_statistics` for the rationale behind the
    optional ``days_back`` window.
    """
    # Round 10 / Phase 5.5: align with get_historical_maintenances.
    where_clause = ""
    params: tuple = ()
    if days_back is not None and int(days_back) > 0:
        cutoff = _utc_cutoff_str(int(days_back))
        where_clause = "WHERE COALESCE(NULLIF(published, ''), last_seen) >= ?"
        params = (cutoff,)
    with _connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM maintenances {where_clause}", params
        ).fetchone()[0]
        scheduled = conn.execute(
            f"SELECT COUNT(*) FROM maintenances {where_clause}{' AND' if where_clause else ' WHERE'} status = 'scheduled'",
            params,
        ).fetchone()[0]
        completed = conn.execute(
            f"SELECT COUNT(*) FROM maintenances {where_clause}{' AND' if where_clause else ' WHERE'} status = 'completed'",
            params,
        ).fetchone()[0]
        sources_rows = conn.execute(
            f"SELECT DISTINCT source FROM maintenances {where_clause}", params
        ).fetchall()
        sources = [r[0] for r in sources_rows if r[0]]
        oldest = conn.execute(
            f"SELECT MIN(first_seen) FROM maintenances {where_clause}", params
        ).fetchone()[0]
        # Round 12 / Phase 2.3: same list-vs-headline parity fix as
        # ``get_incident_statistics`` -- align the maintenance
        # statistics headline with the list windows that order on
        # ``COALESCE(NULLIF(published,''), last_seen)``.
        newest = conn.execute(
            f"SELECT MAX(COALESCE(NULLIF(published, ''), last_seen)) FROM maintenances {where_clause}",
            params,
        ).fetchone()[0]
    return {
        'total': total,
        'scheduled': scheduled,
        'completed': completed,
        'sources': sources,
        'oldest': oldest or '',
        'newest': newest or '',
        'days_back': int(days_back) if days_back is not None and int(days_back) > 0 else None,
    }


# ---------------------------------------------------------------------------
# Combined view (for the UI)
# ---------------------------------------------------------------------------

def get_all_external_intel(days_back: int = 365) -> Dict:
    """Return incidents, bugs, maintenances, and statistics for the external intelligence page.

    Statistics are now scoped to the same ``days_back`` window as the
    rendered lists, so the headline counts on the page agree with the
    rows beneath them. ``*_stats_global`` keys are also returned for
    callers that need the all-time totals.

    Round 4: include explicit ``list_truncated`` / ``list_fetch_limit``
    flags so consumers can disclose that the lists are capped at
    ``_LIST_FETCH_LIMIT`` rows even when ``incident_stats['count']``
    (an unbounded ``COUNT(*)``) reports a larger total.
    """
    _LIST_FETCH_LIMIT = 500

    # Phase 3.2: capture per-source fetch failures so the UI can render
    # "source unavailable" instead of the generic "no records yet"
    # copy. Empty list + a non-empty error in this dict means the
    # storage call raised, not that the source is genuinely empty.
    fetch_errors: Dict[str, str] = {}

    # Round 7 / Phase 3.13: previously ``str(exc)`` was written
    # straight into the JSON payload, which could leak file paths,
    # SQL fragments, secret-bearing connection strings, or
    # Python-version-specific tracebacks into the external
    # intelligence UI.  Route exceptions through ``error_classifier``
    # (when available) so the UI sees a sanitized ``user_message`` /
    # ``kind`` and the unsafe detail stays in the server log.
    def _classify(exc: BaseException) -> str:
        try:
            from error_classifier import classify_exception as _cls
            classified = _cls(exc)
            kind = str(classified.get("kind") or type(exc).__name__).strip()
            user_msg = str(classified.get("user_message") or "").strip()
            if user_msg:
                return f"{kind}: {user_msg}"
            return kind
        except Exception:
            # Conservative fallback: only the exception class name,
            # never ``str(exc)`` (which is what the audit flagged).
            return type(exc).__name__

    def _safe(label: str, callable_, *args, **kwargs):
        try:
            return callable_(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            sanitized = _classify(exc)
            fetch_errors[label] = sanitized
            logger.exception("incident_storage fetch failed for %s", label)
            return [] if label in {"incidents", "bugs", "maintenances"} else {"total": 0, "fetch_error": sanitized}

    incidents = _safe("incidents", get_historical_incidents, days_back=days_back, limit=_LIST_FETCH_LIMIT)
    bugs = _safe("bugs", get_historical_bugs, days_back=days_back, limit=_LIST_FETCH_LIMIT)
    maintenances = _safe("maintenances", get_historical_maintenances, days_back=days_back, limit=_LIST_FETCH_LIMIT)
    incident_stats = _safe("incident_stats", get_incident_statistics, days_back=days_back)
    bug_stats = _safe("bug_stats", get_bug_statistics, days_back=days_back)
    maintenance_stats = _safe("maintenance_stats", get_maintenance_statistics, days_back=days_back)

    def _list_truncated(rows, stats) -> bool:
        # Phase 1.4: stats dicts expose ``total`` (see get_*_statistics
        # ~line 211), not ``count``. The previous ``stats.get('count')``
        # always returned None so list_truncated was always False, lying
        # to the external-intelligence UI when the 500-row cap was hit.
        try:
            stats_dict = stats or {}
            total = int(stats_dict.get('total') or stats_dict.get('count') or 0)
        except (TypeError, ValueError):
            total = 0
        return len(rows) >= _LIST_FETCH_LIMIT and total > _LIST_FETCH_LIMIT

    return {
        'incidents': incidents,
        'bugs': bugs,
        'maintenances': maintenances,
        'incident_stats': incident_stats,
        'bug_stats': bug_stats,
        'maintenance_stats': maintenance_stats,
        # Round 2 / Phase 5.6: wrap the global stats lookups in
        # ``_safe`` too; previously a single failure here propagated as
        # a hard exception and broke ``get_all_external_intel`` even
        # though the windowed stats above already survived. With this
        # wrap, a failed global stat surfaces in ``fetch_errors`` and
        # the EI summary cards can render the failed-state instead of
        # crashing the request.
        'incident_stats_global': _safe('incident_stats_global', get_incident_statistics),
        'bug_stats_global': _safe('bug_stats_global', get_bug_statistics),
        'maintenance_stats_global': _safe('maintenance_stats_global', get_maintenance_statistics),
        'days_back': int(days_back),
        'list_fetch_limit': _LIST_FETCH_LIMIT,
        'list_truncated': {
            'incidents': _list_truncated(incidents, incident_stats),
            'bugs': _list_truncated(bugs, bug_stats),
            'maintenances': _list_truncated(maintenances, maintenance_stats),
        },
        'fetch_errors': fetch_errors,
    }


# ---------------------------------------------------------------------------
# Export / Import for data portability
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = 1


def export_all_data(
    *,
    limit_per_table: Optional[int] = None,
    page: int = 0,
    page_size: Optional[int] = None,
) -> Dict:
    """Export incident/bug/maintenance tables as a JSON-serializable dict.

    Round 5 / Phase 4.11: previously this loaded EVERY row from EVERY
    table into RAM with no upper bound, then JSON-encoded the whole
    thing.  In long-running deployments those tables grow without
    limit and an admin clicking "Export" could OOM the process and
    block the request thread for minutes.

    Now:
    - ``limit_per_table`` (defaults to env ``ADOPTIQ_EXPORT_LIMIT`` or
      50,000) caps the per-table row count.
    - ``page`` / ``page_size`` allow paginated exports (page is
      0-indexed; ``page_size`` defaults to env
      ``ADOPTIQ_EXPORT_PAGE_SIZE`` or 10,000).
    - The response includes ``truncated_*`` flags and counts so the
      caller can detect truncation and fetch the next page.

    Round 12 / Phase 11.8: ``page`` is **0-indexed** -- ``page=0``
    returns the first slice, ``page=1`` returns the second, etc.
    Callers that pass a 1-indexed value (e.g. an HTTP wrapper that
    treats ``?page=1`` as "first page") will silently skip the first
    ``page_size`` rows.  We audited every caller in the repo and the
    only HTTP entrypoint is ``/api/export-intel`` (app_simple.py),
    which now normalizes 1-indexed query strings via
    ``?index=1`` before invoking us.  The negative-clamp +
    Round-12 ``int()`` cast below also guard against accidental
    string / float drift from future callers.
    """
    _env_limit = int(os.environ.get('ADOPTIQ_EXPORT_LIMIT', '50000'))
    _env_page_size = int(os.environ.get('ADOPTIQ_EXPORT_PAGE_SIZE', '10000'))
    if limit_per_table is None:
        limit_per_table = _env_limit
    if page_size is None:
        page_size = _env_page_size
    # Round 12 / Phase 11.8: be tolerant of float/str ``page`` values
    # from JSON / query-string callers so the offset arithmetic below
    # cannot raise a TypeError when an integer is required.
    try:
        page = int(page)
    except Exception:
        page = 0
    try:
        page_size = int(page_size)
    except Exception:
        page_size = _env_page_size
    if page < 0:
        page = 0
    if page_size <= 0:
        page_size = _env_page_size
    # The effective per-page slice is ``min(page_size, limit_per_table - offset)``.
    offset = page * page_size

    # Round 6 / Phase 4.19: lock the (table, order_col) pair to a
    # static allowlist so that even if a future caller forwards user
    # input into ``_fetch`` we cannot end up interpolating arbitrary
    # SQL identifiers.  All identifiers below are ASCII letters /
    # underscores only, matching the SQLite schema in this module.
    _EXPORT_TABLES: Dict[str, str] = {
        'incidents': 'published',
        'bugs': 'discovered_at',
        'maintenances': 'published',
    }
    _IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

    def _fetch(conn, table: str, order_col: str) -> Tuple[List[Dict], int, bool]:
        if table not in _EXPORT_TABLES or _EXPORT_TABLES[table] != order_col:
            raise ValueError(f"export_all_data: unknown table/order_col: {table!r}/{order_col!r}")
        if not _IDENT_RE.match(table) or not _IDENT_RE.match(order_col):
            raise ValueError(f"export_all_data: invalid SQL identifier: {table!r}/{order_col!r}")
        total = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        # Effective remaining budget within ``limit_per_table``
        remaining = max(0, limit_per_table - offset)
        if remaining == 0:
            return [], total, total > offset
        slice_size = min(page_size, remaining)
        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT ? OFFSET ?",
                (slice_size, offset),
            ).fetchall()
        ]
        truncated = (offset + len(rows)) < total
        return rows, total, truncated

    with _connect() as conn:
        incidents, incidents_total, incidents_truncated = _fetch(conn, 'incidents', _EXPORT_TABLES['incidents'])
        bugs, bugs_total, bugs_truncated = _fetch(conn, 'bugs', _EXPORT_TABLES['bugs'])
        maintenances, maintenances_total, maintenances_truncated = _fetch(conn, 'maintenances', _EXPORT_TABLES['maintenances'])
    return {
        'schema_version': _SCHEMA_VERSION,
        'exported_at': _utc_now_str(),
        'page': page,
        'page_size': page_size,
        'limit_per_table': limit_per_table,
        'incidents': incidents,
        'bugs': bugs,
        'maintenances': maintenances,
        'totals': {
            'incidents': incidents_total,
            'bugs': bugs_total,
            'maintenances': maintenances_total,
        },
        'truncated': {
            'incidents': incidents_truncated,
            'bugs': bugs_truncated,
            'maintenances': maintenances_truncated,
        },
    }


def import_all_data(data: Dict) -> Dict:
    """Merge imported data into existing storage. Returns counts per table."""
    if not data or not isinstance(data, dict):
        return {'incidents': 0, 'bugs': 0, 'maintenances': 0}
    try:
        version = int(data.get('schema_version', 1))
    except (TypeError, ValueError):
        version = 1
    if version > _SCHEMA_VERSION:
        logger.warning(f"Import file schema v{version} is newer than supported v{_SCHEMA_VERSION}; proceeding anyway")

    counts = {'incidents': 0, 'bugs': 0, 'maintenances': 0}
    counts['incidents'] = store_historical_incidents([x for x in (data.get('incidents') or []) if isinstance(x, dict)])
    counts['bugs'] = store_historical_bugs([x for x in (data.get('bugs') or []) if isinstance(x, dict)])
    counts['maintenances'] = store_historical_maintenances([x for x in (data.get('maintenances') or []) if isinstance(x, dict)])
    logger.info(f"Imported data: {counts}")
    return counts
