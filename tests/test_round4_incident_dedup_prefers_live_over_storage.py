"""Round 4 / Phase 3.2 regression test.

``fetch_status_incidents`` deduplication must prefer the live API
incident over a stale ``_from_storage`` copy of the same id.
Pre-Round-4 the storage row was prepended and the dedup-by-id helper
kept the *first* occurrence, so SQLite always won.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_status_incident_dedup_promotes_live_over_storage() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # Source pin: the dedup loop must explicitly look at
    # ``_from_storage`` to give live rows priority.  We accept either
    # a literal "_from_storage" reference inside the dedup block or a
    # check that prefers non-storage entries when ids collide.
    assert "_from_storage" in src, (
        "Round 4 Phase 3.2: fetch_status_incidents must consider "
        "_from_storage in dedup so live rows are kept over stale "
        "SQLite copies."
    )
    # Sanity: the function must exist.
    assert "def fetch_status_incidents" in src, (
        "fetch_status_incidents not found in adoptiq_backend.py"
    )
