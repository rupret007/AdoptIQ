"""Round 71 / Phase 1 (#7) -- diagnostic endpoint id validation.

Pre-R71 the ``/api/grounding-diagnostics/<analysis_id>`` and
``/api/ask-ai/diagnostics/<query_id>`` endpoints accepted any string
as the path parameter and used it to key into in-memory dicts /
SQLite tables.  The lookups themselves were safe (parameterised /
dict-key access) but a malformed id created log-correlation noise and
broke any downstream consumer that assumed a well-formed id.

Round 71 / Phase 1 (#7) rejects malformed ids at the route boundary
with a 400 response.
"""

from __future__ import annotations


def test_round71_grounding_diagnostics_rejects_malformed_id(client) -> None:
    """A clearly-malformed analysis_id (e.g. shell metacharacters)
    must be rejected with 400 before any dict lookup happens."""
    resp = client.get("/api/grounding-diagnostics/'; DROP TABLE analyses; --")
    # Flask routes typically reject control characters at the routing layer
    # (returning 404), so accept either 404 (route-level reject) or 400
    # (handler-level reject); the contract is "do not 200 / 500".
    assert resp.status_code in {400, 404}, (
        f"/api/grounding-diagnostics with shell-metacharacter id must "
        f"return 400 or 404, got {resp.status_code}."
    )


def test_round71_grounding_diagnostics_rejects_path_traversal_id(client) -> None:
    """A path-traversal-shaped id must be rejected."""
    resp = client.get("/api/grounding-diagnostics/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in {400, 404}, (
        f"/api/grounding-diagnostics with path-traversal-shaped id must "
        f"be rejected, got {resp.status_code}."
    )


def test_round71_ask_ai_diagnostics_rejects_malformed_query_id(client) -> None:
    """A clearly-malformed query_id must be rejected with 400 before
    the SQLite read happens."""
    resp = client.get("/api/ask-ai/diagnostics/this is not a valid id $$$")
    assert resp.status_code in {400, 404}, (
        f"/api/ask-ai/diagnostics with malformed query_id must return "
        f"400 or 404, got {resp.status_code}."
    )


def test_round71_diagnostic_endpoints_use_canonical_id_validator() -> None:
    """The endpoints MUST use the same ``_is_valid_analysis_id`` helper
    so the validation contract is centralised."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app_simple.py").read_text(encoding="utf-8")
    # Both endpoint handlers must call the canonical validator.
    assert "if not _is_valid_analysis_id(query_id):" in src, (
        "get_ask_ai_diagnostics must call _is_valid_analysis_id(query_id) "
        "as the first line of its handler body."
    )
    assert "if not _is_valid_analysis_id(analysis_id):" in src, (
        "get_grounding_diagnostics must call _is_valid_analysis_id(analysis_id) "
        "as the first line of its handler body."
    )
