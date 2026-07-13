"""Round 43 / Phase 5 regression test.

Pin ``update_analysis_status`` to auto-attach ``traceback.format_exc()``
under the ``error_traceback`` key whenever it's called inside an active
``except`` block with ``status == 'error'``.

Pre-fix the user-facing error in ``analysis_status.json`` was just
``"Analysis failed. Please check the Admin page for details."`` -- with
zero traceback information.  The actual stack lived in
``~/.adoptiq/adoptiq.<pid>.log`` and required deep log archaeology to
diagnose.  Round 43 / Phase 5 makes every error catch site auto-persist
the active traceback (truncated to 8 KB) so the admin console can show
the operator the real cause.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def fake_analysis_status(monkeypatch):
    """Replace the global analysis_status with an empty dict so we can
    inspect what update_analysis_status persists."""
    import app_simple

    fake_status: dict = {"test-aid-43": {}}
    monkeypatch.setattr(app_simple, "analysis_status", fake_status)
    # Stub out save_analysis_status so we don't touch disk in tests.
    monkeypatch.setattr(app_simple, "save_analysis_status", lambda: None)
    return fake_status


def test_update_analysis_status_auto_attaches_traceback_in_except_block(
    fake_analysis_status,
) -> None:
    """When an exception is in flight and the caller passes
    ``status='error'`` without an explicit ``error_traceback`` key,
    ``update_analysis_status`` MUST auto-attach the active traceback.
    """
    from app_simple import update_analysis_status

    try:
        raise ValueError("simulated demo crash like Worksheet autofilter overlap")
    except ValueError:
        update_analysis_status(
            "test-aid-43",
            {"status": "error", "message": "Analysis failed."},
            save=False,
        )

    persisted = fake_analysis_status["test-aid-43"]
    assert persisted.get("status") == "error"
    assert "error_traceback" in persisted, (
        "Round 43 / Phase 5: update_analysis_status must auto-attach "
        "traceback.format_exc() to the persisted status dict when called "
        "inside an except block with status='error'."
    )
    tb_text = persisted["error_traceback"]
    assert "ValueError" in tb_text
    assert "simulated demo crash" in tb_text
    assert "test_round43_status_traceback_persisted" in tb_text or "raise ValueError" in tb_text, (
        "the persisted traceback should include the call-site stack frame, "
        "not just the exception message."
    )


def test_update_analysis_status_respects_explicit_error_traceback(
    fake_analysis_status,
) -> None:
    """If the caller already provides ``error_traceback`` in the updates
    dict, ``update_analysis_status`` MUST NOT overwrite it -- callers that
    construct a richer traceback (e.g. consistency-check error JSON) need
    their value preserved.
    """
    from app_simple import update_analysis_status

    try:
        raise RuntimeError("would-be-auto-attached")
    except RuntimeError:
        update_analysis_status(
            "test-aid-43",
            {
                "status": "error",
                "error_traceback": "explicit value from caller",
            },
            save=False,
        )

    assert fake_analysis_status["test-aid-43"]["error_traceback"] == "explicit value from caller"


def test_update_analysis_status_no_op_when_status_not_error(
    fake_analysis_status,
) -> None:
    """When ``status`` is anything other than ``'error'`` (e.g. ``'running'``,
    ``'completed'``), the auto-attach must NOT fire even if an exception is
    in flight.
    """
    from app_simple import update_analysis_status

    try:
        raise ValueError("not actually an error path")
    except ValueError:
        update_analysis_status(
            "test-aid-43",
            {"status": "running", "progress": 50},
            save=False,
        )

    assert "error_traceback" not in fake_analysis_status["test-aid-43"]


def test_update_analysis_status_truncates_huge_traceback(
    fake_analysis_status,
) -> None:
    """The auto-attached traceback MUST be truncated to ~8 KB so a deeply
    nested stack can't bloat ``analysis_status.json``.
    """
    from app_simple import update_analysis_status

    def _recursive(depth: int) -> None:
        if depth <= 0:
            raise ValueError("deep error")
        _recursive(depth - 1)

    try:
        _recursive(500)  # stack depth large enough to overflow 8 KB
    except (ValueError, RecursionError):
        update_analysis_status(
            "test-aid-43",
            {"status": "error"},
            save=False,
        )

    tb = fake_analysis_status["test-aid-43"].get("error_traceback", "")
    assert tb, "traceback should still be attached"
    assert len(tb) <= 8000, (
        f"traceback must be truncated to <=8000 chars; got {len(tb)}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
