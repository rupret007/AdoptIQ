"""Round 36 / onedrive-sync-auth: pin that ``/api/intel/status`` and
``/api/corpus/status`` surface the OneDrive presence fields.

The Intelligence panel renderer
(``static/js/intel_status.js`` / ``classifyCorpusPanel``) keys off
two new fields on the ``boot`` object:

* ``boot.onedrive_status``     -- ``"synced" | "not_synced" | "unknown" | None``
* ``boot.onedrive_file_count`` -- ``int | None``

Without these on the wire the panel falls through to its
``unknown`` state (grey "checking..." pill) and the user has no
visibility into whether their OneDrive desktop client is providing
the daily-refresh signal.

This test pins:

* The default ``boot`` dict (returned when the bootstrap import
  itself fails) MUST include both keys (set to ``None``) so the
  JS panel can read them without throwing on undefined property
  access.
* The success branch MUST project both fields off
  ``CorpusBootState`` via ``getattr`` (forward-compat).
* End-to-end: ``GET /api/intel/status`` returns both keys with a
  fresh Flask test client.
* The legacy ``boot.sharepoint`` field is still present (back-compat
  with old JS in cached browser tabs) and is always ``None``
  (MSAL is gone).
"""

# ruff: noqa: E501

from __future__ import annotations
from source_shape_utils import assert_in_source

import os
import re
from typing import Any, Dict


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_payload_builder() -> str:
    src_path = os.path.join(_REPO_ROOT, "app_simple.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    match = re.search(
        r"def _r17_corpus_status_payload\(\)[^\n]*:\n(.*?)\n    return payload\n",
        text,
        re.DOTALL,
    )
    assert match is not None, (
        "could not locate _r17_corpus_status_payload body in app_simple.py "
        "-- the test relies on this function being the single source of "
        "truth for the /api/intel/status and /api/corpus/status payloads."
    )
    return match.group(1)


def test_default_payload_includes_r36_onedrive_keys():
    """The default branch of ``_r17_corpus_status_payload`` (taken
    when bootstrap import fails) MUST set both R36 fields to None
    so the JS panel can read them with falsy semantics."""
    body = _read_payload_builder()
    for key in (
        '"onedrive_status": None',
        '"onedrive_file_count": None',
    ):
        assert_in_source(body, key, label='body')


def test_success_branch_projects_r36_onedrive_via_getattr():
    """The success branch MUST read the R36 fields off
    ``boot_state`` via ``getattr`` (forward-compat for older
    CorpusBootState classes that pre-date Round 36)."""
    body = _read_payload_builder()
    fragments = (
        'getattr(boot_state, "onedrive_status", None)',
        'getattr(\n                boot_state, "onedrive_file_count", None,\n            )',
    )
    for fragment in fragments:
        assert_in_source(body, fragment, label='body')


def test_intel_status_endpoint_returns_r36_keys():
    """End-to-end: instantiate the Flask app and assert that the
    JSON response carries the two R36 keys regardless of whether
    OneDrive is actually synced on the test host."""
    import app_simple  # type: ignore  # noqa: F401

    client = app_simple.app.test_client()
    resp = client.get("/api/intel/status")
    assert resp.status_code == 200
    payload: Dict[str, Any] = resp.get_json()
    assert isinstance(payload, dict)
    boot = payload.get("boot")
    assert isinstance(boot, dict), (
        f"payload['boot'] missing or not a dict: {boot!r}"
    )

    for key in ("onedrive_status", "onedrive_file_count"):
        assert key in boot, (
            f"payload['boot'] missing R36 field {key!r}; "
            "static/js/intel_status.js / classifyCorpusPanel reads "
            "this field to choose between 'baked_synced' / "
            "'baked_not_synced' / 'fresh_indexing' / 'fresh_not_synced'. "
            f"Got keys: {sorted(boot.keys())!r}"
        )


def test_intel_status_keeps_sharepoint_key_for_back_compat():
    """The MSAL stack is gone but the legacy ``boot.sharepoint``
    key is kept for back-compat with cached browser tabs running
    pre-Round-36 JS.  It must always be ``None`` (no leak of the
    deleted SharePoint state) and must never be missing."""
    import app_simple  # type: ignore  # noqa: F401

    client = app_simple.app.test_client()
    resp = client.get("/api/intel/status")
    payload = resp.get_json()
    boot = payload["boot"]
    assert_in_source(boot, "sharepoint", label='boot')
    assert boot["sharepoint"] is None, (
        f"boot.sharepoint must be None after Round 36 -- got "
        f"{boot['sharepoint']!r}.  A non-None value indicates the "
        "deleted MSAL state is still being projected somewhere."
    )


def test_corpus_status_endpoint_also_surfaces_r36_keys():
    """``/api/corpus/status`` shares the same payload builder; pin
    that R36 keys are present there too so the admin tile (which
    polls the corpus endpoint, not the intel endpoint) does not
    regress."""
    import app_simple  # type: ignore  # noqa: F401

    client = app_simple.app.test_client()
    resp = client.get("/api/corpus/status")
    assert resp.status_code == 200
    boot = resp.get_json()["boot"]
    for key in ("onedrive_status", "onedrive_file_count"):
        assert key in boot, (
            f"/api/corpus/status missing R36 boot.{key}; the admin "
            "Corpus tile shares this payload."
        )
