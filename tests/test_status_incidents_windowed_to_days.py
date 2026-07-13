"""Round 2 / Phase 1.7 regression test.

``fetch_status_incidents`` MUST honour an explicit ``days_back``
argument and pass it through to ``get_historical_incidents`` so the
external-intel narrative can scope incidents to the report period
instead of always seeing the legacy 365-day window.

This is a source-level pin so the parameter is not silently dropped
during a refactor.
"""
from __future__ import annotations

import inspect
import re

import adoptiq_backend


def test_fetch_status_incidents_accepts_days_back_kwarg() -> None:
    sig = inspect.signature(adoptiq_backend.fetch_status_incidents)
    assert "days_back" in sig.parameters, (
        "Round 2 Phase 1.7: fetch_status_incidents must expose an "
        "explicit days_back parameter so callers can scope to the "
        "report period."
    )


def test_fetch_status_incidents_passes_days_back_to_storage() -> None:
    """The function body must forward ``days_back`` to
    ``get_historical_incidents`` so storage is also windowed.
    Without this, the live API window is honoured but the stored
    history is still a year wide and dominates the merged feed.
    """
    src = inspect.getsource(adoptiq_backend.fetch_status_incidents)
    assert re.search(
        r"get_historical_incidents\([^)]*days_back\s*=",
        src,
        re.DOTALL,
    ), (
        "Round 2 Phase 1.7: fetch_status_incidents must thread "
        "days_back= into get_historical_incidents so the storage "
        "fetch is windowed to the same horizon as the live API."
    )


def test_fetch_status_incidents_marks_default_window() -> None:
    """When ``days_back`` is None, the function should mark the
    default window as in use so renderers can disclose it.
    """
    src = inspect.getsource(adoptiq_backend.fetch_status_incidents)
    assert "window_default_used" in src or "_window_default_used" in src, (
        "Round 2 Phase 1.7: fetch_status_incidents must surface a "
        "marker (window_default_used / _window_default_used) when "
        "the legacy 365-day default is used so renderers can call "
        "out the assumption."
    )
