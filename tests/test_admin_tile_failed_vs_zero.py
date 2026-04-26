"""Round 2 / Phase 2.2 regression test.

Admin dashboard KPI tiles must distinguish "the underlying query
failed" from "the table is genuinely empty".  ``get_total_count``
returns ``None`` on failure so the tile template can render "n/a"
(or a failure chip) instead of the indistinguishable ``0`` that
operators previously misread as "all clear".
"""
from __future__ import annotations

import pytest


@pytest.fixture
def admin_module(tmp_path, monkeypatch):
    db_file = tmp_path / "admin_monitoring_failtile_test.db"
    import enhanced_admin_dashboard_v2 as ead

    monkeypatch.setattr(ead, "DB_PATH", str(db_file))
    monkeypatch.setattr(ead, "_db_initialized_for_path", None)
    ead.init_database()
    return ead


def test_get_total_count_returns_none_on_failure(admin_module, monkeypatch):
    """Forcing the underlying query to raise must produce ``None``,
    not ``0``, so the template can render "n/a" rather than mask the
    failure as a healthy zero count.
    """
    class _BadCursor:
        def __init__(self):
            self.description = None
        def execute(self, *_a, **_kw):
            raise RuntimeError("simulated DB failure")
        def fetchone(self):
            return None
        def close(self):
            return None

    class _BadConn:
        def cursor(self):
            return _BadCursor()
        def __enter__(self):
            return self
        def __exit__(self, *_a):
            return False
        def close(self):
            return None
        def commit(self):
            return None

    from contextlib import contextmanager

    @contextmanager
    def _bad_conn(*_a, **_kw):
        yield _BadConn()

    monkeypatch.setattr(admin_module, "db_connection", _bad_conn)
    # Also neutralize log_error so its own db_connection call does
    # not re-trigger our simulated failure path.
    monkeypatch.setattr(admin_module, "log_error", lambda *a, **kw: None)
    result = admin_module.get_total_count("report_history")
    assert result is None, (
        "Round 2 Phase 2.2: get_total_count must return None on "
        "failure so the admin KPI tile can render n/a rather than "
        "the indistinguishable 0."
    )


def test_admin_tile_template_handles_none(admin_module):
    """Sanity: the dashboard HTML branches on ``totals.X is none``
    rather than treating ``None`` as ``0``.  This pins the template
    branch that drives the failed-vs-zero distinction.
    """
    import inspect, pathlib

    # Locate the rendered template string in the module source.
    src = pathlib.Path(inspect.getsourcefile(admin_module)).read_text(encoding="utf-8")
    assert "totals.report_history is none" in src, (
        "Round 2 Phase 2.2: the admin dashboard template must branch "
        "on `totals.report_history is none` so a failed fetch renders "
        "n/a instead of being indistinguishable from an empty table."
    )
