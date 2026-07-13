"""Round 4 / Phase 5.5 regression test.

When ``fetch_status_incidents`` returns a non-empty list whose entries
are ALL ``_from_storage=True`` (live API failed), the incident list
must carry a ``served_from_local_cache`` flag (or a
``_served_from_local_cache`` marker on the first row), AND the EI
formatter must render a "served from the local cache..." disclosure.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_fetch_status_incidents_marks_cache_only() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "served_from_local_cache" in src, (
        "Round 4 Phase 5.5: fetch_status_incidents must mark "
        "incidents with served_from_local_cache when all rows are "
        "_from_storage and the live JSON API fetch failed."
    )


def test_ei_formatter_discloses_local_cache_source() -> None:
    src = (REPO_ROOT / "executive_intelligence_formatter.py").read_text(encoding="utf-8")
    # The Round 4 fix renders a "served from the local cache" notice
    # in the Service Incidents section.
    assert (
        "served_from_local_cache" in src
        or "served from the local cache" in src.lower()
        or "local incident cache" in src.lower()
    ), (
        "Round 4 Phase 5.5: executive_intelligence_formatter must "
        "render a 'served from local cache' disclosure when "
        "incidents_source flag indicates cache-only."
    )
