"""Round 36 / onedrive-sync-auth: pin that the daily-refresh worker
uses the local OneDrive presence check (no MSAL / no Microsoft
Graph network calls).

Round 34 still gated the refresh on
``sharepoint_corpus_source._is_signed_in()`` which relied on a
cached MSAL refresh token in the OS keychain.  That path could
silently lose its token (admin-consent revocation, IT Mac re-image,
keychain corruption) and stop refreshing the corpus indefinitely.
Round 36 removes the MSAL gate entirely and replaces it with the
purely-local ``_check_onedrive_sync_status()`` probe.

These tests pin:

* ``_daily_refresh_loop`` calls ``_check_onedrive_sync_status``
  (not any MSAL/Graph helper).
* When ``onedrive_status == "synced"`` and ``_should_refresh()``
  returns True, the worker calls ``request_refresh(rebuild=False)``.
* When ``onedrive_status != "synced"`` the worker silently skips
  (does NOT call ``request_refresh``); ``last_refresh_attempt_ts``
  is NOT bumped (a skipped tick is not an "attempted refresh").
* The corpus_bootstrap module no longer exports any MSAL / Graph
  surface (no signin / signout / refresh helpers, no Graph client).
"""

from __future__ import annotations

import threading
import time

import pytest

import corpus_bootstrap


@pytest.fixture(autouse=True)
def _reset_state():
    corpus_bootstrap.reset_for_tests()
    yield
    corpus_bootstrap.reset_for_tests()


# ---------------------------------------------------------------------------
# MSAL / Graph surface is gone.
# ---------------------------------------------------------------------------


def test_corpus_bootstrap_module_drops_msal_helpers():
    """Round 36 removed the MSAL-flavored sign-in / sign-out / refresh
    entry points from ``corpus_bootstrap``.  This test pins that
    those names are no longer exported -- callers (the Flask routes
    that were also removed in Round 36) MUST NOT regress and start
    importing them again."""
    forbidden = (
        "begin_sharepoint_signin",
        "request_sharepoint_refresh",
        "sharepoint_signout",
        "_is_sharepoint_signed_in",
        "_refresh_sharepoint_cache_for_bootstrap",
    )
    public = set(getattr(corpus_bootstrap, "__all__", ()))
    for name in forbidden:
        assert name not in public, (
            f"corpus_bootstrap.__all__ leaks legacy MSAL helper "
            f"{name!r}; Round 36 removed the MSAL/Graph runtime path."
        )
        assert not hasattr(corpus_bootstrap, name), (
            f"corpus_bootstrap.{name} exists; the MSAL/Graph runtime "
            f"path was retired in Round 36 -- delete the function so "
            f"the dead-code path cannot be re-wired by accident."
        )


def test_sharepoint_corpus_source_module_is_gone():
    """The MSAL / Graph client lived in ``sharepoint_corpus_source``.
    The whole module was deleted in Round 36 because the runtime
    no longer authenticates against Microsoft Graph (the OneDrive
    desktop client handles that auth flow now)."""
    with pytest.raises(ImportError):
        import sharepoint_corpus_source  # type: ignore  # noqa: F401


# ---------------------------------------------------------------------------
# Daily-refresh worker now drives off the local OneDrive presence check.
# ---------------------------------------------------------------------------


def test_daily_refresh_calls_local_presence_check_not_msal(monkeypatch):
    """When the 24h boundary triggers, the worker MUST call
    ``_check_onedrive_sync_status()`` (the local presence probe)
    and MUST NOT call any Graph / MSAL helper -- those are gone."""
    # Mark the corpus as enabled so the worker's first guard passes.
    monkeypatch.setattr(
        corpus_bootstrap, "is_enabled", lambda: True
    )

    # Force ``_should_refresh`` to True so the worker reaches the
    # presence check (not blocked by the 24h timer math).
    monkeypatch.setattr(
        corpus_bootstrap, "_should_refresh", lambda **kw: True
    )

    presence_calls: list[int] = []

    def fake_presence_probe():
        presence_calls.append(1)
        # Return ``not_synced`` so the worker exits the iteration
        # without hitting request_refresh -- we only want to prove
        # that the presence helper is on the daily-refresh path.
        return "not_synced", 0, "/tmp/no-such-onedrive"

    monkeypatch.setattr(
        corpus_bootstrap,
        "_check_onedrive_sync_status",
        fake_presence_probe,
    )

    refresh_calls: list[bool] = []

    def fake_request_refresh(*, rebuild=False):  # noqa: ARG001
        refresh_calls.append(True)
        return True

    monkeypatch.setattr(
        corpus_bootstrap, "request_refresh", fake_request_refresh
    )

    # Fast-tick: drop tick to 50ms so the test exits in <1s.
    monkeypatch.setattr(corpus_bootstrap, "_DAILY_REFRESH_TICK_S", 0.05)

    # Spawn the worker, let it tick a few times, then signal stop.
    started = corpus_bootstrap.start_daily_refresh_worker()
    assert started is True
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and not presence_calls:
            time.sleep(0.05)
    finally:
        corpus_bootstrap.stop()

    assert presence_calls, (
        "_daily_refresh_loop did NOT call _check_onedrive_sync_status; "
        "the Round 36 local-presence gate is missing -- the daemon "
        "cannot tell whether OneDrive is synced and may push refreshes "
        "against an unmounted folder."
    )
    assert not refresh_calls, (
        "request_refresh was invoked even though presence reported "
        "'not_synced' -- the worker MUST silently skip when the "
        "OneDrive folder is missing/empty."
    )


def test_daily_refresh_skips_when_onedrive_not_synced(monkeypatch):
    """When the presence probe says ``not_synced`` the worker MUST
    NOT call ``request_refresh`` and MUST NOT bump
    ``last_refresh_attempt_ts`` -- skipping a tick because the
    folder is unavailable is not an "attempted refresh" (otherwise
    the panel would falsely report "Last refresh: just now")."""
    monkeypatch.setattr(corpus_bootstrap, "is_enabled", lambda: True)
    monkeypatch.setattr(
        corpus_bootstrap, "_should_refresh", lambda **kw: True
    )
    monkeypatch.setattr(
        corpus_bootstrap,
        "_check_onedrive_sync_status",
        lambda: ("not_synced", 0, "/tmp/x"),
    )

    refresh_calls: list[bool] = []

    def fake_request_refresh(*, rebuild=False):  # noqa: ARG001
        refresh_calls.append(True)
        return True

    monkeypatch.setattr(
        corpus_bootstrap, "request_refresh", fake_request_refresh
    )
    monkeypatch.setattr(corpus_bootstrap, "_DAILY_REFRESH_TICK_S", 0.05)

    corpus_bootstrap.start_daily_refresh_worker()
    try:
        # Wait for at least one tick.
        time.sleep(0.4)
    finally:
        corpus_bootstrap.stop()

    assert refresh_calls == [], (
        "request_refresh was called despite onedrive_status='not_synced'"
    )
    state = corpus_bootstrap.get_state()
    # last_refresh_attempt_ts must remain None (never bumped on skip).
    assert state.last_refresh_attempt_ts is None, (
        "last_refresh_attempt_ts was bumped on a skipped tick -- the "
        "panel will falsely report 'Last refresh: just now'."
    )


def test_daily_refresh_invokes_local_request_refresh_when_synced(monkeypatch):
    """When OneDrive is synced AND the 24h timer expired, the worker
    MUST call ``request_refresh(rebuild=False)`` (the local index
    pass) -- no Graph / MSAL involvement.  This pins the new
    "local refresh only" contract."""
    monkeypatch.setattr(corpus_bootstrap, "is_enabled", lambda: True)
    monkeypatch.setattr(
        corpus_bootstrap, "_should_refresh", lambda **kw: True
    )
    monkeypatch.setattr(
        corpus_bootstrap,
        "_check_onedrive_sync_status",
        lambda: ("synced", 7, "/tmp/od"),
    )

    refresh_called = threading.Event()
    refresh_kwargs: list[dict] = []

    def fake_request_refresh(*, rebuild=False):
        refresh_kwargs.append({"rebuild": rebuild})
        refresh_called.set()
        return True

    monkeypatch.setattr(
        corpus_bootstrap, "request_refresh", fake_request_refresh
    )
    monkeypatch.setattr(corpus_bootstrap, "_DAILY_REFRESH_TICK_S", 0.05)

    corpus_bootstrap.start_daily_refresh_worker()
    try:
        assert refresh_called.wait(timeout=2.0), (
            "request_refresh was never called within 2s of starting "
            "the worker -- the local-refresh path may be broken."
        )
    finally:
        corpus_bootstrap.stop()

    assert refresh_kwargs, "request_refresh was not invoked"
    # ``rebuild=False`` is the contract: incremental upserts only,
    # never a destructive rebuild on a routine 24h tick.
    assert refresh_kwargs[0]["rebuild"] is False, (
        "daily-refresh worker MUST call request_refresh(rebuild=False); "
        "a rebuild=True would wipe the user's incrementally-indexed "
        "intel_uploads on every tick."
    )

    state = corpus_bootstrap.get_state()
    # Successful path bumps last_refresh_attempt_ts; we don't assert
    # the exact value, just that it was set (was None before tick).
    assert state.last_refresh_attempt_ts is not None, (
        "last_refresh_attempt_ts must be bumped when a refresh is "
        "attempted; the panel relies on this for 'Last refresh: 5m ago'."
    )
    # Presence check should also have populated the OneDrive fields
    # on the state snapshot.
    assert state.onedrive_status == "synced"
    assert state.onedrive_file_count == 7


def test_start_daily_refresh_worker_does_not_import_msal(monkeypatch):
    """Defense-in-depth: starting the worker must not trigger any
    ``import msal`` (the package is no longer in requirements.txt
    and would crash the .app at runtime if it became a dependency
    again).  Probing ``sys.modules`` after start_worker exits is
    enough to catch a regression."""
    import sys

    # Snapshot any pre-existing msal entries (developer machines may
    # have it installed via other tools); we only care about NEW
    # imports triggered by start_daily_refresh_worker.
    pre = set(sys.modules.keys())

    started = corpus_bootstrap.start_daily_refresh_worker()
    assert started is True
    try:
        # Let the worker spin up its loop and call its first iteration.
        time.sleep(0.05)
    finally:
        corpus_bootstrap.stop()

    new = set(sys.modules.keys()) - pre
    msal_imports = [name for name in new if name == "msal" or name.startswith("msal.")]
    assert not msal_imports, (
        f"daily-refresh worker imported MSAL submodules: {msal_imports!r} "
        "-- the Round 36 pivot removed MSAL entirely; a regression "
        "here means the keychain / device-code path is creeping back."
    )
