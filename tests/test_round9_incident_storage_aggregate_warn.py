"""Round 9 / Phase 6.5: incident_storage.py aggregates per-row WARNING."""
from __future__ import annotations

import logging
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_marker_incident_storage_aggregate_warn() -> None:
    src = REPO_ROOT.joinpath('incident_storage.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 6.5' in src, (
        'Round 9 / Phase 6.5 marker missing in incident_storage.py'
    )
    assert 'first_error_sample' in src, (
        'incident_storage.py: first_error_sample aggregator missing'
    )


def test_store_historical_incidents_aggregates_failures(monkeypatch, caplog) -> None:
    """Force every row to fail and ensure exactly one aggregated WARNING is emitted."""
    import incident_storage as _is

    # Monkeypatch _connect to provide a connection object whose execute()
    # always raises so every row fails.
    class _Conn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, *args, **kwargs):
            raise RuntimeError('synthetic insert failure')

    monkeypatch.setattr(_is, '_connect', lambda: _Conn())

    incidents = [{'id': f'inc-{i}', 'title': f't{i}'} for i in range(5)]
    with caplog.at_level(logging.WARNING, logger='incident_storage'):
        stored = _is.store_historical_incidents(incidents)
    assert stored == 0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    # Exactly one aggregated warning, not one per row.
    assert len(warnings) == 1, f'expected 1 aggregated warning, got {len(warnings)}'
    msg = warnings[0].getMessage()
    assert 'Failed to store 5 incident row(s)' in msg
    assert 'first error' in msg.lower()
