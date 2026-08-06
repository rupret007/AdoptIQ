"""Round 35 / native-corpus: pin that ``/api/intel/status`` projects
the Round 35 ``CorpusBootState`` fields onto the JSON ``boot`` dict.

The user-facing Intelligence panel (``static/js/intel_status.js``)
reads ``boot.source`` and ``boot.indexed_at`` to render the
"Indexed (sign in to refresh)" / "Last bake YYYY-MM-DD" labels for
the new "baked" panel state introduced in Round 35.  Without these
fields on the wire the panel falls back to a generic "Status
unknown" pill even when a baked corpus is loaded -- a silent
regression that would only show up on installs where the user has
never connected their MSAL session.

The other three timer-state fields (``last_successful_refresh_ts``,
``last_refresh_attempt_ts``, ``last_refresh_error``) are not
consumed by the JS today but are surfaced for diagnostics so an
operator can debug a stalled daily-refresh worker via curl without
attaching a debugger.

Tests pin both the success branch (state objects projected) and
the default branch (None defaults so the JS does not crash on
undefined keys when the bootstrap import itself fails).
"""

# ruff: noqa: E501

from __future__ import annotations
from source_shape_utils import assert_in_source

import os
import re
from typing import Any, Dict


def _read_payload_builder() -> str:
    """Load app_simple.py and return the body of
    ``_r17_corpus_status_payload`` so tests can grep for the
    Round 35 field projections without spinning up a Flask client.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_path = os.path.join(repo_root, "app_simple.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        text = fh.read()

    match = re.search(
        r"def _r17_corpus_status_payload\(\)[^\n]*:\n(.*?)\n    return payload\n",
        text,
        re.DOTALL,
    )
    assert match is not None, (
        "could not locate _r17_corpus_status_payload body in app_simple.py"
    )
    return match.group(1)


def test_default_payload_includes_r35_boot_fields() -> None:
    """When the bootstrap import fails, the default ``boot`` dict
    MUST still expose the Round 35 keys (set to ``None``) so the
    JS panel can read them with falsy semantics rather than throw
    on undefined property access."""
    body = _read_payload_builder()
    for key in (
        '"source": None',
        '"indexed_at": None',
        '"last_successful_refresh_ts": None',
        '"last_successful_update_at": None',
        '"last_refresh_attempt_ts": None',
        '"last_refresh_error": None',
        '"embedder_status": None',
        '"embedder_load_error": None',
        '"dense_retrieval_status": None',
        '"dense_vectors_upserted": None',
        '"dense_vectors_considered": None',
        '"dense_vector_error": None',
        '"ask_ai_retrieval_method": None',
    ):
        assert_in_source(body, key, label='body')


def test_success_branch_threads_r35_boot_fields_via_getattr() -> None:
    """The success branch MUST read the Round 35 fields off
    ``boot_state`` via ``getattr`` (forward-compat for older
    CorpusBootState classes that pre-date Round 35).  The five
    field names below are the contract between
    ``corpus_bootstrap.CorpusBootState`` and
    ``static/js/intel_status.js``."""
    body = _read_payload_builder()
    for fragment in (
        'getattr(boot_state, "source", None)',
        'getattr(boot_state, "indexed_at", None)',
        'getattr(\n                boot_state, "last_successful_refresh_ts", None,\n            )',
        'getattr(\n                boot_state, "last_refresh_attempt_ts", None,\n            )',
        'getattr(\n                boot_state, "last_refresh_error", None,\n            )',
        'getattr(boot_state, "embedder_status", None)',
        'getattr(boot_state, "embedder_load_error", None)',
        'getattr(boot_state, "dense_retrieval_status", None)',
        'getattr(boot_state, "dense_vectors_upserted", None)',
        'getattr(boot_state, "dense_vectors_considered", None)',
        'getattr(boot_state, "dense_vector_error", None)',
    ):
        assert_in_source(body, fragment, label='body')


def test_intel_status_endpoint_returns_r35_keys_at_startup() -> None:
    """End-to-end: instantiate the Flask app via ``app_simple.app``
    and assert that ``GET /api/intel/status`` returns the Round 35
    keys regardless of whether the bake actually happened.  Pins the
    JSON shape that the user-facing JS panel depends on."""
    # Import lazily so the test does not pay the Flask import cost
    # for collection-only test runs.
    import app_simple  # type: ignore  # noqa: F401

    client = app_simple.app.test_client()
    resp = client.get("/api/intel/status")
    assert resp.status_code == 200, (
        f"/api/intel/status returned HTTP {resp.status_code}; "
        f"this endpoint MUST be 200 even when the corpus is "
        f"unavailable (Round 17 / Phase D.5 contract)."
    )
    payload: Dict[str, Any] = resp.get_json()
    assert isinstance(payload, dict), (
        f"/api/intel/status payload is not a dict (got {type(payload)!r})"
    )
    boot = payload.get("boot")
    assert isinstance(boot, dict), (
        f"payload['boot'] missing or not a dict: {boot!r}"
    )

    for key in (
        "source",
        "indexed_at",
        "last_successful_refresh_ts",
        "last_successful_update_at",
        "last_refresh_attempt_ts",
        "last_refresh_error",
        "embedder_status",
        "embedder_load_error",
        "dense_retrieval_status",
        "dense_vectors_upserted",
        "dense_vectors_considered",
        "dense_vector_error",
        "ask_ai_retrieval_method",
    ):
        assert key in boot, (
            f"payload['boot'] missing Round 35 field {key!r}; "
            f"static/js/intel_status.js depends on these for the "
            f"AdoptIQ Knowledge Corpus panel.  Got keys: "
            f"{sorted(boot.keys())!r}"
        )
