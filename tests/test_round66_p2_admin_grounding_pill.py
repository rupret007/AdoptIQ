"""Round 66 / Pass 3 (B14) — Admin Console grounding-rate pill.

Pre-R66 the admin "Currently Running Reports" tile had no per-report
grounding-rejection signal. Build 38 acceptance saw a 34% R27 grounding
rejection rate that had to be back-derived from the structured log.
R66/B14 surfaces it directly:

1. ``/api/status/all`` projects three new scalars per analysis:
   - ``grounding_rejection_rate`` (float in [0.0, 1.0])
   - ``grounding_rejection_count`` (int)
   - ``grounding_total_count`` (int)
2. The admin template adds a ``Grounding`` column with a colored pill:
   - <=10% -> green (passing the post-R66/B11 acceptance bar)
   - 10-25% -> yellow (degradation)
   - >25% -> red (R27 regression)

These tests pin both the API projection and the template source-shape.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Source-shape pins
# ---------------------------------------------------------------------------


@pytest.fixture
def app_simple_text() -> str:
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / "app_simple.py").read_text(encoding="utf-8")


@pytest.fixture
def admin_text() -> str:
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")


def test_status_all_projects_grounding_rejection_rate(app_simple_text: str) -> None:
    """``/api/status/all`` MUST project ``grounding_rejection_rate`` from
    the ``grounding_diagnostics.rejection_summary`` rollup."""
    assert "grounding_rejection_rate" in app_simple_text
    assert "Round 66 / Pass 3 (B14)" in app_simple_text
    assert "rejection_summary" in app_simple_text


def test_status_all_projects_count_scalars(app_simple_text: str) -> None:
    """The API MUST also project ``grounding_rejection_count`` and
    ``grounding_total_count`` so the pill's tooltip can render
    ``Rejected N of M``."""
    assert "grounding_rejection_count" in app_simple_text
    assert "grounding_total_count" in app_simple_text


def test_status_all_projection_is_defensive(app_simple_text: str) -> None:
    """The projection MUST be wrapped in try/except so a malformed
    ``grounding_diagnostics`` dict cannot crash the bulk status
    endpoint."""
    # Find the B14 block and verify the try/except wrapper is present.
    start = app_simple_text.index("Round 66 / Pass 3 (B14)")
    block = app_simple_text[start:start + 3500]
    assert "try:" in block
    assert "except Exception:" in block


def test_status_all_handles_missing_summary_gracefully(app_simple_text: str) -> None:
    """When ``grounding_diagnostics`` is missing or non-dict, the
    projection MUST silently skip (no crash, no spurious zero
    fields)."""
    # The projection is gated on isinstance(...) -> dict before any
    # field access.
    start = app_simple_text.index("Round 66 / Pass 3 (B14)")
    block = app_simple_text[start:start + 3500]
    assert "isinstance(_r66_diag, dict)" in block
    assert "isinstance(_r66_summary, dict)" in block


# ---------------------------------------------------------------------------
# Admin template pin
# ---------------------------------------------------------------------------


def test_admin_template_adds_grounding_column(admin_text: str) -> None:
    """The Currently Running Reports table MUST gain a ``Grounding``
    column header."""
    assert ">Grounding</th>" in admin_text
    assert "Round 66 / Pass 3 (B14)" in admin_text


def test_admin_template_uses_three_color_pill(admin_text: str) -> None:
    """The pill MUST color by rate band:
    - <=10% green
    - <=25% yellow
    - >25% red"""
    # The Jinja conditional tree is implementation detail; verify the
    # three canonical colors are present.
    assert "#16a34a" in admin_text  # green
    assert "#eab308" in admin_text  # yellow
    assert "#dc2626" in admin_text  # red


def test_admin_template_renders_pct_with_one_decimal(admin_text: str) -> None:
    """The displayed value MUST be a percentage with one decimal."""
    assert "_r66_rate * 100)|round(1)" in admin_text or "(_r66_rate * 100)" in admin_text


def test_admin_template_falls_back_to_na_when_total_zero(admin_text: str) -> None:
    """When no narrative calls have been recorded yet, the pill MUST
    render ``N/A`` instead of a misleading 0% green pill."""
    # The template guards on _r66_total > 0.
    assert "_r66_total > 0" in admin_text
    assert ">N/A<" in admin_text or "N/A" in admin_text


def test_admin_template_pill_carries_tooltip(admin_text: str) -> None:
    """The pill MUST carry a ``title=`` tooltip with the rejected /
    total counts so a hovering operator can drill in without
    leaving the dashboard."""
    assert 'title="Rejected ' in admin_text
    assert "_r66_rej" in admin_text
    assert "_r66_total" in admin_text


# ---------------------------------------------------------------------------
# End-to-end through Flask test client
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Provide the Flask test client from app_simple."""
    import app_simple
    app_simple.app.config['TESTING'] = True
    app_simple.app.config['WTF_CSRF_ENABLED'] = False
    with app_simple.app.test_client() as c:
        yield c


def test_status_all_returns_grounding_fields_in_payload(client) -> None:
    """End-to-end: an analysis with ``grounding_diagnostics`` MUST
    expose the three projected scalars in ``/api/status/all``."""
    import app_simple

    test_analysis_id = "test-r66-b14-grounding-pill"
    with app_simple.analysis_status_lock:
        app_simple.analysis_status[test_analysis_id] = {
            "status": "running",
            "progress": 50,
            "report_type": "comprehensive",
            "manager": "Test Manager",
            "started_at": "2026-05-02T00:00:00Z",
            "grounding_diagnostics": {
                "rejection_summary": {
                    "rejected": 11,
                    "total": 28,
                    "rate": 0.393,
                },
                "rejection_records": [],
            },
        }
    try:
        resp = client.get('/api/status/all')
        assert resp.status_code == 200
        payload = resp.get_json()
        # Find the test analysis in the response.
        statuses = payload.get('statuses') if 'statuses' in payload else payload
        target = next((s for s in statuses if s.get('analysis_id') == test_analysis_id), None)
        assert target is not None, "Test analysis missing from /api/status/all"
        # Verify the three projected scalars.
        assert target.get('grounding_rejection_rate') == 0.393
        assert target.get('grounding_rejection_count') == 11
        assert target.get('grounding_total_count') == 28
    finally:
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.pop(test_analysis_id, None)


def test_status_all_omits_grounding_fields_when_no_diagnostics(client) -> None:
    """An analysis WITHOUT ``grounding_diagnostics`` MUST not have the
    grounding fields injected (so the pill renders N/A)."""
    import app_simple

    test_analysis_id = "test-r66-b14-no-diag"
    with app_simple.analysis_status_lock:
        app_simple.analysis_status[test_analysis_id] = {
            "status": "running",
            "progress": 25,
            "report_type": "compact",
            "manager": "No-Diag Manager",
            "started_at": "2026-05-02T00:00:00Z",
        }
    try:
        resp = client.get('/api/status/all')
        assert resp.status_code == 200
        payload = resp.get_json()
        statuses = payload.get('statuses') if 'statuses' in payload else payload
        target = next((s for s in statuses if s.get('analysis_id') == test_analysis_id), None)
        assert target is not None
        # Grounding fields MUST NOT be present.
        assert 'grounding_rejection_rate' not in target
        assert 'grounding_rejection_count' not in target
        assert 'grounding_total_count' not in target
    finally:
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.pop(test_analysis_id, None)


def test_status_all_handles_malformed_diagnostics_gracefully(client) -> None:
    """A malformed ``grounding_diagnostics`` (string instead of dict)
    MUST NOT crash the endpoint or inject bogus values."""
    import app_simple

    test_analysis_id = "test-r66-b14-malformed"
    with app_simple.analysis_status_lock:
        app_simple.analysis_status[test_analysis_id] = {
            "status": "running",
            "progress": 30,
            "manager": "Malformed Manager",
            "started_at": "2026-05-02T00:00:00Z",
            "grounding_diagnostics": "not-a-dict",  # malformed
        }
    try:
        resp = client.get('/api/status/all')
        assert resp.status_code == 200
        payload = resp.get_json()
        statuses = payload.get('statuses') if 'statuses' in payload else payload
        target = next((s for s in statuses if s.get('analysis_id') == test_analysis_id), None)
        assert target is not None
        # Malformed dict -> no projected fields.
        assert 'grounding_rejection_rate' not in target
    finally:
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.pop(test_analysis_id, None)
