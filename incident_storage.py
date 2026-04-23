"""
Persistent storage for external intelligence data (incidents and bugs).
Uses SQLite so history survives between report runs and app restarts.
"""

import sqlite3
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
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


init_db()


# ---------------------------------------------------------------------------
# Incident storage (API expected by adoptiq_backend.fetch_status_incidents)
# ---------------------------------------------------------------------------

def store_historical_incidents(incidents: List[Dict]) -> int:
    """Upsert incidents; return count of rows inserted or updated."""
    if not incidents:
        return 0
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    stored = 0
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
                logger.warning(f"Error storing incident {inc_id}: {e}")
    logger.info(f"Stored {stored} incidents in external_intelligence.db")
    return stored


def get_historical_incidents(days_back: int = 90, limit: int = 50) -> List[Dict]:
    """Return incidents seen within the last *days_back* days, newest first."""
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%d %H:%M:%S')
    with _connect() as conn:
        rows = conn.execute("""
            SELECT * FROM incidents
            WHERE last_seen >= ?
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
    where_clause = ""
    params: tuple = ()
    if days_back is not None and int(days_back) > 0:
        cutoff = (datetime.utcnow() - timedelta(days=int(days_back))).strftime('%Y-%m-%d %H:%M:%S')
        where_clause = "WHERE last_seen >= ?"
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
        newest = conn.execute(
            f"SELECT MAX(last_seen) FROM incidents {where_clause}", params
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
    """Upsert bugs; return count of rows inserted or updated."""
    if not bugs:
        return 0
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    stored = 0
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
                logger.warning(f"Error storing bug {bug_id}: {e}")
    logger.info(f"Stored {stored} bugs in external_intelligence.db")
    return stored


def get_historical_bugs(days_back: int = 90, limit: int = 50) -> List[Dict]:
    """Return bugs seen within the last *days_back* days, newest first."""
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%d %H:%M:%S')
    with _connect() as conn:
        rows = conn.execute("""
            SELECT * FROM bugs
            WHERE last_seen >= ?
            ORDER BY discovered_at DESC, last_seen DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()
    return [dict(r) for r in rows]


def get_bug_statistics(days_back: Optional[int] = None) -> Dict:
    """Aggregate statistics for the stored bugs.

    See :func:`get_incident_statistics` for the rationale behind the
    optional ``days_back`` window.
    """
    where_clause = ""
    params: tuple = ()
    if days_back is not None and int(days_back) > 0:
        cutoff = (datetime.utcnow() - timedelta(days=int(days_back))).strftime('%Y-%m-%d %H:%M:%S')
        where_clause = "WHERE last_seen >= ?"
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
    """Upsert maintenances; return count of rows inserted or updated."""
    if not maintenances:
        return 0
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    stored = 0
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
                logger.warning(f"Error storing maintenance {maint_id}: {e}")
    logger.info(f"Stored {stored} maintenances in external_intelligence.db")
    return stored


def get_historical_maintenances(days_back: int = 90, limit: int = 200) -> List[Dict]:
    """Return maintenances seen within the last *days_back* days, newest first."""
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).strftime('%Y-%m-%d %H:%M:%S')
    with _connect() as conn:
        rows = conn.execute("""
            SELECT * FROM maintenances
            WHERE last_seen >= ?
            ORDER BY published DESC, last_seen DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()
    return [dict(r) for r in rows]


def get_maintenance_statistics(days_back: Optional[int] = None) -> Dict:
    """Aggregate statistics for the stored maintenances.

    See :func:`get_incident_statistics` for the rationale behind the
    optional ``days_back`` window.
    """
    where_clause = ""
    params: tuple = ()
    if days_back is not None and int(days_back) > 0:
        cutoff = (datetime.utcnow() - timedelta(days=int(days_back))).strftime('%Y-%m-%d %H:%M:%S')
        where_clause = "WHERE last_seen >= ?"
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
        newest = conn.execute(
            f"SELECT MAX(last_seen) FROM maintenances {where_clause}", params
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
    """
    return {
        'incidents': get_historical_incidents(days_back=days_back, limit=500),
        'bugs': get_historical_bugs(days_back=days_back, limit=500),
        'maintenances': get_historical_maintenances(days_back=days_back, limit=500),
        'incident_stats': get_incident_statistics(days_back=days_back),
        'bug_stats': get_bug_statistics(days_back=days_back),
        'maintenance_stats': get_maintenance_statistics(days_back=days_back),
        'incident_stats_global': get_incident_statistics(),
        'bug_stats_global': get_bug_statistics(),
        'maintenance_stats_global': get_maintenance_statistics(),
        'days_back': int(days_back),
    }


# ---------------------------------------------------------------------------
# Export / Import for data portability
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = 1


def export_all_data() -> Dict:
    """Export all tables as a JSON-serializable dict with schema version."""
    with _connect() as conn:
        incidents = [dict(r) for r in conn.execute("SELECT * FROM incidents ORDER BY published DESC").fetchall()]
        bugs = [dict(r) for r in conn.execute("SELECT * FROM bugs ORDER BY discovered_at DESC").fetchall()]
        maintenances = [dict(r) for r in conn.execute("SELECT * FROM maintenances ORDER BY published DESC").fetchall()]
    return {
        'schema_version': _SCHEMA_VERSION,
        'exported_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        'incidents': incidents,
        'bugs': bugs,
        'maintenances': maintenances,
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
