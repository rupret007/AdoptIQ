"""Round 68 / Build 42 (A2 / A3): pin the ``/api/version`` endpoint
contract and the restart-required banner wiring.

The endpoint exists so the operator can verify which binary is actually
serving the UI without opening a report.  ``restart_required: true``
indicates that a newer .app is installed on disk than the one currently
running in this process -- the exact trap that produced the Build 41
acceptance reports from a pre-Build-41 binary.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


REQUIRED_KEYS = {
    "ok",
    "version",
    "build",
    "process_started_at_utc",
    "code_loaded_at_utc",
    "dmg_install_at_utc",
    "restart_required",
    "frozen",
}


def _iso_z(offset_hours: float = 0.0) -> str:
    ts = datetime.now(timezone.utc) + timedelta(hours=offset_hours)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_api_version_returns_canonical_payload(client):
    resp = client.get("/api/version")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload is not None
    assert REQUIRED_KEYS.issubset(set(payload.keys()))
    assert payload["ok"] is True
    # ``version`` / ``build`` should resolve to the config constants.
    from config import ADOPTIQ_BUILD, ADOPTIQ_VERSION  # noqa: PLC0415

    assert payload["version"] == str(ADOPTIQ_VERSION)
    assert payload["build"] == str(ADOPTIQ_BUILD)


def test_api_version_process_started_at_utc_is_iso_z(client):
    resp = client.get("/api/version")
    payload = resp.get_json()
    assert payload["process_started_at_utc"].endswith("Z")
    # ``YYYY-MM-DDTHH:MM:SSZ`` -> 20 chars.
    assert len(payload["process_started_at_utc"]) == 20


def test_api_version_restart_required_false_in_dev(client):
    """In dev (``getattr(sys, 'frozen', False) == False``) the dmg
    install timestamp resolves to an empty string, so the
    ``restart_required`` invariant must be False."""

    resp = client.get("/api/version")
    payload = resp.get_json()
    if not payload["frozen"]:
        # In dev runs ``dmg_install_at_utc`` is empty; therefore the
        # comparison cannot fire.
        assert payload["dmg_install_at_utc"] == ""
        assert payload["restart_required"] is False


def test_api_version_restart_required_truthy_when_dmg_newer(monkeypatch, client):
    """Simulate the post-install / pre-restart condition by stubbing
    out the resolver helpers."""

    import _r68_build_label as helper  # noqa: PLC0415

    older = _iso_z(-2.0)  # process started 2h ago
    newer = _iso_z()  # dmg installed just now

    monkeypatch.setattr(helper, "_PROCESS_STARTED_AT_UTC", older, raising=False)
    monkeypatch.setattr(helper, "get_process_started_at_utc", lambda: older)
    monkeypatch.setattr(helper, "_resolve_dmg_install_at_utc", lambda: newer)
    monkeypatch.setattr(
        helper,
        "_resolve_code_loaded_at_utc",
        lambda: older,
    )

    resp = client.get("/api/version")
    payload = resp.get_json()
    assert payload["restart_required"] is True
    assert payload["process_started_at_utc"] == older
    assert payload["dmg_install_at_utc"] == newer


def test_api_version_restart_not_flagged_when_install_equal_to_process_start(
    monkeypatch, client
):
    """Equal mtimes (a build-and-immediately-launch scenario) must NOT
    trip the banner -- otherwise every fresh launch would show it."""

    import _r68_build_label as helper  # noqa: PLC0415

    same = _iso_z()
    monkeypatch.setattr(helper, "_PROCESS_STARTED_AT_UTC", same, raising=False)
    monkeypatch.setattr(helper, "get_process_started_at_utc", lambda: same)
    monkeypatch.setattr(helper, "_resolve_dmg_install_at_utc", lambda: same)
    monkeypatch.setattr(helper, "_resolve_code_loaded_at_utc", lambda: same)

    resp = client.get("/api/version")
    payload = resp.get_json()
    assert payload["restart_required"] is False


def test_base_template_has_restart_banner_markup():
    """``templates/base.html`` MUST pre-render the banner element so the
    JS only has to flip ``hidden``.  Source-shape pin so a future
    template refactor does not silently drop the banner."""

    body = (Path(__file__).resolve().parent.parent / "templates" / "base.html").read_text(
        encoding="utf-8"
    )
    assert "Round 68 / Build 42 (A2)" in body
    assert 'id="r68-restart-required-banner"' in body
    assert 'id="r68-restart-required-detail"' in body
    assert "r68_restart_banner.js" in body


def test_restart_banner_js_calls_api_version_once():
    """Source-shape pin: ``/api/version`` once per load; update status may poll."""

    body = (
        Path(__file__).resolve().parent.parent
        / "static"
        / "js"
        / "r68_restart_banner.js"
    ).read_text(encoding="utf-8")

    assert "/api/version" in body
    assert "restart_required" in body
    # Round 128: update banner polls /api/update/status; restart banner does not.
    assert "_R128_UPDATE_POLL_MS" in body
    assert "function runChecks()" in body
    assert "check();" in body
    assert "checkUpdate();" in body
    assert "setInterval(function () {\n            checkUpdate(true);" in body


@pytest.mark.parametrize(
    "css_class",
    [
        "alert",
        "alert-warning",
        "rounded-0",
    ],
)
def test_restart_banner_css_classes_present(css_class):
    body = (Path(__file__).resolve().parent.parent / "templates" / "base.html").read_text(
        encoding="utf-8"
    )
    assert css_class in body
