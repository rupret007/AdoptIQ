"""Round 38 - ``/start_leader_report`` provenance split, end-to-end.

Round 38 / Phase 1 split the legacy single ``csone_file`` variable in
``start_leader_report`` into two distinct provenance branches:

* ``csone_file_explicit``    -- set only when the operator actively
                                uploaded a file via the form's
                                ``csone_file`` field.
* ``csone_file_autopicked``  -- set only when no upload occurred and
                                the OneDrive autodiscovery fallback
                                ``get_latest_csone_from_folder()``
                                returned a path.

The endpoint then writes two new keys into ``analysis_status`` so the
worker's two-pass validator can distinguish them at run-time:

* ``csone_file_was_uploaded``  -- ``True`` iff explicit upload.
* ``csone_file_path``          -- the resolved-effective path (either
                                  the explicit upload OR the
                                  autopicked OneDrive file, whichever
                                  is set).

This file pins the runtime behaviour by driving the Flask test client
against ``POST /start_leader_report`` and inspecting the resulting
``analysis_status`` dict directly.  Source-shape pins live in the
companion file ``test_round38_leader_csone_validation_two_pass.py``.
"""
from __future__ import annotations

import io
import os
from typing import Optional


def _post_leader(
    monkeypatch,
    *,
    upload: bool,
    autopicked_path: Optional[str],
):
    """Helper: drive ``POST /start_leader_report`` with the requested
    provenance shape.

    Stubs out CSRF, threading, file-validation, and the OneDrive
    autodiscovery fallback so the test can isolate the endpoint's
    provenance write without touching real Snowflake / file uploads.

    Returns the ``(client, analysis_id, status_dict_snapshot)`` tuple.
    """
    import threading

    import app_simple

    # Disable CSRF for the test client invocation -- we are testing the
    # endpoint's provenance write logic, not its CSRF policy (which has
    # its own dedicated tests).
    monkeypatch.setitem(app_simple.app.config, "WTF_CSRF_ENABLED", False)

    # Stub the OneDrive autodiscovery fallback to return the test's
    # chosen path.  Returning ``None`` simulates "OneDrive not synced
    # / nothing to autopick".
    monkeypatch.setattr(
        app_simple,
        "get_latest_csone_from_folder",
        lambda: autopicked_path,
    )

    # Stub validate_file_upload so the in-memory bytes pass through.
    monkeypatch.setattr(
        app_simple,
        "validate_file_upload",
        lambda _f: (True, "ok"),
    )

    # Stub the worker thread launch so the background generator does
    # not run during the test (it would try to talk to real Snowflake).
    class _NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return None

        @property
        def daemon(self):
            return True

        @daemon.setter
        def daemon(self, value):
            return None

    monkeypatch.setattr(threading, "Thread", _NoopThread)

    # Drop any leftover analysis_status entries from previous tests so
    # the assertion below can find OUR entry unambiguously.
    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()

    client = app_simple.app.test_client()

    form_data = {
        "manager": "Round38 Tester",
        "days": "30",
    }
    if upload:
        # Non-empty bytes so the endpoint's ``if file and file.filename``
        # guard takes the upload branch.
        form_data["csone_file"] = (
            io.BytesIO(b"PK\x03\x04 fake xlsx body"),
            "leader_test_upload.xlsx",
        )

    resp = client.post(
        "/start_leader_report",
        data=form_data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, (
        f"endpoint POST failed unexpectedly: {resp.status_code} "
        f"{resp.get_data(as_text=True)!r}"
    )

    with app_simple.analysis_status_lock:
        # Round 38 / Phase 1 endpoint creates exactly one analysis_id
        # entry per POST.
        assert len(app_simple.analysis_status) == 1, (
            f"expected exactly one analysis_status entry per POST, "
            f"got {len(app_simple.analysis_status)}: "
            f"{list(app_simple.analysis_status.keys())}"
        )
        analysis_id = next(iter(app_simple.analysis_status))
        status_snapshot = dict(app_simple.analysis_status[analysis_id])

    return client, analysis_id, status_snapshot


def test_explicit_upload_sets_csone_file_was_uploaded_true(monkeypatch, tmp_path):
    """Round 38 / Phase 1: when the operator uploads a CSOne file via
    the form's ``csone_file`` field, ``csone_file_was_uploaded`` MUST
    be ``True`` AND ``csone_file_path`` MUST resolve to a path inside
    the configured upload folder (not the OneDrive autopicked path,
    which the worker would have ignored).
    """
    # Ensure the autodiscovery fallback would have returned something
    # different so we can prove the explicit-upload branch wins.
    fake_autopicked = str(tmp_path / "from_onedrive_autopicked.xlsx")
    with open(fake_autopicked, "wb") as fh:
        fh.write(b"PK\x03\x04 onedrive fallback body")

    _client, _aid, status = _post_leader(
        monkeypatch,
        upload=True,
        autopicked_path=fake_autopicked,
    )

    assert status.get("csone_file_was_uploaded") is True, (
        "Round 38 / Phase 1 regression: explicit upload via "
        "request.files['csone_file'] did NOT result in "
        "status['csone_file_was_uploaded'] == True.  Got "
        f"{status.get('csone_file_was_uploaded')!r}.  This breaks "
        "Pass 2 fail-loud behaviour for explicit uploads."
    )

    csone_file_path = status.get("csone_file_path")
    assert csone_file_path, (
        "Round 38 / Phase 1 regression: status['csone_file_path'] "
        "was not written for an explicit upload.  Worker code "
        "queues off this key for the resolved-effective path."
    )
    assert csone_file_path != fake_autopicked, (
        f"Round 38 / Phase 1 regression: explicit upload was overridden "
        f"by the OneDrive autopicked path ({csone_file_path!r}).  "
        "Explicit uploads must always win."
    )
    # The legacy ``csone_file`` back-compat key must mirror the
    # explicit-effective path so existing log lines continue to work.
    assert status.get("csone_file") == csone_file_path, (
        "Round 38 / Phase 1 regression: legacy back-compat "
        "``csone_file`` key drifted from ``csone_file_path``.  Both "
        "must point at the same resolved-effective path so existing "
        "log / progress messages keep working."
    )


def test_no_upload_with_autopicked_sets_was_uploaded_false(monkeypatch, tmp_path):
    """Round 38 / Phase 1: when the operator did NOT upload anything
    but ``get_latest_csone_from_folder()`` returned a path, the
    endpoint must record ``csone_file_was_uploaded=False`` and surface
    the autopicked path via ``csone_file_path``.

    This is the soft-fail path: the worker's Pass 2 guard must read
    ``False`` here so an empty-after-scope autopicked file emits a
    ``partial_data_warning`` instead of aborting the report.
    """
    autopicked = str(tmp_path / "auto_from_onedrive.xlsx")
    with open(autopicked, "wb") as fh:
        fh.write(b"PK\x03\x04 onedrive sync body")

    _client, _aid, status = _post_leader(
        monkeypatch,
        upload=False,
        autopicked_path=autopicked,
    )

    assert status.get("csone_file_was_uploaded") is False, (
        "Round 38 / Phase 1 regression: OneDrive autodiscovery hit "
        "was misclassified as an explicit upload (got "
        f"{status.get('csone_file_was_uploaded')!r}).  This makes Pass 2 "
        "fail loud on autopicked files and re-introduces the original "
        "``csone missing or empty`` false-positive."
    )
    assert status.get("csone_file_path") == autopicked, (
        f"Round 38 / Phase 1 regression: status['csone_file_path'] "
        f"({status.get('csone_file_path')!r}) does not match the "
        f"autodiscovered path ({autopicked!r}).  The worker keys off "
        "this for the partial_data_warning logging."
    )
    # And the legacy back-compat key must also point at the
    # autodiscovered file.
    assert status.get("csone_file") == autopicked, (
        f"Round 38 / Phase 1 regression: legacy ``csone_file`` "
        f"({status.get('csone_file')!r}) does not mirror the "
        f"autopicked path."
    )


def test_no_upload_no_autopick_leaves_provenance_blank(monkeypatch):
    """Round 38 / Phase 1: when neither an explicit upload nor an
    autodiscovery hit is available, the endpoint must still write the
    new provenance keys (so worker code can read them with
    ``status.get(...)`` without ``KeyError``), but
    ``csone_file_was_uploaded`` MUST be ``False`` and
    ``csone_file_path`` / legacy ``csone_file`` MUST be falsy
    (``None`` or empty).
    """
    _client, _aid, status = _post_leader(
        monkeypatch,
        upload=False,
        autopicked_path=None,
    )

    assert "csone_file_was_uploaded" in status, (
        "Round 38 / Phase 1 regression: ``csone_file_was_uploaded`` "
        "key is missing from status when no file is provided.  The "
        "worker's Pass 2 guard MUST be able to call "
        "``status.get('csone_file_was_uploaded')`` on every analysis "
        "id without KeyError."
    )
    assert "csone_file_path" in status, (
        "Round 38 / Phase 1 regression: ``csone_file_path`` key is "
        "missing from status when no file is provided."
    )

    assert status["csone_file_was_uploaded"] is False, (
        f"expected csone_file_was_uploaded=False when nothing was "
        f"provided; got {status['csone_file_was_uploaded']!r}"
    )
    assert not status["csone_file_path"], (
        f"expected csone_file_path to be falsy when nothing was "
        f"provided; got {status['csone_file_path']!r}"
    )
    assert not status.get("csone_file"), (
        f"expected legacy csone_file to be falsy when nothing was "
        f"provided; got {status.get('csone_file')!r}"
    )
