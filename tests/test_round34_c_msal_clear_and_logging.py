"""Round 34 / C -- MSAL token clearing + signin logging hygiene.

Two narrow Build8 fixes:

* **C1**: ``SharePointGraphClient.clear_token_cache`` mutated the
  in-memory MSAL state (``_msal_app``, ``_msal_cache``,
  ``_pending_flow``) WITHOUT holding ``self._lock``.  Meanwhile
  ``start_device_code_flow`` and ``await_device_code_completion``
  both acquire that lock when reading/writing ``_pending_flow``.
  A signout that landed between those two methods could observe a
  half-cleared state.

* **C2**: ``start_device_code_flow`` logged the freshly-issued
  ``user_code`` at INFO level.  The user_code is the short
  alphanumeric string the user types into
  ``microsoft.com/devicelogin`` to complete the sign-in.  Anyone
  who reads the log within the ~15 min validity window can
  complete the flow and steal the user's session.  The
  prompt is explicit: no device codes in logs (or exception
  messages) at any level.

Both tests are pure unit tests with no real keyring / MSAL / Graph
traffic.
"""
from __future__ import annotations

import logging
import threading
import types

import pytest


import sharepoint_corpus_source as scs


# ---------------------------------------------------------------------------
# Fakes -- minimal MSAL surface for the two methods under test.
# ---------------------------------------------------------------------------


class _FakeMsalApp:
    def __init__(self):
        self._initiate_calls = 0
        self.initiate_payload = {
            "user_code": "ABCD-EFGH",
            "device_code": "FAKE_DEVICE_CODE_NOT_TO_BE_LOGGED",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "message": "To sign in, type the code ABCD-EFGH at "
                       "https://microsoft.com/devicelogin",
        }

    def initiate_device_flow(self, scopes):
        self._initiate_calls += 1
        return dict(self.initiate_payload)


class _FakeMsalModule:
    """Minimal stand-in for the ``msal`` package."""

    def __init__(self, app):
        self._app = app

    def PublicClientApplication(self, *args, **kwargs):  # noqa: N802
        return self._app

    class SerializableTokenCache:
        def __init__(self):
            self.has_state_changed = False

        def deserialize(self, blob):
            self.has_state_changed = False

        def serialize(self):
            return ""


def _build_client(token_cache=None):
    fake_app = _FakeMsalApp()
    fake_msal = _FakeMsalModule(fake_app)
    client = scs.SharePointGraphClient(
        client_id="test-client-id",
        token_cache=token_cache,
        msal_module=fake_msal,
        # No real HTTP -- callers in these tests don't hit Graph.
        http_get=lambda *a, **kw: None,
    )
    return client, fake_app


# ---------------------------------------------------------------------------
# C1 -- clear_token_cache acquires the lock around the in-memory reset
# ---------------------------------------------------------------------------


def test_c1_clear_token_cache_holds_lock_during_in_memory_reset():
    """Pin the contract by replacing the lock with a probe that
    records every acquire/release.  ``clear_token_cache`` must
    bracket the ``_msal_app/_msal_cache/_pending_flow`` writes inside
    a ``with self._lock`` block."""
    client, _app = _build_client()

    acquire_count = {"n": 0}

    class _ProbeLock:
        def __init__(self):
            self._inner = threading.Lock()

        def __enter__(self):
            acquire_count["n"] += 1
            return self._inner.__enter__()

        def __exit__(self, exc_type, exc, tb):
            return self._inner.__exit__(exc_type, exc, tb)

        # Some code paths call .acquire/.release directly; pin both
        # surfaces.
        def acquire(self, *a, **kw):
            acquire_count["n"] += 1
            return self._inner.acquire(*a, **kw)

        def release(self):
            return self._inner.release()

    client._lock = _ProbeLock()
    # Pre-populate the in-memory state so we can verify it gets reset.
    client._msal_app = object()
    client._msal_cache = object()
    client._pending_flow = {"user_code": "ABCD"}

    client.clear_token_cache()

    assert acquire_count["n"] >= 1, (
        "clear_token_cache must acquire self._lock at least once "
        "around the in-memory state reset"
    )
    # And the state must actually be cleared.
    assert client._msal_app is None
    assert client._msal_cache is None
    assert client._pending_flow is None


def test_c1_clear_token_cache_idempotent_under_concurrent_calls():
    """Two threads calling clear_token_cache simultaneously must not
    corrupt the in-memory state -- the lock serializes them."""
    client, _ = _build_client()
    client._msal_app = object()
    client._msal_cache = object()
    client._pending_flow = {"user_code": "ABCD"}

    barrier = threading.Barrier(2)
    errors = []

    def _signout():
        try:
            barrier.wait(timeout=2)
            for _ in range(20):
                client.clear_token_cache()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=_signout)
    t2 = threading.Thread(target=_signout)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors, f"concurrent signout raised: {errors!r}"
    assert client._msal_app is None
    assert client._msal_cache is None
    assert client._pending_flow is None


# ---------------------------------------------------------------------------
# C2 -- device code never appears in logs
# ---------------------------------------------------------------------------


def test_c2_start_device_code_flow_does_not_log_user_code(caplog):
    """The freshly-issued user_code is what an attacker needs to
    complete the sign-in -- it must NOT appear in any log line at
    any level.  Verification URI and expiry are fine to log."""
    client, _app = _build_client()
    flow_secret = "ABCD-EFGH"
    # Capture every level so a regression that drops to DEBUG is
    # also caught.
    with caplog.at_level(logging.DEBUG, logger="sharepoint_corpus_source"):
        result = client.start_device_code_flow()

    assert result.user_code == flow_secret  # The UI still gets it.

    # No log record may contain the user_code -- check both formatted
    # message and raw args.
    for rec in caplog.records:
        formatted = rec.getMessage()
        assert flow_secret not in formatted, (
            f"user_code leaked in log message: {formatted!r}"
        )
        for arg in rec.args or ():
            if isinstance(arg, str):
                assert flow_secret not in arg, (
                    f"user_code leaked in log args: {arg!r}"
                )

    # The verification URI should be present (operator visibility).
    assert any(
        "verification_uri" in rec.getMessage() for rec in caplog.records
    ), "verification_uri should be logged for operator triage"


def test_c2_unexpected_payload_error_does_not_leak_flow_dict(caplog):
    """When initiate_device_flow returns garbage, the raised
    SharePointAuthRequired must NOT contain the raw flow payload --
    that payload may carry partial/leaked credential material from
    the Graph API.  Only the key list is safe to surface."""
    client, fake_app = _build_client()
    # Force a bad payload shape so the error path fires.
    secret_blob = "REFRESH_TOKEN_LEAK_CANDIDATE_xyz"
    fake_app.initiate_payload = {
        "refresh_token_hint": secret_blob,
        "wrong_key": "no user_code here",
    }
    with pytest.raises(scs.SharePointAuthRequired) as excinfo:
        client.start_device_code_flow()

    msg = str(excinfo.value)
    assert secret_blob not in msg, (
        f"raw flow payload leaked through SharePointAuthRequired: {msg!r}"
    )
    # The key list IS allowed and helpful for triage.
    assert "keys=" in msg or "type" in msg


# ---------------------------------------------------------------------------
# C-misc -- existing token-cache clear surface (KeyringTokenCache.clear)
# ---------------------------------------------------------------------------


class _FakeKeyringModule:
    def __init__(self, *, get_returns="", raises_on_delete=False):
        self.calls = []
        self._get_returns = get_returns
        self._raises_on_delete = raises_on_delete

    def get_password(self, service, account):
        self.calls.append(("get", service, account))
        return self._get_returns

    def set_password(self, service, account, value):
        self.calls.append(("set", service, account))

    def delete_password(self, service, account):
        self.calls.append(("delete", service, account))
        if self._raises_on_delete:
            raise RuntimeError("no such entry")


def test_c_keyring_cache_clear_does_not_log_token_bytes(caplog, tmp_path):
    """KeyringTokenCache.clear must never include cache bytes (the
    serialized refresh-token JSON) in logs even when the keyring
    delete raises."""
    secret = "REFRESH_TOKEN_OPAQUE_BYTES_xyz"
    fb = tmp_path / "token_cache.json"
    fb.write_text(secret, encoding="utf-8")

    fake_kr = _FakeKeyringModule(raises_on_delete=True)
    cache = scs.KeyringTokenCache(
        fallback_path=fb,
        keyring_module=fake_kr,
    )

    with caplog.at_level(logging.DEBUG, logger="sharepoint_corpus_source"):
        result = cache.clear()

    assert result["file"] is True
    assert not fb.exists()
    for rec in caplog.records:
        formatted = rec.getMessage()
        assert secret not in formatted, (
            f"refresh-token bytes leaked in log: {formatted!r}"
        )
        for arg in rec.args or ():
            assert secret not in str(arg), (
                f"refresh-token bytes leaked in log args: {arg!r}"
            )
